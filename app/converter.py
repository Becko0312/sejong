"""Page-checkpointed conversion; source documents are data, never instructions."""
import html
import json
from pathlib import Path
import pymupdf


def convert(source: Path, output: Path, progress=lambda done, total: None, title=None):
    output.mkdir(parents=True, exist_ok=True)
    pages = []
    with pymupdf.open(source) as document:
        if document.needs_pass:
            raise ValueError('Password-protected PDFs are not supported.')
        total = len(document)
        for index, page in enumerate(document):
            number = index + 1
            checkpoint = output / f'page-{number}.json'
            if checkpoint.exists() and (output / f'page-{number}.png').exists() and (output / f'page-{number}.html').exists():
                item = json.loads(checkpoint.read_text())
            else:
                text = page.get_text(sort=True).strip()
                image = f'page-{number}.png'
                page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False).save(output / image)
                item = {'number': number, 'html': f'page-{number}.html', 'image': image,
                        'text': text, 'needs_ocr': not bool(text),
                        'width': page.rect.width, 'height': page.rect.height}
                (output / item['html']).write_text(
                    '<!doctype html><html lang="ko"><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1">'
                    f'<title>Page {number}</title><style>body{{margin:0;background:#eee}}'
                    'img{display:block;width:100%;max-width:1000px;height:auto;margin:auto}'
                    'details{max-width:900px;margin:1rem auto;padding:1rem;background:white}'
                    'pre{white-space:pre-wrap}</style>'
                    f'<img src="{image}" alt="Textbook page {number}">'
                    '<details><summary>Extracted text</summary><pre>'
                    + html.escape(text or 'This page needs OCR. Visual content is preserved.')
                    + '</pre></details></html>', encoding='utf-8')
                checkpoint.write_text(json.dumps(item, ensure_ascii=False), encoding='utf-8')
            pages.append(item)
            progress(number, total)
    manifest = {'schema_version': '1.0', 'title': title or source.stem, 'page_count': len(pages),
                'languages': ['ko', 'mn'], 'conversion': 'page-images-with-extracted-text',
                'ocr_required_pages': sum(p['needs_ocr'] for p in pages), 'pages': pages}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    links = ''.join(f'<a href="page-{p["number"]}.html">Page {p["number"]}</a> ' for p in pages)
    (output / 'index.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Converted textbook</title><style>body{font:18px system-ui;max-width:1000px;margin:40px auto;padding:20px}'
        'nav{display:flex;flex-wrap:wrap;gap:12px}a{padding:10px;background:#eef5f2;color:#174b3d}</style>'
        '<h1>Converted textbook</h1><p>Choose a page. Images preserve the source layout; '
        'pages without text need OCR before AI tutoring.</p><nav>' + links + '</nav></html>', encoding='utf-8')
    return manifest
