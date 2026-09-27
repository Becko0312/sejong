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
