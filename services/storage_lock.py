"""OS-released lock shared by folder intake and reset operations."""
from contextlib import contextmanager
from functools import wraps
import hashlib
import os
from pathlib import Path
import tempfile


@contextmanager
def folder_operation_lock(root: Path):
    # Outside local_data so resetting that directory cannot unlink an active lock.
    identity = hashlib.sha256(os.path.normcase(str(root.resolve())).encode()).hexdigest()
    path = Path(tempfile.gettempdir()) / f'invoice-ai-outlook-{identity}.lock'
    with path.open('a+b') as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('An Outlook run or reset is already in progress. Wait for it to finish.') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def exclusive_folder_operation(function):
    @wraps(function)
    def guarded(storage, *args, **kwargs):
        with folder_operation_lock(storage.root):
            return function(storage, *args, **kwargs)
    return guarded
