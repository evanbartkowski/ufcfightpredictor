"""Install image-built serving state once, without training in the web factory."""
import os
from pathlib import Path
import shutil


def install_prebuilt(directory):
    source = os.environ.get('PREBUILT_DATA_DIR')
    if not source:
        return
    source, target = Path(source), Path(directory)
    target.mkdir(parents=True,exist_ok=True)
    # Never overwrite an existing user database or restore a mismatched model
    # onto it. Existing volumes are prepared by the explicit training job.
    if (target/'ufc.sqlite3').exists() or not all((source/name).is_file() for name in ('ufc.sqlite3','model.joblib')):
        return
    for name in ('ufc.sqlite3','model.joblib'):
        shutil.copy2(source/name,target/name)
