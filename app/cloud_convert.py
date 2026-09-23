"""Credential-free child process: convert one local PDF, then exit."""
import ctypes
import errno
import json
from pathlib import Path
import resource
import sys
import zipfile
from app.converter import convert, atomic_json


def sandbox():
    resource.setrlimit(resource.RLIMIT_AS, (1536*1024**2, 1536*1024**2))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1200*1024**2, 1200*1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_CPU, (1800, 1800))
    # Fail closed if seccomp is unavailable. Subprocesses inherit this filter.
    lib = ctypes.CDLL('libseccomp.so.2')
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    ctx = lib.seccomp_init(0x7fff0000)
    if not ctx:
        raise RuntimeError('Unable to initialize converter sandbox.')
    try:
        for syscall in (b'socket', b'connect', b'ptrace', b'process_vm_readv', b'process_vm_writev'):
            code = lib.seccomp_syscall_resolve_name(syscall)
            if code < 0 or lib.seccomp_rule_add(ctx, 0x00050000 | errno.EPERM, code, 0) != 0:
                raise RuntimeError('Unable to configure converter sandbox.')
        if lib.seccomp_load(ctx) != 0:
            raise RuntimeError('Unable to enforce converter sandbox.')
    finally:
        lib.seccomp_release(ctx)


def run(folder):
    sandbox()
    job = json.loads((folder / 'job.json').read_text())
    try:
        convert(folder / 'source.pdf', folder / 'output',
                lambda done, total: atomic_json(folder / 'progress.json', {'done': done, 'total': total}),
                title=job['name'], ocr=job['ocr'], languages=job['languages'])
        with zipfile.ZipFile(folder / 'book.zip', 'w', zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted((folder / 'output').iterdir()):
                if path.is_symlink() or path.suffix not in {'.png', '.html', '.json'}:
                    continue
                if path.name.startswith('page-') and path.suffix == '.json':
                    item = json.loads(path.read_text())
                    item.pop('checkpoint_key', None)
                    bundle.writestr(path.name, json.dumps(item, ensure_ascii=False))
                else:
                    bundle.write(path, path.name)
        atomic_json(folder / 'result.json', {'ok': True})
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else 'PDF conversion failed or exceeded resource limits.'
        atomic_json(folder / 'result.json', {'ok': False, 'error': message[:300]})
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(run(Path(sys.argv[1])))
