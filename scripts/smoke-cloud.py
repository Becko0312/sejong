"""Upload a synthetic PDF to a deployed converter and verify private export."""
import io
import json
from pathlib import Path
import sys
import time
import zipfile
import httpx

base = sys.argv[1].rstrip('/')
source = Path(sys.argv[2])
with httpx.Client(base_url=base, timeout=90) as client:
    response = client.get('/api/session')
    response.raise_for_status()
    session = response.json()
    headers = {'X-CSRF-Token': session['csrf'], 'Origin': base}
    data = source.read_bytes()
    response = client.post('/api/uploads', json={'name': source.name, 'size': len(data), 'ocr': True, 'languages': 'eng'}, headers=headers)
    response.raise_for_status()
    job = response.json()['id']
    for index, start in enumerate(range(0, len(data), session['chunk_bytes'])):
        response = client.put(f'/api/uploads/{job}/chunks/{index}', content=data[start:start+session['chunk_bytes']], headers=headers)
        response.raise_for_status()
    response = client.post(f'/api/uploads/{job}/complete', headers=headers)
    response.raise_for_status()
    for attempt in range(60):
        response = client.get(f'/api/jobs/{job}')
        response.raise_for_status()
        state = response.json()
        print(json.dumps({k: state[k] for k in ('status', 'done', 'total', 'attempts', 'error')}), flush=True)
        if state['status'] in {'completed', 'failed'}:
            break
        time.sleep(10)
    assert state['status'] == 'completed', state
    response = client.get(f'/api/jobs/{job}/download')
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['page_count'] == 1
        assert manifest['pages'][0]['text_source'] == 'ocr'
        assert 'Sejong' in manifest['pages'][0]['text'], manifest['pages'][0]['text']
        assert 'index.html' in archive.namelist()
    with httpx.Client(base_url=base, timeout=90) as other:
        other.get('/api/session').raise_for_status()
        assert other.get(f'/api/jobs/{job}/download').status_code == 404
    assert client.get(f'/books/{job}/index.html').status_code == 200
    client.delete(f'/api/jobs/{job}', headers=headers).raise_for_status()
    assert client.get(f'/api/jobs/{job}/download').status_code == 404
    print('PASS: live OCR, ZIP integrity, ownership, HTML preview, and deletion revocation', flush=True)
