import csv
import hashlib
import json
import sqlite3
import unicodedata
import threading
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def identity(name):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(name or '')).casefold() if c.isalnum())


def bout_id(date, red, blue):
    return hashlib.sha256('|'.join([date, *sorted([identity(red), identity(blue)])]).encode()).hexdigest()[:24]


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'ufc.sqlite3'
        self._local = threading.local()
        self._metrics_lock = threading.Lock()
        self._metrics = Counter()
        self.record_queries = False
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS fights (id TEXT PRIMARY KEY, date TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS fighters (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS predictions (id TEXT PRIMARY KEY, date TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS latest_predictions (id TEXT PRIMARY KEY, date TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS prediction_revisions (id TEXT NOT NULL, revision TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (id, revision));
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions (name TEXT PRIMARY KEY, version INTEGER NOT NULL);
                INSERT OR IGNORE INTO revisions VALUES ('all',0);
                CREATE INDEX IF NOT EXISTS fights_date ON fights(date DESC,id);
                CREATE INDEX IF NOT EXISTS predictions_date ON predictions(date DESC,id);
                CREATE INDEX IF NOT EXISTS latest_predictions_date ON latest_predictions(date DESC,id);
                CREATE INDEX IF NOT EXISTS fighter_names ON fighters(json_extract(payload,'$.name') COLLATE NOCASE);
                CREATE TABLE IF NOT EXISTS fighter_bouts (fighter_id TEXT NOT NULL, bout_id TEXT NOT NULL, date TEXT NOT NULL, PRIMARY KEY(fighter_id,bout_id));
                CREATE INDEX IF NOT EXISTS fighter_bouts_date ON fighter_bouts(fighter_id,date DESC,bout_id);
            ''')
            for table in ('fights', 'fighters', 'predictions', 'latest_predictions', 'prediction_revisions', 'meta'):
                for operation in ('INSERT', 'UPDATE', 'DELETE'):
                    db.execute(f'''CREATE TRIGGER IF NOT EXISTS change_{table}_{operation} AFTER {operation} ON {table}
                        BEGIN
                        INSERT INTO revisions VALUES ('all',1) ON CONFLICT(name) DO UPDATE SET version=version+1;
                        INSERT INTO revisions VALUES ('{table}',1) ON CONFLICT(name) DO UPDATE SET version=version+1;
                        END''')
            db.executescript('''
                CREATE TRIGGER IF NOT EXISTS bout_insert AFTER INSERT ON fights BEGIN
                  INSERT OR REPLACE INTO fighter_bouts VALUES(identity(json_extract(NEW.payload,'$.red')),NEW.id,NEW.date);
                  INSERT OR REPLACE INTO fighter_bouts VALUES(identity(json_extract(NEW.payload,'$.blue')),NEW.id,NEW.date);
                END;
                CREATE TRIGGER IF NOT EXISTS bout_update AFTER UPDATE ON fights BEGIN
                  DELETE FROM fighter_bouts WHERE bout_id=OLD.id;
                  INSERT OR REPLACE INTO fighter_bouts VALUES(identity(json_extract(NEW.payload,'$.red')),NEW.id,NEW.date);
                  INSERT OR REPLACE INTO fighter_bouts VALUES(identity(json_extract(NEW.payload,'$.blue')),NEW.id,NEW.date);
                END;
                CREATE TRIGGER IF NOT EXISTS bout_delete AFTER DELETE ON fights BEGIN
                  DELETE FROM fighter_bouts WHERE bout_id=OLD.id;
                END;
            ''')
            if not db.execute("SELECT 1 FROM revisions WHERE name='derived_schema'").fetchone():
                for corner in ('red','blue'):
                    db.execute(f"INSERT OR IGNORE INTO fighter_bouts SELECT identity(json_extract(payload,'$.{corner}')),id,date FROM fights")
                db.execute("INSERT INTO revisions VALUES ('derived_schema',1)")

    def _connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.create_function('identity', 1, identity, deterministic=True)
        with self._metrics_lock:
            self._metrics['connections'] += 1
        if self.record_queries:
            def trace(statement):
                with self._metrics_lock:
                    self._metrics['statements'] += 1
            db.set_trace_callback(trace)
        return db

    @contextmanager
    def connect(self):
        existing = getattr(self._local, 'connection', None)
        if existing is not None:
            yield existing
            return
        db = self._connection()
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def scope(self):
        """Reuse one connection within a request or a short transaction, then close.

        Connections are never shared across threads or held open across network
        scraping. This avoids long-lived Windows file handles and stale reads.
        """
        if getattr(self._local, 'connection', None) is not None:
            yield self._local.connection
            return
        with self.connect() as db:
            self._local.connection = db
            try:
                yield db
            finally:
                del self._local.connection

    def diagnostics(self):
        with self._metrics_lock:
            return dict(self._metrics)

    def revision(self, table='all'):
        with self.connect() as db:
            row = db.execute('SELECT version FROM revisions WHERE name=?', (table,)).fetchone()
        return row[0] if row else 0

    def snapshot(self):
        with self.connect() as db:
            if not db.in_transaction:
                db.execute('BEGIN')
            return dict(revision=db.execute("SELECT version FROM revisions WHERE name='all'").fetchone()[0],
                        tables={table: [json.loads(r[0]) for r in db.execute(f'SELECT payload FROM {table}')]
                                for table in ('fights','fighters','predictions','latest_predictions')},
                        meta={k: json.loads(v) for k,v in db.execute('SELECT key,payload FROM meta')})

    def meta_many(self, keys):
        if not keys:
            return {}
        with self.connect() as db:
            return {key: json.loads(value) for key,value in db.execute(
                'SELECT key,payload FROM meta WHERE key IN (' + ','.join('?' for _ in keys) + ')', list(keys))}

    def set_meta_many(self, values):
        with self.connect() as db:
            db.executemany('INSERT OR REPLACE INTO meta VALUES (?,?)', [(k,json.dumps(v)) for k,v in values.items()])

    def fighters_by_names(self, names):
        keys = sorted({identity(name) for name in names})
        if not keys:
            return {}
        with self.connect() as db:
            return {key: json.loads(value) for key,value in db.execute(
                'SELECT id,payload FROM fighters WHERE id IN (' + ','.join('?' for _ in keys) + ')', keys)}

    def fighter_history(self, name, page=1, per_page=25):
        with self.connect() as db:
            key = identity(name)
            count = db.execute('SELECT COUNT(*) FROM fighter_bouts WHERE fighter_id=?', (key,)).fetchone()[0]
            rows = db.execute('''SELECT f.payload FROM fighter_bouts b JOIN fights f ON f.id=b.bout_id
                WHERE b.fighter_id=? ORDER BY b.date DESC,b.bout_id LIMIT ? OFFSET ?''', (key,per_page,(page-1)*per_page)).fetchall()
        return dict(items=[json.loads(r[0]) for r in rows], total=count, page=page, per_page=per_page)

    def fighter_page(self, query='', division='', page=1, per_page=25):
        where, args = [], []
        if query:
            needle = identity(query)
            where.append('id LIKE ?' if needle else '0')
            if needle:
                args.append('%' + needle + '%')
        if division:
            where.append("json_extract(payload,'$.weight_class') = ?")
            args.append(division)
        clause = ' WHERE ' + ' AND '.join(where) if where else ''
        with self.connect() as db:
            count = db.execute('SELECT COUNT(*) FROM fighters' + clause, args).fetchone()[0]
            rows = db.execute('SELECT payload FROM fighters' + clause + " ORDER BY json_extract(payload,'$.name'),id LIMIT ? OFFSET ?", args+[per_page,(page-1)*per_page]).fetchall()
        return dict(items=[json.loads(r[0]) for r in rows], total=count, page=page, per_page=per_page)

    def recent_bouts(self, names, limit=6):
        keys = sorted({identity(name) for name in names})
        if not keys:
            return {}
        placeholders = ','.join('?' for _ in keys)
        with self.connect() as db:
            rows = db.execute(f'''SELECT fighter_id,payload,total FROM (
                SELECT b.fighter_id,f.payload,COUNT(*) OVER(PARTITION BY b.fighter_id) total,
                  ROW_NUMBER() OVER(PARTITION BY b.fighter_id ORDER BY b.date DESC,b.bout_id) rn
                FROM fighter_bouts b JOIN fights f ON f.id=b.bout_id WHERE b.fighter_id IN ({placeholders})
                ) WHERE rn<=?''',keys+[limit]).fetchall()
        result = {}
        for key,payload,total in rows:
            entry = result.setdefault(key,dict(items=[],total=total))
            entry['items'].append(json.loads(payload))
        return result

    def meta(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))

    def rows(self, table):
        if table not in {'fights', 'fighters', 'predictions', 'latest_predictions', 'prediction_revisions'}:
            raise ValueError('Unknown table')
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute(f'SELECT payload FROM {table}')]

    def settled_predictions(self):
        with self.connect() as db:
            return [(json.loads(p),json.loads(f)) for p,f in db.execute(
                'SELECT p.payload,f.payload FROM predictions p JOIN fights f ON f.id=p.id')]

    def save_fight(self, fight):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO fights VALUES (?,?,?)', (fight['id'], fight['date'], json.dumps(fight)))

    def save_fighter(self, fighter):
        from .profiles import normalize_profile
        fighter = dict(fighter, normalized=normalize_profile(fighter))
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO fighters VALUES (?,?)', (identity(fighter['name']), json.dumps(fighter)))

    def save_prediction(self, prediction):
        # An issued forecast is immutable; corrections to results live in fights.
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO predictions VALUES (?,?,?)',
                       (prediction['id'], prediction['date'], json.dumps(prediction)))

    def publish_prediction(self, prediction):
        """Update the live view while retaining the original and substantive revisions."""
        inputs = {k:prediction.get(k) for k in ('red', 'blue', 'date', 'model_version', 'features', 'red_probability', 'fighter_stats')}
        if inputs['fighter_stats']:
            inputs['fighter_stats'] = {corner:{k:v for k,v in profile.items() if k != 'fetched_at'}
                                       for corner,profile in inputs['fighter_stats'].items()}
        revision = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:20]
        payload = json.dumps(dict(prediction, revision=revision))
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO predictions VALUES (?,?,?)', (prediction['id'], prediction['date'], payload))
            db.execute('INSERT OR REPLACE INTO latest_predictions VALUES (?,?,?)', (prediction['id'], prediction['date'], payload))
            db.execute('INSERT OR IGNORE INTO prediction_revisions VALUES (?,?,?)', (prediction['id'], revision, payload))

    def publish_predictions(self, predictions):
        with self.scope():
            for prediction in predictions:
                self.publish_prediction(prediction)

    def bootstrap(self, root):
        if self.meta('seeded') and self.meta('seeded_schema') == 5:
            return
        with self.scope():
            self._bootstrap(root)

    def _bootstrap(self, root):

        def _num(val):
            if val is None:
                return None
            s = str(val).strip()
            if not s:
                return None
            try:
                return float(s)
            except Exception:
                return None

        with (Path(root) / 'ufc-master.csv').open(encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                fight = dict(
                    id=bout_id(row['Date'], row['RedFighter'], row['BlueFighter']),
                    date=row['Date'], red=row['RedFighter'], blue=row['BlueFighter'],
                    winner=row['Winner'].lower() if row['Winner'] in ('Red', 'Blue') else 'other',
                    method=row.get('Finish', ''), weight_class=row.get('WeightClass', ''),
                    event='Historical UFC event', source='bundled ufc-master.csv',
                    red_age=_num(row.get('RedAge')),
                    blue_age=_num(row.get('BlueAge')),
                    red_height=_num(row.get('RedHeightCms')),
                    blue_height=_num(row.get('BlueHeightCms')),
                    red_reach=_num(row.get('RedReachCms')),
                    blue_reach=_num(row.get('BlueReachCms')),
                    red_weight=_num(row.get('RedWeightLbs')),
                    blue_weight=_num(row.get('BlueWeightLbs')),
                    red_stance=row.get('RedStance') or 'Orthodox',
                    blue_stance=row.get('BlueStance') or 'Orthodox',
                    red_sig_str_landed=_num(row.get('RedAvgSigStrLanded')),
                    blue_sig_str_landed=_num(row.get('BlueAvgSigStrLanded')),
                    red_sig_str_pct=_num(row.get('RedAvgSigStrPct')),
                    blue_sig_str_pct=_num(row.get('BlueAvgSigStrPct')),
                    red_td_landed=_num(row.get('RedAvgTDLanded')),
                    blue_td_landed=_num(row.get('BlueAvgTDLanded')),
                    red_td_pct=_num(row.get('RedAvgTDPct')),
                    blue_td_pct=_num(row.get('BlueAvgTDPct')),
                    red_sub_att=_num(row.get('RedAvgSubAtt')),
                    blue_sub_att=_num(row.get('BlueAvgSubAtt')),
                    red_longest_streak=_num(row.get('RedLongestWinStreak')),
                    blue_longest_streak=_num(row.get('BlueLongestWinStreak')),
                    red_title_bouts=_num(row.get('RedTotalTitleBouts')),
                    blue_title_bouts=_num(row.get('BlueTotalTitleBouts')),
                    red_ko_wins=_num(row.get('RedWinsByKO')),
                    blue_ko_wins=_num(row.get('BlueWinsByKO')),
                    red_sub_wins=_num(row.get('RedWinsBySubmission')),
                    blue_sub_wins=_num(row.get('BlueWinsBySubmission')),
                    red_dec_wins=_num(row.get('RedWinsByDecisionUnanimous')),
                    blue_dec_wins=_num(row.get('BlueWinsByDecisionUnanimous')),
                    red_rank=_num(row.get('RMatchWCRank')),
                    blue_rank=_num(row.get('BMatchWCRank')),
                    red_odds=_num(row.get('RedOdds')),
                    blue_odds=_num(row.get('BlueOdds')),
                    title_bout=int(row.get('TitleBout', '0') in ('1', 'True', 'true')),
                    total_rounds=int(_num(row.get('NumberOfRounds')) or 3)
                )
                self.save_fight(fight)
        with (Path(root) / 'fighter_stats.csv').open(encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                if row['name']:
                    self.save_fighter(dict(row, source='Bundled snapshot (2024; not live)', fetched_at=None))
        self.set_meta('seeded', now())
        self.set_meta('seeded_schema', 5)
