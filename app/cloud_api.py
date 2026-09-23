"""Stateless, scale-to-zero API for the Container Apps Jobs deployment."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import mimetypes
import os
import re
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from azure.core.exceptions import AzureError, ResourceNotFoundError
from app import main as common
from app import builder, tutor
from app.cloud_store import CloudStore, StoreError, CHUNK_BYTES, MAX_BYTES, RETENTION, public


@asynccontextmanager
async def lifespan(app):
    if not common.PUBLIC_ORIGIN.startswith('https://') and os.getenv('ALLOW_STORAGE_EMULATOR') != '1':
        raise RuntimeError('Cloud deployments require an HTTPS PUBLIC_ORIGIN.')
    app.state.store = CloudStore()
    await run_in_threadpool(app.state.store.initialize)
    yield


app = FastAPI(title='Sejong PDF Studio', lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount('/static', StaticFiles(directory=common.STATIC), name='static')
app.middleware('http')(common.headers)


@app.exception_handler(StoreError)
async def store_error(request, exc):
    return JSONResponse({'detail': exc.detail}, status_code=exc.code)


@app.exception_handler(AzureError)
async def storage_error(request, exc):
    return JSONResponse({'detail': 'Temporary storage error. Please retry shortly.'}, status_code=503)


def storage(request):
    return request.app.state.store


def client_ip(request):
    # ACA's ingress appends the verified connecting IP as the last XFF entry.
    # Only trust this header on the ACA-only cloud deployment, never the local emulator.
    host = request.client.host if request.client else 'unknown'
    if os.getenv('TRUST_AZURE_INGRESS') == '1':
        host = request.headers.get('x-forwarded-for', host).split(',')[-1].strip()
    return hashlib.sha256(host.encode()).hexdigest()


@app.get('/')
def home():
    return FileResponse(common.STATIC / 'index.html')


@app.get('/healthz')
def health():
    return {'status': 'ok', 'mode': 'azure-jobs'}


@app.get('/api/session')
def session(request: Request, response: Response):
    result = common.session(request, response)
    result.update(upload_mode='chunks', chunk_bytes=CHUNK_BYTES, max_upload_mb=MAX_BYTES//1024**2,
                  retention_hours=RETENTION//3600, tutor=tutor.config(),
                  expiry_notice='Access expires after 24 hours. Storage cleanup may take longer.')
    return result


class Upload(BaseModel):
    name: str = Field(max_length=200)
    size: int
    ocr: bool = False
    languages: str = 'eng'


@app.post('/api/uploads', status_code=201)
def begin_upload(payload: Upload, request: Request):
    owner = common.mutation(request)
    return storage(request).create(owner, client_ip(request), payload.name, payload.size, payload.ocr, payload.languages)


@app.put('/api/uploads/{job_id}/chunks/{index}', status_code=204)
async def upload_chunk(job_id: str, index: int, request: Request):
    owner = common.mutation(request)
    await run_in_threadpool(storage(request).job, job_id, owner)
    data = bytearray()
    async with asyncio.timeout(90):
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > CHUNK_BYTES:
                raise StoreError(413, 'Upload chunk is too large.')
    await run_in_threadpool(storage(request).chunk, job_id, owner, index, bytes(data))
    return Response(status_code=204)


@app.post('/api/uploads/{job_id}/complete', status_code=202)
def complete_upload(job_id: str, request: Request):
    return storage(request).complete_upload(job_id, common.mutation(request))


@app.get('/api/jobs')
def jobs(request: Request):
    owner = common.identity(request)
    storage(request).repair_dispatches()
    return storage(request).list_jobs(owner)


@app.get('/api/jobs/{job_id}')
def job(job_id: str, request: Request):
    return public(storage(request).job(job_id, common.identity(request)))


@app.post('/api/jobs/{job_id}/retry')
def retry(job_id: str, request: Request):
    return storage(request).retry(job_id, common.mutation(request), client_ip(request))


@app.delete('/api/jobs/{job_id}', status_code=204)
def delete(job_id: str, request: Request):
    storage(request).delete(job_id, common.mutation(request))
    return Response(status_code=204)


def result_blob(job_id, filename, request, download=False):
    job = storage(request).job(job_id, common.identity(request))
    if job['status'] != 'completed':
        raise StoreError(409, 'Conversion is not complete.')
    try:
        client = storage(request).export(job_id, filename)
        storage(request).reserve_download(client.get_blob_properties().size)
        blob = client.download_blob()
    except ResourceNotFoundError:
        raise StoreError(404, 'File not found.')
    headers = {'Content-Security-Policy': "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'self'; sandbox allow-same-origin"}
    if download:
        headers['Content-Disposition'] = 'attachment; filename="converted-book.zip"'
    return StreamingResponse(blob.chunks(), media_type=mimetypes.guess_type(filename)[0] or 'application/octet-stream', headers=headers)


@app.get('/api/jobs/{job_id}/download')
def download(job_id: str, request: Request):
    return result_blob(job_id, 'book.zip', request, True)


@app.get('/books/{job_id}/{filename}')
def asset(job_id: str, filename: str, request: Request):
    if not re.fullmatch(r'(index\.html|manifest\.json|page-[1-9][0-9]*\.(html|png|json))', filename):
        raise StoreError(404, 'File not found.')
    return result_blob(job_id, filename, request)


@app.get('/course/{job_id}')
def course(job_id: str):
    # The reader is a static app; the data endpoints it calls enforce ownership.
    return FileResponse(common.STATIC / 'course.html')


async def load_manifest(job_id, request):
    store = storage(request)
    job = await run_in_threadpool(store.job, job_id, common.identity(request))
    if job['status'] != 'completed':
        raise StoreError(409, 'Conversion is not complete.')
    try:
        raw = await run_in_threadpool(lambda: store.export(job_id, 'manifest.json').download_blob().readall())
    except ResourceNotFoundError:
        raise StoreError(404, 'Course content is not available.')
    return json.loads(raw)


@app.get('/api/courses/{job_id}')
async def course_structure(job_id: str, request: Request):
    course = builder.build_course(await load_manifest(job_id, request))
    course['tutor'] = tutor.config()
    return course


class TutorRequest(BaseModel):
    question: str = Field(max_length=tutor.MAX_QUESTION)
    page: int = Field(ge=1)
    history: list[dict] = Field(default_factory=list, max_length=tutor.MAX_HISTORY * 2)


@app.post('/api/tutor/{job_id}')
async def ask_tutor(job_id: str, body: TutorRequest, request: Request):
    common.mutation(request)
    manifest = await load_manifest(job_id, request)
    page = tutor.locate_page(manifest, body.page)
    if page is None:
        raise StoreError(404, 'That page is not part of this course.')
    try:
        return await run_in_threadpool(tutor.answer, body.question, manifest.get('title', 'this textbook'), page, body.history)
    except tutor.TutorError as exc:
        raise StoreError(503, exc.message)
