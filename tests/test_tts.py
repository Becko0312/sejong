"""Azure TTS module and /api/tts endpoint. The Azure Speech REST call is mocked."""
import types
import pytest
from app import tts


@pytest.fixture(autouse=True)
def clear_cache():
    tts._cache.clear()
    yield
    tts._cache.clear()


def enable(monkeypatch, status=200, content=b'ID3fake-mp3'):
    """Point the module at fake Azure credentials and capture REST calls."""
    calls = []
    monkeypatch.setenv('AZURE_SPEECH_KEY', 'speech-key')
    monkeypatch.setenv('AZURE_SPEECH_REGION', 'japaneast')
    monkeypatch.delenv('TTS_VOICE_MN', raising=False)
    import requests

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append({'url': url, 'ssml': data.decode('utf-8'), 'headers': headers})
        return types.SimpleNamespace(status_code=status, content=content)

    monkeypatch.setattr(requests, 'post', fake_post)
    return calls


# ---------- module ----------

def test_disabled_without_credentials(monkeypatch):
    monkeypatch.delenv('AZURE_SPEECH_KEY', raising=False)
    monkeypatch.delenv('AZURE_SPEECH_REGION', raising=False)
    assert tts.enabled() is False
    assert tts.config() == {'enabled': False}
    with pytest.raises(tts.TtsError):
        tts.synthesize('Сайн уу', 'mn-MN')


def test_voice_mapping_and_mongolian_override(monkeypatch):
    assert tts.voice_for('mn-MN') == 'mn-MN-YesuiNeural'
    monkeypatch.setenv('TTS_VOICE_MN', 'mn-MN-BataarNeural')
    assert tts.voice_for('mn') == 'mn-MN-BataarNeural'
    monkeypatch.delenv('TTS_VOICE_MN', raising=False)
    assert tts.voice_for('ko-KR') == 'ko-KR-SunHiNeural'
    assert tts.voice_for('ko') == 'ko-KR-SunHiNeural'
    assert tts.voice_for('fr-FR') == tts.DEFAULT_VOICE
    assert tts.voice_for('') == tts.DEFAULT_VOICE


def test_synthesize_posts_ssml_and_caches(monkeypatch):
    calls = enable(monkeypatch)
    audio = tts.synthesize('Сайн уу <b>', 'mn-MN')
    assert audio == b'ID3fake-mp3'
    assert len(calls) == 1
    assert calls[0]['url'] == 'https://japaneast.tts.speech.microsoft.com/cognitiveservices/v1'
    assert calls[0]['headers']['Ocp-Apim-Subscription-Key'] == 'speech-key'
    assert 'mn-MN-YesuiNeural' in calls[0]['ssml']
    assert '&lt;b&gt;' in calls[0]['ssml'] and '<b>' not in calls[0]['ssml']
    # Same voice+text is served from cache without another REST call.
    assert tts.synthesize('Сайн уу <b>', 'mn-MN') == audio
    assert len(calls) == 1
    # A different language re-synthesizes with its own voice.
    tts.synthesize('안녕하세요', 'ko-KR')
    assert len(calls) == 2 and 'ko-KR-SunHiNeural' in calls[1]['ssml']


def test_synthesize_rejects_bad_input_and_upstream(monkeypatch):
    enable(monkeypatch)
    with pytest.raises(tts.TtsError):
        tts.synthesize('   ', 'mn-MN')
    with pytest.raises(tts.TtsError):
        tts.synthesize('x' * (tts.MAX_TEXT + 1), 'mn-MN')
    enable(monkeypatch, status=401)
    with pytest.raises(tts.TtsError, match='credentials'):
        tts.synthesize('Сайн уу', 'mn-MN')
    enable(monkeypatch, status=429)
    with pytest.raises(tts.TtsError, match='busy'):
        tts.synthesize('Сайн уу', 'mn-MN')
    enable(monkeypatch, content=b'')
    with pytest.raises(tts.TtsError):
        tts.synthesize('Сайн уу', 'mn-MN')


# ---------- API ----------

def test_api_tts_returns_audio(admin, monkeypatch):
    client, headers = admin
    enable(monkeypatch)
    res = client.post('/api/tts', json={'text': 'Сайн уу', 'lang': 'mn-MN'}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.headers['content-type'] == 'audio/mpeg'
    assert res.content == b'ID3fake-mp3'


def test_api_tts_guards(admin, client_user, monkeypatch):
    client, headers = client_user
    enable(monkeypatch)
    assert client.post('/api/tts', json={'text': 'hi'}).status_code == 401
    assert client.post('/api/tts', json={'text': 'hi'}, headers={'x-csrf-token': 'wrong'}).status_code == 403
    assert client.post('/api/tts', json={'text': 'x' * (tts.MAX_TEXT + 1)}, headers=headers).status_code == 422
    admin_client, admin_headers = admin
    monkeypatch.delenv('AZURE_SPEECH_KEY', raising=False)
    assert admin_client.post('/api/tts', json={'text': 'hi'}, headers=admin_headers).status_code == 503


def test_me_reports_tts_config(admin, monkeypatch):
    client, headers = admin
    enable(monkeypatch)
    assert client.get('/api/me', headers=headers).json()['tts'] == {'enabled': True}
