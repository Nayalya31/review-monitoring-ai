import os
import sqlite3
import time
from pathlib import Path


def healthy(path, max_age=150):
    if not Path(path).is_file():
        return False
    try:
        with sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro',uri=True) as db:
            row=db.execute("SELECT value FROM meta WHERE key='heartbeat'").fetchone()
            return row is not None and time.time()-float(row[0])<max_age
    except (sqlite3.Error,ValueError):
        return False


if __name__=='__main__':
    raise SystemExit(0 if healthy(os.getenv('DATABASE_PATH','data/reviews.sqlite3')) else 1)
