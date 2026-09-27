"""Lesson plans (printed pages + offset), AI summaries/tests and graded scores. Gemini mocked."""
import json
import time
import types
import pytest
from app import builder, main, store, summary

SUMMARY = {'title': '저는 한국 사람이에요', 'title_tr': 'Би солонгос хүн', 'goals': ['Өөрийгөө танилцуулах'],
           'sections': [{'kind': 'grammar', 'heading': 'N + 이에요/예요', 'explanation': 'Тайлбар',
                         'items': [{'ko': '저는 학생이에요.', 'rom': 'jeoneun haksaengieyo.', 'tr': 'Би оюутан.', 'note': ''},
                                   {'ko': '', 'rom': 'dropped', 'tr': ''}]},
                        {'kind': 'bogus', 'heading': 'Empty', 'explanation': '', 'items': []}]}
TEST = {'questions': [{'q': 'Аль нь зөв бэ?', 'ko': '저는 학생___.', 'rom': 'jeoneun haksaeng___.',
                       'choices': [{'text': '이에요', 'rom': 'ieyo'}, {'text': '예요', 'rom': 'yeyo'},
                                   {'text': '은', 'rom': 'eun'}, {'text': '를', 'rom': 'reul'}],
                       'answer': 0, 'explain': 'Гийгүүлэгчээр төгссөн.'},
                      {'q': 'broken', 'choices': [{'text': 'a'}], 'answer': 3}]}


def fake_gemini(monkeypatch):
    """Summary on the first call of each lesson, test on the second; returns request bodies."""
    calls = []
    monkeypatch.setenv('GEMINI_API_KEY', 'AQ.test')
    import requests

    def fake_post(url, params=None, json=None, timeout=None):
        calls.append(json)
        prompt = json['contents'][0]['parts'][0]['text']
        text = __import__('json').dumps(TEST if '<lesson_summary>' in prompt else SUMMARY)
        return types.SimpleNamespace(status_code=200,
                                     json=lambda: {'candidates': [{'content': {'parts': [{'text': text}]}}]})

    monkeypatch.setattr(requests, 'post', fake_post)
    return calls


def book():
    pages = [{'number': n, 'text': ''} for n in range(1, 13)]
    pages[1]['text'] = ('목차 1 과 저는 한국 사 람 이에요 3 БИ СОЛОНГОС ХҮН 2 과 회 사 원 이 아니에요 6 '
                        '3 과 저도 드 라 마 를 좋 아 합니다 9 종합 문 제 (1 과 ~3 과 ) 11')
    pages[3]['text'] = '저는 한국 사 람 이에요 Part 1'
    pages[4]['text'] = '안녕하세요? 저는 학생이에요.'
    pages[6]['text'] = '회사원이 아니에요'
    return {'title': 'Korean 1', 'page_count': 12, 'pages': pages}


def seed_job(env, job_id='job1'):
    out = env / job_id / 'output'
    out.mkdir(parents=True)
    (out / 'manifest.json').write_text(json.dumps(book()), encoding='utf-8')
    with store.connect() as db:
        db.execute("INSERT INTO jobs(id, name, status, done, total, created, owner, expires, available, title, source_lang, target_lang)"
                   " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (job_id, 'doc.pdf', 'completed', 12, 12, time.time(), 'admin', time.time() + 3600, 1, 'K', 'Korean', 'Mongolian'))


PLAN = {'offset': 1, 'lessons': [{'index': 1, 'title': '저는 한국 사람이에요', 'start': 3, 'end': 5},
                                 {'index': 2, 'title': '회사원이 아니에요', 'start': 6, 'end': 8}]}


# ---------- plan ----------

def test_suggest_plan_reads_contents_page_and_offset():
    plan = builder.suggest_plan(book()['pages'])
    assert plan['offset'] == 1                     # printed 3 is PDF page 4
    assert [(l['index'], l['start'], l['end']) for l in plan['lessons']] == [(1, 3, 5), (2, 6, 8), (3, 9, 11)]
    assert plan['lessons'][0]['title'] == '저는 한국 사 람 이에요'


def test_suggest_plan_interpolates_a_garbled_lesson():
    pages = [{'number': 1, 'text': '1 과 A가 10 2 과 B나 22 122) 흐림 34 4 과 D라 46'}]
    pages += [{'number': n, 'text': ''} for n in range(2, 80)]
    lessons = builder.suggest_plan(pages)['lessons']
    assert [(l['index'], l['start']) for l in lessons] == [(1, 10), (2, 22), (3, 34), (4, 46)]
    assert lessons[2]['title'] == 'Lesson 3'


@pytest.mark.parametrize('plan, message', [
    ({'offset': 0, 'lessons': []}, 'at least one'),
    ({'offset': 0, 'lessons': [{'index': 1, 'start': 5, 'end': 3}]}, 'after the last'),
    ({'offset': 5, 'lessons': [{'index': 1, 'start': 3, 'end': 9}]}, 'outside the book'),
    ({'offset': 0, 'lessons': [{'index': 1, 'start': 1, 'end': 5}, {'index': 2, 'start': 5, 'end': 6}]}, 'overlap'),
    ({'offset': 0, 'lessons': [{'index': 1, 'start': 1, 'end': 2}, {'index': 1, 'start': 3, 'end': 4}]}, 'unique'),
])
def test_validate_plan_rejects_bad_ranges(plan, message):
    with pytest.raises(builder.PlanError, match=message):
        builder.validate_plan(plan, 12)


def test_build_course_uses_plan_pages():
    course = builder.build_course(book(), builder.validate_plan(PLAN, 12))
    assert [(l['index'], l['start_page'], l['end_page']) for l in course['lessons']] == [(1, 4, 6), (2, 7, 9)]
    assert course['front_pages'] == [1, 2, 3] and course['other_pages'] == [10, 11, 12]
    assert course['pages'][4]['lesson'] == 1


# ---------- generation ----------

def test_generate_validates_model_json(env, monkeypatch):
    calls = fake_gemini(monkeypatch)
    lesson = {**PLAN['lessons'][0], 'offset': 1}
    data = summary.generate(book(), lesson, 'Korean', 'Mongolian')
    assert len(calls) == 2
    prompt = calls[0]['contents'][0]['parts'][0]['text']
    assert '[page 4]' in prompt and '저는 학생이에요' in prompt and 'Mongolian' in prompt
    assert len(data['summary']['sections']) == 1                      # the empty section is dropped
    assert data['summary']['sections'][0]['items'] == [SUMMARY['sections'][0]['items'][0]]
    [q] = data['test']['questions']                                   # the broken question is dropped
    assert q['choices'][q['answer']]['text'] == '이에요'              # answer follows the shuffle


def test_save_plan_drops_changed_lessons(env):
    data_dir = env / 'job1'
    plan = builder.validate_plan(PLAN, 12)
    for l in plan['lessons']:
        summary._write_json(summary.lesson_path(data_dir, l['index']),
                            {'lesson': {**l, 'offset': 1}, 'summary': SUMMARY, 'test': TEST})
    changed = builder.validate_plan({**PLAN, 'lessons': [PLAN['lessons'][0], {**PLAN['lessons'][1], 'end': 9}]}, 12)
    summary.save_plan(data_dir, changed)
    assert summary.built_lessons(data_dir, changed) == [1]


# ---------- endpoints ----------

def test_admin_plan_build_and_student_test_flow(admin, env, monkeypatch):
    client, headers = admin
    seed_job(env)
    fake_gemini(monkeypatch)
    info = client.get('/api/admin/courses/job1/lessons').json()
    assert info['saved'] is False and info['plan']['offset'] == 1 and info['enabled'] is True
    assert client.post('/api/admin/courses/job1/summary', headers=headers).status_code == 409   # no plan yet
    bad = client.put('/api/admin/courses/job1/lessons', headers=headers, json={'offset': 0, 'lessons': []})
    assert bad.status_code == 400
    res = client.put('/api/admin/courses/job1/lessons', headers=headers, json=PLAN)
    assert res.status_code == 200 and res.json()['estimate']['lessons'] == 2
    course = client.get('/api/courses/job1').json()
    assert [l['start_page'] for l in course['lessons']] == [4, 7]
    monkeypatch.setattr(summary, 'start_batch', lambda d, m, p, t, e: summary.build_all(d, m, p, t, e) or True)
    assert client.post('/api/admin/courses/job1/summary', headers=headers).status_code == 200
    assert summary.read_status(env / 'job1')['state'] == 'done'
    assert client.get('/api/courses/job1').json()['summary']['built'] == [1, 2]

    s = client.get('/api/courses/job1/lessons/1/summary').json()
    assert s['title'] == SUMMARY['title'] and s['sections'][0]['items'][0]['rom']
    t = client.get('/api/courses/job1/lessons/1/test').json()
    assert 'answer' not in t['questions'][0] and 'explain' not in t['questions'][0] and t['score'] is None
    answer = summary.load_lesson(env / 'job1', 1)['test']['questions'][0]['answer']
    wrong = (answer + 1) % 4
    graded = client.post('/api/courses/job1/lessons/1/test', headers=headers, json={'answers': [wrong]}).json()
    assert graded['score'] == 0 and graded['results'][0]['answer'] == answer
    graded = client.post('/api/courses/job1/lessons/1/test', headers=headers, json={'answers': [answer]}).json()
    assert graded['score'] == 1 and graded['score_record'] == {'best': 1, 'last': 1, 'total': 1, 'attempts': 2}
    client.post('/api/courses/job1/lessons/1/test', headers=headers, json={'answers': [wrong]})
    assert client.get('/api/courses/job1/lessons/1/test').json()['score']['best'] == 1
    assert client.get('/api/courses/job1/lessons/9/summary').status_code == 404


def test_students_cannot_plan_and_need_csrf(client_user, env, monkeypatch):
    client, headers = client_user
    seed_job(env)
    assert client.get('/api/admin/courses/job1/lessons').status_code == 403
    assert client.put('/api/admin/courses/job1/lessons', headers=headers, json=PLAN).status_code == 403
    summary.save_plan(env / 'job1', builder.validate_plan(PLAN, 12))
    summary._write_json(summary.lesson_path(env / 'job1', 1),
                        {'lesson': {**PLAN['lessons'][0], 'offset': 1}, 'summary': SUMMARY,
                         'test': {'questions': [{'q': 'Q', 'ko': '', 'rom': '', 'choices': [{'text': 'a', 'rom': ''}, {'text': 'b', 'rom': ''}],
                                                 'answer': 1, 'explain': 'E'}]}})
    assert client.post('/api/courses/job1/lessons/1/test', json={'answers': [1]}).status_code == 403
    graded = client.post('/api/courses/job1/lessons/1/test', headers=headers, json={'answers': [1]})
    assert graded.status_code == 200 and graded.json()['score'] == 1
