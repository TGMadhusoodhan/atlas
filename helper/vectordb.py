"""Semantic long-term memory for ATLAS — local ChromaDB + onnxruntime embeddings.

Every conversation turn is embedded and stored; relevant past memories are recalled
by *meaning* (not recency), so ATLAS can remember what you discussed days ago. Shared
by voice and the sidebar through ai_helper.py. Nothing leaves the machine.

Embeddings use ChromaDB's built-in onnxruntime model (all-MiniLM-L6-v2, CPU) rather
than torch/sentence-transformers, so each ai_helper process stays light.

All functions fail soft: if the store can't initialise, memory silently no-ops and
normal chat is unaffected.
"""
from __future__ import annotations

import datetime
import hashlib
import sys
import threading
from pathlib import Path

DB_DIR = Path.home() / ".local/share/ai-sidebar/memory/chroma"
COLLECTION = "conversation_memory"

_collection = None
_init_failed = False
_lock = threading.Lock()


def _log(*a) -> None:
    print("[vectordb]", *a, file=sys.stderr, flush=True)


def _ensure():
    """Lazily open the persistent collection. Returns it, or None if unavailable."""
    global _collection, _init_failed
    if _collection is not None or _init_failed:
        return _collection
    with _lock:
        if _collection is not None or _init_failed:
            return _collection
        try:
            import chromadb
            from chromadb.config import Settings
            from chromadb.utils import embedding_functions

            DB_DIR.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(
                path=str(DB_DIR),
                settings=Settings(anonymized_telemetry=False, allow_reset=False),
            )
            ef = embedding_functions.DefaultEmbeddingFunction()  # onnxruntime all-MiniLM
            _collection = client.get_or_create_collection(
                name=COLLECTION,
                embedding_function=ef,
                metadata={"hnsw:space": "cosine"},
            )
            _log(f"ready ({_collection.count()} memories) at {DB_DIR}")
        except Exception as e:
            _init_failed = True
            _log(f"init failed ({type(e).__name__}: {e}) — memory disabled this session")
    return _collection


def add(text: str, source: str = "chat", extra: dict | None = None) -> None:
    """Store a memory. Deduplicates identical text via a content-hash id."""
    text = (text or "").strip()
    if len(text) < 8:
        return
    col = _ensure()
    if col is None:
        return
    try:
        mid = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
        meta = {"source": source, "ts": datetime.datetime.now().isoformat()}
        if extra:
            meta.update({k: v for k, v in extra.items() if isinstance(v, (str, int, float, bool))})
        col.upsert(ids=[mid], documents=[text], metadatas=[meta])
    except Exception as e:
        _log(f"add failed: {e}")


def search(query: str, k: int = 5, max_distance: float = 0.85) -> list[dict]:
    """Return up to k relevant memories, closest first. Weak matches (cosine distance
    above max_distance) are dropped so we don't inject unrelated noise."""
    query = (query or "").strip()
    if not query:
        return []
    col = _ensure()
    if col is None:
        return []
    try:
        n = min(k, max(col.count(), 1))
        res = col.query(query_texts=[query], n_results=n)
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        out = []
        for _id, doc, dist, meta in zip(ids, docs, dists, metas):
            if dist is not None and dist > max_distance:
                continue
            out.append({"id": _id, "text": doc, "distance": dist, "meta": meta or {}})
        return out
    except Exception as e:
        _log(f"search failed: {e}")
        return []


def delete(ids: list[str]) -> int:
    """Delete memories by id. Returns how many were requested for deletion."""
    ids = [i for i in (ids or []) if i]
    if not ids:
        return 0
    col = _ensure()
    if col is None:
        return 0
    try:
        col.delete(ids=ids)
        return len(ids)
    except Exception as e:
        _log(f"delete failed: {e}")
        return 0


def clear() -> int:
    """Wipe ALL memories. Returns the number removed."""
    col = _ensure()
    if col is None:
        return 0
    try:
        ids = col.get().get("ids", [])
        if ids:
            col.delete(ids=ids)
        return len(ids)
    except Exception as e:
        _log(f"clear failed: {e}")
        return 0


def count() -> int:
    col = _ensure()
    if col is None:
        return 0
    try:
        return col.count()
    except Exception:
        return 0
