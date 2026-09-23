"""Small public-beta cloud store: private blobs, a leased catalog, and a queue.

The catalog is deliberately bounded; no persistent VM, disk, SQL server or Redis.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
import uuid

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError, HttpResponseError
from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from azure.storage.queue import QueueClient

CHUNK_BYTES = 4 * 1024**2
MAX_BYTES = int(os.getenv('MAX_UPLOAD_MB', '200')) * 1024**2
RETENTION = int(os.getenv('RETENTION_HOURS', '24')) * 3600
ACTIVE = {'uploading', 'dispatching', 'queued', 'processing'}


class StoreError(Exception):
    def __init__(self, code, detail):
        self.code, self.detail = code, detail
        super().__init__(detail)


def visible(job, owner=None):
    if not job or job['expires'] <= time.time() or (owner is not None and job['owner'] != owner):
        raise StoreError(404, 'Job not found or expired.')
    return job


def public(job):
    return {k: job[k] for k in ('id', 'name', 'status', 'created', 'expires', 'done', 'total', 'ocr', 'languages', 'error', 'attempts', 'size')}


class CloudStore:
    def __init__(self):
        connection = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
        options = dict(connection_timeout=5, read_timeout=15, retry_total=2, retry_backoff_factor=0.5)
        queue_name = os.getenv('AZURE_QUEUE_NAME', 'conversions')
        if connection:
            if os.getenv('ALLOW_STORAGE_EMULATOR') != '1':
                raise RuntimeError('Connection strings are supported only in explicit local emulator mode.')
            self.blobs = BlobServiceClient.from_connection_string(connection, **options)
            self.queue = QueueClient.from_connection_string(connection, queue_name, **options)
        else:
            account = os.environ['AZURE_STORAGE_ACCOUNT']
            credential = ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])
            self.blobs = BlobServiceClient(f'https://{account}.blob.core.windows.net', credential=credential, **options)
            self.queue = QueueClient(f'https://{account}.queue.core.windows.net', queue_name, credential=credential, **options)
        self.catalog = self.blobs.get_blob_client('control', 'catalog.json')
        self.slot = self.blobs.get_blob_client('control', 'worker-slot')

    def initialize(self):
        # Infrastructure creates private containers and queue; only the emulator self-provisions.
        if os.getenv('ALLOW_STORAGE_EMULATOR') == '1':
            for name in ('control', 'uploads', 'exports'):
                try:
                    self.blobs.create_container(name)
                except ResourceExistsError:
                    pass
            try:
                self.queue.create_queue()
            except ResourceExistsError:
                pass
        if not self.slot.exists():
            try:
                self.slot.upload_blob(b'', overwrite=False)
            except ResourceExistsError:
                pass
        if self.catalog.exists():
            return
        try:
            self.catalog.upload_blob(json.dumps({'jobs': {}, 'requests': []}), overwrite=False)
        except ResourceExistsError:
            pass

    def read(self):
        return json.loads(self.catalog.download_blob().readall())

    @contextmanager
    def transaction(self):
        lease = None
        for attempt in range(30):
            try:
                lease = self.catalog.acquire_lease(lease_duration=60)
                break
            except HttpResponseError as exc:
                if exc.status_code != 409:
                    raise
                time.sleep(0.1)
        if lease is None:
            raise StoreError(503, 'The service is busy. Please retry shortly.')
        try:
            state = self.read()
            now = time.time()
            state['executions'] = [t for t in state.get('executions', []) if t > now-86400]
            state['requests'] = [r for r in state['requests'] if r['time'] > now-86400]
            state['jobs'] = {k: v for k, v in state['jobs'].items() if v['expires'] > now-86400}
            yield state
            self.catalog.upload_blob(json.dumps(state), overwrite=True, lease=lease)
        finally:
            lease.release()

    def job(self, job_id, owner=None):
        return visible(self.read()['jobs'].get(job_id), owner)

    def list_jobs(self, owner):
        return sorted([public(j) for j in self.read()['jobs'].values() if j['owner'] == owner and j['expires'] > time.time()], key=lambda j: j['created'], reverse=True)

    @staticmethod
    def quota(state, owner, ip, new_job=True):
        now = time.time()
        live = [j for j in state['jobs'].values() if j['expires'] > now]
        recent = [r for r in state['requests'] if r['time'] > now-86400]
        if (sum(j['status'] in ACTIVE for j in live) >= int(os.getenv('MAX_ACTIVE_JOBS', '10')) or
            (new_job and sum(j['owner'] == owner for j in live) >= int(os.getenv('JOBS_PER_SESSION', '3'))) or
            sum(r['ip'] == ip for r in recent) >= int(os.getenv('JOBS_PER_IP_DAY', '10')) or
            len(recent) >= int(os.getenv('DAILY_CONVERSION_LIMIT', '10'))):
            raise StoreError(429, 'The conversion allowance is used. Please try again later.')
        state['requests'].append({'ip': ip, 'time': now})

    def create(self, owner, ip, name, size, ocr, languages):
        if not 5 <= size <= MAX_BYTES:
            raise StoreError(413, 'The PDF exceeds the upload size limit or is empty.')
        if languages not in {'eng', 'kor', 'mon', 'kor+mon+eng'}:
            raise StoreError(400, 'Unsupported OCR languages.')
        now, job_id = time.time(), uuid.uuid4().hex
        job = {'id': job_id, 'owner': owner, 'name': Path(name.replace('\\', '/')).name[:200] or 'document.pdf',
               'size': size, 'status': 'uploading', 'created': now, 'expires': now+RETENTION, 'done': 0, 'total': 0,
               'error': None, 'ocr': ocr, 'languages': languages, 'attempts': 0, 'generation': 0, 'blocks': [],
               'source_ready': False, 'lease_until': 0, 'run_id': ''}
        with self.transaction() as state:
            self.quota(state, owner, ip)
            state['jobs'][job_id] = job
        self.source(job_id).upload_blob(b'', overwrite=False)
        return public(job)

    def source(self, job_id):
        return self.blobs.get_blob_client('uploads', f'{job_id}/source.pdf')

    def chunk(self, job_id, owner, index, data):
        job = self.job(job_id, owner)
        expected = min(CHUNK_BYTES, job['size'] - index * CHUNK_BYTES)
        if job['status'] != 'uploading' or index < 0 or index > len(job['blocks']) or len(data) != expected or expected <= 0:
            raise StoreError(409, 'Invalid upload chunk or upload already finalized.')
        if index == 0 and not data.startswith(b'%PDF-'):
            raise StoreError(400, 'Please upload a valid PDF file.')
        block = f'{index:06d}-{hashlib.sha256(data).hexdigest()[:48]}'
        if index < len(job['blocks']):
            if job['blocks'][index] != block:
                raise StoreError(409, 'This chunk was already uploaded with different content.')
            return
        self.source(job_id).stage_block(block, data, length=len(data))
        with self.transaction() as state:
            current = visible(state['jobs'].get(job_id), owner)
            if current['status'] != 'uploading':
                raise StoreError(409, 'Upload already finalized.')
            if index == len(current['blocks']):
                current['blocks'].append(block)
            elif index >= len(current['blocks']) or current['blocks'][index] != block:
                raise StoreError(409, 'Conflicting upload chunk.')

    def complete_upload(self, job_id, owner):
        with self.transaction() as state:
            job = visible(state['jobs'].get(job_id), owner)
            if job['status'] == 'uploading':
                if len(job['blocks']) != (job['size'] + CHUNK_BYTES - 1) // CHUNK_BYTES:
                    raise StoreError(409, 'Upload all chunks before starting conversion.')
                self.source(job_id).commit_block_list(job['blocks'], content_settings=ContentSettings(content_type='application/pdf'))
                job.update(source_ready=True, status='dispatching', blocks=[])
            elif job['status'] not in {'dispatching', 'queued', 'processing', 'completed'}:
                raise StoreError(409, 'This upload cannot be completed.')
        self.dispatch(job_id)
        return public(self.job(job_id, owner))

    def dispatch(self, job_id):
        job = self.job(job_id)
        if job['status'] != 'dispatching':
            return
        # Durable outbox state is written before send; duplicate sends are safe.
        self.queue.send_message(json.dumps({'id': job_id, 'generation': job['generation']}), time_to_live=RETENTION)
        with self.transaction() as state:
            current = state['jobs'].get(job_id)
            if current and current['status'] == 'dispatching' and current['generation'] == job['generation']:
                current['status'] = 'queued'

    def repair_dispatches(self):
        for job in self.read()['jobs'].values():
            if job['status'] == 'dispatching' and job['expires'] > time.time():
                self.dispatch(job['id'])

    def retry(self, job_id, owner, ip):
        with self.transaction() as state:
            job = visible(state['jobs'].get(job_id), owner)
            if job['status'] != 'failed' or job['attempts'] >= 3 or not job['source_ready']:
                raise StoreError(409, 'This conversion cannot be retried.')
            self.quota(state, owner, ip, new_job=False)
            job.update(status='dispatching', error=None, generation=job['generation']+1)
        self.dispatch(job_id)
        return public(self.job(job_id, owner))

    def claim(self, job_id, generation):
        with self.transaction() as state:
            job = state['jobs'].get(job_id)
            if not job or job['expires'] <= time.time() or job['generation'] != generation or job['status'] not in {'dispatching', 'queued', 'processing'}:
                return None
            if job['lease_until'] > time.time():
                raise StoreError(409, 'This job already has a worker.')
            if job['attempts'] >= 3:
                job.update(status='failed', error='Execution retry limit reached.')
                return None
            if len(state.get('executions', [])) >= int(os.getenv('DAILY_CONVERSION_LIMIT', '10')):
                job.update(status='failed', error='Daily processing allowance reached. Retry tomorrow.')
                return None
            state.setdefault('executions', []).append(time.time())
            job.update(status='processing', attempts=job['attempts']+1, run_id=secrets.token_hex(16), lease_until=time.time()+90)
            return dict(job)

    def update_run(self, job_id, run_id, **values):
        with self.transaction() as state:
            job = visible(state['jobs'].get(job_id))
            if job['run_id'] != run_id:
                raise StoreError(409, 'Worker ownership changed.')
            job.update(values)

    def delete(self, job_id, owner):
        with self.transaction() as state:
            job = visible(state['jobs'].get(job_id), owner)
            job.update(expires=0, status='deleted')
        self.queue.send_message(json.dumps({'id': job_id, 'generation': job['generation'], 'action': 'delete'}), time_to_live=RETENTION)

    def purge(self, job_id):
        for container in ('uploads', 'exports'):
            client = self.blobs.get_container_client(container)
            for item in client.list_blobs(name_starts_with=f'{job_id}/'):
                client.delete_blob(item.name)

    def reserve_download(self, size):
        # Bound public beta egress separately from CPU job quotas.
        with self.transaction() as state:
            day = int(time.time() // 86400)
            transfer = state.get('transfer', {'day': day, 'bytes': 0, 'requests': 0})
            if transfer['day'] != day:
                transfer = {'day': day, 'bytes': 0, 'requests': 0}
            if transfer['bytes'] + size > int(os.getenv('DOWNLOAD_MB_PER_DAY', '1024')) * 1024**2 or transfer['requests'] >= 10000:
                raise StoreError(429, 'Daily download allowance reached. Please return tomorrow.')
            transfer['bytes'] += size
            transfer['requests'] += 1
            state['transfer'] = transfer

    def export(self, job_id, name):
        return self.blobs.get_blob_client('exports', f'{job_id}/{name}')
