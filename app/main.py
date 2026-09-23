"""HTTP service: accepts bounded raw uploads; never parses untrusted PDFs."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import time
import uuid
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from app import builder, store, tutor

PUBLIC_ORIGIN = os.getenv('PUBLIC_ORIGIN', '').rstrip('/')
COOKIE = 'sejong_session'
STATIC = Path(__file__).parent / 'static'


def identity(request):
    token = request.cookies.get(COOKIE, '')
    if not re.fullmatch(r'[0-9a-f]{64}', token):
        raise HTTPException(401, 'Open the converter to start a private session.')
    return hashlib.sha256(token.encode()).hexdigest()


def mutation(request):
    owner = identity(request)
    if not secrets.compare_digest(request.headers.get('x-csrf-token', ''), owner):
        raise HTTPException(403, 'Session check failed. Refresh the page and try again.')
    origin = request.headers.get('origin')
    expected = PUBLIC_ORIGIN or str(request.base_url).rstrip('/')
    if origin and origin != expected:
        raise HTTPException(403, 'Cross-site upload blocked.')
    return owner


@asynccontextmanager
async def lifespan(app):
    if PUBLIC_ORIGIN and not PUBLIC_ORIGIN.startswith('https://'):
        raise RuntimeError('Public deployments require an HTTPS PUBLIC_ORIGIN.')
    store.initialize()
    yield


app = FastAPI(title='Sejong PDF Studio', lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount('/static', StaticFiles(directory=STATIC), name='static')


@app.middleware('http')
async def headers(request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    response.headers.setdefault('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
    if PUBLIC_ORIGIN:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response


@app.get('/')
def home():
    return FileResponse(STATIC / 'index.html')


@app.get('/healthz')
def health():
    with store.connect() as db:
        db.execute('SELECT 1')
    return {'status': 'ok'}


@app.get('/api/session')
def session(request: Request, response: Response):
    token = request.cookies.get(COOKIE, '')
    if not re.fullmatch(r'[0-9a-f]{64}', token):
        token = secrets.token_hex(32)
    response.set_cookie(COOKIE, token, httponly=True, secure=bool(PUBLIC_ORIGIN), samesite='strict', max_age=30*86400)
    return {'csrf': hashlib.sha256(token.encode()).hexdigest(), 'max_upload_mb': store.MAX_BYTES // 1024 // 1024,
            'retention_hours': store.RETENTION // 3600, 'max_pages': int(os.getenv('MAX_PAGES', '500')),
            'tutor': tutor.config()}


def get_job(job_id, owner):
    with store.connect() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=? AND owner=? AND expires>?', (job_id, owner, time.time())).fetchone()
    if not row:
        raise HTTPException(404, 'Job not found or expired.')
    return dict(row)


def public(job):
    return {k: v for k, v in job.items() if k != 'owner'}


@app.get('/api/jobs')
def jobs(request: Request):
    owner = identity(request)
    with store.connect() as db:
        return [public(dict(r)) for r in db.execute('SELECT * FROM jobs WHERE owner=? AND expires>? ORDER BY created DESC', (owner, time.time()))]


@app.post('/api/jobs', status_code=202)
async def upload(request: Request, name: str = 'document.pdf', ocr: bool = False, languages: str = 'eng'):
    owner = mutation(request)
    if languages not in {'eng', 'kor', 'mon', 'kor+mon+eng'}:
        raise HTTPException(400, 'Unsupported OCR languages.')
    if request.headers.get('content-type', '').split(';')[0] != 'application/pdf':
        raise HTTPException(415, 'Send a PDF as the request body.')
    try:
        length = int(request.headers.get('content-length', '0'))
    except ValueError:
        raise HTTPException(400, 'Invalid upload length.')
    if length < 0 or length > store.MAX_BYTES:
        raise HTTPException(413, 'The file exceeds the upload limit.')
    ip = hashlib.sha256((request.client.host if request.client else 'unknown').encode()).hexdigest()
    job_id, now = uuid.uuid4().hex, time.time()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        active = db.execute("SELECT count(*) FROM jobs WHERE status IN ('uploading','queued','processing')").fetchone()[0]
        owned = db.execute('SELECT count(*) FROM jobs WHERE owner=? AND expires>?', (owner, now)).fetchone()[0]
        requests = db.execute('SELECT count(*) FROM requests WHERE ip=? AND created>?', (ip, now-86400)).fetchone()[0]
        if active >= store.MAX_ACTIVE or owned >= store.PER_SESSION or requests >= store.PER_IP:
            raise HTTPException(429, 'Conversion limit reached. Please try again later.')
        # Reserve enough free disk for every active job's maximum output and archive.
        reserve = (active + 1) * (store.MAX_BYTES + 2 * int(os.getenv('MAX_OUTPUT_MB', '1024')) * 1024**2) + 1024**3
        if shutil.disk_usage(store.DATA).free < reserve:
            raise HTTPException(503, 'Storage is busy. Please try again later.')
        db.execute('INSERT INTO jobs(id,name,status,created,owner,expires,ocr,languages) VALUES(?,?,?,?,?,?,?,?)',
                   (job_id, Path(name.replace('\\', '/')).name[:200] or 'document.pdf', 'uploading', now, owner, now+store.RETENTION, int(ocr), languages))
        db.execute('INSERT INTO requests VALUES(?,?)', (ip, now))
    folder = store.DATA / job_id
    try:
        folder.mkdir()
        size, signature = 0, b''
        async with asyncio.timeout(600):
            with (folder / 'source.pdf').open('wb') as target:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > store.MAX_BYTES:
                        raise HTTPException(413, 'The file exceeds the upload limit.')
                    signature = (signature + chunk)[:5]
                    target.write(chunk)
        if signature != b'%PDF-':
            raise HTTPException(400, 'Please upload a valid PDF file.')
        store.update(job_id, status='queued')
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        with store.connect() as db:
            db.execute('DELETE FROM jobs WHERE id=?', (job_id,))
        raise
    return public(get_job(job_id, owner))


@app.get('/api/jobs/{job_id}')
def detail(job_id: str, request: Request):
    return public(get_job(job_id, identity(request)))


@app.post('/api/jobs/{job_id}/retry')
def retry(job_id: str, request: Request):
    owner = mutation(request)
    get_job(job_id, owner)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        active = db.execute("SELECT count(*) FROM jobs WHERE status IN ('uploading','queued','processing')").fetchone()[0]
        if active >= store.MAX_ACTIVE:
            raise HTTPException(429, 'Conversion queue is full. Please retry later.')
        changed = db.execute("UPDATE jobs SET status='queued', error=NULL WHERE id=? AND owner=? AND status='failed' AND attempts<3", (job_id, owner)).rowcount
    if not changed:
        raise HTTPException(409, 'Only failed jobs with fewer than three attempts can be retried.')
    return public(get_job(job_id, owner))


@app.delete('/api/jobs/{job_id}', status_code=204)
def delete(job_id: str, request: Request):
    owner = mutation(request)
    get_job(job_id, owner)
    # The worker owns removal, avoiding races with in-flight PDF processes.
    with store.connect() as db:
        db.execute('UPDATE jobs SET expires=0 WHERE id=? AND owner=?', (job_id, owner))
    return Response(status_code=204)


@app.get('/api/jobs/{job_id}/download')
def download(job_id: str, request: Request):
    if get_job(job_id, identity(request))['status'] != 'completed':
        raise HTTPException(409, 'Conversion is not complete.')
    return FileResponse(store.DATA / job_id / 'book.zip', filename='converted-book.zip', media_type='application/zip')


@app.get('/books/{job_id}/{filename}')
def asset(job_id: str, filename: str, request: Request):
    if get_job(job_id, identity(request))['status'] != 'completed':
        raise HTTPException(409, 'Conversion is not complete.')
    if not re.fullmatch(r'(index\.html|manifest\.json|page-[1-9][0-9]*\.(html|png|json))', filename):
        raise HTTPException(404, 'Page not found.')
    path = store.DATA / job_id / 'output' / filename
    if not path.is_file():
        raise HTTPException(404, 'Page not found.')
    return FileResponse(path, headers={'Content-Security-Policy': "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'self'; sandbox allow-same-origin"})


@app.get('/course/{job_id}')
def course(job_id: str):
    # The reader is a static app; ownership is enforced by the data endpoints it calls.
    return FileResponse(STATIC / 'course.html')


def load_manifest(job_id, owner):
    if get_job(job_id, owner)['status'] != 'completed':
        raise HTTPException(409, 'Conversion is not complete.')
    path = store.DATA / job_id / 'output' / 'manifest.json'
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise HTTPException(404, 'Course content is not available.')


@app.get('/api/courses/{job_id}')
def course_structure(job_id: str, request: Request):
    course = builder.build_course(load_manifest(job_id, identity(request)))
    course['tutor'] = tutor.config()
    return course


class TutorRequest(BaseModel):
    question: str = Field('', max_length=tutor.MAX_QUESTION)
    page: int = Field(ge=1)
    history: list[dict] = Field(default_factory=list, max_length=tutor.MAX_HISTORY * 2)
    mode: str | None = Field(None, max_length=20)


@app.post('/api/tutor/{job_id}')
def ask_tutor(job_id: str, body: TutorRequest, request: Request):
    owner = mutation(request)
    manifest = load_manifest(job_id, owner)
    page = tutor.locate_page(manifest, body.page)
    if page is None:
        raise HTTPException(404, 'That page is not part of this course.')
    try:
        return tutor.answer(body.question, manifest.get('title', 'this textbook'), page, body.history, body.mode)
    except tutor.TutorError as exc:
        raise HTTPException(503, exc.message)
