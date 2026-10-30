"""Private document storage. Routes get the backend through the `get_storage` dependency, so tests (and a later
S3-compatible backend) can substitute it without touching the document service."""
from app.config import settings
from app.storage.base import InvalidKey, Storage, StorageError, check_key, new_key
from app.storage.local import LocalStorage

__all__ = ["InvalidKey", "LocalStorage", "Storage", "StorageError", "check_key", "get_storage", "new_key"]


def get_storage() -> Storage:
    # Read STORAGE_DIR on every call (not at import), so the test suite's temporary directory always applies.
    return LocalStorage(settings.storage_dir)
