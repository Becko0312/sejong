# Book2Course

Turn a textbook PDF into a live, AI-tutored course website — in one workflow:

1. **Convert** — PDF → image-backed HTML pages with a searchable text layer (offline Poppler + optional Tesseract OCR). **No AI API or token billing in this stage.**
2. **Open as a course** — any completed conversion becomes a navigable reader (table of contents, page viewer, searchable text) at `/course/{id}`.
3. **Learn with an AI tutor** — each course page carries a voice-capable AI tutor that teaches from *that page's* content.

Stages 1–2 stay completely free and offline. Stage 3 is the only part that calls a paid model API, is fully optional, and is off unless an API key is configured (see [AI tutor](#ai-tutor-stage-3)). Azure Container Apps deployment uses a scale-to-zero API and queue-triggered conversion jobs.

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

The course reader and any future external builder import the same `manifest.json`. It contains `engine`, `ocr_languages`, `page_count`, `ocr_required_pages`, `review_required_pages` and `pages`. Each page has relative `html` and `image` paths, `text`, `text_source` (`embedded`, `ocr`, `none`), word coordinates normalized to page size, review flags and dimensions. Scanned text is never treated as an instruction — neither in exports nor when passed to the tutor.

## Course experience (stage 2)

Every completed conversion opens as a course at `/course/{id}` — a reader with a page-by-page table of contents, the rendered page, its searchable text and a docked tutor panel. The reader is a static app that reads the owner-scoped `/books/{id}/manifest.json` and page images; it never exposes another visitor's private job. Pages flagged `needs_review` are marked in the table of contents. The "Open as course" action appears on each completed job in the workspace.

## AI tutor (stage 3)

The tutor is grounded in the page the student is currently viewing: the page's text is sent to the model as reference **data** (delimited, and explicitly not treated as instructions, because it may be imperfect OCR from an untrusted PDF). It teaches a Mongolian-speaking beginner in simple Mongolian with romanized Korean examples.

- **Optional and opt-in.** With no `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`) configured, `/api/session` reports `tutor.enabled: false`, the panel shows "offline", and the course still fully works. The converter never requires a key.
- **Voice.** Speech input and read-aloud use the browser's built-in Web Speech APIs (no extra service or tokens); availability depends on the browser. Korean recognition is well supported; Mongolian recognition varies.
- **Model and cost.** Defaults to `claude-opus-5`. Override with `TUTOR_MODEL` (e.g. `claude-sonnet-5` or `claude-haiku-4-5` to lower cost), `TUTOR_EFFORT` (default `low`) and `TUTOR_MAX_TOKENS`. This stage is billed per use by Anthropic; the free converter budget does not cover it.
- **Endpoint.** `POST /api/tutor/{id}` with `{question, page, history}` and the session CSRF header; owner-scoped, and it degrades to a friendly `503` on any model error.

## Local public-beta protections

Anyone can start a session without signup, but jobs are private to a random HTTP-only session cookie. Every list, status, page, manifest, ZIP, retry and delete endpoint checks ownership. Mutations require a CSRF token; public cookies require HTTPS. Clearing cookies loses access; there are no accounts or cross-device recovery yet. Job URLs are not public sharing links.

Defaults: 200 MiB uploads, 500 pages, 1 GiB output, three retained jobs per session, ten attempts per IP per 24 hours, ten active jobs globally, at most three execution attempts per job, one hour processing per attempt and 24-hour retention from upload. Files are streamed directly to disk with a size limit; the web process does not parse PDFs. Invalid PDF signatures fail immediately; malformed PDF structure fails in the worker. Expiry blocks access immediately; actual deletion follows the worker cleanup cycle. Processing jobs are terminated when deleted or expired. Failed upload attempts count toward the IP quota.

The converter container has no network, runs as an unprivileged user, drops capabilities, has a read-only root filesystem, and is limited to 2 GiB memory / 1.5 CPU / 64 processes. Each job runs in a subprocess with additional memory, CPU, file-size and timeout limits. It stores page checkpoints and retries after restarts. API and worker containers share the data volume; workers are **not isolated per tenant**. This is a low-traffic, single-node beta architecture, not high availability or a hardened document processing service for regulated data. See [Azure launch notes](deploy/azure/README.md) for remaining operational limits.

## Azure

[Deployment instructions and cost controls](deploy/azure/README.md). The selected deployment is Azure Container Apps Jobs with a **US$25/month target**. Supporting storage, requests and bandwidth are billable; budget alerts are not a hard cap. Cloud defaults restrict conversion starts to ten per rolling day and downloads to 1 GiB/day. No always-on VM is required.

Cloud uploads use `POST /api/uploads` with JSON name/size/ocr/languages, sequential `PUT /api/uploads/{id}/chunks/{index}` requests (4 MiB each), and `POST /api/uploads/{id}/complete`. All mutations use the session CSRF token. The frontend selects this protocol from `/api/session`. Export and ownership APIs match the local version. Cloud data uses private Azure Blob Storage and Storage Queue; it does not use local SQLite. Cloud conversion timeout is 30 minutes, with a 35-minute platform limit. Cloud expiry revokes access at 24 hours; asynchronous storage lifecycle cleanup follows.

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

See [THIRD_PARTY.md](THIRD_PARTY.md). Poppler and Tesseract are existing open-source tools; this service invokes their command-line interfaces. PyMuPDF and its AGPL/commercial dependency have been removed from the runtime. Public deployment images contain code and open-source tools only, never uploaded documents.
