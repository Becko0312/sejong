# Sejong PDF Studio

A self-hosted, open-source-tool-powered PDF-to-HTML converter, with optional OCR. **No AI API, model API key, paid conversion service or token billing.** Azure deployment is prepared but has not been launched.

## Start locally with Docker

```sh
docker compose up --build -d
```

Open http://127.0.0.1:8000. If that port is occupied, use `PORT=8001 docker compose up --build -d` and open http://127.0.0.1:8001. Docker installs the conversion tools inside the worker image; no host Poppler/Tesseract installation is required. Python dependencies are pinned in `requirements.lock`. Conversion works with the worker's network disabled.

- **Poppler:** inspect PDFs, render pages, extract words and bounding boxes.
- **Tesseract:** optional local OCR for pages without embedded text; English, Korean, Mongolian or all three. This uses trained OCR models on CPU, with no cloud tokens or external document transmission.
- **FastAPI + SQLite:** private browser sessions, upload API, durable job queue and progress.
- **Caddy:** automatic public HTTPS when the deployment's `public` profile is enabled.

## Conversion output

Portable ZIP with an HTML index, individual HTML pages, PNG assets, extracted/OCR text and a versioned manifest (`schema_version: 1.1`). Each HTML page preserves layout using a rendered image and adds selectable word positions plus visible searchable text. This is **image-backed HTML with a text layer**, not reconstructed semantic or editable book layout. The export works as static files, without this service. Embedded audio and interactive PDF forms are not converted.

OCR is off by default because it uses more CPU. Enable it for scanned PDFs such as the supplied textbook. OCR recognition and reading order are imperfect, especially for exercises, columns and mixed-language pages. `needs_review: true` identifies OCR pages; review text before using it in lessons or tutoring. No text hallucination or generative processing is used. Sparse embedded text can still require OCR even if the page is not flagged; this version only OCRs pages with no extracted words.

The future builder can import the ZIP and read `manifest.json`. It contains `engine`, `ocr_languages`, `page_count`, `ocr_required_pages`, `review_required_pages` and `pages`. Each page has relative `html` and `image` paths, `text`, `text_source` (`embedded`, `ocr`, `none`), word coordinates normalized to page size, review flags and dimensions. Scanned text is never treated as an instruction. No builder or tutor is implemented in this stage.

## Public-beta protections

Anyone can start a session without signup, but jobs are private to a random HTTP-only session cookie. Every list, status, page, manifest, ZIP, retry and delete endpoint checks ownership. Mutations require a CSRF token; public cookies require HTTPS. Clearing cookies loses access; there are no accounts or cross-device recovery yet. Job URLs are not public sharing links.

Defaults: 200 MiB uploads, 500 pages, 1 GiB output, three retained jobs per session, ten attempts per IP per 24 hours, ten active jobs globally, at most three execution attempts per job, one hour processing per attempt and 24-hour retention from upload. Files are streamed directly to disk with a size limit; the web process does not parse PDFs. Invalid PDF signatures fail immediately; malformed PDF structure fails in the worker. Expiry blocks access immediately; actual deletion follows the worker cleanup cycle. Processing jobs are terminated when deleted or expired. Failed upload attempts count toward the IP quota.

The converter container has no network, runs as an unprivileged user, drops capabilities, has a read-only root filesystem, and is limited to 2 GiB memory / 1.5 CPU / 64 processes. Each job runs in a subprocess with additional memory, CPU, file-size and timeout limits. It stores page checkpoints and retries after restarts. API and worker containers share the data volume; workers are **not isolated per tenant**. This is a low-traffic, single-node beta architecture, not high availability or a hardened document processing service for regulated data. See [Azure launch notes](deploy/azure/README.md) for remaining operational limits.

## Azure

[Deployment package](deploy/azure/README.md): Bicep, cloud-init, HTTPS proxy and a read-only price lookup. No cloud resource has been created. The owner selected **prepare deployment and confirm budget before launch**.

Software/tool usage is free of token fees. Azure VM, disk, IP, bandwidth and operations cost money. In Korea Central, the captured September 23, 2026 retail prices imply roughly **US$42.61/month base** at 730 VM/IP hours: Linux B2als v2 $34.164 + E6 64 GiB SSD $4.80 + Standard public IPv4 $3.65. Excludes disk operations, egress, taxes, optional services and account-specific discounts. See `deploy/azure/cost-estimate.json` and refresh the estimate before launch. Budgets are alerts, not hard cost caps.

## API

1. `GET /api/session` sets the session cookie and returns `csrf` plus upload limits.
2. `POST /api/jobs?name=book.pdf&ocr=true&languages=kor%2Bmon%2Beng` sends **raw PDF bytes**, `Content-Type: application/pdf` and `X-CSRF-Token`. This replaces the earlier multipart API to enforce size limits before parser buffering.
3. `GET /api/jobs`, `GET /api/jobs/{id}`: private state and progress.
4. `GET /api/jobs/{id}/download`: ZIP; `GET /books/{id}/manifest.json`: builder import metadata.
5. `POST /api/jobs/{id}/retry`: bounded retry of a failed job; `DELETE /api/jobs/{id}`: revoke access and schedule physical deletion. Both need the CSRF header.

A builder will need a deliberate authenticated transfer integration; it cannot simply fetch another visitor's private URL. For now, use exported ZIPs.

## Development and verification

Python 3.12, host `poppler-utils`, and optional `tesseract-ocr` language packs are needed for development outside Docker.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
# Separate terminal, same SEJONG_DATA:
.venv/bin/python -m app.worker
```

Tests cover cross-session authorization, CSRF, invalid uploads, quotas, expiry/deletion, page limits, retry caps, text escaping, checkpoint reuse and repair, real Poppler processing and ZIP integrity. Real container OCR was additionally checked against a scanned Korean/Mongolian textbook page, including both scripts, review flags and denied cross-session ZIP access. The supplied PDF and all test results remain local/ignored.

## Existing local data

The original 280-page visual export in the repository's ignored `data/` is preserved. Docker uses a new named volume. Legacy rows migrated from the first prototype are deliberately hidden from anonymous visitors and excluded from automatic cleanup. Do not publish the original textbook or copy it into the public deployment as a default shared sample.

## Source licenses

See [THIRD_PARTY.md](THIRD_PARTY.md). Poppler and Tesseract are existing open-source tools; this service invokes their command-line interfaces. PyMuPDF and its AGPL/commercial dependency have been removed from the runtime. No container image is published by this task.
