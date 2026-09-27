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
    seg = segment.strip(' .·ㆍ-–—:|\t')
    seg = re.split(r'\s+\d{1,4}(?:\s|$)', seg, maxsplit=1)[0]      # printed page number
    seg = re.split(r'[\n\r|]', seg, maxsplit=1)[0]
    seg = re.split(r'[Ѐ-ӿ]', seg, maxsplit=1)[0]        # a Cyrillic (e.g. Mongolian) translation
    return seg.strip(' .·ㆍ-–—:|\t')[:MAX_TITLE] or None


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
        title = toc_titles.get(number) or _clean_title(text[marker_end:marker_end + 70])
        lessons.append({'index': number, 'title': title, 'start_page': page['number']})
        expected += 1
    last = pages[-1]['number']
    for i, lesson in enumerate(lessons):
        lesson['end_page'] = lessons[i + 1]['start_page'] - 1 if i + 1 < len(lessons) else last
    return lessons, language


def build_course(manifest, plan=None):
    """A reader-ready course structure derived from a conversion manifest.

    An admin-saved lesson plan (see validate_plan) replaces the automatic detection."""
    pages = manifest.get('pages', [])
    lessons, language = detect_lessons(pages)
    if plan:
        lessons = plan_lessons(plan)
    light_pages = []
    for page in pages:
        number = page.get('number')
        current = next((l['index'] for l in lessons if l['start_page'] <= number <= l['end_page']), None)
        light_pages.append({
            'number': number,
            'text': page.get('text', ''),
            'text_source': page.get('text_source', 'none'),
            'needs_ocr': page.get('needs_ocr', False),
            'needs_review': page.get('needs_review', False),
            'lesson': current,
        })
    first = lessons[0]['start_page'] if lessons else None
    return {
        'title': manifest.get('title', 'Course'),
        'page_count': manifest.get('page_count', len(pages)),
        'language': language,
        'lessons': lessons,
        'front_pages': [p['number'] for p in light_pages if p['lesson'] is None and p['number'] < first] if lessons else [],
        'other_pages': [p['number'] for p in light_pages if p['lesson'] is None and p['number'] > first] if lessons else [],
        'pages': light_pages,
    }


# ---- Admin lesson plan: printed page ranges typed from the book's contents page ----
# Printed page numbers (what the contents page says) differ from PDF page numbers by a
# fixed offset (cover and front matter), so the plan stores printed numbers + one offset.
MAX_PLAN_LESSONS = 100
_TOC_PAGE = re.compile(r'\s(\d{1,4})(?=\s|$)')


def _squash(text):
    return re.sub(r'\s+', '', text or '')


_REVIEW_RANGE = re.compile(r'\([^()]*~[^()]*\)')     # "(1과~5과)": a review unit, not a lesson


def _useful_title(title):
    """OCR sometimes leaves only digits or brackets where a garbled title was."""
    return title if title and len(re.sub(r'[\W\d_]', '', title)) >= 2 else None


def _page_entries(text):
    """(lesson number, title, printed page) pairs listed on one page, in reading order."""
    text = _REVIEW_RANGE.sub(' ', text)
    found = sorted(_markers(text), key=lambda m: m[1])
    entries = []
    for i, (number, _, end, _lang) in enumerate(found):
        stop = found[i + 1][1] if i + 1 < len(found) else len(text)
        segment = text[end:stop]
        printed = _TOC_PAGE.search(' ' + segment)
        if printed:
            entries.append((number, _useful_title(_clean_title(segment)), int(printed.group(1))))
    return entries


def _fits(known, number, printed):
    """True when a lesson's printed page keeps the plan in book order."""
    if number in known:
        return False
    before = [p for n, (_, p) in known.items() if n < number]
    after = [p for n, (_, p) in known.items() if n > number]
    return (not before or printed > max(before)) and (not after or printed < min(after))


def _toc_entries(pages):
    """(lesson number, title, printed start page) merged from the contents pages.

    The densest contents page is trusted first; entries from other pages (a
    contents page often continues on the next one, and course overviews list
    lesson numbers too) are only kept when they fit its page order."""
    per_page = [_page_entries(page.get('text') or '') for page in pages]
    per_page = sorted((e for e in per_page if len(e) >= 3), key=len, reverse=True)
    known = {}
    for entries in per_page:
        for number, title, printed in entries:
            if _fits(known, number, printed):
                known[number] = (title, printed)
    return [(n, *known[n]) for n in sorted(known)]


def _guess_offset(pages, entries):
    """PDF page minus printed page, found by locating the first lesson's title page."""
    if not entries:
        return 0
    _, title, printed = entries[0]
    key = _squash(title)[:6]
    # The lesson itself comes after the contents page that lists it (course overviews
    # before the contents page repeat the titles too).
    contents = [p['number'] for p in pages if len(_page_entries(p.get('text') or '')) >= 3]
    after = min(contents, default=0)
    if key:
        for page in pages:
            delta = page['number'] - printed
            if page['number'] > after and -5 <= delta <= 30 and key in _squash(page.get('text'))[:120]:
                return delta
    return 0


def suggest_plan(pages):
    """A best-effort lesson plan from the contents page, for the admin to correct.

    Lessons the OCR garbled are interpolated between their neighbours (textbook
    lessons are usually the same length); lessons after the last readable entry
    are left for the admin to add."""
    entries = _toc_entries(pages)
    offset = _guess_offset(pages, entries)
    known = {n: (t, p) for n, t, p in entries}
    lessons = []
    if entries:
        for number in range(entries[0][0], entries[-1][0] + 1):
            if number in known:
                title, start = known[number]
            else:
                before = max(n for n in known if n < number)
                after = min(n for n in known if n > number)
                span = (known[after][1] - known[before][1]) / (after - before)
                title, start = None, round(known[before][1] + span * (number - before))
            lessons.append({'index': number, 'title': title or f'Lesson {number}', 'start': start})
    last_printed = (pages[-1]['number'] if pages else 0) - offset
    lengths = [b['start'] - a['start'] for a, b in zip(lessons, lessons[1:])]
    typical = sorted(lengths)[len(lengths) // 2] if lengths else 10
    for i, lesson in enumerate(lessons):
        nxt = lessons[i + 1]['start'] - 1 if i + 1 < len(lessons) else lesson['start'] + typical - 1
        lesson['end'] = max(lesson['start'], min(nxt, last_printed))
    return {'offset': offset, 'lessons': lessons}


class PlanError(ValueError):
    pass


def validate_plan(plan, page_count):
    """Normalize an admin-submitted plan; raises PlanError with a readable message."""
    try:
        offset = int(plan.get('offset', 0))
        rows = list(plan.get('lessons') or [])
    except (TypeError, ValueError, AttributeError):
        raise PlanError('The lesson plan is malformed.')
    if not -50 <= offset <= 500:
        raise PlanError('The page offset must be between -50 and 500.')
    if not rows:
        raise PlanError('Add at least one lesson.')
    if len(rows) > MAX_PLAN_LESSONS:
        raise PlanError(f'A course can have at most {MAX_PLAN_LESSONS} lessons.')
    lessons, seen, last_end = [], set(), None
    for row in rows:
        try:
            index, start, end = int(row['index']), int(row['start']), int(row['end'])
        except (TypeError, ValueError, KeyError):
            raise PlanError('Every lesson needs a number, a first page and a last page.')
        label = f'Lesson {index}'
        if index < 1 or index > 999 or index in seen:
            raise PlanError(f'{label}: lesson numbers must be unique and between 1 and 999.')
        if start > end:
            raise PlanError(f'{label}: the first page is after the last page.')
        if start + offset < 1 or end + offset > page_count:
            raise PlanError(f'{label}: pages {start}–{end} fall outside the book '
                            f'(printed pages {1 - offset}–{page_count - offset} with offset {offset}).')
        if last_end is not None and start <= last_end:
            raise PlanError(f'{label}: its pages overlap the previous lesson. List lessons in book order.')
        seen.add(index)
        last_end = end
        title = str(row.get('title') or '').strip()[:MAX_TITLE * 2] or label
        lessons.append({'index': index, 'title': title, 'start': start, 'end': end})
    return {'offset': offset, 'lessons': lessons}


def plan_lessons(plan):
    """A validated plan as reader lessons addressed by PDF page number."""
    offset = plan['offset']
    return [{'index': l['index'], 'title': l['title'], 'start_page': l['start'] + offset,
             'end_page': l['end'] + offset, 'printed_start': l['start'], 'printed_end': l['end']}
            for l in plan['lessons']]
