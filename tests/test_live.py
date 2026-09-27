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
