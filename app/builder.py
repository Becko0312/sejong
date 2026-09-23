"""Book2Course stage 2 — build a lesson-structured course from a conversion.

Language books number their lessons: Korean "1과"/"제1과"/"1단원", Japanese
"第1課"/"レッスン1", Chinese "第1课", or English "Lesson/Unit/Chapter 1". This
module reads the converted pages' text and groups them into lessons, so the
reader can present a real course (units → pages) instead of a flat page list.
It is deterministic and free; the AI tutor is a separate stage. When no lesson
markers are found (many PDFs), the course simply has no lessons and the reader
falls back to a page list.
"""
import re

# Each pattern captures the lesson number in group 1, tagged with a language.
LESSON_MARKERS = [
    (re.compile(r'제\s*(\d{1,3})\s*과'), 'korean'),
    (re.compile(r'(\d{1,3})\s*과(?=\s|$)'), 'korean'),
    (re.compile(r'(\d{1,3})\s*단원'), 'korean'),
    (re.compile(r'第\s*(\d{1,3})\s*課'), 'japanese'),
    (re.compile(r'(\d{1,3})\s*課(?=\s|$)'), 'japanese'),
    (re.compile(r'レッスン\s*(\d{1,3})'), 'japanese'),
    (re.compile(r'第\s*(\d{1,3})\s*课'), 'chinese'),
    (re.compile(r'(?:Lesson|Unit|Chapter)\s+(\d{1,3})', re.IGNORECASE), 'english'),
]
HEAD_CHARS = 80          # a lesson heading sits near the top of the page's text
MAX_TITLE = 60


def _markers(text):
    """All (number, start, end, language) lesson markers found in a page's text."""
    found = []
    for pattern, language in LESSON_MARKERS:
        for match in pattern.finditer(text):
            found.append((int(match.group(1)), match.start(), match.end(), language))
    return found


def _clean_title(segment):
    """A lesson title from the text after a marker: stop at the printed page number."""
    seg = segment.strip(' .·-–—:|\t')
    seg = re.split(r'\s+\d{1,4}(?:\s|$)', seg, maxsplit=1)[0]
    seg = re.split(r'[\n\r|]', seg, maxsplit=1)[0].strip()
    return seg[:MAX_TITLE] or None


def _toc_titles(pages):
    """Clean lesson titles read from the contents page (the densest marker page)."""
    best, best_count = None, 0
    for page in pages:
        found = _markers(page.get('text') or '')
        if len({n for n, *_ in found}) >= 3 and len(found) > best_count:
            best, best_count = page, len(found)
    if not best:
        return {}, None
    text = best.get('text') or ''
    ordered = sorted(_markers(text), key=lambda m: m[1])
    titles, language = {}, None
    for i, (number, _, end, lang) in enumerate(ordered):
        stop = ordered[i + 1][1] if i + 1 < len(ordered) else len(text)
        title = _clean_title(text[end:stop])
        if number not in titles and title:
            titles[number] = title
            language = language or lang
    return titles, language


def detect_lessons(pages):
    """Group pages into lessons. `pages` are dicts with 'number' and 'text'.

    Returns (lessons, language). Titles come from the contents page when present;
    a page opens a lesson when it carries exactly one lesson number and that
    number is the next expected one near the top (contents/review pages that list
    several numbers are skipped).
    """
    if not pages:
        return [], None
    toc_titles, language = _toc_titles(pages)
    lessons, expected = [], 1
    for page in pages:
        text = page.get('text') or ''
        found = _markers(text)
        numbers = {n for n, *_ in found}
        if len(numbers) != 1:
            continue
        head = [m for m in found if m[1] < HEAD_CHARS and m[0] == expected]
        if not head:
            continue
        number, _, marker_end, lang = head[0]
        language = language or lang
        title = toc_titles.get(number) or _clean_title(text[marker_end:marker_end + 120])
        lessons.append({'index': number, 'title': title, 'start_page': page['number']})
        expected += 1
    last = pages[-1]['number']
    for i, lesson in enumerate(lessons):
        lesson['end_page'] = lessons[i + 1]['start_page'] - 1 if i + 1 < len(lessons) else last
    return lessons, language


def build_course(manifest):
    """A reader-ready course structure derived from a conversion manifest."""
    pages = manifest.get('pages', [])
    lessons, language = detect_lessons(pages)
    by_start = {lesson['start_page']: lesson['index'] for lesson in lessons}
    current = None
    light_pages = []
    for page in pages:
        number = page.get('number')
        if number in by_start:
            current = by_start[number]
        light_pages.append({
            'number': number,
            'text': page.get('text', ''),
            'text_source': page.get('text_source', 'none'),
            'needs_ocr': page.get('needs_ocr', False),
            'needs_review': page.get('needs_review', False),
            'lesson': current if lessons else None,
        })
    return {
        'title': manifest.get('title', 'Course'),
        'page_count': manifest.get('page_count', len(pages)),
        'language': language,
        'lessons': lessons,
        'front_pages': [p['number'] for p in light_pages if p['lesson'] is None] if lessons else [],
        'pages': light_pages,
    }
