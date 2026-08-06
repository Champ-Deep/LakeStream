"""Pluggable binary storage for screenshots and other artifacts (v2).

Ships a local-filesystem backend. The StorageBackend interface lets an S3 (or
other) backend drop in later without touching callers.
"""

import hashlib
import os
from abc import ABC, abstractmethod

import structlog

log = structlog.get_logger()


class StorageBackend(ABC):
    @abstractmethod
    def save_screenshot(self, job_id: str, data: bytes) -> str | None:
        """Persist a PNG and return a relative reference, or None on failure."""

    @abstractmethod
    def read_screenshot(self, relpath: str) -> bytes | None:
        """Read a screenshot by its relative reference, or None if missing."""


class FilesystemStorage(StorageBackend):
    """Writes screenshots under {base_dir}/{job_id}/{sha}.png."""

    def __init__(self, base_dir: str):
        self.base_dir = os.path.abspath(base_dir)

    def save_screenshot(self, job_id: str, data: bytes) -> str | None:
        try:
            sha = hashlib.sha256(data).hexdigest()[:16]
            relpath = os.path.join(job_id, f"{sha}.png")
            full = os.path.join(self.base_dir, relpath)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(data)
            return relpath
        except Exception as e:
            log.warning("screenshot_save_failed", job_id=job_id, error=str(e))
            return None

    def read_screenshot(self, relpath: str) -> bytes | None:
        # Guard against path traversal: resolved path must stay under base_dir.
        full = os.path.abspath(os.path.join(self.base_dir, relpath))
        if not full.startswith(self.base_dir + os.sep):
            log.warning("screenshot_path_traversal_blocked", relpath=relpath)
            return None
        try:
            with open(full, "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None


_backend: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _backend
    if _backend is None:
        from src.config.settings import get_settings

        _backend = FilesystemStorage(get_settings().screenshot_dir)
    return _backend
