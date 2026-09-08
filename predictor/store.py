import csv
import hashlib
import json
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def identity(name):
    return ''.join(c for c in unicodedata.normalize('NFKD', name).casefold() if c.isalnum())


def bout_id(date, red, blue):
    return hashlib.sha256('|'.join([date, *sorted([identity(red), identity(blue)])]).encode()).hexdigest()[:24]


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'ufc.sqlite3'
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS fights (id TEXT PRIMARY KEY, date TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS fighters (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS predictions (id TEXT PRIMARY KEY, date TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS latest_predictions (id TEXT PRIMARY KEY, date TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS prediction_revisions (id TEXT NOT NULL, revision TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (id, revision));
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

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

    def save_fight(self, fight):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO fights VALUES (?,?,?)', (fight['id'], fight['date'], json.dumps(fight)))

    def save_fighter(self, fighter):
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

    def bootstrap(self, root):
        if self.meta('seeded'):
            return
        with (Path(root) / 'ufc-master.csv').open(encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                fight = dict(id=bout_id(row['Date'], row['RedFighter'], row['BlueFighter']),
                             date=row['Date'], red=row['RedFighter'], blue=row['BlueFighter'],
                             winner=row['Winner'].lower() if row['Winner'] in ('Red', 'Blue') else 'other',
                             method=row['Finish'], weight_class=row['WeightClass'], event='Historical UFC event',
                             source='bundled ufc-master.csv')
                self.save_fight(fight)
        with (Path(root) / 'fighter_stats.csv').open(encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                if row['name']:
                    self.save_fighter(dict(row, source='Bundled snapshot (2024; not live)', fetched_at=None))
        self.set_meta('seeded', now())
