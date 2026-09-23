"""Exercise the real HTTP -> offline worker -> OCR -> ZIP path locally."""
import io
import json
from pathlib import Path
import sys
import time
import zipfile
import httpx

url = sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8001'
source = Path('data/verification/scanned-korean-mongolian.pdf')
with httpx.Client(base_url=url, timeout=30) as client:
    csrf=client.get('/api/session').json()['csrf']
    r=client.post('/api/jobs', params={'name': source.name,'ocr':'true','languages':'kor+mon+eng'},
                  content=source.read_bytes(),headers={'content-type':'application/pdf','x-csrf-token':csrf})
    r.raise_for_status()
    job=r.json(); Path('data/verification/smoke-job.json').write_text(json.dumps({'id':job['id'],'cookies':dict(client.cookies)}))
    for _ in range(120):
        state=client.get('/api/jobs/'+job['id']).json()
        if state['status'] in ('completed','failed'): break
        time.sleep(1)
    assert state['status']=='completed', state
    manifest=client.get(f'/books/{job["id"]}/manifest.json').json()
    assert manifest['pages'][0]['text_source']=='ocr'
    assert len(manifest['pages'][0]['text'])>50
    text=manifest['pages'][0]['text']
    assert any('\uac00'<=char<='\ud7a3' for char in text), 'No Korean recognized'
    assert any('\u0400'<=char<='\u04ff' for char in text), 'No Cyrillic recognized'
    archive=client.get(f'/api/jobs/{job["id"]}/download').content
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        assert z.testzip() is None
        assert 'page-1.html' in z.namelist()
    with httpx.Client(base_url=url) as other:
        other.get('/api/session')
        assert other.get(f'/api/jobs/{job["id"]}/download').status_code==404
    Path('data/verification/ocr-result.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps({'job':job['id'],'status':state['status'],'ocr_characters':len(text),'review_required':manifest['pages'][0]['needs_review'],'cross_session_download':'blocked','zip':'valid'},indent=2))
