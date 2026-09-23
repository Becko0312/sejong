import os
from pathlib import Path
import sqlite3

DATA = Path(os.getenv('SEJONG_DATA', 'data')).resolve()
MAX_BYTES = int(os.getenv('MAX_UPLOAD_MB', '200')) * 1024 * 1024
RETENTION = int(os.getenv('RETENTION_HOURS', '24')) * 3600
MAX_ACTIVE = int(os.getenv('MAX_ACTIVE_JOBS', '10'))
PER_SESSION = int(os.getenv('JOBS_PER_SESSION', '3'))
PER_IP = int(os.getenv('JOBS_PER_IP_DAY', '10'))


def connect():
    db = sqlite3.connect(DATA / 'jobs.sqlite3', timeout=30)
    db.row_factory = sqlite3.Row
    return db


def initialize():
    DATA.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, name TEXT, status TEXT, done INTEGER DEFAULT 0, total INTEGER DEFAULT 0, error TEXT, created REAL)')
        columns = {r['name'] for r in db.execute('PRAGMA table_info(jobs)')}
        for name, declaration in {'owner': "TEXT DEFAULT 'legacy'", 'expires': 'REAL DEFAULT 0', 'ocr': 'INTEGER DEFAULT 0',
                                  'languages': "TEXT DEFAULT 'eng'", 'attempts': 'INTEGER DEFAULT 0'}.items():
            if name not in columns:
                db.execute(f'ALTER TABLE jobs ADD COLUMN {name} {declaration}')
        db.execute('CREATE TABLE IF NOT EXISTS requests (ip TEXT, created REAL)')
        db.execute('CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(owner)')


def update(job_id, **values):
    with connect() as db:
        db.execute('UPDATE jobs SET ' + ','.join(f'{k}=?' for k in values) + ' WHERE id=?', [*values.values(), job_id])
