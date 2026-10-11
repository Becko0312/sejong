import asyncio
import json
from app import live


class FakeGemini:
    def __init__(self):
        self.sent = []
        self.incoming = asyncio.Queue()
        self.incoming.put_nowait(json.dumps({'setupComplete': {}}))

    async def send(self, raw):
        self.sent.append(json.loads(raw))
        if 'clientContent' in self.sent[-1]:
            self.incoming.put_nowait(None)  # end the stream once the kickoff arrives

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def test_tutor_opens_the_lesson_with_the_greeting(monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(live.websockets, 'connect', lambda *a, **k: fake)
    to_browser = []

    async def client_send(msg):
        to_browser.append(msg)

    async def client_messages():
        await asyncio.sleep(1)
        return
        yield

    asyncio.run(live.proxy(client_send, client_messages(), 'Korean 1', {'number': 3, 'text': '안녕하세요'}))
    assert to_browser == [{'type': 'ready'}]
    assert 'setup' in fake.sent[0]
    kickoff = fake.sent[1]['clientContent']
    assert kickoff['turnComplete'] is True
    assert 'Сайн байна уу? Өнөөдрийн хичээлээ эхэлцгээе' in kickoff['turns'][0]['parts'][0]['text']


class ScriptedGemini(FakeGemini):
    """A Gemini connection that plays `script` (raw dicts) after setupComplete, then closes."""
    def __init__(self, script):
        super().__init__()
        for item in script:
            self.incoming.put_nowait(json.dumps(item))
        self.incoming.put_nowait(None)

    async def send(self, raw):
        self.sent.append(json.loads(raw))


def test_session_resumes_on_a_new_connection_after_goaway(monkeypatch):
    first = ScriptedGemini([
        {'sessionResumptionUpdate': {'newHandle': 'h1', 'resumable': True}},
        {'goAway': {'timeLeft': '5s'}},
    ])
    second = ScriptedGemini([
        {'serverContent': {'outputTranscription': {'text': 'still here'}}},
    ])
    connections = iter([first, second])

    def connect(*a, **k):
        conn = next(connections, None)
        if conn is None:
            raise OSError('Gemini unreachable')         # later reconnects fail: the proxy gives up
        return conn
    monkeypatch.setattr(live.websockets, 'connect', connect)
    to_browser = []

    async def client_send(msg):
        to_browser.append(msg)

    async def client_messages():
        await asyncio.sleep(5)
        return
        yield

    asyncio.run(live.proxy(client_send, client_messages(), 'Korean 1', {'number': 3, 'text': '안녕하세요'}))
    setup1, setup2 = first.sent[0]['setup'], second.sent[0]['setup']
    assert setup1['sessionResumption'] == {} and setup1['contextWindowCompression'] == {'slidingWindow': {}}
    assert setup2['sessionResumption'] == {'handle': 'h1'}
    assert len(second.sent) == 1                       # no second greeting on the resumed connection
    assert to_browser == [{'type': 'ready'}, {'type': 'tutor_text', 'text': 'still here'}]


def test_audio_during_reconnect_is_buffered_and_sent_after(monkeypatch):
    first = ScriptedGemini([{'sessionResumptionUpdate': {'newHandle': 'h1', 'resumable': True}}])
    second = FakeGemini()
    second.incoming.get_nowait()                       # hold back setupComplete: still connecting

    async def record(raw):
        second.sent.append(json.loads(raw))
    second.send = record
    connections = iter([first, second])
    monkeypatch.setattr(live.websockets, 'connect', lambda *a, **k: next(connections))

    async def client_send(msg):
        pass

    async def client_messages():
        await asyncio.sleep(0.05)                      # first connection is gone, second not ready
        yield {'type': 'audio', 'data': 'AAAA'}
        await asyncio.sleep(0.05)
        second.incoming.put_nowait(json.dumps({'setupComplete': {}}))
        await asyncio.sleep(0.05)
        second.incoming.put_nowait(None)

    asyncio.run(live.proxy(client_send, client_messages(), 'Korean 1', {'number': 3, 'text': '안녕하세요'}))
    assert not [m for m in first.sent if 'realtimeInput' in m]
    assert second.sent[1:] == [{'realtimeInput': {'mediaChunks': [{'mimeType': 'audio/pcm;rate=16000', 'data': 'AAAA'}]}}]


def test_gives_up_when_there_is_nothing_to_resume(monkeypatch):
    calls = []

    def connect(*a, **k):
        calls.append(1)
        return ScriptedGemini([{'goAway': {'timeLeft': '1s'}}])
    monkeypatch.setattr(live.websockets, 'connect', connect)

    async def client_send(msg):
        pass

    async def client_messages():
        await asyncio.sleep(5)
        return
        yield

    asyncio.run(live.proxy(client_send, client_messages(), 'Korean 1', {'number': 3, 'text': 'x'}))
    assert len(calls) == 1


class FakeRealtime:
    """An OpenAI Realtime connection: acks session.update, then plays `script` and closes."""
    def __init__(self, script, stay_open=False):
        self.sent, self.headers = [], None
        self.incoming = asyncio.Queue()
        self.script, self.stay_open = script, stay_open

    async def send(self, raw):
        msg = json.loads(raw)
        self.sent.append(msg)
        if msg['type'] == 'session.update':
            self.incoming.put_nowait(json.dumps({'type': 'session.updated'}))
            for item in self.script:
                self.incoming.put_nowait(json.dumps(item))
            if not self.stay_open:
                self.incoming.put_nowait(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def use_openai(monkeypatch, connections, azure=False):
    monkeypatch.setenv('LIVE_PROVIDER', 'openai')
    if azure:
        monkeypatch.setenv('AZURE_OPENAI_ENDPOINT', 'https://sejong-openai.openai.azure.com/')
        monkeypatch.setenv('AZURE_OPENAI_KEY', 'az-test')
        monkeypatch.setenv('AZURE_OPENAI_REALTIME_DEPLOYMENT', 'rt-mini')
    else:
        monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    calls = []
    conns = iter(connections)

    def connect(url, additional_headers=None, **kw):
        calls.append((url, additional_headers))
        conn = next(conns, None)
        if conn is None:
            raise OSError('unreachable')
        return conn
    monkeypatch.setattr(live.websockets, 'connect', connect)
    return calls


def test_live_config_follows_the_provider(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('AZURE_OPENAI_ENDPOINT', raising=False)
    monkeypatch.setenv('LIVE_PROVIDER', 'openai')
    assert live.config()['enabled'] is False
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    assert live.config() == {'enabled': True, 'provider': 'openai', 'input_rate': 24000}
    assert live.openai_target()[0] == 'wss://api.openai.com/v1/realtime?model=gpt-realtime-mini'
    monkeypatch.setenv('AZURE_OPENAI_ENDPOINT', 'https://sejong-openai.openai.azure.com/')
    monkeypatch.setenv('AZURE_OPENAI_KEY', 'az-test')
    monkeypatch.setenv('AZURE_OPENAI_REALTIME_DEPLOYMENT', 'rt-mini')
    assert live.openai_target() == ('wss://sejong-openai.openai.azure.com/openai/v1/realtime?model=rt-mini',
                                    {'api-key': 'az-test'})
    monkeypatch.setenv('LIVE_PROVIDER', 'gemini')
    monkeypatch.setenv('GEMINI_API_KEY', 'AQ.test')
    assert live.config()['input_rate'] == 16000


def test_openai_talk_relays_audio_transcripts_and_usage(monkeypatch):
    conn = FakeRealtime([
        {'type': 'response.output_audio_transcript.delta', 'delta': 'Сайн байна уу?'},
        {'type': 'response.output_audio.delta', 'delta': 'UklGRg=='},
        {'type': 'response.done', 'response': {'usage': {'input_tokens': 900, 'output_tokens': 120}}},
        {'type': 'input_audio_buffer.speech_started'},
        {'type': 'conversation.item.input_audio_transcription.delta', 'item_id': 'i1', 'delta': 'Баярлалаа'},
        {'type': 'conversation.item.input_audio_transcription.completed', 'item_id': 'i1', 'transcript': 'Баярлалаа'},
        {'type': 'conversation.item.input_audio_transcription.completed', 'item_id': 'i2', 'transcript': '감사합니다'},
    ], stay_open=True)
    calls = use_openai(monkeypatch, [conn], azure=True)
    to_browser, usage = [], {}

    async def client_send(msg):
        to_browser.append(msg)

    async def client_messages():
        await asyncio.sleep(0.05)                      # audio before the session is ready is dropped
        yield {'type': 'audio', 'data': 'AAAA'}
        await asyncio.sleep(0.05)
        conn.incoming.put_nowait(None)

    asyncio.run(live.proxy(client_send, client_messages(), 'Korean 1', {'number': 3, 'text': '안녕하세요'}, usage))
    assert calls[0] == ('wss://sejong-openai.openai.azure.com/openai/v1/realtime?model=rt-mini', {'api-key': 'az-test'})
    setup = conn.sent[0]['session']
    assert setup['audio']['input']['format'] == {'type': 'audio/pcm', 'rate': 24000}
    assert 'Сайн байна уу? Өнөөдрийн хичээлээ эхэлцгээе' in conn.sent[1]['item']['content'][0]['text']
    assert conn.sent[2] == {'type': 'response.create'}
    assert {'type': 'input_audio_buffer.append', 'audio': 'AAAA'} in conn.sent
    assert to_browser == [
        {'type': 'ready'},
        {'type': 'tutor_text', 'text': 'Сайн байна уу?'},
        {'type': 'audio', 'data': 'UklGRg=='},
        {'type': 'turn_end'},
        {'type': 'interrupted'},
        {'type': 'user_text', 'text': 'Баярлалаа'},          # streamed once, not repeated on completed
        {'type': 'user_text', 'text': '감사합니다'},           # no deltas: shown when completed
    ]
    assert usage == {'prompt': 900, 'response': 120}


def test_openai_reconnect_replays_the_conversation(monkeypatch):
    first = FakeRealtime([
        {'type': 'conversation.item.input_audio_transcription.completed', 'item_id': 'i1', 'transcript': 'Hello'},
        {'type': 'response.output_audio_transcript.delta', 'delta': 'Hi there'},
        {'type': 'response.done', 'response': {}},
    ])
    second = FakeRealtime([], stay_open=True)
    use_openai(monkeypatch, [first, second])
    monkeypatch.setattr(live, 'MIN_HEALTHY_SECONDS', 0)

    async def client_send(msg):
        pass

    async def client_messages():
        await asyncio.sleep(0.2)
        yield {'type': 'text', 'text': 'still there?'}
        await asyncio.sleep(0.1)

    asyncio.run(live.proxy(client_send, client_messages(), 'Korean 1', {'number': 3, 'text': 'x'}))
    resumed = second.sent[0]['session']['instructions']
    assert 'CONVERSATION SO FAR' in resumed and 'Student: Hello' in resumed and 'Tutor: Hi there' in resumed
    assert all('Өнөөдрийн' not in json.dumps(m) for m in second.sent[1:])   # no second greeting
    assert second.sent[1]['item']['content'][0]['text'] == 'still there?'
