"""Receive one Azure Storage message, convert in a sandbox, acknowledge, exit."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError
from app.cloud_store import CloudStore, StoreError, MAX_BYTES
from app.worker import stop_process

SAFE_FILE = re.compile(r'(page-[1-9][0-9]*\.(png|html|json)|index\.html|manifest\.json)')


def transfer_checkpoints(store, job, output, completed, heartbeat):
    # A page checkpoint is uploaded last; a partial page is safely regenerated.
    for number in range(1, completed + 1):
        checkpoint = output / f'page-{number}.json'
        marker = output / f'page-{number}.uploaded'
        if marker.exists() or not checkpoint.exists():
            continue
        for suffix in ('png', 'html', 'json'):
            path = output / f'page-{number}.{suffix}'
            if path.is_symlink() or not path.is_file():
                raise ValueError('Unsafe conversion output.')
            with path.open('rb') as stream:
                store.export(job['id'], f'checkpoints/{path.name}').upload_blob(stream, overwrite=True, progress_hook=lambda *_: heartbeat())
        heartbeat()
        marker.touch()


def execute(store, job, heartbeat):
    with tempfile.TemporaryDirectory(prefix='sejong-') as tmp:
        folder = Path(tmp)
        output = folder / 'output'
        output.mkdir()
        source = store.source(job['id'])
        if source.get_blob_properties().size != job['size'] or job['size'] > MAX_BYTES:
            raise ValueError('Uploaded PDF size does not match the accepted upload.')
        heartbeat()
        with (folder / 'source.pdf').open('wb') as stream:
            for chunk in source.download_blob().chunks():
                stream.write(chunk)
                heartbeat()
        # Resume only this job's fixed-name checkpoint files from previous attempts.
        client = store.blobs.get_container_client('exports')
        for item in client.list_blobs(name_starts_with=f'{job["id"]}/checkpoints/'):
            name = item.name.rsplit('/', 1)[-1]
            if SAFE_FILE.fullmatch(name):
                with (output / name).open('wb') as stream:
                    client.download_blob(item.name).readinto(stream)
                heartbeat()
        (folder / 'job.json').write_text(json.dumps({k: job[k] for k in ('name', 'ocr', 'languages')}))
        # No storage credentials or managed-identity endpoint variables reach native tools.
        env = {k: os.environ[k] for k in ('PATH', 'LANG', 'PYTHONPATH', 'MAX_PAGES', 'MAX_OUTPUT_MB') if k in os.environ}
        env.update(PYTHONDONTWRITEBYTECODE='1', OMP_THREAD_LIMIT='1')
        process = subprocess.Popen([sys.executable, '-m', 'app.cloud_convert', str(folder)],
                                   env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + int(os.getenv('JOB_TIMEOUT_SECONDS', '1800'))
        last_sync, done, total = 0, 0, 0
        try:
            while process.poll() is None:
                if time.monotonic() > deadline:
                    raise ValueError('Conversion exceeded the job time limit.')
                if time.monotonic() - last_sync >= 10:
                    heartbeat()
                    progress = folder / 'progress.json'
                    if progress.exists():
                        data = json.loads(progress.read_text())
                        done, total = data['done'], data['total']
                        store.update_run(job['id'], job['run_id'], done=done, total=total)
                        transfer_checkpoints(store, job, output, done, heartbeat)
                    last_sync = time.monotonic()
                time.sleep(1)
            heartbeat()
            result_file = folder / 'result.json'
            result = json.loads(result_file.read_text()) if result_file.exists() else {'ok': False, 'error': 'Converter exited unexpectedly.'}
            if not result['ok']:
                raise ValueError(result['error'])
            for path in output.iterdir():
                if SAFE_FILE.fullmatch(path.name) and not path.is_symlink():
                    with path.open('rb') as stream:
                        store.export(job['id'], path.name).upload_blob(stream, overwrite=True, progress_hook=lambda *_: heartbeat())
                    heartbeat()
            with (folder / 'book.zip').open('rb') as stream:
                store.export(job['id'], 'book.zip').upload_blob(stream, overwrite=True, progress_hook=lambda *_: heartbeat())
            manifest = json.loads((output / 'manifest.json').read_text())
            store.update_run(job['id'], job['run_id'], status='completed', done=manifest['page_count'], total=manifest['page_count'], error=None, lease_until=0)
        finally:
            stop_process(process)


def process_one(store, slot):
    message = next(iter(store.queue.receive_messages(messages_per_page=1, visibility_timeout=90)), None)
    if message is None:
        return 0
    try:
        payload = json.loads(message.content)
        if not re.fullmatch(r'[0-9a-f]{32}', payload['id']) or not isinstance(payload['generation'], int):
            raise ValueError('Malformed queue message.')
    except (ValueError, KeyError, TypeError):
        store.queue.delete_message(message)
        return 0
    if payload.get('action') == 'delete':
        store.purge(payload['id'])
        store.queue.delete_message(message)
        return 0
    job = None
    try:
        job = store.claim(payload['id'], payload['generation'])
        if job is None:
            store.queue.delete_message(message)
            return 0
        receipt, last_renewal, last_check = message.pop_receipt, 0, 0
        def heartbeat():
            nonlocal receipt, last_renewal, last_check
            if time.monotonic()-last_check < 5:
                return
            last_check = time.monotonic()
            current = store.job(job['id'])
            if current['run_id'] != job['run_id']:
                raise StoreError(409, 'Worker lease was superseded.')
            if time.monotonic()-last_renewal >= 20:
                slot.renew()
                updated = store.queue.update_message(message.id, receipt, visibility_timeout=90)
                receipt = updated.pop_receipt
                store.update_run(job['id'], job['run_id'], lease_until=time.time()+90)
                last_renewal = time.monotonic()
        execute(store, job, heartbeat)
        store.queue.delete_message(message.id, receipt)
        return 0
    except StoreError as exc:
        if job and exc.code == 404:
            store.purge(job['id'])
            try:
                store.queue.delete_message(message.id, receipt)
            except ResourceNotFoundError:
                pass
            return 0
        # Another execution owns the job. Leave the message for visibility-timeout redelivery.
        return 0
    except ValueError as exc:
        if job:
            store.update_run(job['id'], job['run_id'], status='failed', error=str(exc)[:300], lease_until=0)
            store.queue.delete_message(message.id, receipt)
        return 0
    # Unexpected storage/network errors intentionally escape: the message remains for redelivery.


def run_once(store=None):
    store = store or CloudStore()
    store.initialize()
    try:
        slot = store.slot.acquire_lease(lease_duration=60)
    except HttpResponseError as exc:
        if exc.status_code == 409:
            return 0
        raise
    try:
        return process_one(store, slot)
    finally:
        slot.release()


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
    sys.exit(run_once())
