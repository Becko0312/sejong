# Sejong PDF Studio

First application in the planned PDF converter → web builder → Korean voice tutor workflow.

## Run locally

Python 3.12 recommended:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. Upload an unencrypted PDF up to 1 GB. A single background worker saves progress in SQLite and writes a checkpoint for each page. Restarting the server requeues interrupted conversions; completed pages are reused. Use one server process (no `--workers` or live reload during conversion).

## What conversion means

Every PDF page becomes a PNG at 108 DPI inside an HTML page. This preserves typography, Korean/Mongolian characters, illustrations and layout, including scanned PDFs. Embedded text is extracted separately and escaped in HTML. This is **image-backed HTML**, not a reconstructed semantic or editable document. Pages with no embedded text have `needs_ocr: true`. OCR, reading-order reconstruction, audio extraction and lesson segmentation are not implemented yet. Tiny or incomplete embedded text can still require manual review even if the flag is false.

The downloadable ZIP contains `index.html`, `page-N.html`, `page-N.png`, per-page checkpoints and `manifest.json`. Open the index locally or serve the extracted directory as static files. The original PDF and generated textbook content stay in ignored `data/` and are not committed.

## Builder handoff (schema 1.0)

`GET /api/jobs` lists jobs; `POST /api/jobs` accepts multipart `file`; `GET /api/jobs/{id}` exposes status and progress. `POST /api/jobs/{id}/retry` resumes failed work. `GET /api/jobs/{id}/download` returns the portable package. `GET /books/{id}/manifest.json` provides the future builder's import contract:

```json
{
  "schema_version": "1.0",
  "title": "source",
  "page_count": 1,
  "languages": ["ko", "mn"],
  "conversion": "page-images-with-extracted-text",
  "ocr_required_pages": 1,
  "pages": [{"number": 1, "html": "page-1.html", "image": "page-1.png", "text": "", "needs_ocr": true, "width": 532.9, "height": 720}]
}
```

Asset paths are relative to the manifest. Languages are the intended Korean/Mongolian course languages, not automatic detection. The future builder should validate schema version, restrict assets to the package directory, treat all document text as untrusted content, and require OCR/review before grounding a tutor in scanned pages. There is no running builder or automatic downstream delivery yet.

## Deployment boundary

This is a working **single-user local MVP**, not a public multi-tenant SaaS. Do not expose it publicly as-is: it has no authentication, billing, tenant isolation, quotas or request rate limits. Production needs isolated PDF workers with CPU/memory/time limits, object storage, a durable external queue, upload quotas and authentication. A 1 GB upload cap does not bound PDF rendering cost. Keep the service bound to loopback until those are added. Failed uploads are cleaned up; stored jobs currently require manual cleanup. Large uploads should eventually use resumable multipart object-storage uploads.

## Validation

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Tests cover text escaping, scanned-page detection, page checkpoint reuse and missing-artifact repair, invalid uploads, background conversion, the manifest and downloadable ZIP.

## Next stages

1. Add Korean/Mongolian OCR, reading-order quality checks and correction UI.
2. Harden converter for multi-user hosting and authenticated builder import.
3. Build the course editor around reviewed text and stable page references.
4. Add live voice tutoring grounded in approved lesson content.
