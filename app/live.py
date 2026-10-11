"""Live voice tutoring: a server-side proxy between the browser and a realtime voice model.

The browser streams microphone audio (PCM16, base64) over our own WebSocket; we relay it to
the provider chosen by LIVE_PROVIDER and stream the model's spoken audio (24 kHz PCM16) and
transcripts back. Keys stay server-side, and the session is grounded in the page being viewed.

- gemini: Gemini Live API (bidiGenerateContent), mic at 16 kHz.
- openai: OpenAI Realtime API (e.g. gpt-realtime-mini), mic at 24 kHz. Uses Azure OpenAI when
  AZURE_OPENAI_ENDPOINT/AZURE_OPENAI_KEY are set, otherwise OPENAI_API_KEY at api.openai.com.
"""
import asyncio
import collections
import json
import logging
import os
import time
import websockets
from app import languages, tutor

GEMINI_WS = 'wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent'
LIVE_MODEL = os.getenv('GEMINI_LIVE_MODEL', 'gemini-2.5-flash-native-audio-latest')
# Safety cap for one session (a forgotten open tab); the student's balance usually ends it first.
MAX_SECONDS = int(os.getenv('LIVE_MAX_SECONDS', '3600'))
# Talk time is charged ahead in blocks of this many seconds while the session runs.
BILLING_BLOCK = int(os.getenv('LIVE_BILLING_BLOCK_SECONDS', '60'))
# Google closes a Live connection after ~10 minutes (sending goAway first). We reconnect with a
# session-resumption handle so the talk continues; this many failed reconnects in a row ends it.
MAX_RECONNECT_FAILURES = 2
# A connection that ends sooner than this is treated as failed, so a broken setup cannot loop.
MIN_HEALTHY_SECONDS = 5
log = logging.getLogger(__name__)
OPENAI_MODEL = os.getenv('OPENAI_LIVE_MODEL', 'gpt-realtime-mini')
OPENAI_VOICE = os.getenv('OPENAI_LIVE_VOICE', 'marin')
OPENAI_TRANSCRIBE = os.getenv('OPENAI_LIVE_TRANSCRIBE_MODEL', 'gpt-4o-mini-transcribe')
# semantic_vad waits for the student to finish a thought (learners pause a lot); 'low' = patient.
OPENAI_EAGERNESS = os.getenv('OPENAI_LIVE_EAGERNESS', 'low')
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

# The tutor speaks first: as soon as Gemini is ready we send this hidden kickoff turn.
GREETING = 'Сайн байна уу? Өнөөдрийн хичээлээ эхэлцгээе, энэ хуудас нь {topic} сэдвийн тухай бичигдсэн байна.'
KICKOFF = (
    "[The student just opened live voice mode. Start the lesson now, speaking Mongolian. Open with exactly this "
    "sentence, replacing {topic} with a short Mongolian name for the topic of the current page (read it from the "
    "lesson title and page text): \"" + GREETING + "\" Then, in one short sentence, invite the student to begin — "
    "for example with the page's first word or phrase.]"
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


def provider():
    return os.getenv('LIVE_PROVIDER', 'gemini').strip().lower()


def openai_target():
    """(url, headers) for the OpenAI Realtime WebSocket, or None when no credential is set."""
    endpoint, azure_key = os.getenv('AZURE_OPENAI_ENDPOINT', '').rstrip('/'), os.getenv('AZURE_OPENAI_KEY')
    if endpoint and azure_key:
        deployment = os.getenv('AZURE_OPENAI_REALTIME_DEPLOYMENT', OPENAI_MODEL)
        url = endpoint.replace('https://', 'wss://', 1) + f'/openai/v1/realtime?model={deployment}'
        return url, {'api-key': azure_key}
    if os.getenv('OPENAI_API_KEY'):
        return (f'wss://api.openai.com/v1/realtime?model={OPENAI_MODEL}',
                {'Authorization': f'Bearer {os.getenv("OPENAI_API_KEY")}'})
    return None


def enabled():
    if provider() == 'openai':
        return openai_target() is not None
    return provider() == 'gemini' and bool(os.getenv('GEMINI_API_KEY'))


def config():
    """What the browser needs: whether Live works, and the mic sample rate the provider expects."""
    on = enabled()
    return {'enabled': on, 'provider': provider() if on else None,
            'input_rate': 24000 if provider() == 'openai' else 16000}


async def proxy(client_send, client_messages, book_title, page, usage=None, course_languages=None):
    """Relay a Live talk to the configured provider (see proxy_gemini / proxy_openai)."""
    run = proxy_openai if provider() == 'openai' else proxy_gemini
    return await run(client_send, client_messages, book_title, page, usage, course_languages)


async def proxy_gemini(client_send, client_messages, book_title, page, usage=None, course_languages=None):
    """Relay audio/text between a browser (client_send / client_messages) and Gemini Live.

    client_send(dict): coroutine sending a JSON message to the browser.
    client_messages: async iterator yielding decoded JSON dicts from the browser.
    usage: optional dict that accumulates billed tokens ('prompt'/'response') per turn.
    course_languages: language names the student may speak (see languages.for_course).

    One Gemini connection only lives ~10 minutes, so a talk is carried over as many connections
    as needed: context-window compression lifts the 15-minute audio-session limit, and session
    resumption lets each new connection continue the same conversation. The browser never notices.
    """
    usage = usage if usage is not None else {}
    usage.setdefault('prompt', 0)
    usage.setdefault('response', 0)
    turn = {}
    key = os.getenv('GEMINI_API_KEY')
    instruction = system_instruction(book_title, page, course_languages)
    # gemini: the connection that takes input (None while connecting); handle: latest resume point.
    state = {'gemini': None, 'handle': None}
    # Student input that arrives while we reconnect; sent once the new connection is ready.
    pending = collections.deque(maxlen=400)

    def setup():
        return {'setup': {
            'model': f'models/{LIVE_MODEL}',
            'generationConfig': {'responseModalities': ['AUDIO'],
                                 'speechConfig': {'voiceConfig': {'prebuiltVoiceConfig': {'voiceName': VOICE}}}},
            'systemInstruction': {'parts': [{'text': instruction}]},
            'realtimeInputConfig': {'automaticActivityDetection': {
                'startOfSpeechSensitivity': START_SENSITIVITY, 'prefixPaddingMs': PREFIX_PADDING_MS}},
            'inputAudioTranscription': {},
            'outputAudioTranscription': {},
            'contextWindowCompression': {'slidingWindow': {}},
            'sessionResumption': {'handle': state['handle']} if state['handle'] else {},
        }}

    def add_turn():
        usage['prompt'] += int(turn.get('promptTokenCount') or 0)
        usage['response'] += int(turn.get('responseTokenCount') or turn.get('candidatesTokenCount') or 0)
        turn.clear()

    def to_gemini(message):
        kind = message.get('type')
        if kind == 'audio':
            return {'realtimeInput': {'mediaChunks': [{'mimeType': 'audio/pcm;rate=16000', 'data': message['data']}]}}
        if kind == 'text' and message.get('text'):
            return {'clientContent': {'turns': [{'role': 'user', 'parts': [{'text': message['text'][:2000]}]}],
                                      'turnComplete': True}}
        return None

    async def browser_to_gemini():
        async for message in client_messages:
            out = to_gemini(message)
            if out is None:
                continue
            gemini = state['gemini']
            if gemini is None:
                pending.append(out)
                continue
            try:
                await gemini.send(json.dumps(out))
            except websockets.ConnectionClosed:
                pending.append(out)

    async def connection(resuming):
        """Run one Gemini connection; returns True if it got ready (setupComplete)."""
        ready = False
        try:
            async with websockets.connect(f'{GEMINI_WS}?key={key}', max_size=None, ping_interval=20) as gemini:
                await gemini.send(json.dumps(setup()))
                async for raw in gemini:
                    data = json.loads(raw)
                    if data.get('usageMetadata'):
                        # Reported per turn (latest wins); every turn re-bills the whole context.
                        turn.update(data['usageMetadata'])
                    update = data.get('sessionResumptionUpdate')
                    if update and update.get('resumable') and update.get('newHandle'):
                        state['handle'] = update['newHandle']
                    if 'setupComplete' in data:
                        ready = True
                        if resuming:
                            while pending:
                                await gemini.send(json.dumps(pending.popleft()))
                        else:
                            pending.clear()
                            await client_send({'type': 'ready'})
                            await gemini.send(json.dumps({'clientContent': {
                                'turns': [{'role': 'user', 'parts': [{'text': KICKOFF}]}], 'turnComplete': True}}))
                        state['gemini'] = gemini
                        continue
                    if data.get('goAway') is not None:
                        # Google is about to close this connection: move to a new one now.
                        log.info('Gemini Live goAway (%s left); resuming on a new connection',
                                 data['goAway'].get('timeLeft'))
                        return ready
                    content = data.get('serverContent')
                    if not content:
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
        except (websockets.ConnectionClosed, OSError) as exc:
            log.info('Gemini Live connection dropped: %s', exc)
        finally:
            state['gemini'] = None
        return ready

    up = asyncio.ensure_future(browser_to_gemini())
    failures, resuming = 0, False
    try:
        while True:
            opened = time.monotonic()
            down = asyncio.ensure_future(connection(resuming))
            await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
            if up.done():                      # the student left
                down.cancel()
                break
            lasted = time.monotonic() - opened >= MIN_HEALTHY_SECONDS
            failures = 0 if down.result() and lasted else failures + 1
            if not state['handle'] or failures >= MAX_RECONNECT_FAILURES:
                break                          # nothing to resume from, or Gemini keeps refusing
            resuming = True
    finally:
        up.cancel()
        add_turn()


async def proxy_openai(client_send, client_messages, book_title, page, usage=None, course_languages=None):
    """Relay audio/text between a browser and the OpenAI Realtime API; same contract as proxy_gemini.

    A Realtime session lasts at most 60 minutes (30 on Azure) and cannot be resumed, so if it ends
    while the student is still talking we open a new one and give it the recent transcript.
    """
    usage = usage if usage is not None else {}
    usage.setdefault('prompt', 0)
    usage.setdefault('response', 0)
    target = openai_target()
    instruction = system_instruction(book_title, page, course_languages)
    state = {'conn': None}
    pending = collections.deque(maxlen=400)
    history = []                    # (speaker, text) of finished turns, replayed after a reconnect
    partial = {'you': {}, 'tutor': ''}

    def session(resuming):
        text = instruction
        if resuming and history:
            text += ("\n\nCONVERSATION SO FAR (continue it naturally; do not greet the student again):\n"
                     + '\n'.join(f'{who}: {said}' for who, said in history[-30:]))
        return {'type': 'session.update', 'session': {
            'type': 'realtime',
            'instructions': text,
            'output_modalities': ['audio'],
            'audio': {
                'input': {'format': {'type': 'audio/pcm', 'rate': 24000},
                          'transcription': {'model': OPENAI_TRANSCRIBE,
                                            'prompt': 'The student speaks Mongolian, Korean or English.'},
                          'turn_detection': {'type': 'semantic_vad', 'eagerness': OPENAI_EAGERNESS}},
                'output': {'format': {'type': 'audio/pcm', 'rate': 24000}, 'voice': OPENAI_VOICE},
            },
        }}

    def to_openai(message):
        kind = message.get('type')
        if kind == 'audio':
            return [{'type': 'input_audio_buffer.append', 'audio': message['data']}]
        if kind == 'text' and message.get('text'):
            return [{'type': 'conversation.item.create', 'item': {
                        'type': 'message', 'role': 'user',
                        'content': [{'type': 'input_text', 'text': message['text'][:2000]}]}},
                    {'type': 'response.create'}]
        return []

    async def browser_to_openai():
        async for message in client_messages:
            for out in to_openai(message):
                conn = state['conn']
                if conn is None:
                    pending.append(out)
                    continue
                try:
                    await conn.send(json.dumps(out))
                except websockets.ConnectionClosed:
                    pending.append(out)

    async def connection(resuming):
        """Run one Realtime session; returns True if it got ready (session.updated)."""
        ready = False
        url, headers = target
        try:
            async with websockets.connect(url, additional_headers=headers, max_size=None, ping_interval=20) as conn:
                await conn.send(json.dumps(session(resuming)))
                async for raw in conn:
                    data = json.loads(raw)
                    kind = data.get('type', '')
                    if kind == 'session.updated' and not ready:
                        ready = True
                        if resuming:
                            while pending:
                                await conn.send(json.dumps(pending.popleft()))
                        else:
                            pending.clear()
                            await client_send({'type': 'ready'})
                            await conn.send(json.dumps({'type': 'conversation.item.create', 'item': {
                                'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': KICKOFF}]}}))
                            await conn.send(json.dumps({'type': 'response.create'}))
                        state['conn'] = conn
                    elif kind == 'input_audio_buffer.speech_started':
                        await client_send({'type': 'interrupted'})      # student barged in: stop playback
                    elif kind == 'response.output_audio.delta':
                        await client_send({'type': 'audio', 'data': data['delta']})
                    elif kind == 'response.output_audio_transcript.delta':
                        partial['tutor'] += data.get('delta', '')
                        await client_send({'type': 'tutor_text', 'text': data.get('delta', '')})
                    elif kind == 'conversation.item.input_audio_transcription.delta':
                        item = data.get('item_id')
                        partial['you'][item] = partial['you'].get(item, '') + data.get('delta', '')
                        await client_send({'type': 'user_text', 'text': data.get('delta', '')})
                    elif kind == 'conversation.item.input_audio_transcription.completed':
                        streamed = partial['you'].pop(data.get('item_id'), None)
                        said = (data.get('transcript') or '').strip()
                        if streamed is None and said:   # no deltas came: show the whole line now
                            await client_send({'type': 'user_text', 'text': said})
                        if said:
                            history.append(('Student', said))
                    elif kind == 'response.done':
                        used = (data.get('response') or {}).get('usage') or {}
                        usage['prompt'] += int(used.get('input_tokens') or 0)
                        usage['response'] += int(used.get('output_tokens') or 0)
                        if partial['tutor'].strip():
                            history.append(('Tutor', partial['tutor'].strip()))
                        partial['tutor'] = ''
                        await client_send({'type': 'turn_end'})
                    elif kind == 'error':
                        log.warning('OpenAI Realtime error: %s', str(data.get('error'))[:300])
        except (websockets.ConnectionClosed, websockets.InvalidStatus, OSError) as exc:
            log.info('OpenAI Realtime connection ended: %s', exc)
        finally:
            state['conn'] = None
        return ready

    up = asyncio.ensure_future(browser_to_openai())
    failures, resuming = 0, False
    try:
        while True:
            opened = time.monotonic()
            down = asyncio.ensure_future(connection(resuming))
            await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
            if up.done():
                down.cancel()
                break
            lasted = time.monotonic() - opened >= MIN_HEALTHY_SECONDS
            failures = 0 if down.result() and lasted else failures + 1
            if failures >= MAX_RECONNECT_FAILURES:
                break
            resuming = True
    finally:
        up.cancel()
