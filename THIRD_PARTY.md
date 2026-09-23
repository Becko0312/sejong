# Conversion components

- **Poppler** (`pdfinfo`, `pdftoppm`, `pdftotext`): GPL-2.0-or-later; see the exact packaged copyrights under `/usr/share/doc/poppler-utils/copyright` and `/usr/share/doc/libpoppler126/copyright` in the worker image. Upstream: https://poppler.freedesktop.org/ . Debian source package: https://sources.debian.org/src/poppler/ .
- **Tesseract OCR**: Apache-2.0. Upstream license/source: https://github.com/tesseract-ocr/tesseract . Language data sources and licenses: https://github.com/tesseract-ocr/tessdata_fast . Packaged notices are under `/usr/share/doc/tesseract-ocr*/copyright`.
- **FastAPI**, **Uvicorn** and their Python dependencies: see installed distribution metadata and their upstream license files. Runtime versions are in `requirements.lock`.
- **Caddy**: Apache-2.0; https://github.com/caddyserver/caddy .

The images are built using official Python/Debian and Caddy images and Debian packages. Retain upstream license notices. If distributing binary container images, provide the corresponding source and license materials required by their exact packaged versions; published images include package versions and links to exact Debian source versions in `/usr/share/sejong/debian-sources.tsv`, along with packaged license notices under `/usr/share/doc`. Python source distributions are available from PyPI for the versions in `requirements.lock`; the application source is this repository.

Considered but not included: **pdf2htmlEX**, GPL-3.0-or-later, https://github.com/pdf2htmlEX/pdf2htmlEX . It offers native positioned HTML but is not necessary for this first pipeline. Poppler provides the rendering/text primitives and Tesseract addresses the user's scanned textbook. **PyMuPDF is no longer a runtime dependency**.
