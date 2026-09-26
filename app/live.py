"""Gemini Live voice tutoring: a server-side proxy between the browser and Gemini.

The browser streams microphone audio (16 kHz PCM16, base64) over our own WebSocket;
we relay it to the Gemini Live API and stream the model's spoken audio (24 kHz PCM16)
and transcripts back. The Gemini key stays server-side, and the session is grounded
in the page the student is viewing. Requires the Gemini provider (bidiGenerateContent).
"""
import asyncio
import json
import os
import websockets
from app import tutor

GEMINI_WS = 'wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent'
LIVE_MODEL = os.getenv('GEMINI_LIVE_MODEL', 'gemini-2.5-flash-native-audio-latest')
MAX_SECONDS = int(os.getenv('LIVE_MAX_SECONDS', '300'))
VOICE = os.getenv('GEMINI_LIVE_VOICE', 'Aoede')

VOICE_STYLE = (
    "\n\nYou are now in a LIVE SPOKEN conversation — the student hears your voice and talks back. "
    "Speak naturally and briefly: one or two short sentences per turn, like a real tutor chatting. "
    "When you say a Korean/target-language word, say it clearly and, if the student seems unsure, give the "
    "meaning in their language. Do not read long lists aloud; invite the student to speak and practice."
)


def system_instruction(book_title, page):
    return tutor.SYSTEM + VOICE_STYLE + "\n\n" + tutor._page_context(book_title, page)


async def proxy(client_send, client_messages, book_title, page):
    """Relay audio/text between a browser (client_send / client_messages) and Gemini Live.

    client_send(dict): coroutine sending a JSON message to the browser.
    client_messages: async iterator yielding decoded JSON dicts from the browser.
    """
    key = os.getenv('GEMINI_API_KEY')
    setup = {'setup': {
        'model': f'models/{LIVE_MODEL}',
        'generationConfig': {'responseModalities': ['AUDIO'],
                             'speechConfig': {'voiceConfig': {'prebuiltVoiceConfig': {'voiceName': VOICE}}}},
        'systemInstruction': {'parts': [{'text': system_instruction(book_title, page)}]},
        'inputAudioTranscription': {},
        'outputAudioTranscription': {},
    }}
    async with websockets.connect(f'{GEMINI_WS}?key={key}', max_size=None, ping_interval=20) as gemini:
        await gemini.send(json.dumps(setup))

        async def browser_to_gemini():
            async for message in client_messages:
                kind = message.get('type')
                if kind == 'audio':
                    await gemini.send(json.dumps({'realtimeInput': {'mediaChunks': [
                        {'mimeType': 'audio/pcm;rate=16000', 'data': message['data']}]}}))
                elif kind == 'text' and message.get('text'):
                    await gemini.send(json.dumps({'clientContent': {
                        'turns': [{'role': 'user', 'parts': [{'text': message['text'][:2000]}]}], 'turnComplete': True}}))

        async def gemini_to_browser():
            async for raw in gemini:
                data = json.loads(raw)
                if 'setupComplete' in data:
                    await client_send({'type': 'ready'})
                    continue
                content = data.get('serverContent')
                if not content:
                    if data.get('goAway') is not None:
                        await client_send({'type': 'end'})
                    continue
                if content.get('interrupted'):
                    await client_send({'type': 'interrupted'})
                for part in (content.get('modelTurn', {}).get('parts') or []):
                    inline = part.get('inlineData')
                    if inline and (inline.get('mimeType') or '').startswith('audio/'):
                        await client_send({'type': 'audio', 'data': inline['data']})
                if content.get('inputTranscription', {}).get('text'):
                    await client_send({'type': 'user_text', 'text': content['inputTranscription']['text']})
                if content.get('outputTranscription', {}).get('text'):
                    await client_send({'type': 'tutor_text', 'text': content['outputTranscription']['text']})
                if content.get('turnComplete'):
                    await client_send({'type': 'turn_end'})

        up = asyncio.ensure_future(browser_to_gemini())
        down = asyncio.ensure_future(gemini_to_browser())
        try:
            await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            up.cancel()
            down.cancel()
