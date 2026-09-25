# Book2Course

A **"language book → language course" platform**. An administrator uploads a language textbook (Korean, Japanese, …) and it becomes an interactive course; students sign up and learn with a proactive AI tutor. It runs as a single web app (deployable as a container on Azure, Fly, Render, etc.).

**Accounts and roles.** The homepage has one login form.
- **Admin** (seeded from `ADMIN_USERNAME` / `ADMIN_PASSWORD`): sees an **Add course** form — upload a PDF, set the title and from→to languages, and the pipeline converts and builds a course — plus a manage list (publish/hide, retry, delete, open).
- **Client / student** (self sign-up): sees a **catalog** of available courses and studies any of them with the tutor.

**The build pipeline (per course).**
1. **Convert** — PDF → image-backed HTML pages with a searchable text layer (offline Poppler + optional Tesseract OCR). No AI or token billing in this stage.
2. **Build** — pages are grouped into lessons (language-book markers like 과 / 課 / Lesson) into a real course website: landing page, lesson-grouped navigation, page viewer.
3. **Teach** — a proactive AI tutor auto-starts on each lesson and offers Explain / Vocabulary / Quiz / Practice, grounded in the current page. This is the only part that calls a paid model API; without a key the courses still read, the tutor just shows offline. Per-student daily tutor limits (`CLIENT_TUTOR_PER_DAY`) bound the cost of open sign-up.

Access is role-based: study endpoints require sign-in, and clients only reach published courses.

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

## Course builder (stage 2)

Every completed conversion opens as a real course website at `/course/{id}`: a landing page with lesson cards, a lesson-grouped collapsible table of contents, a page viewer with searchable text, and a docked tutor panel. The "Open as course" action appears on each completed job.

Lessons are detected from the pages' text by `app/builder.py` using language-book numbering markers — Korean `과`/`단원`, Japanese `課`/`レッスン`, Chinese `课`, English `Lesson`/`Unit`/`Chapter`. Titles are read from the contents page; a page opens a lesson when it carries exactly one lesson number matching the next expected one near the top (contents and review pages that list several numbers are skipped). Detection is deterministic and free — **no AI is used to build the structure** (the AI is only the tutor). Books with no markers simply present a flat page list. `GET /api/courses/{id}` returns the structure (owner-scoped): `title`, `language`, `lessons` (index, title, start/end page), `front_pages`, and light per-page metadata with the heavy word coordinates stripped. The reader is a static app; ownership is enforced by that endpoint and by `/books/{id}/...`.

## AI tutor — proactive teacher (stage 3)

The tutor is a **proactive teacher**, not just a Q&A bot. Opening a lesson auto-starts it teaching that lesson (once per lesson), and one-tap actions run **Explain**, **Vocabulary**, **Quiz me**, and **Practice** on the current page. Every turn is grounded in the page the student is viewing: the page's text is sent to the model as reference **data** (delimited, and explicitly not treated as instructions, because it may be imperfect OCR from an untrusted PDF), along with the current lesson.

It is **language-general**: it teaches the book's target language (Korean, Japanese, …) and explains in the student's own language — mirroring what they write, else the translation language printed on the page, else simple English — always with native-script examples plus romanization. `POST /api/tutor/{id}` takes `{question, page, history, mode}`, where `mode` is one of `intro`/`explain`/`vocab`/`quiz`/`practice` (a typed `question` needs no mode).

## Accounts, roles and access

Auth is username/password with PBKDF2-hashed passwords and server-side sessions (`app/auth.py`). `GET /api/me` reports the current user; `POST /api/register` self-registers a client and logs in; `POST /api/login` / `POST /api/logout` manage the session. The admin account is seeded from `ADMIN_USERNAME` / `ADMIN_PASSWORD` on startup — set a strong password before deploying.

Courses are a global catalog owned by the admin. Admin-only: `POST /api/jobs` (upload + convert, with `title`/`source_lang`/`target_lang`/`description`), `PATCH /api/jobs/{id}` (publish/hide + edit), `POST /api/jobs/{id}/retry`, `DELETE /api/jobs/{id}`, `GET /api/admin/courses` (all courses). Any signed-in user: `GET /api/catalog` (published courses) and, for a published course (or any course if admin), `GET /api/courses/{id}`, `/books/{id}/…`, and `POST /api/tutor/{id}`. Mutations require the session CSRF token; the tutor is rate limited per student per day (`CLIENT_TUTOR_PER_DAY`, default 60); admins are exempt.

The earlier per-session share links were removed in favour of this catalog. The scale-to-zero Azure Jobs variant (`app/cloud_api.py`) predates the platform and is not wired to auth/catalog; the deployable app is this container (`app.main`).

- **Optional and opt-in.** With no provider key configured, `/api/session` reports `tutor.enabled: false`, the panel shows "offline", and the course still fully works. The converter never requires a key.
- **Provider (`TUTOR_PROVIDER`).** `gemini` (default when `GEMINI_API_KEY` is set) calls Google's Generative Language REST API — model `GEMINI_MODEL` (default `gemini-2.5-flash`, with model thinking disabled for speed/cost). `anthropic` (needs `ANTHROPIC_API_KEY`) uses the Claude Messages API — model `TUTOR_MODEL` (default `claude-opus-5`) and `TUTOR_EFFORT` (default `low`). Both honour `TUTOR_MAX_TOKENS`. This stage is billed per use by the provider; the free converter budget does not cover it.
- **Voice.** Speech *input* uses the browser's built-in Web Speech API (no extra service); availability depends on the browser and Mongolian recognition varies. Read-aloud is **server-side** when `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION` are set: the reader splits each reply by script and `POST /api/tts` synthesizes every segment with Azure neural voices (Mongolian `mn-MN-YesuiNeural` — overridable via `TTS_VOICE_MN`; Korean, Japanese, Chinese and English have their own voices), so mn-MN speech works in every browser even though none ships a Mongolian voice. Without a key the endpoint returns `503` and the reader falls back to the browser's own voices. Results are cached in memory and the Azure Speech F0 free tier covers 500k neural characters/month. Parenthesized romanizations such as `(itta, eoptta)` are on-screen reading aids and are skipped when speaking; parentheses holding the book's own scripts (Hangul, Cyrillic, kana, Han) are still read.

## Interactive pages (optional AI stage)

Extraction stays free; re-orchestration is a separate, admin-gated AI stage (`app/enrich.py`). For each page with extracted text, Gemini reorganizes the raw OCR into clean study units — one Korean sentence per entry with its printed romanization and translation — stored as `output/interactive-<n>.json`. The reader then offers a **📄 Book / ✨ Interactive** switch: the interactive view lists those sentences with a 🔊 listen button on each (synthesized by `/api/tts`), while the scanned page remains one tap away.

Cost is admin-controlled end to end: the admin Courses page shows, per completed course, how many pages have text and a conservative token + USD estimate (chars/2 + system prompt per page in, ~220 tokens/page out, Gemini Flash list pricing) **before** anything is spent; "Build interactive pages" re-states the estimate in a confirmation and then builds in a background thread with live progress (`building n/N`). Students only ever see pages an admin has built — their switch appears once pages exist, and unbuilt pages return a friendly `409`. Admins may also open the interactive view per page on demand. The model receives page text strictly as delimited data, its JSON is validated field-by-field, and with no `GEMINI_API_KEY` the whole stage disappears from the UI.
- **Endpoint.** `POST /api/tutor/{id}` with `{question, page, history}` and the session CSRF header; owner-scoped, and it degrades to a friendly `503` on any model error.
- **Verified** end to end on the real scanned Sejong textbook: Korean/Mongolian OCR → course reader → a page-grounded `gemini-2.5-flash` tutor teaching in Mongolian with romanized Korean.

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
