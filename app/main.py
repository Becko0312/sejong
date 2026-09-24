"""Book2Course platform: login/roles, admin-built courses, a client catalog, tutor."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from app import auth, builder, store, tts, tutor

PUBLIC_ORIGIN = os.getenv('PUBLIC_ORIGIN', '').rstrip('/')
STATIC = Path(__file__).parent / 'static'
OCR_LANGUAGES = {'eng', 'kor', 'mon', 'kor+mon+eng'}


@asynccontextmanager
async def lifespan(app):
    if PUBLIC_ORIGIN and not PUBLIC_ORIGIN.startswith('https://'):
        raise RuntimeError('Public deployments require an HTTPS PUBLIC_ORIGIN.')
    store.initialize()
    auth.seed_admin()
    yield


app = FastAPI(title='Book2Course', lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount('/static', StaticFiles(directory=STATIC), name='static')


@app.middleware('http')
async def headers(request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    response.headers.setdefault('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
    if PUBLIC_ORIGIN:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response


# ---- Auth helpers ----

def current(request):
    user = auth.session(request)
    if not user:
        raise HTTPException(401, 'Please sign in.')
    return user


def check_csrf(request, user):
    if not auth.csrf_ok(request, user):
        raise HTTPException(403, 'Session check failed. Refresh the page and try again.')
    origin = request.headers.get('origin')
    expected = PUBLIC_ORIGIN or str(request.base_url).rstrip('/')
    if origin and origin != expected:
        raise HTTPException(403, 'Cross-site request blocked.')


def require_admin(request, csrf=True):
    user = current(request)
    if user['role'] != 'admin':
        raise HTTPException(403, 'This action is for administrators only.')
    if csrf:
        check_csrf(request, user)
    return user


def account(user):
    return {'authenticated': True, 'username': user['username'], 'role': user['role'],
            'csrf': hashlib.sha256(user['token'].encode()).hexdigest() if 'token' in user else user['csrf'],
            'tutor': tutor.config(), 'tts': tts.config()}


def set_cookie(response, token):
    response.set_cookie(auth.COOKIE, token, httponly=True, secure=bool(PUBLIC_ORIGIN), samesite='strict', max_age=store.SESSION_TTL)


class Credentials(BaseModel):
    username: str = Field(max_length=32)
    password: str = Field(max_length=200)


@app.get('/')
def home():
    return FileResponse(STATIC / 'index.html')


@app.get('/healthz')
def health():
    with store.connect() as db:
        db.execute('SELECT 1')
    return {'status': 'ok'}


@app.get('/api/me')
def me(request: Request):
    user = auth.session(request)
    if not user:
        return {'authenticated': False, 'tutor': tutor.config()}
    return account(user)


@app.post('/api/register')
def register(body: Credentials, request: Request):
    check_csrf_origin(request)
    try:
        auth.register(body.username, body.password, 'client')
        token, role = auth.login(body.username, body.password)
    except auth.AuthError as exc:
        raise HTTPException(exc.code, exc.message)
    response = JSONResponse(account({'username': body.username.strip(), 'role': role, 'token': token}))
    set_cookie(response, token)
    return response


@app.post('/api/login')
def do_login(body: Credentials, request: Request):
    check_csrf_origin(request)
    try:
        token, role = auth.login(body.username, body.password)
    except auth.AuthError as exc:
        raise HTTPException(exc.code, exc.message)
    response = JSONResponse(account({'username': (body.username or '').strip(), 'role': role, 'token': token}))
    set_cookie(response, token)
    return response


@app.post('/api/logout')
def do_logout(request: Request):
    user = auth.session(request)
    if user:
        auth.logout(user['token'])
    response = Response(status_code=204)
    response.delete_cookie(auth.COOKIE)
    return response


def check_csrf_origin(request):
    # Login/register have no session yet, so only the same-origin check applies.
    origin = request.headers.get('origin')
    expected = PUBLIC_ORIGIN or str(request.base_url).rstrip('/')
    if origin and origin != expected:
        raise HTTPException(403, 'Cross-site request blocked.')


# ---- Courses: admin builds them, everyone signed in can study available ones ----

CATALOG_FIELDS = ('id', 'title', 'name', 'source_lang', 'target_lang', 'description', 'status', 'available', 'created')


def catalog_item(job):
    item = {k: job.get(k) for k in CATALOG_FIELDS}
    item['title'] = job.get('title') or job.get('name') or 'Untitled course'
    return item


@app.get('/api/catalog')
def catalog(request: Request):
    current(request)  # any signed-in user
    with store.connect() as db:
        rows = db.execute('SELECT * FROM jobs WHERE status=? AND available=1 AND expires>? ORDER BY created DESC',
                          ('completed', time.time())).fetchall()
    return [catalog_item(dict(r)) for r in rows]


@app.get('/api/admin/courses')
def admin_courses(request: Request):
    require_admin(request, csrf=False)
    with store.connect() as db:
        rows = db.execute('SELECT * FROM jobs WHERE expires>? ORDER BY created DESC', (time.time(),)).fetchall()
    items = []
    for r in rows:
        job = dict(r)
        item = catalog_item(job)
        item.update(done=job.get('done'), total=job.get('total'), error=job.get('error'),
                    attempts=job.get('attempts'), ocr=job.get('ocr'))
        items.append(item)
    return items


def viewable(job_id, user):
    with store.connect() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=? AND status=? AND expires>?', (job_id, 'completed', time.time())).fetchone()
    if not row:
        raise HTTPException(404, 'Course not found.')
    job = dict(row)
    if user['role'] != 'admin' and not job['available']:
        raise HTTPException(404, 'Course not found.')
    return job


def admin_job(job_id):
    with store.connect() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=? AND expires>?', (job_id, time.time())).fetchone()
    if not row:
        raise HTTPException(404, 'Course not found.')
    return dict(row)


@app.post('/api/jobs', status_code=202)
async def upload(request: Request, name: str = 'document.pdf', ocr: bool = False, languages: str = 'eng',
                 source_lang: str = '', target_lang: str = '', title: str = '', description: str = ''):
    admin = require_admin(request)
    if languages not in OCR_LANGUAGES:
        raise HTTPException(400, 'Unsupported OCR languages.')
    if request.headers.get('content-type', '').split(';')[0] != 'application/pdf':
        raise HTTPException(415, 'Send a PDF as the request body.')
    try:
        length = int(request.headers.get('content-length', '0'))
    except ValueError:
        raise HTTPException(400, 'Invalid upload length.')
    if length < 0 or length > store.MAX_BYTES:
        raise HTTPException(413, 'The file exceeds the upload limit.')
    job_id, now = uuid.uuid4().hex, time.time()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        active = db.execute("SELECT count(*) FROM jobs WHERE status IN ('uploading','queued','processing')").fetchone()[0]
        if active >= store.MAX_ACTIVE:
            raise HTTPException(429, 'Too many conversions running. Please try again shortly.')
        reserve = (active + 1) * (store.MAX_BYTES + 2 * int(os.getenv('MAX_OUTPUT_MB', '1024')) * 1024**2) + 1024**3
        if shutil.disk_usage(store.DATA).free < reserve:
            raise HTTPException(503, 'Storage is busy. Please try again later.')
        db.execute('INSERT INTO jobs(id,name,status,created,owner,expires,ocr,languages,title,source_lang,target_lang,description,available) '
                   'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)',
                   (job_id, Path(name.replace('\\', '/')).name[:200] or 'document.pdf', 'uploading', now, admin['username'],
                    now + store.COURSE_RETENTION, int(ocr), languages, title[:200] or None, source_lang[:40] or None,
                    target_lang[:40] or None, description[:1000] or None))
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
    return catalog_item(admin_job(job_id))


class CourseUpdate(BaseModel):
    available: bool | None = None
    title: str | None = Field(None, max_length=200)
    source_lang: str | None = Field(None, max_length=40)
    target_lang: str | None = Field(None, max_length=40)
    description: str | None = Field(None, max_length=1000)


@app.patch('/api/jobs/{job_id}')
def edit_course(job_id: str, body: CourseUpdate, request: Request):
    require_admin(request)
    admin_job(job_id)
    fields = {k: (int(v) if k == 'available' else v) for k, v in body.model_dump(exclude_none=True).items()}
    if fields:
        store.update(job_id, **fields)
    return catalog_item(admin_job(job_id))


@app.post('/api/jobs/{job_id}/retry')
def retry(job_id: str, request: Request):
    require_admin(request)
    admin_job(job_id)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        active = db.execute("SELECT count(*) FROM jobs WHERE status IN ('uploading','queued','processing')").fetchone()[0]
        if active >= store.MAX_ACTIVE:
            raise HTTPException(429, 'Conversion queue is full. Please retry later.')
        changed = db.execute("UPDATE jobs SET status='queued', error=NULL WHERE id=? AND status='failed' AND attempts<3", (job_id,)).rowcount
    if not changed:
        raise HTTPException(409, 'Only failed courses with fewer than three attempts can be retried.')
    return catalog_item(admin_job(job_id))


@app.delete('/api/jobs/{job_id}', status_code=204)
def delete(job_id: str, request: Request):
    require_admin(request)
    admin_job(job_id)
    with store.connect() as db:
        db.execute('UPDATE jobs SET expires=0, available=0 WHERE id=?', (job_id,))
    return Response(status_code=204)


@app.get('/api/jobs/{job_id}/download')
def download(job_id: str, request: Request):
    require_admin(request, csrf=False)
    if admin_job(job_id)['status'] != 'completed':
        raise HTTPException(409, 'Conversion is not complete.')
    return FileResponse(store.DATA / job_id / 'book.zip', filename='converted-book.zip', media_type='application/zip')


@app.get('/course/{job_id}')
def course(job_id: str):
    # Static reader shell; the data endpoints it calls enforce sign-in and availability.
    return FileResponse(STATIC / 'course.html')


def read_manifest(job_id):
    try:
        return json.loads((store.DATA / job_id / 'output' / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise HTTPException(404, 'Course content is not available.')


@app.get('/api/courses/{job_id}')
def course_structure(job_id: str, request: Request):
    job = viewable(job_id, current(request))
    course = builder.build_course(read_manifest(job_id))
    course['title'] = job.get('title') or course.get('title')
    course['source_lang'] = job.get('source_lang')
    course['target_lang'] = job.get('target_lang')
    course['tutor'] = tutor.config()
    course['tts'] = tts.config()
    return course


@app.get('/books/{job_id}/{filename}')
def asset(job_id: str, filename: str, request: Request):
    viewable(job_id, current(request))
    if not re.fullmatch(r'(index\.html|manifest\.json|page-[1-9][0-9]*\.(html|png|json))', filename):
        raise HTTPException(404, 'Page not found.')
    path = store.DATA / job_id / 'output' / filename
    if not path.is_file():
        raise HTTPException(404, 'Page not found.')
    return FileResponse(path, headers={'Content-Security-Policy': "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'self'; sandbox allow-same-origin"})


class TutorRequest(BaseModel):
    question: str = Field('', max_length=tutor.MAX_QUESTION)
    page: int = Field(ge=1)
    history: list[dict] = Field(default_factory=list, max_length=tutor.MAX_HISTORY * 2)
    mode: str | None = Field(None, max_length=20)


@app.post('/api/tutor/{job_id}')
def ask_tutor(job_id: str, body: TutorRequest, request: Request):
    user = current(request)
    check_csrf(request, user)
    job = viewable(job_id, user)
    if user['role'] != 'admin':
        now = time.time()
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            used = db.execute('SELECT count(*) FROM usage WHERE subject=? AND created>?', (user['username'], now - 86400)).fetchone()[0]
            if used >= store.CLIENT_TUTOR_PER_DAY:
                raise HTTPException(429, 'You have reached today\'s tutor limit. Please continue tomorrow.')
            db.execute('INSERT INTO usage(subject, created) VALUES(?,?)', (user['username'], now))
    manifest = read_manifest(job_id)
    page = tutor.locate_page(manifest, body.page)
    if page is None:
        raise HTTPException(404, 'That page is not part of this course.')
    try:
        return tutor.answer(body.question, job.get('title') or manifest.get('title', 'this textbook'), page, body.history, body.mode)
    except tutor.TutorError as exc:
        raise HTTPException(503, exc.message)


class TtsRequest(BaseModel):
    text: str = Field('', max_length=tts.MAX_TEXT)
    lang: str = Field('en-US', max_length=20)


@app.post('/api/tts')
def text_to_speech(body: TtsRequest, request: Request):
    # Azure neural voices for the reader's read-aloud; browsers lack mn-MN voices.
    user = current(request)
    check_csrf(request, user)
    try:
        audio = tts.synthesize(body.text, body.lang)
    except tts.TtsError as exc:
        raise HTTPException(503, exc.message)
    return Response(audio, media_type='audio/mpeg')
