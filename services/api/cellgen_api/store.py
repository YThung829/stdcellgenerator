"""Persistence for sandboxes, snapshots, constraints and bundles.

MongoDB is the intended home (the plan calls for it), but the interface is kept
narrow and a file-backed implementation ships alongside it so the API runs with
nothing installed. For a single user that is not a downgrade -- the data is a
handful of small JSON documents.

Everything stored here is small by design: a sandbox record is essentially one
id, and a snapshot is exported session JSON (hundreds of bytes). Solver output
does not belong here.
"""

from __future__ import annotations

import abc
import json
import time
import uuid
from pathlib import Path


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Store(abc.ABC):
    """Document storage, keyed by collection and id."""

    @abc.abstractmethod
    def put(self, collection: str, doc_id: str, doc: dict) -> None: ...

    @abc.abstractmethod
    def get(self, collection: str, doc_id: str) -> dict | None: ...

    @abc.abstractmethod
    def list(self, collection: str) -> list[dict]: ...

    @abc.abstractmethod
    def delete(self, collection: str, doc_id: str) -> None: ...

    def upsert(self, collection: str, doc: dict) -> dict:
        """Store a document, assigning ``_id`` and timestamps when absent."""
        doc = dict(doc)
        doc.setdefault("_id", new_id(collection[:3]))
        doc.setdefault("created_at", time.time())
        doc["updated_at"] = time.time()
        self.put(collection, doc["_id"], doc)
        return doc


class FileStore(Store):
    """One JSON file per document under ``root/<collection>/<id>.json``."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, collection: str, doc_id: str) -> Path:
        d = self.root / collection
        d.mkdir(parents=True, exist_ok=True)
        # Ids are generated, but never let one escape its collection directory.
        safe = doc_id.replace("/", "_").replace("..", "_")
        return d / f"{safe}.json"

    def put(self, collection: str, doc_id: str, doc: dict) -> None:
        self._path(collection, doc_id).write_text(json.dumps(doc, indent=2))

    def get(self, collection: str, doc_id: str) -> dict | None:
        p = self._path(collection, doc_id)
        return json.loads(p.read_text()) if p.is_file() else None

    def list(self, collection: str) -> list[dict]:
        d = self.root / collection
        if not d.is_dir():
            return []
        docs = [json.loads(p.read_text()) for p in d.glob("*.json")]
        return sorted(docs, key=lambda x: x.get("created_at", 0), reverse=True)

    def delete(self, collection: str, doc_id: str) -> None:
        self._path(collection, doc_id).unlink(missing_ok=True)


class MongoStore(Store):
    """The same interface over MongoDB."""

    def __init__(self, url: str, database: str = "cellgen"):
        from pymongo import MongoClient

        self.db = MongoClient(url)[database]

    def put(self, collection: str, doc_id: str, doc: dict) -> None:
        doc = dict(doc, _id=doc_id)
        self.db[collection].replace_one({"_id": doc_id}, doc, upsert=True)

    def get(self, collection: str, doc_id: str) -> dict | None:
        return self.db[collection].find_one({"_id": doc_id})

    def list(self, collection: str) -> list[dict]:
        return list(self.db[collection].find().sort("created_at", -1))

    def delete(self, collection: str, doc_id: str) -> None:
        self.db[collection].delete_one({"_id": doc_id})


def build_store(mongo_url: str | None, file_root: Path) -> Store:
    """MongoStore when a URL is configured and reachable, else FileStore."""
    if mongo_url:
        from loguru import logger

        try:
            store = MongoStore(mongo_url)
            store.db.command("ping")
            logger.info(f"using MongoDB at {mongo_url}")
            return store
        except Exception as exc:
            logger.warning(f"MongoDB at {mongo_url} unreachable ({exc}); using files")
    return FileStore(file_root)
