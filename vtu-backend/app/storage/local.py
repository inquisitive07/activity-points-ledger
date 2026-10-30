"""Private local file storage (the MVP backend, CLAUDE.md 18.5). Files live under STORAGE_DIR, which is never
mounted as a static directory; the only way out is GET /documents/{id}/content after authorization."""
import contextlib
import os
import tempfile
from pathlib import Path
from typing import BinaryIO

from app.storage.base import InvalidKey, Storage, StorageError, check_key


class LocalStorage(Storage):
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        """The file for `key`, guaranteed to be inside the storage root (also after resolving symlinks)."""
        check_key(key)
        root = self.root.resolve()
        path = (root / key).resolve()
        if not path.is_relative_to(root) or path == root:
            raise InvalidKey(f"storage key {key!r} escapes the storage root")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Write a private temp file next to the target, flush it to disk, then hard-link it into place. link() is
        # atomic and fails if the target exists, so readers never see a partial file and nothing is overwritten.
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")      # mode 0600
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.link(tmp, path)
        except OSError as exc:
            raise StorageError(f"could not write {key!r}: {exc}") from exc
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
        _fsync_dir(path.parent)

    def open(self, key: str) -> BinaryIO:
        path = self._path(key)
        try:
            return open(path, "rb")
        except OSError as exc:
            raise StorageError(f"could not read {key!r}: {exc}") from exc

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def discard(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(f"could not remove {key!r}: {exc}") from exc


def _fsync_dir(directory: Path) -> None:
    """Make the new directory entry durable (best effort: not every filesystem supports it)."""
    with contextlib.suppress(OSError):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
