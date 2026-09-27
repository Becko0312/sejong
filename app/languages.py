"""The languages Book2Course courses may use — one list shared by upload, OCR and the tutor.

To add a language: add a row here and install its Tesseract pack in the Dockerfile
(`tesseract-ocr-<ocr code>` with underscores as dashes, e.g. chi_sim → chi-sim).
"""

# name (stored on the course) → OCR pack, native name, and a hint that helps Gemini Live
# tell the spoken language apart from look-alikes.
LANGUAGES = {
    'Mongolian': {'ocr': 'mon', 'native': 'Монгол', 'hint': 'Mongolian (Cyrillic; e.g. "Сайн байна уу", "Би ойлгохгүй байна")'},
    'Korean': {'ocr': 'kor', 'native': '한국어', 'hint': 'Korean (e.g. "안녕하세요", "감사합니다")'},
    'English': {'ocr': 'eng', 'native': 'English', 'hint': 'English'},
    'Japanese': {'ocr': 'jpn', 'native': '日本語', 'hint': 'Japanese (e.g. "こんにちは", "ありがとう")'},
    'Chinese': {'ocr': 'chi_sim', 'native': '中文', 'hint': 'Mandarin Chinese (e.g. "你好", "谢谢")'},
    'Russian': {'ocr': 'rus', 'native': 'Русский', 'hint': 'Russian (Cyrillic; e.g. "Здравствуйте", "Спасибо")'},
}
OCR_CODES = {v['ocr']: k for k, v in LANGUAGES.items()}
# Our students are Mongolian and every course also allows English, so a live session always
# accepts these two besides the course's own language pair.
STUDENT_LANGUAGES = ('Mongolian', 'English')


def normalize(value):
    """Map 'korean', 'KOREAN', 'kor' or '한국어' to 'Korean'; None if it is not a supported language."""
    value = (value or '').strip()
    if not value:
        return None
    for name, info in LANGUAGES.items():
        if value.lower() in (name.lower(), info['ocr'], info['native'].lower()):
            return name
    return None


def ocr_for(*names):
    """Tesseract language string for a course pair, always with English (textbooks mix it in)."""
    codes = []
    for name in (*names, 'English'):
        code = LANGUAGES.get(name, {}).get('ocr')
        if code and code not in codes:
            codes.append(code)
    return '+'.join(codes)


def for_course(source_lang, target_lang, ocr_languages=''):
    """Every language a student may speak in this course, most specific first.

    Older courses without a language pair fall back to the OCR languages they were built with."""
    names = [normalize(source_lang), normalize(target_lang)]
    if not any(names):
        names = [OCR_CODES.get(code) for code in (ocr_languages or '').split('+')]
    result = []
    for name in (*names, *STUDENT_LANGUAGES):
        if name and name not in result:
            result.append(name)
    return result
