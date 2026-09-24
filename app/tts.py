"""Azure Speech text-to-speech for the course reader (optional, server-side).

Browsers and Windows ship no Mongolian TTS voice, so `speechSynthesis` cannot
read mn-MN text reliably. When AZURE_SPEECH_KEY and AZURE_SPEECH_REGION are
set, the app exposes /api/tts and synthesizes each spoken segment with Azure
neural voices, identically in every browser. With no key the endpoint reports
"not configured" and the reader falls back to the browser voices it has.
"""
import os
from collections import OrderedDict
from xml.sax.saxutils import escape

ENDPOINT = 'https://{region}.tts.speech.microsoft.com/cognitiveservices/v1'
OUTPUT_FORMAT = 'audio-24khz-48kbitrate-mono-mp3'
MAX_TEXT = 2000
# Language tag -> default Azure neural voice. TTS_VOICE_MN overrides the
# Mongolian voice (for example mn-MN-BataarNeural for a male voice).
VOICES = {
    'mn-MN': 'mn-MN-YesuiNeural',
    'ko-KR': 'ko-KR-SunHiNeural',
    'ja-JP': 'ja-JP-NanamiNeural',
    'zh-CN': 'zh-CN-XiaoxiaoNeural',
    'en-US': 'en-US-AriaNeural',
    'ru-RU': 'ru-RU-SvetlanaNeural',
}
DEFAULT_VOICE = 'en-US-AriaNeural'
# Tutor replies repeat little, but caching keeps replays free and instant.
CACHE_LIMIT = 128

_cache = OrderedDict()


class TtsError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def enabled():
    """True when an Azure Speech key and region are configured."""
    return bool(os.getenv('AZURE_SPEECH_KEY') and os.getenv('AZURE_SPEECH_REGION'))


def config():
    return {'enabled': enabled()}


def voice_for(lang):
    """The Azure neural voice for a BCP-47-ish language tag."""
    lang = (lang or '').strip()
    if lang.lower().startswith('mn'):
        return os.getenv('TTS_VOICE_MN', '').strip() or VOICES['mn-MN']
    if lang in VOICES:
        return VOICES[lang]
    base = lang.split('-')[0].lower()
    for tag, voice in VOICES.items():
        if tag.split('-')[0].lower() == base:
            return voice
    return DEFAULT_VOICE


def synthesize(text, lang='en-US'):
    """Return MP3 bytes of `text` spoken by the voice for `lang`."""
    text = (text or '').strip()
    if not text:
        raise TtsError('There is no text to read aloud.')
    if len(text) > MAX_TEXT:
        raise TtsError('That text is too long to read aloud.')
    if not enabled():
        raise TtsError('Speech is not configured on this server yet.')
    voice = voice_for(lang)
    key = (voice, text)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    ssml = ('<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{lang}">'
            '<voice name="{voice}">{text}</voice></speak>').format(
        lang=voice.rsplit('-', 1)[0], voice=voice, text=escape(text))
    import requests
    try:
        response = requests.post(
            ENDPOINT.format(region=os.getenv('AZURE_SPEECH_REGION')),
            data=ssml.encode('utf-8'),
            headers={'Ocp-Apim-Subscription-Key': os.getenv('AZURE_SPEECH_KEY', ''),
                     'Content-Type': 'application/ssml+xml',
                     'X-Microsoft-OutputFormat': OUTPUT_FORMAT,
                     'User-Agent': 'book2course'},
            timeout=30)
    except requests.RequestException as exc:
        raise TtsError('Speech is not available right now. Please try again.') from exc
    if response.status_code in (401, 403):
        raise TtsError('The speech credentials are invalid.')
    if response.status_code == 429:
        raise TtsError('Speech is busy right now. Please try again in a moment.')
    if response.status_code != 200 or not response.content:
        raise TtsError('Speech is not available right now. Please try again.')
    audio = response.content
    _cache[key] = audio
    while len(_cache) > CACHE_LIMIT:
        _cache.popitem(last=False)
    return audio
