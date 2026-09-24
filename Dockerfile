FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SEJONG_DATA=/data
WORKDIR /app
RUN groupadd --gid 10001 studio && useradd --uid 10001 --gid studio --create-home studio \
    && mkdir /data && chown studio:studio /data
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
RUN mkdir -p /usr/share/sejong && dpkg-query -W -f='${binary:Package}\t${source:Package}\t${source:Version}\n' > /usr/share/sejong/debian-packages.tsv
RUN python -c "import pathlib, urllib.parse; p=pathlib.Path('/usr/share/sejong'); rows=[l.split('\\t') for l in (p/'debian-packages.tsv').read_text().splitlines()]; (p/'debian-sources.tsv').write_text(''.join(pkg+'\\t'+'https://snapshot.debian.org/package/'+urllib.parse.quote(src,safe='')+'/'+urllib.parse.quote(ver,safe='')+'/\\n' for pkg,src,ver in rows))"
USER studio

FROM base AS api
COPY app ./app
COPY THIRD_PARTY.md /usr/share/sejong/THIRD_PARTY.md
EXPOSE 8000
CMD ["uvicorn", "app.entrypoint:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]

FROM base AS worker
USER root
RUN apt-get update && apt-get install --no-install-recommends -y \
    libseccomp2 poppler-utils tesseract-ocr tesseract-ocr-eng tesseract-ocr-kor tesseract-ocr-mon \
    && rm -rf /var/lib/apt/lists/*
RUN dpkg-query -W -f='${binary:Package}\t${source:Package}\t${source:Version}\n' > /usr/share/sejong/debian-packages.tsv
COPY app ./app
COPY THIRD_PARTY.md /usr/share/sejong/THIRD_PARTY.md
RUN python -c "import pathlib, urllib.parse; p=pathlib.Path('/usr/share/sejong'); rows=[l.split('\\t') for l in (p/'debian-packages.tsv').read_text().splitlines()]; (p/'debian-sources.tsv').write_text(''.join(pkg+'\\t'+'https://snapshot.debian.org/package/'+urllib.parse.quote(src,safe='')+'/'+urllib.parse.quote(ver,safe='')+'/\\n' for pkg,src,ver in rows))"
USER studio
ENV OMP_THREAD_LIMIT=1
CMD ["python", "-m", "app.worker"]

FROM worker AS cloud-worker
CMD ["python", "-m", "app.cloud_job"]

# Combined platform image: API + conversion worker in one container (Azure Container Apps).
# Runs as root so the mounted Azure Files volume (/data) is writable regardless of its
# SMB uid/gid; the conversion still runs in a resource-limited subprocess.
FROM worker AS app
USER root
COPY deploy/start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh
EXPOSE 8000
CMD ["/usr/local/bin/start.sh"]
