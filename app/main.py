import json
import os
import sqlite3
import threading
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

import pymupdf
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.converter import convert

DATA = Path(os.environ.get('SEJONG_DATA', 'data')).resolve()
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'jobs.sqlite3'
MAX_BYTES = 1024 * 1024 * 1024


def connect():
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def initialize():
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, name TEXT, status TEXT, done INTEGER DEFAULT 0, total INTEGER DEFAULT 0, error TEXT, created REAL)')
        db.execute("UPDATE jobs SET status='queued' WHERE status='processing'")


def update(job_id, **values):
    with connect() as db:
        db.execute('UPDATE jobs SET ' + ','.join(f'{k}=?' for k in values) + ' WHERE id=?', [*values.values(), job_id])


def worker(stop):
    while not stop.is_set():
        with connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
        if not row:
            stop.wait(1)
            continue
        job_id = row['id']
        update(job_id, status='processing', error=None)
        try:
            output = DATA / job_id / 'output'
            convert(DATA / job_id / 'source.pdf', output,
                    lambda done, total: update(job_id, done=done, total=total), title=Path(row['name']).stem)
            with zipfile.ZipFile(DATA / job_id / 'book.zip', 'w', zipfile.ZIP_DEFLATED) as bundle:
                for path in output.iterdir():
                    bundle.write(path, path.name)
            update(job_id, status='completed')
        except Exception as exc:
            update(job_id, status='failed', error=str(exc)[:500])


@asynccontextmanager
async def lifespan(app):
    initialize()
    stop = threading.Event()
    thread = threading.Thread(target=worker, args=(stop,), daemon=True)
    thread.start()
    yield
    stop.set()
    thread.join(timeout=5)


app = FastAPI(title='Sejong PDF Studio', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=Path(__file__).parent / 'static'), name='static')


@app.get('/')
def home():
    return FileResponse(Path(__file__).parent / 'static/index.html')


@app.get('/api/jobs')
def jobs():
    with connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM jobs ORDER BY created DESC')]


def get_job(job_id):
    with connect() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Job not found')
    return dict(row)


@app.post('/api/jobs', status_code=202)
async def upload(file: UploadFile):
    job_id = uuid.uuid4().hex
    folder = DATA / job_id
    folder.mkdir()
    source = folder / 'source.pdf'
    try:
        size = 0
        with source.open('wb') as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, 'Maximum file size is 1 GB')
                target.write(chunk)
        with pymupdf.open(source) as pdf:
            if not pdf.is_pdf or pdf.needs_pass or len(pdf) == 0:
                raise ValueError('Please upload an unencrypted PDF with at least one page.')
            total = len(pdf)
        with connect() as db:
            db.execute('INSERT INTO jobs(id,name,status,total,created) VALUES(?,?,?,?,?)',
                       (job_id, Path(file.filename or 'Untitled.pdf').name, 'queued', total, time.time()))
    except Exception as exc:
        source.unlink(missing_ok=True)
        folder.rmdir()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(400, 'Unable to read PDF. Upload a valid, unencrypted PDF.') from exc
    finally:
        await file.close()
    return get_job(job_id)


@app.get('/api/jobs/{job_id}')
def detail(job_id: str):
    return get_job(job_id)


@app.post('/api/jobs/{job_id}/retry')
def retry(job_id: str):
    if get_job(job_id)['status'] != 'failed':
        raise HTTPException(409, 'Only failed jobs can be retried')
    update(job_id, status='queued', error=None)
    return get_job(job_id)


@app.get('/api/jobs/{job_id}/download')
def download(job_id: str):
    if get_job(job_id)['status'] != 'completed':
        raise HTTPException(409, 'Conversion is not complete')
    return FileResponse(DATA / job_id / 'book.zip', filename='converted-book.zip')


@app.get('/books/{job_id}/{filename}')
def asset(job_id: str, filename: str):
    get_job(job_id)
    root = DATA / job_id / 'output'
    path = (root / filename).resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(404, 'Page not found')
    return FileResponse(path, headers={'Content-Security-Policy': "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"})
