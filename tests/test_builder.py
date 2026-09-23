"""Lesson detection and course structure (deterministic, no AI)."""
from app import builder


def pages(*texts):
    return [{'number': i + 1, 'text': t, 'text_source': 'ocr', 'needs_review': True, 'needs_ocr': False}
            for i, t in enumerate(texts)]


def test_korean_lessons_skip_toc_and_group_pages():
    manifest = {'title': 'Sejong Korean 1', 'page_count': 7, 'pages': pages(
        '표지 Sejong Korean 1',                                                                    # 1 cover
        '목차 1과 저는 한국 사람이에요 21 2과 회사원이 아니에요 33 3과 저도 드라마를 좋아합니다 45',   # 2 TOC -> titles, skip
        '1과 저는 한국 사람이에요 새 단어 어휘 문법',                                                 # 3 lesson 1 start
        '연습 문제 계속',                                                                          # 4 lesson 1
        '2과 회사원이 아니에요 새 단어',                                                             # 5 lesson 2 start
        '3과 저도 드라마를 좋아합니다 표현',                                                          # 6 lesson 3 start
        '마무리',                                                                                 # 7 lesson 3
    )}
    course = builder.build_course(manifest)
    assert course['language'] == 'korean'
    assert [(l['index'], l['title'], l['start_page'], l['end_page']) for l in course['lessons']] == [
        (1, '저는 한국 사람이에요', 3, 4), (2, '회사원이 아니에요', 5, 5), (3, '저도 드라마를 좋아합니다', 6, 7)]
    assert course['front_pages'] == [1, 2]
    assert [p['lesson'] for p in course['pages']] == [None, None, 1, 1, 2, 3, 3]


def test_japanese_and_english_markers():
    jp = builder.detect_lessons(pages('第1課 あいさつ 15', 'つづき', '第2課 かぞく'))
    assert jp[1] == 'japanese'
    assert [l['index'] for l in jp[0]] == [1, 2]
    en = builder.detect_lessons(pages('Lesson 1 Greetings', 'more', 'Unit 2 Family'))
    assert en[1] == 'english'
    assert [l['start_page'] for l in en[0]] == [1, 3]


def test_no_markers_means_no_lessons():
    course = builder.build_course({'title': 'Scan', 'page_count': 2, 'pages': pages('random text', 'more text')})
    assert course['lessons'] == []
    assert course['front_pages'] == []
    assert all(p['lesson'] is None for p in course['pages'])


def test_out_of_order_numbers_do_not_start_lessons():
    # A stray "5과" before lesson 1 must not open a lesson; only the expected next number does.
    lessons, _ = builder.detect_lessons(pages('참고 5과 관련 내용', '1과 인사 hello', '2과 가족'))
    assert [l['index'] for l in lessons] == [1, 2]
    assert lessons[0]['start_page'] == 2
