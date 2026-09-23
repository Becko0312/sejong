import io
import json
import os
import subprocess
import sys
import zipfile
import pytest
from app import store
from app.converter import convert
from app.worker import cleanup
from tests.conftest import sample


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


def run_worker(job_id):
    result = subprocess.run([sys.executable, '-m', 'app.runner', job_id],
                            env={**os.environ, 'SEJONG_DATA': str(store.DATA)}, timeout=30)
    assert result.returncode == 0


def test_admin_upload_convert_and_catalog(admin):
    a, ha = admin
    r = a.post('/api/jobs?name=book.pdf&title=Korean+1&source_lang=Korean&target_lang=Mongolian',
               content=sample(), headers={**ha, 'content-type': 'application/pdf'})
    assert r.status_code == 202, r.text
    job = r.json()
    assert job['title'] == 'Korean 1' and job['source_lang'] == 'Korean'
    run_worker(job['id'])
    # Admin sees the course in the admin list and can open it.
    assert any(c['id'] == job['id'] and c['status'] == 'completed' for c in a.get('/api/admin/courses').json())
    manifest = a.get(f'/books/{job["id"]}/manifest.json').json()
    assert manifest['schema_version'] == '1.1'
    course = a.get(f'/api/courses/{job["id"]}').json()
    assert course['title'] == 'Korean 1' and course['target_lang'] == 'Mongolian'
    with zipfile.ZipFile(io.BytesIO(a.get(f'/api/jobs/{job["id"]}/download').content)) as z:
        assert 'index.html' in z.namelist()


def test_client_cannot_upload_or_manage(admin, client_user):
    a, ha = admin
    job = a.post('/api/jobs?name=b.pdf', content=sample(), headers={**ha, 'content-type': 'application/pdf'}).json()
    run_worker(job['id'])
    c, hc = client_user
    # A client is blocked from every admin action.
    assert c.post('/api/jobs?name=x.pdf', content=sample(), headers={**hc, 'content-type': 'application/pdf'}).status_code == 403
    assert c.get('/api/admin/courses').status_code == 403
    assert c.delete(f'/api/jobs/{job["id"]}', headers=hc).status_code == 403
    assert c.patch(f'/api/jobs/{job["id"]}', json={'available': False}, headers=hc).status_code == 403


def test_catalog_shows_only_available_completed_courses(admin, client_user):
    a, ha = admin
    job = a.post('/api/jobs?name=b.pdf&title=Visible', content=sample(), headers={**ha, 'content-type': 'application/pdf'}).json()
    run_worker(job['id'])
    c, hc = client_user
    assert [x['id'] for x in c.get('/api/catalog').json()] == [job['id']]
    assert c.get(f'/api/courses/{job["id"]}').status_code == 200
    # Admin hides it -> disappears from the client catalog and view.
    assert a.patch(f'/api/jobs/{job["id"]}', json={'available': False}, headers=ha).status_code == 200
    assert c.get('/api/catalog').json() == []
    assert c.get(f'/api/courses/{job["id"]}').status_code == 404
    assert a.get(f'/api/courses/{job["id"]}').status_code == 200  # admin still sees it


def test_course_endpoints_require_sign_in(admin):
    a, ha = admin
    job = a.post('/api/jobs?name=b.pdf', content=sample(), headers={**ha, 'content-type': 'application/pdf'}).json()
    run_worker(job['id'])
    from fastapi.testclient import TestClient
    from app import main
    with TestClient(main.app) as anon:
        assert anon.get('/api/catalog').status_code == 401
        assert anon.get(f'/api/courses/{job["id"]}').status_code == 401
        assert anon.get(f'/books/{job["id"]}/manifest.json').status_code == 401


def test_retry_cap_and_page_limit(admin, tmp_path):
    a, ha = admin
    job = a.post('/api/jobs?name=b.pdf', content=sample(), headers={**ha, 'content-type': 'application/pdf'}).json()
    store.update(job['id'], status='failed', attempts=3)
    assert a.post(f'/api/jobs/{job["id"]}/retry', headers=ha).status_code == 409
    source = tmp_path / 'large.pdf'
    source.write_bytes(sample())
    os.environ['MAX_PAGES'] = '1'
    try:
        with pytest.raises(ValueError, match='page limit'):
            convert(source, tmp_path / 'out')
    finally:
        del os.environ['MAX_PAGES']


def test_parallel_schema_initialization(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(store, 'DATA', tmp_path)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: store.initialize(), range(4)))
    with store.connect() as db:
        columns = {r['name'] for r in db.execute('PRAGMA table_info(jobs)')}
    assert {'owner', 'available', 'source_lang', 'target_lang', 'title'} <= columns
