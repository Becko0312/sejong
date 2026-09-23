"""One bounded job subprocess. Deployed inside the offline worker container."""
import json
import os
import resource
import sys
import zipfile
from app import store
from app.converter import convert


def run(job_id):
    resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024**2, 1536 * 1024**2))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1200 * 1024**2, 1200 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_CPU, (3600, 3600))
    with store.connect() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    folder = store.DATA / job_id
    try:
        convert(folder / 'source.pdf', folder / 'output',
                lambda done, total: store.update(job_id, done=done, total=total),
                title=row['name'], ocr=bool(row['ocr']), languages=row['languages'])
        with zipfile.ZipFile(folder / 'book.partial', 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted((folder / 'output').iterdir()):
                if path.suffix not in {'.html', '.png', '.json'}:
                    continue
                if path.name.startswith('page-') and path.suffix == '.json':
                    # Checkpoint metadata is private implementation detail.
                    data = json.loads(path.read_text())
                    data.pop('checkpoint_key', None)
                    archive.writestr(path.name, json.dumps(data, ensure_ascii=False))
                else:
                    archive.write(path, path.name)
        (folder / 'book.partial').replace(folder / 'book.zip')
        store.update(job_id, status='completed', error=None)
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else 'Conversion failed. The file may be damaged or exceed resource limits.'
        store.update(job_id, status='failed', error=message[:300])
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(run(sys.argv[1]))
