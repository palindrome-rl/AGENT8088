"""Mem0 memory store adapter.

Implements the MemoryStore contract against the Mem0 library (https://github.com/mem0ai/mem0).
All calls are non-raising and fail gracefully if mem0ai is not installed or errors.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("agent8088.memory.mem0")

# Telemetry is disabled unconditionally -- nothing leaves the machine.
os.environ["MEM0_TELEMETRY"] = "false"


def _import_mem0():
    try:
        import mem0
        return getattr(mem0, "Memory", None)
    except Exception as exc:
        log.debug("mem0 library import failed: %s", exc)
        return None


class Mem0MemoryStore:
    """Adapter wrapping Mem0 to match Agent8088's MemoryStore contract."""

    def __init__(self, config: Optional[Dict[str, Any]] = None, client_factory=None, embedder=None):
        self.config = config or {}
        self.client_factory = client_factory
        self.embedder = embedder
        self.path = Path(self.config.get("path") or os.path.expanduser("~/.agent8088/mem0"))
        self.vector_store_type = str(self.config.get("vector_store", "qdrant")).strip().lower()
        self._memory = None
        self._available = None

    def _init_memory(self):
        if self._available is False:
            return None
        if self._memory is not None:
            return self._memory

        memory_cls = _import_mem0()
        if memory_cls is None:
            self._available = False
            return None

        try:
            self.path.mkdir(parents=True, exist_ok=True)
            cfg = {
                "vector_store": {
                    "provider": self.vector_store_type,
                    "config": {
                        "path": str(self.path),
                    },
                },
                "version": "v1.1",
            }
            llm_provider = str(self.config.get("llm_provider") or "").strip().lower()
            if llm_provider:
                cfg["llm"] = {
                    "provider": llm_provider,
                    "config": {
                        "model": str(self.config.get("llm_model") or ""),
                    }
                }
                if llm_provider == "ollama":
                    cfg["llm"]["config"]["ollama_base_url"] = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

            embed_provider = str(self.config.get("embed_provider") or "").strip().lower()
            if embed_provider:
                cfg["embedder"] = {
                    "provider": embed_provider,
                    "config": {
                        "model": str(self.config.get("embed_model") or ""),
                    }
                }
                if embed_provider == "ollama":
                    cfg["embedder"]["config"]["ollama_base_url"] = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

            if hasattr(memory_cls, "from_config"):
                self._memory = memory_cls.from_config(cfg)
            else:
                self._memory = memory_cls()
            self._available = True
            return self._memory
        except Exception as exc:
            log.warning("failed to initialize mem0 memory engine: %s", exc)
            self._available = False
            return None

    def available(self) -> bool:
        if self._available is not None:
            return self._available
        mem = self._init_memory()
        return mem is not None

    def connect(self):
        self._init_memory()
        return self

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    # -- writes ------------------------------------------------------------

    def add(self, text, *, user_id: str, embedding=None, embed_model: str = "",
            project: Optional[str] = None, agent_id: Optional[str] = None,
            run_id: Optional[str] = None, categories=None, source: str = "extracted") -> Optional[str]:
        mem = self._init_memory()
        if mem is None:
            return None
        text = " ".join(str(text).split())
        if not text:
            return None

        metadata = {
            "project": project,
            "agent_id": agent_id,
            "run_id": run_id,
            "categories": categories or [],
            "source": source,
        }
        try:
            resp = mem.add(text, user_id=str(user_id), metadata=metadata)
            items = []
            if isinstance(resp, dict):
                items = resp.get("results") or []
            elif isinstance(resp, list):
                items = resp
            if items and isinstance(items[0], dict):
                return str(items[0].get("id") or "")
            return "stored"
        except Exception as exc:
            log.debug("mem0 add failed: %s", exc)
            return None

    def delete(self, memory_id: str) -> bool:
        mem = self._init_memory()
        if mem is None:
            return False
        try:
            mem.delete(str(memory_id))
            return True
        except Exception as exc:
            log.debug("mem0 delete failed: %s", exc)
            return False

    def delete_all(self, *, user_id: str) -> int:
        mem = self._init_memory()
        if mem is None:
            return 0
        try:
            if hasattr(mem, "delete_all"):
                mem.delete_all(user_id=str(user_id))
                return 1
            existing = self.get_all(user_id=user_id)
            for item in existing:
                self.delete(item["id"])
            return len(existing)
        except Exception as exc:
            log.debug("mem0 delete_all failed: %s", exc)
            return 0

    # -- reads -------------------------------------------------------------

    def search(self, query: str, *, user_id: str, embedding=None, model: str = "",
               limit: int = 5, rrf_k: int = 60, min_score: float = 0.0,
               record_access: bool = True) -> List[Dict[str, Any]]:
        mem = self._init_memory()
        if mem is None:
            return []
        try:
            resp = mem.search(str(query), user_id=str(user_id), limit=int(limit))
            items = []
            if isinstance(resp, dict):
                items = resp.get("results") or []
            elif isinstance(resp, list):
                items = resp

            results = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                fact = item.get("memory") or item.get("text") or ""
                if not fact:
                    continue
                score = float(item.get("score") or 0.0)
                if score < min_score:
                    continue
                results.append({
                    "id": str(item.get("id") or ""),
                    "text": str(fact),
                    "score": score,
                    "categories": item.get("categories") or [],
                })
            return results
        except Exception as exc:
            log.debug("mem0 search failed: %s", exc)
            return []

    def get_all(self, *, user_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        mem = self._init_memory()
        if mem is None:
            return []
        try:
            resp = mem.get_all(user_id=str(user_id))
            items = []
            if isinstance(resp, dict):
                items = resp.get("results") or []
            elif isinstance(resp, list):
                items = resp

            out = []
            for item in items[:limit]:
                if not isinstance(item, dict):
                    continue
                fact = item.get("memory") or item.get("text") or ""
                out.append({
                    "id": str(item.get("id") or ""),
                    "text": str(fact),
                    "created_at": item.get("created_at"),
                })
            return out
        except Exception as exc:
            log.debug("mem0 get_all failed: %s", exc)
            return []

    def recent(self, *, user_id: str, run_id: Optional[str] = None, limit: int = 20) -> List[str]:
        items = self.get_all(user_id=user_id, limit=limit)
        return [item["text"] for item in items if item.get("text")]

    def count(self, *, user_id: Optional[str] = None) -> int:
        if not user_id:
            user_id = "owner"
        return len(self.get_all(user_id=user_id))

    def status(self) -> Dict[str, Any]:
        return {
            "engine": "mem0",
            "available": self.available(),
            "vector_store": self.vector_store_type,
            "path": str(self.path),
        }
