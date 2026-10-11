"""Gemini backend switch: AI Studio (API key) vs Vertex AI (Google Cloud credits)."""
import types
from app import google_ai, tutor


def use_vertex(monkeypatch):
    monkeypatch.setenv('GOOGLE_VERTEX_PROJECT', 'proj-1')
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.setattr(google_ai, 'access_token', lambda: 'ya29.test')


def test_aistudio_is_used_without_a_vertex_project(monkeypatch):
    monkeypatch.delenv('GOOGLE_VERTEX_PROJECT', raising=False)
    monkeypatch.setenv('GEMINI_API_KEY', 'AQ.test')
    url, params, headers = google_ai.rest_request('gemini-2.5-flash')
    assert url.startswith('https://generativelanguage.googleapis.com/') and params == {'key': 'AQ.test'} and headers == {}
    ws, ws_headers, model = google_ai.live_target('gemini-2.5-flash-native-audio-latest')
    assert ws.endswith('?key=AQ.test') and model == 'models/gemini-2.5-flash-native-audio-latest'


def test_vertex_requests_use_the_project_and_a_bearer_token(monkeypatch):
    use_vertex(monkeypatch)
    url, params, headers = google_ai.rest_request('gemini-2.5-flash')
    assert url == ('https://aiplatform.googleapis.com/v1/projects/proj-1/locations/global'
                   '/publishers/google/models/gemini-2.5-flash:generateContent')
    assert params == {} and headers == {'Authorization': 'Bearer ya29.test'}
    ws, ws_headers, model = google_ai.live_target('ignored')
    assert ws == 'wss://us-central1-aiplatform.googleapis.com/ws/google.cloud.aiplatform.v1.LlmBidiService/BidiGenerateContent'
    assert model == 'projects/proj-1/locations/us-central1/publishers/google/models/gemini-live-2.5-flash-native-audio'
    assert ws_headers == {'Authorization': 'Bearer ya29.test'}


def test_tutor_runs_on_vertex_without_an_api_key(monkeypatch):
    use_vertex(monkeypatch)
    monkeypatch.delenv('TUTOR_PROVIDER', raising=False)
    assert tutor.provider() == 'gemini' and tutor.enabled()
    capture = {}
    import requests
    monkeypatch.setattr(requests, 'post', lambda url, params=None, json=None, timeout=None, headers=None:
                        capture.update(url=url, headers=headers) or types.SimpleNamespace(
                            status_code=200, json=lambda: {'candidates': [{'content': {'parts': [{'text': 'ok'}]}}]}))
    assert tutor.answer('hi', 'Book', {'number': 1, 'text': '안녕'})['reply'] == 'ok'
    assert 'aiplatform.googleapis.com' in capture['url'] and capture['headers']['Authorization'] == 'Bearer ya29.test'


def test_vertex_sign_in_failure_is_a_friendly_tutor_error(monkeypatch):
    monkeypatch.setenv('GOOGLE_VERTEX_PROJECT', 'proj-1')
    monkeypatch.setenv('TUTOR_PROVIDER', 'gemini')

    def fail():
        raise google_ai.AuthError('Google Cloud sign-in failed: DefaultCredentialsError')
    monkeypatch.setattr(google_ai, 'access_token', fail)
    try:
        tutor.answer('hi', 'Book', {'number': 1, 'text': '안녕'})
    except tutor.TutorError as exc:
        assert 'could not respond' in exc.message
    else:
        raise AssertionError('expected TutorError')
