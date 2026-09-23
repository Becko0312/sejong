import json
import time
import zipfile
import io
import pymupdf
from fastapi.testclient import TestClient
from app.converter import convert
from app import main


def sample():
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 40), '<script>alert(1)</script> Korean textbook')
    doc.new_page()
    return doc.tobytes()


def test_conversion_and_resume(tmp_path):
    source = tmp_path / 'sample.pdf'
    source.write_bytes(sample())
    output = tmp_path / 'output'
    result = convert(source, output)
    assert result['page_count'] == 2
    assert result['ocr_required_pages'] == 1
    assert '&lt;script&gt;' in (output / 'page-1.html').read_text()
    stamp = (output / 'page-1.png').stat().st_mtime_ns
    (output / 'page-2.html').unlink()
    convert(source, output)
    assert (output / 'page-1.png').stat().st_mtime_ns == stamp
    assert (output / 'page-2.html').exists()


def test_upload_convert_download_and_invalid_input(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path)
    monkeypatch.setattr(main, 'DB', tmp_path / 'jobs.sqlite3')
    with TestClient(main.app) as client:
        assert client.post('/api/jobs', files={'file': ('bad.pdf', b'no pdf')}).status_code == 400
        r = client.post('/api/jobs', files={'file': ('book.pdf', sample(), 'application/pdf')})
        assert r.status_code == 202
        job = r.json()
        assert client.post(f'/api/jobs/{job["id"]}/retry').status_code == 409
        for _ in range(100):
            job = client.get(f'/api/jobs/{job["id"]}').json()
            if job['status'] in ['completed', 'failed']:
                break
            time.sleep(.1)
        assert job['status'] == 'completed', job
        manifest = client.get(f'/books/{job["id"]}/manifest.json').json()
        assert manifest['page_count'] == 2
        archive = client.get(f'/api/jobs/{job["id"]}/download')
        with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
            assert 'index.html' in bundle.namelist()
            assert json.loads(bundle.read('manifest.json'))['page_count'] == 2
        assert client.get('/api/jobs/unknown').status_code == 404
        assert client.get(f'/books/{job["id"]}/source.pdf').status_code == 404
