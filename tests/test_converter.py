import io
import json
import subprocess
import sys
import time
import zipfile
import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas
from app import main, store
from app.converter import convert
from app.worker import cleanup


def sample():
    buffer = io.BytesIO()
    doc = canvas.Canvas(buffer)
    doc.drawString(40, 740, '<script>alert(1)</script> Korean textbook')
    doc.showPage()
    doc.showPage()
    doc.save()
    return buffer.getvalue()


@pytest.fixture
def clients(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    with TestClient(main.app) as a, TestClient(main.app) as b:
        ca = a.get('/api/session').json()['csrf']
        cb = b.get('/api/session').json()['csrf']
        yield a, b, {'x-csrf-token': ca, 'content-type': 'application/pdf'}, {'x-csrf-token': cb, 'content-type': 'application/pdf'}


def test_conversion_and_resume(tmp_path):
    source = tmp_path / 'sample.pdf'
    source.write_bytes(sample())
    output = tmp_path / 'output'
    result = convert(source, output)
    assert result['page_count'] == 2
    assert result['ocr_required_pages'] == 1
    assert '&lt;script&gt;' in (output / 'page-1.html').read_text()
    assert result['pages'][0]['words']
    stamp = (output / 'page-1.png').stat().st_mtime_ns
    (output / 'page-2.html').unlink()
    convert(source, output)
    assert (output / 'page-1.png').stat().st_mtime_ns == stamp
    assert (output / 'page-2.html').exists()


def test_private_upload_download_and_delete(clients, monkeypatch):
    a, b, ha, hb = clients
    assert a.post('/api/jobs', content=b'bad', headers=ha).status_code == 400
    assert a.post('/api/jobs', content=sample(), headers={'content-type': 'application/pdf'}).status_code == 403
    assert a.post('/api/jobs', content=sample(), headers={**ha, 'origin': 'https://attacker.test'}).status_code == 403
    r = a.post('/api/jobs?name=book.pdf', content=sample(), headers=ha)
    assert r.status_code == 202
    job = r.json()
    assert b.get('/api/jobs').json() == []
    assert b.get(f'/api/jobs/{job["id"]}').status_code == 404
    assert b.post(f'/api/jobs/{job["id"]}/retry', headers=hb).status_code == 404
    assert b.get(f'/api/jobs/{job["id"]}/download').status_code == 404
    assert b.get(f'/books/{job["id"]}/manifest.json').status_code == 404
    assert b.delete(f'/api/jobs/{job["id"]}', headers=hb).status_code == 404
    assert a.get(f'/api/jobs/{job["id"]}/download').status_code == 409
    # Run exactly the process used by the isolated worker.
    import os
    result = subprocess.run([sys.executable, '-m', 'app.runner', job['id']],
                            env={**os.environ, 'SEJONG_DATA': str(store.DATA)}, timeout=30)
    assert result.returncode == 0
    assert a.get(f'/api/jobs/{job["id"]}').json()['status'] == 'completed'
    manifest = a.get(f'/books/{job["id"]}/manifest.json').json()
    assert manifest['schema_version'] == '1.1'
    with zipfile.ZipFile(io.BytesIO(a.get(f'/api/jobs/{job["id"]}/download').content)) as archive:
        assert 'index.html' in archive.namelist()
        assert 'checkpoint_key' not in json.loads(archive.read('page-1.json'))
    assert a.get(f'/books/{job["id"]}/source.pdf').status_code == 404
    assert a.delete(f'/api/jobs/{job["id"]}', headers=ha).status_code == 204
    assert a.get(f'/api/jobs/{job["id"]}').status_code == 404
    cleanup()
    assert not (store.DATA / job['id']).exists()


def test_quotas_size_expiry_and_no_anonymous_listing(clients, monkeypatch):
    a, b, ha, hb = clients
    monkeypatch.setattr(store, 'MAX_BYTES', 100)
    assert a.post('/api/jobs', content=sample(), headers=ha).status_code == 413
    monkeypatch.setattr(store, 'MAX_BYTES', 100000)
    monkeypatch.setattr(store, 'PER_SESSION', 1)
    r = a.post('/api/jobs', content=sample(), headers=ha)
    assert r.status_code == 202
    assert a.post('/api/jobs', content=sample(), headers=ha).status_code == 429
    store.update(r.json()['id'], expires=time.time()-1)
    assert a.get('/api/jobs').json() == []
    cleanup()
    a.cookies.clear()
    assert a.get('/api/jobs').status_code == 401


def test_retry_cap_and_page_limit(clients, monkeypatch, tmp_path):
    a, _, ha, _ = clients
    job = a.post('/api/jobs', content=sample(), headers=ha).json()
    store.update(job['id'], status='failed', attempts=3)
    assert a.post(f'/api/jobs/{job["id"]}/retry', headers=ha).status_code == 409
    source = tmp_path / 'large.pdf'
    source.write_bytes(sample())
    monkeypatch.setenv('MAX_PAGES', '1')
    with pytest.raises(ValueError, match='page limit'):
        convert(source, tmp_path / 'output')


def test_parallel_schema_initialization(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(store, 'DATA', tmp_path)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: store.initialize(), range(4)))
    with store.connect() as db:
        columns = {r['name'] for r in db.execute('PRAGMA table_info(jobs)')}
    assert {'owner', 'expires', 'ocr', 'attempts'} <= columns
