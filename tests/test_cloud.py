"""Real Blob/Queue integration tests against a disposable local Azurite service."""
import base64
import io
import json
import os
import time
import zipfile
import pytest
from fastapi.testclient import TestClient
from tests.test_converter import sample

pytestmark = pytest.mark.skipif(os.getenv('RUN_AZURE_EMULATOR_TESTS') != '1', reason='Needs local Azurite')


@pytest.fixture
def cloud(monkeypatch):
    key = base64.b64encode(b'sejong-local-development-only-key').decode()
    connection = f'DefaultEndpointsProtocol=http;AccountName=studio;AccountKey={key};BlobEndpoint=http://127.0.0.1:10000/studio;QueueEndpoint=http://127.0.0.1:10001/studio;'
    monkeypatch.setenv('ALLOW_STORAGE_EMULATOR', '1')
    monkeypatch.setenv('AZURE_STORAGE_CONNECTION_STRING', connection)
    from app.cloud_store import CloudStore
    from app.cloud_api import app
    store = CloudStore()
    store.initialize()
    with store.transaction() as state:
        state.update(jobs={}, requests=[], executions=[])
    store.queue.clear_messages()
    with TestClient(app) as a, TestClient(app) as b:
        ha = {'x-csrf-token': a.get('/api/session').json()['csrf']}
        hb = {'x-csrf-token': b.get('/api/session').json()['csrf']}
        yield store, a, b, ha, hb


def upload(a, headers):
    data=sample()
    result=a.post('/api/uploads',json={'name':'book.pdf','size':len(data)},headers=headers)
    assert result.status_code==201, result.text
    job=result.json()
    assert a.put(f'/api/uploads/{job["id"]}/chunks/0',content=data,headers=headers).status_code==204
    assert a.put(f'/api/uploads/{job["id"]}/chunks/0',content=data,headers=headers).status_code==204
    assert a.post(f'/api/uploads/{job["id"]}/complete',headers=headers).status_code==202
    return job


def test_cloud_upload_worker_privacy_and_zip(cloud):
    store,a,b,ha,hb=cloud
    job=upload(a,ha)
    assert b.get('/api/jobs').json()==[]
    for path in (f'/api/jobs/{job["id"]}',f'/api/jobs/{job["id"]}/download',f'/books/{job["id"]}/manifest.json'):
        assert b.get(path).status_code==404
    assert b.post(f'/api/jobs/{job["id"]}/retry',headers=hb).status_code==404
    from app.cloud_job import run_once
    assert run_once(store)==0
    result=a.get(f'/api/jobs/{job["id"]}').json()
    assert result['status']=='completed',result
    with zipfile.ZipFile(io.BytesIO(a.get(f'/api/jobs/{job["id"]}/download').content)) as archive:
        assert archive.testzip() is None
        assert json.loads(archive.read('manifest.json'))['page_count']==2
    assert a.get(f'/books/{job["id"]}/index.html').status_code==200
    assert a.delete(f'/api/jobs/{job["id"]}',headers=ha).status_code==204
    assert a.get(f'/api/jobs/{job["id"]}/download').status_code==404
    run_once(store)
    assert not store.source(job['id']).exists()
    assert not store.export(job['id'],'book.zip').exists()


def test_outbox_recovery_and_duplicate_delivery(cloud,monkeypatch):
    store,a,b,ha,hb=cloud
    data=sample()
    job=a.post('/api/uploads',json={'name':'recover.pdf','size':len(data)},headers=ha).json()
    owner=store.read()['jobs'][job['id']]['owner']
    store.chunk(job['id'],owner,0,data)
    original=store.queue.send_message
    monkeypatch.setattr(store.queue,'send_message',lambda *args,**kwargs: (_ for _ in ()).throw(RuntimeError('simulated outage')))
    with pytest.raises(RuntimeError):store.complete_upload(job['id'],owner)
    assert store.job(job['id'])['status']=='dispatching'
    monkeypatch.setattr(store.queue,'send_message',original)
    store.repair_dispatches()
    store.queue.send_message(json.dumps({'id':job['id'],'generation':0}))
    from app.cloud_job import run_once
    run_once(store);run_once(store)
    assert store.job(job['id'])['attempts']==1


def test_validation_limits_and_worker_fencing(cloud,monkeypatch):
    store,a,b,ha,hb=cloud
    assert a.post('/api/uploads',json={'name':'bad.pdf','size':999999999},headers=ha).status_code==413
    data=sample()
    job=a.post('/api/uploads',json={'name':'book.pdf','size':len(data)},headers=ha).json()
    assert a.post(f'/api/uploads/{job["id"]}/complete',headers=ha).status_code==409
    assert a.put(f'/api/uploads/{job["id"]}/chunks/0',content=data).status_code==403
    assert b.put(f'/api/uploads/{job["id"]}/chunks/0',content=data,headers=hb).status_code==404
    assert a.put(f'/api/uploads/{job["id"]}/chunks/1',content=data,headers=ha).status_code==409
    a.put(f'/api/uploads/{job["id"]}/chunks/0',content=data,headers=ha)
    a.post(f'/api/uploads/{job["id"]}/complete',headers=ha)
    claimed=store.claim(job['id'],0)
    from app.cloud_store import StoreError
    with pytest.raises(StoreError):store.claim(job['id'],0)
    with pytest.raises(StoreError):store.update_run(job['id'],'incorrect',status='completed')
    monkeypatch.setenv('DAILY_CONVERSION_LIMIT','1')
    assert a.post('/api/uploads',json={'name':'second.pdf','size':len(data)},headers=ha).status_code==429


def test_sandbox_denies_network():
    import subprocess,sys
    result=subprocess.run([sys.executable,'-c', 'from app.cloud_convert import sandbox; sandbox(); import socket; socket.socket()'],capture_output=True)
    assert result.returncode!=0
    assert b'Operation not permitted' in result.stderr
