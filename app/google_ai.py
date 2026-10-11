"""Where Gemini requests go: Google AI Studio (API key) or Vertex AI (Google Cloud).

Vertex AI is the same Gemini, billed as a Google Cloud service, so Google Cloud credits
(e.g. the $300 free trial) can pay for it; AI Studio usage cannot use those credits.
Setting GOOGLE_VERTEX_PROJECT switches every Gemini call (tutor, interactive pages,
summaries, Live voice) to Vertex AI; leaving it unset keeps GEMINI_API_KEY / AI Studio.

Vertex authenticates with Google Application Default Credentials: on the server that is a
workload-identity-federation config (GOOGLE_APPLICATION_CREDENTIALS), so the Azure VM's own
managed identity signs in and no Google key file is stored anywhere.
"""
import os
import threading

AISTUDIO_REST = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
AISTUDIO_LIVE = ('wss://generativelanguage.googleapis.com/ws/'
                 'google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent')
# Text models run in the 'global' location; the native-audio Live model is offered in us-central1.
VERTEX_LOCATION = os.getenv('GOOGLE_VERTEX_LOCATION', 'global')
VERTEX_LIVE_LOCATION = os.getenv('GOOGLE_VERTEX_LIVE_LOCATION', 'us-central1')
VERTEX_LIVE_MODEL = os.getenv('GOOGLE_VERTEX_LIVE_MODEL', 'gemini-live-2.5-flash-native-audio')
SCOPES = ['https://www.googleapis.com/auth/cloud-platform']

_lock = threading.Lock()
_credentials = None


class AuthError(Exception):
    pass


def vertex_project():
    return os.getenv('GOOGLE_VERTEX_PROJECT', '').strip()


def backend():
    return 'vertex' if vertex_project() else 'aistudio'


def configured():
    """True when Gemini can be called at all (credential present for the chosen backend)."""
    if backend() == 'vertex':
        return True        # credentials are resolved (and errors reported) on first use
    return bool(os.getenv('GEMINI_API_KEY'))


def access_token():
    """A fresh OAuth access token for Vertex AI (cached and refreshed before it expires)."""
    global _credentials
    import google.auth
    import google.auth.transport.requests
    with _lock:
        try:
            if _credentials is None:
                _credentials, _ = google.auth.default(scopes=SCOPES)
            if not _credentials.valid:
                _credentials.refresh(google.auth.transport.requests.Request())
        except Exception as exc:   # missing/invalid credentials, metadata or STS errors
            _credentials = None
            raise AuthError(f'Google Cloud sign-in failed: {type(exc).__name__}') from exc
        return _credentials.token


def _host(location):
    return 'aiplatform.googleapis.com' if location == 'global' else f'{location}-aiplatform.googleapis.com'


def rest_request(model):
    """(url, params, headers) for one generateContent call to `model`."""
    if backend() == 'vertex':
        url = (f'https://{_host(VERTEX_LOCATION)}/v1/projects/{vertex_project()}/locations/{VERTEX_LOCATION}'
               f'/publishers/google/models/{model}:generateContent')
        return url, {}, {'Authorization': f'Bearer {access_token()}'}
    return AISTUDIO_REST.format(model=model), {'key': os.getenv('GEMINI_API_KEY')}, {}


def live_target(aistudio_model):
    """(websocket url, headers, setup model name) for a Gemini Live connection."""
    if backend() == 'vertex':
        url = (f'wss://{_host(VERTEX_LIVE_LOCATION)}/ws/'
               'google.cloud.aiplatform.v1.LlmBidiService/BidiGenerateContent')
        model = (f'projects/{vertex_project()}/locations/{VERTEX_LIVE_LOCATION}'
                 f'/publishers/google/models/{VERTEX_LIVE_MODEL}')
        return url, {'Authorization': f'Bearer {access_token()}'}, model
    return f'{AISTUDIO_LIVE}?key={os.getenv("GEMINI_API_KEY")}', {}, f'models/{aistudio_model}'
