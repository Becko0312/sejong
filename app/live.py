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
from app import languages, tutor

GEMINI_WS = 'wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent'
LIVE_MODEL = os.getenv('GEMINI_LIVE_MODEL', 'gemini-2.5-flash-native-audio-latest')
MAX_SECONDS = int(os.getenv('LIVE_MAX_SECONDS', '300'))
VOICE = os.getenv('GEMINI_LIVE_VOICE', 'Aoede')
# Barge-in tuning: short noises or speaker echo must not count as the student speaking,
# otherwise Gemini aborts its reply mid-sentence and waits for a turn that never came.
START_SENSITIVITY = os.getenv('GEMINI_LIVE_START_SENSITIVITY', 'START_SENSITIVITY_LOW')
PREFIX_PADDING_MS = int(os.getenv('GEMINI_LIVE_PREFIX_PADDING_MS', '200'))

VOICE_STYLE = (
    "\n\nYou are now in a LIVE SPOKEN conversation — the student hears your voice and talks back. "
    "Speak naturally and briefly: one or two short sentences per turn, like a real tutor chatting. "
    "When you say a Korean/target-language word, say it clearly and, if the student seems unsure, give the "
    "meaning in their language. Do not read long lists aloud; invite the student to speak and practice."
)


def language_rules(names):
    """Native-audio Live models cannot be locked to a language code (Google: "restrict the languages
    ... in the system instructions"), so we name the course's languages explicitly. Without this,
    Mongolian speech is sometimes heard as Hindi or Thai."""
    names = names or list(languages.STUDENT_LANGUAGES)
    listed = ', '.join(names[:-1]) + (f' or {names[-1]}' if len(names) > 1 else names[0])
    hints = '\n'.join(f'- {languages.LANGUAGES[n]["hint"]}' for n in names if n in languages.LANGUAGES)
    return (
        f"\n\nLANGUAGES IN THIS SESSION: the student speaks ONLY {listed} — often mixing them in one sentence "
        f"while practising.\n{hints}\n"
        f"Every word you hear is in one of these languages. Never interpret the student's speech as Hindi, Thai, "
        f"or any other language, and never answer in one. If you are unsure which language you heard, assume "
        f"it is Mongolian, the student's native language. Reply only in {listed}: explain in the language the "
        f"student is speaking (Mongolian by default), and say practice words in the language being studied."
    )


def system_instruction(book_title, page, course_languages=None):
    return (tutor.SYSTEM + VOICE_STYLE + language_rules(course_languages) + "\n\n"
            + tutor._page_context(book_title, page))


async def proxy(client_send, client_messages, book_title, page, usage=None, course_languages=None):
    """Relay audio/text between a browser (client_send / client_messages) and Gemini Live.

    client_send(dict): coroutine sending a JSON message to the browser.
    client_messages: async iterator yielding decoded JSON dicts from the browser.
    usage: optional dict that accumulates billed tokens ('prompt'/'response') per turn.
    course_languages: language names the student may speak (see languages.for_course).
    """
    usage = usage if usage is not None else {}
    usage.setdefault('prompt', 0)
    usage.setdefault('response', 0)
    turn = {}
    key = os.getenv('GEMINI_API_KEY')
    setup = {'setup': {
        'model': f'models/{LIVE_MODEL}',
        'generationConfig': {'responseModalities': ['AUDIO'],
                             'speechConfig': {'voiceConfig': {'prebuiltVoiceConfig': {'voiceName': VOICE}}}},
        'systemInstruction': {'parts': [{'text': system_instruction(book_title, page, course_languages)}]},
        'realtimeInputConfig': {'automaticActivityDetection': {
            'startOfSpeechSensitivity': START_SENSITIVITY, 'prefixPaddingMs': PREFIX_PADDING_MS}},
        'inputAudioTranscription': {},
        'outputAudioTranscription': {},
    }}
    def add_turn():
        usage['prompt'] += int(turn.get('promptTokenCount') or 0)
        usage['response'] += int(turn.get('responseTokenCount') or turn.get('candidatesTokenCount') or 0)
        turn.clear()

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
                if data.get('usageMetadata'):
                    # Reported per turn (latest wins); every turn re-bills the whole context.
                    turn.update(data['usageMetadata'])
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
                    add_turn()
                    await client_send({'type': 'turn_end'})

        up = asyncio.ensure_future(browser_to_gemini())
        down = asyncio.ensure_future(gemini_to_browser())
        try:
            await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            up.cancel()
            down.cancel()
            add_turn()
