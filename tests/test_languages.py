"""Supported course languages: upload validation, OCR packs, and the Gemini Live language rules."""
from app import languages, live, store
from tests.conftest import sample


def test_normalize_and_ocr():
    assert languages.normalize('korean') == languages.normalize('kor') == languages.normalize('한국어') == 'Korean'
    assert languages.normalize('Hindi') is None
    assert languages.ocr_for('Korean', 'Mongolian') == 'kor+mon+eng'
    assert languages.ocr_for('Chinese', 'Russian') == 'chi_sim+rus+eng'


def test_course_languages_always_include_mongolian_and_english():
    assert languages.for_course('Korean', 'Mongolian') == ['Korean', 'Mongolian', 'English']
    assert languages.for_course('Japanese', 'English') == ['Japanese', 'English', 'Mongolian']
    # Older courses without a pair fall back to their OCR languages.
    assert languages.for_course(None, None, 'kor+mon+eng') == ['Korean', 'Mongolian', 'English']


def test_live_instruction_names_the_course_languages():
    text = live.system_instruction('Korean 1', {'number': 1, 'text': '안녕'}, ['Korean', 'Mongolian', 'English'])
    assert 'ONLY Korean, Mongolian or English' in text
    assert 'Never interpret' in text and 'Hindi' in text and 'assume it is Mongolian' in text
    assert 'Сайн байна уу' in text


def test_upload_derives_ocr_from_language_pair(admin):
    a, ha = admin
    headers = {**ha, 'content-type': 'application/pdf'}
    job = a.post('/api/jobs?name=b.pdf&source_lang=japanese&target_lang=Mongolian', content=sample(), headers=headers).json()
    with store.connect() as db:
        row = db.execute('SELECT source_lang, target_lang, languages FROM jobs WHERE id=?', (job['id'],)).fetchone()
    assert tuple(row) == ('Japanese', 'Mongolian', 'mon+jpn+eng')
    res = a.post('/api/jobs?name=b.pdf&source_lang=Hindi&target_lang=Mongolian', content=sample(), headers=headers)
    assert res.status_code == 400 and 'Unsupported language' in res.json()['detail']
    assert a.patch(f"/api/jobs/{job['id']}", json={'source_lang': 'Thai'}, headers=ha).status_code == 400
    assert a.patch(f"/api/jobs/{job['id']}", json={'source_lang': 'russian'}, headers=ha).json()['source_lang'] == 'Russian'
    assert a.get('/api/me').json()['languages'] == list(languages.LANGUAGES)


def test_native_language_drives_the_live_greeting():
    assert languages.for_course('Korean', 'Mongolian', native='English') == ['Korean', 'Mongolian', 'English']
    assert languages.for_course('Korean', 'Japanese', native='Russian') == ['Korean', 'Japanese', 'Russian', 'Mongolian', 'English']
    english = live.kickoff('English')
    assert 'speaking English' in english and 'Сайн байна уу' not in english
    assert 'Сайн байна уу' in live.kickoff(None) and 'speaking Mongolian' in live.kickoff('Mongolian')
    assert 'In English' in live.page_turn(3, 'English')
    rules = live.language_rules(['Korean', 'English', 'Mongolian'], 'English')
    assert 'assume it is English' in rules and '(English by default)' in rules


def test_sign_up_stores_the_mother_tongue(env):
    from fastapi.testclient import TestClient
    from app import main
    with TestClient(main.app) as client:      # lifespan creates the tables
        assert 'English' in client.get('/api/me').json()['native_languages']
        res = client.post('/api/register', json={'username': 'eng_student', 'password': 'password123', 'native_lang': 'english'})
        assert res.status_code == 200 and res.json()['native_lang'] == 'English'
        bad = client.post('/api/register', json={'username': 'x_student', 'password': 'password123', 'native_lang': 'Klingon'})
        assert bad.status_code == 400
        # Older app versions do not send it: Mongolian, as before.
        old = client.post('/api/register', json={'username': 'mn_student', 'password': 'password123'})
        assert old.json()['native_lang'] == 'Mongolian'
