"""Single durable SQLite queue worker; restart-safe, network-free in Compose."""
import fcntl
import os
import signal
import subprocess
import sys
import time
import shutil
from app import store


def cleanup():
    now = time.time()
    with store.connect() as db:
        rows = db.execute("SELECT id FROM jobs WHERE owner!='legacy' AND (expires<? OR (status='uploading' AND created<?)) AND status!='processing'", (now, now-660)).fetchall()
        for row in rows:
            shutil.rmtree(store.DATA / row['id'], ignore_errors=True)
            db.execute('DELETE FROM jobs WHERE id=?', (row['id'],))
        db.execute('DELETE FROM requests WHERE created<?', (now-86400,))
    (store.DATA / 'worker-heartbeat').touch()


def stop_process(process):
    # Kill the whole group even when the leader exited, so child tools cannot linger.
    try:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run():
    store.initialize()
    with (store.DATA / 'worker.lock').open('w') as lock:
        store.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with store.connect() as db:
            db.execute("UPDATE jobs SET status='queued' WHERE status='processing'")
        stopping = False
        def shutdown(*args):
            nonlocal stopping
            stopping = True
        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        while not stopping:
            cleanup()
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT id FROM jobs WHERE status='queued' AND owner!='legacy' AND expires>? ORDER BY created LIMIT 1", (time.time(),)).fetchone()
                if row:
                    db.execute("UPDATE jobs SET status='processing',attempts=attempts+1 WHERE id=?", (row['id'],))
            if not row:
                time.sleep(1)
                continue
            process = subprocess.Popen([sys.executable, '-m', 'app.runner', row['id']], start_new_session=True,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + int(os.getenv('JOB_TIMEOUT_SECONDS', '3600'))
            cancelled = False
            while process.poll() is None and not stopping:
                (store.DATA / 'worker-heartbeat').touch()
                with store.connect() as db:
                    record = db.execute('SELECT expires FROM jobs WHERE id=?', (row['id'],)).fetchone()
                cancelled = not record or record['expires'] <= time.time()
                if cancelled or time.monotonic() > deadline:
                    break
                time.sleep(1)
            stop_process(process)
            with store.connect() as db:
                current = db.execute('SELECT status FROM jobs WHERE id=?', (row['id'],)).fetchone()
            if stopping:
                if current and current['status'] == 'processing':
                    store.update(row['id'], status='queued')
            elif current and (current['status'] == 'processing' or cancelled):
                store.update(row['id'], status='failed', error='Conversion cancelled or exceeded the processing limit.')


if __name__ == '__main__':
    run()
