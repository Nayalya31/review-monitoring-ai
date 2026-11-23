"""Consistent online SQLite backup; never copy a live WAL database with cp."""
import argparse
import os
import sqlite3
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('source')
parser.add_argument('destination')
a=parser.parse_args()
os.umask(0o077)
if Path(a.destination).exists():
    raise SystemExit('Destination already exists')
Path(a.destination).parent.mkdir(parents=True,exist_ok=True)
with sqlite3.connect('file:'+str(Path(a.source).resolve())+'?mode=ro',uri=True) as source, sqlite3.connect(a.destination) as dest:
    source.backup(dest)
    if dest.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
        raise SystemExit('Backup integrity failed')
print('Backup verified')
