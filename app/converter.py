"""Offline Poppler + optional Tesseract conversion. No model APIs or tokens."""
import csv
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

ENGINE = 'poppler-tesseract-v2'
LANGUAGES = {'eng', 'kor', 'mon'}


def command(args, timeout=90):
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, check=True,
                                env={**os.environ, 'OMP_THREAD_LIMIT': '1', 'LC_ALL': 'C.UTF-8'})
        return result.stdout.decode('utf-8', errors='replace')
    except subprocess.TimeoutExpired as exc:
        raise ValueError('A page exceeded the processing time limit.') from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError('The PDF could not be processed by the conversion tool.') from exc


def inspect_pdf(source):
    info = command(['pdfinfo', str(source)], timeout=30)
    match = re.search(r'^Pages:\s+(\d+)', info, re.M)
    if not match or re.search(r'^Encrypted:\s+yes', info, re.M):
        raise ValueError('Upload an unencrypted PDF with at least one page.')
    total = int(match[1])
    if not 1 <= total <= int(os.getenv('MAX_PAGES', '500')):
        raise ValueError('The PDF exceeds the page limit.')
    return total


def atomic_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def embedded_words(source, number):
    xml = command(['pdftotext', '-f', str(number), '-l', str(number), '-bbox', str(source), '-'])
    root = ET.fromstring(xml)
    ns = {'h': 'http://www.w3.org/1999/xhtml'}
    page = root.find('.//h:page', ns)
    if page is None:
        raise ValueError('Unable to read page dimensions.')
    width, height = float(page.attrib['width']), float(page.attrib['height'])
    if not 0 < width <= 14400 or not 0 < height <= 14400:
        raise ValueError('Page dimensions exceed the rendering limit.')
    words = []
    for word in page.findall('h:word', ns):
        a = word.attrib
        words.append({'text': ''.join(word.itertext()), 'x': float(a['xMin']) / width,
                      'y': float(a['yMin']) / height,
                      'w': (float(a['xMax']) - float(a['xMin'])) / width,
                      'h': (float(a['yMax']) - float(a['yMin'])) / height})
    return width, height, words


def ocr_words(image, languages, width, height):
    # pdftoppm scales the longest side to 1800 pixels, preserving aspect ratio.
    scale = 1800 / max(width, height)
    tsv = command(['tesseract', str(image), 'stdout', '-l', languages, '--psm', '3', 'tsv'], timeout=120)
    words = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter='\t'):
        if row.get('level') != '5' or not row.get('text', '').strip():
            continue
        words.append({'text': row['text'].strip(), 'x': int(row['left']) / (width * scale),
                      'y': int(row['top']) / (height * scale),
                      'w': int(row['width']) / (width * scale),
                      'h': int(row['height']) / (height * scale), 'confidence': float(row['conf'])})
    return words


def write_page(output, item, total):
    n = item['number']
    overlay = ''.join('<span style="left:{:.5f}%;top:{:.5f}%;width:{:.5f}%;height:{:.5f}%">{}</span>'.format(
        w['x'] * 100, w['y'] * 100, w['w'] * 100, w['h'] * 100, html.escape(w['text'])) for w in item['words'])
    nav = f'<a href="index.html">All pages</a> · Page {n} of {total}'
    if n > 1:
        nav += f' · <a href="page-{n-1}.html">Previous</a>'
    if n < total:
        nav += f' · <a href="page-{n+1}.html">Next</a>'
    notice = 'OCR text is machine-recognized and needs review.' if item['text_source'] == 'ocr' else (
        'No text detected. Visual page is preserved.' if item['needs_ocr'] else 'Text extracted from the PDF.')
    (output / item['html']).write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>Page {n}</title><style>body{{margin:0;background:#f3f5f0;font:16px system-ui}}'
        'nav,details{max-width:960px;margin:16px auto;padding:16px;background:white}a{color:#24543d}'
        '.page{position:relative;max-width:1000px;margin:auto}.page img{display:block;width:100%}'
        '.layer{position:absolute;inset:0}.layer span{position:absolute;color:transparent;white-space:pre;overflow:hidden;line-height:1}'
        '.layer span::selection{background:#3377ff66}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
        f'<nav>{nav}</nav><div class="page"><img src="{item["image"]}" alt="Document page {n}">'
        f'<div class="layer" aria-hidden="true">{overlay}</div></div><details open><summary>Searchable text</summary><p>{notice}</p><pre>'
        + html.escape(item['text'] or 'No text available.') + '</pre></details></html>', encoding='utf-8')


def convert(source: Path, output: Path, progress=lambda done, total: None, title=None, ocr=False, languages='eng'):
    if not set(languages.split('+')) <= LANGUAGES:
        raise ValueError('Unsupported OCR language.')
    if ocr:
        installed = command(['tesseract', '--list-langs'])
        if not all(lang in installed.splitlines() for lang in languages.split('+')):
            raise ValueError('The selected OCR language packs are not installed.')
    source = source.resolve()
    total = inspect_pdf(source)
    output.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as stream:
        fingerprint = hashlib.file_digest(stream, 'sha256').hexdigest()
    key = f'{ENGINE}:{fingerprint}:{ocr}:{languages}'
    pages = []
    for number in range(1, total + 1):
        checkpoint = output / f'page-{number}.json'
        item = None
        if checkpoint.exists():
            try:
                candidate = json.loads(checkpoint.read_text())
                if candidate.get('checkpoint_key') == key and all((output / f'page-{number}.{ext}').is_file() for ext in ('png', 'html')):
                    item = candidate
            except (ValueError, OSError):
                pass
        if item is None:
            width, height, words = embedded_words(source, number)
            image = f'page-{number}.png'
            command(['pdftoppm', '-f', str(number), '-l', str(number), '-singlefile', '-scale-to', '1800',
                     '-png', str(source), str(output / f'page-{number}')])
            text_source = 'embedded' if words else 'none'
            if not words and ocr:
                words = ocr_words(output / image, languages, width, height)
                text_source = 'ocr'
            text = ' '.join(w['text'] for w in words)
            item = {'number': number, 'html': f'page-{number}.html', 'image': image,
                    'text': text, 'text_source': text_source, 'words': words,
                    'needs_ocr': not bool(text), 'needs_review': text_source == 'ocr' or not bool(text),
                    'width': width, 'height': height, 'checkpoint_key': key}
            write_page(output, item, total)
            atomic_json(checkpoint, item)
        pages.append(item)
        if sum(p.stat().st_size for p in output.iterdir() if p.is_file()) > int(os.getenv('MAX_OUTPUT_MB', '1024')) * 1024 * 1024:
            raise ValueError('The converted output exceeds the storage limit.')
        progress(number, total)
    manifest = {'schema_version': '1.1', 'title': title or source.stem, 'page_count': len(pages),
                'ocr_languages': languages.split('+') if ocr else [], 'engine': ENGINE,
                'conversion': 'page-images-with-text-layer', 'ocr_required_pages': sum(p['needs_ocr'] for p in pages),
                'review_required_pages': sum(p['needs_review'] for p in pages),
                'pages': [{k: v for k, v in p.items() if k != 'checkpoint_key'} for p in pages]}
    atomic_json(output / 'manifest.json', manifest)
    links = ''.join(f'<a href="page-{p["number"]}.html">Page {p["number"]}</a>' for p in pages)
    (output / 'index.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Converted document</title>'
        '<style>body{font:18px system-ui;max-width:1000px;margin:40px auto;padding:20px}nav{display:flex;flex-wrap:wrap;gap:12px}'
        'a{padding:10px;background:#eef5f2;color:#174b3d}</style><h1>' + html.escape(title or source.stem) + '</h1>'
        '<p>Converted offline with Poppler. OCR text, when enabled, is produced by Tesseract and requires review.</p><nav>' + links + '</nav></html>', encoding='utf-8')
    return manifest
