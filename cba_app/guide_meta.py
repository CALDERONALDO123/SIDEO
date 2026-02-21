import hashlib
import json
from datetime import datetime, timezone

from django.core.files.base import ContentFile
from django.core.files.storage import Storage, default_storage


GUIDE_PDF_STORAGE_NAME = "guides/guia.pdf"
GUIDE_META_STORAGE_NAME = "guides/guia.meta.json"


def _sha256_storage(storage: Storage, storage_name: str) -> str:
    h = hashlib.sha256()
    with storage.open(storage_name, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_guide_meta(storage: Storage | None = None) -> dict | None:
    storage = storage or default_storage
    if not storage.exists(GUIDE_META_STORAGE_NAME):
        return None
    try:
        with storage.open(GUIDE_META_STORAGE_NAME, "rb") as fh:
            raw = fh.read().decode("utf-8", errors="replace")
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def write_guide_meta(meta: dict, storage: Storage | None = None) -> None:
    storage = storage or default_storage
    payload = json.dumps(meta, ensure_ascii=False, indent=2)
    if storage.exists(GUIDE_META_STORAGE_NAME):
        try:
            storage.delete(GUIDE_META_STORAGE_NAME)
        except Exception:
            pass
    storage.save(GUIDE_META_STORAGE_NAME, ContentFile(payload.encode("utf-8")))


def compute_and_store_guide_meta(storage: Storage | None = None, *, pdf_storage_name: str | None = None) -> dict | None:
    storage = storage or default_storage
    target = (pdf_storage_name or GUIDE_PDF_STORAGE_NAME).strip() or GUIDE_PDF_STORAGE_NAME
    if not storage.exists(target):
        return None

    sha256 = _sha256_storage(storage, target)
    meta = {
        "sha256": sha256,
        "version": sha256[:16],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "pdf_storage_name": target,
    }
    write_guide_meta(meta, storage=storage)
    return meta


def ensure_guide_meta(storage: Storage | None = None, *, pdf_storage_name: str | None = None) -> dict | None:
    storage = storage or default_storage
    target = (pdf_storage_name or GUIDE_PDF_STORAGE_NAME).strip() or GUIDE_PDF_STORAGE_NAME
    if not storage.exists(target):
        return None

    meta = read_guide_meta(storage=storage)
    if meta and isinstance(meta.get("version"), str) and meta.get("version"):
        return meta

    return compute_and_store_guide_meta(storage=storage, pdf_storage_name=target)
