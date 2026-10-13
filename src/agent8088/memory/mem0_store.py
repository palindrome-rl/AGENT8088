"""Mem0 memory store adapter.

Implements the MemoryStore contract against the Mem0 library (https://github.com/mem0ai/mem0).
All calls are non-raising and fail gracefully if mem0ai is not installed or errors.
"""

import json
import inspect
import logging
import os
import threading
from functools import wraps
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("agent8088.memory.mem0")


def _serialized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped

# Telemetry is disabled unconditionally -- nothing leaves the machine.
os.environ["MEM0_TELEMETRY"] = "false"
os.environ["POSTHOG_DISABLED"] = "1"

# Silence noisy third-party library warnings (e.g. spaCy optional models, posthog)
for _noisy_logger in ("mem0", "mem0.utils.spacy_models", "posthog"):
    logging.getLogger(_noisy_logger).setLevel(logging.ERROR)


# mem0's own LLM registry (mem0.utils.factory.LlmFactory). Hardcoded rather than
# read from mem0 at runtime so an import failure or an internal rename degrades to
# "map it to openai" -- which is right for every OpenAI-compatible endpoint --
# instead of raising. Anything agent8088 supports that is missing here is
# OpenAI-compatible anyway: custom, cerebras, mistral, moonshot, openrouter, qwen.
_MEM0_LLM_PROVIDERS = frozenset({
    "anthropic", "aws_bedrock", "azure_openai", "azure_openai_structured",
    "deepseek", "gemini", "groq", "langchain", "litellm", "lmstudio", "minimax",
    "ollama", "openai", "openai_structured", "sarvam", "together", "vllm", "xai",
})


def _import_mem0():
    try:
        for _noisy_logger in ("mem0", "mem0.utils.spacy_models", "posthog"):
            logging.getLogger(_noisy_logger).setLevel(logging.ERROR)
        try:
            import mem0.utils.spacy_models as _spacy_mod
            _spacy_mod.logger.setLevel(logging.ERROR)
            _spacy_mod._load_failed_full = True
            _spacy_mod._load_failed_lemma = True
        except Exception:
            pass
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
        self._lock = threading.RLock()
        self.operation_error = ""
        self.failed_operation = ""
        # Which embedder the config chain below actually settles on. /memory
        # reports this instead of guessing: the default is Ollama (nomic, shared
        # with the native engine), while `memory_mem0_embed_provider=fastembed`
        # opts out into a local MiniLM copy.
        self.embedder_info = None
        self.llm_info = None
        # Why initialisation failed, for /memory to show. This used to be a
        # log.warning nobody ever saw: the engine fell back to native, said
        # nothing about why, and an unsupported-provider config looked identical
        # to mem0ai simply not being installed.
        self.last_error = ""

    @_serialized
    def _init_memory(self):
        if self._available is False:
            return None
        if self._memory is not None:
            return self._memory

        memory_cls = _import_mem0()
        if memory_cls is None:
            self.last_error = ("the mem0ai package is not installed "
                               "(install it with: agent8088 --memory-setup)")
            self._available = False
            return None

        try:
            self.path.mkdir(parents=True, exist_ok=True)
            embed_dims = 384

            client_api_key = None
            client_base_url = None
            if self.client_factory:
                try:
                    c = self.client_factory()
                    client_api_key = getattr(c, "api_key", None)
                    client_base_url = str(getattr(c, "base_url", "")).rstrip("/")
                except Exception:
                    pass

            # The configured embedding client is authoritative for Ollama
            # embeddings. OLLAMA_HOST is often a server bind address such as
            # 0.0.0.0, which a client cannot connect to; preferring it made
            # mem0 fail even while Agent8088's native embedder worked.
            embed_provider = str(self.config.get("embed_provider") or "ollama").strip().lower()
            configured_ollama_url = (client_base_url.removesuffix("/v1")
                                     if embed_provider == "ollama" and client_base_url else "")
            ollama_url = configured_ollama_url or os.environ.get("OLLAMA_HOST") or "http://localhost:11434"

            llm_provider = str(self.config.get("llm_provider") or "").strip().lower()

            # mem0 always builds an LLM, defaulting to OpenAI, whose client refuses
            # to construct without OPENAI_API_KEY -- so an unset provider killed the
            # whole engine. Extraction is agent8088's own (add() uses infer=False),
            # so mem0's LLM is never called; it just has to construct.
            llm_provider = llm_provider or "openai"
            llm_section = None
            if llm_provider:
                # mem0 validates this name against its own registry and refuses
                # anything outside it -- so passing agent8088's own provider names
                # straight through ("custom", "qwen", "moonshot", "cerebras",
                # "openrouter", "mistral") failed the whole engine with
                # "Unsupported LLM provider: <name>", and mem0 silently degraded
                # to the native store. Everything agent8088 speaks to is
                # OpenAI-compatible unless mem0 knows the name natively, so
                # anything unrecognised becomes "openai" aimed at that provider's
                # own base_url rather than at api.openai.com.
                resolved_provider = (llm_provider if llm_provider in _MEM0_LLM_PROVIDERS
                                     else "openai")
                llm_cfg = {
                    "model": str(self.config.get("llm_model") or "gpt-4o-mini"),
                }
                if client_api_key or resolved_provider == "openai":
                    llm_cfg["api_key"] = client_api_key or "none"
                if resolved_provider == "ollama":
                    llm_cfg["ollama_base_url"] = ollama_url
                elif resolved_provider == "openai" and client_base_url:
                    # Only meaningful for the OpenAI-shaped client; setting it for
                    # anthropic/gemini/groq would be noise at best.
                    llm_cfg["openai_base_url"] = client_base_url
                llm_section = {
                    "provider": resolved_provider,
                    "config": llm_cfg,
                }
                self.llm_info = {"requested": llm_provider, "resolved": resolved_provider}

            embedder_section = None
            # parse_memory_engine_config defaults to `ollama`; a bare store
            # config uses the same default for parity with the native engine.
            if embed_provider == "ollama":
                embed_dims = 768
                embedder_section = {
                    "provider": "ollama",
                    "config": {
                        "model": str(self.config.get("embed_model") or "nomic-embed-text"),
                        "ollama_base_url": ollama_url,
                    },
                }
            elif embed_provider in {"openai", "custom"} and client_base_url:
                embed_dims = 1536
                embedder_section = {
                    "provider": "openai",
                    "config": {
                        "model": str(self.config.get("embed_model") or "text-embedding-3-small"),
                        "api_key": client_api_key or "none",
                        "openai_base_url": client_base_url,
                    },
                }
            else:
                # The explicit fastembed opt-out: memory_mem0_embed_provider=
                # fastembed (or any value that is not ollama/openai/custom).
                # No longer pre-warmed by the installers -- the first /memory
                # under this choice fetches the ~90 MB MiniLM model, pinned
                # under AGENT8088_HOME so it survives the temp-dir purges that
                # macOS and Windows both do. The cache is keyed off
                # AGENT8088_HOME rather than the mem0 dir: the model cache is
                # shared, not part of the store, so pointing memory_mem0_dir
                # elsewhere must not orphan it. An explicit FASTEMBED_CACHE_PATH
                # from the user still wins.
                try:
                    import fastembed  # noqa: F401 -- verify the optional runtime imports
                    agent_home = os.environ.get(
                        "AGENT8088_HOME", os.path.expanduser("~/.agent8088"))
                    os.environ.setdefault(
                        "FASTEMBED_CACHE_PATH", os.path.join(agent_home, "fastembed"))
                    embed_dims = 384
                    embedder_section = {
                        "provider": "fastembed",
                        "config": {
                            "model": "sentence-transformers/all-MiniLM-L6-v2",
                        },
                    }
                except ImportError:
                    if client_base_url:
                        embed_dims = 1536
                        embedder_section = {
                            "provider": "openai",
                            "config": {
                                "model": str(self.config.get("embed_model") or "text-embedding-3-small"),
                                "api_key": client_api_key or "none",
                                "openai_base_url": client_base_url,
                            },
                        }

            qdrant_cfg: Dict[str, Any] = {
                "path": str(self.path),
            }
            if self.vector_store_type == "qdrant":
                # Older Mem0 releases delete a local path on reopen when this
                # is false. Every Agent8088 memory store is persistent.
                qdrant_cfg["on_disk"] = True
            if embed_dims:
                qdrant_cfg["embedding_model_dims"] = embed_dims

            cfg = {
                "vector_store": {
                    "provider": self.vector_store_type,
                    "config": qdrant_cfg,
                },
                "version": "v1.1",
                "history_db_path": str(self.path / "history.db"),
            }
            if llm_section:
                cfg["llm"] = llm_section
            if embedder_section:
                cfg["embedder"] = embedder_section
                self.embedder_info = {
                    "provider": embedder_section["provider"],
                    "model": embedder_section["config"].get("model", ""),
                    "dims": embed_dims,
                }

            # Mem0's constructor validates its configuration but does not
            # contact the embedding service. In particular, an unpulled local
            # Ollama model used to look healthy until the first recall, where
            # Mem0 swallowed the failure and returned an empty list. The native
            # embedder uses the same provider/model configuration, so probe it
            # once before claiming Mem0 is available. Falling back to SQLite
            # preserves local keyword recall and status explains the split.
            if (self.embedder is not None
                    and embed_provider == "ollama"):
                probe = self.embedder
                model = embedder_section["config"]["model"]
                if getattr(probe, "model", "") != model and self.client_factory:
                    from .embed import Embedder
                    probe = Embedder(self.client_factory, model)
                if probe.available():
                    dims = getattr(probe, "dim", None)
                    if isinstance(dims, int) and dims > 0:
                        qdrant_cfg["embedding_model_dims"] = dims
                        self.embedder_info["dims"] = dims
                else:
                    detail = getattr(probe, "last_error", "")
                    self.last_error = (f"embedding service unavailable for "
                                       f"{embed_provider}/{model}"
                                       + (f": {detail}" if detail else ""))[:300]
                    self._memory = None
                    self._available = False
                    return None
            if hasattr(memory_cls, "from_config"):
                self._memory = memory_cls.from_config(cfg)
            else:
                self._memory = memory_cls()
            self._available = True
            return self._memory
        except Exception as exc:
            log.warning("failed to initialize mem0 memory engine: %s", exc)
            # Kept so /memory can name the reason. Collapsed to one line because
            # pydantic's validation errors span several and the status table has
            # a single cell to say it in.
            self.last_error = " ".join(str(exc).split())[:300]
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

    @_serialized
    def close(self) -> None:
        memory = self._memory
        self._memory = None
        self._available = None
        if memory is None:
            return
        # Embedded Qdrant holds an exclusive file lock on Windows. A config
        # reload or engine switch must release it before reopening the store.
        client = getattr(getattr(memory, "vector_store", None), "client", None)
        close = getattr(client, "close", None)
        if callable(close):
            close()

    def _failed(self, operation, exc):
        self.failed_operation = operation
        self.operation_error = " ".join(str(exc).split())[:300] or type(exc).__name__

    def _succeeded(self, operation):
        if self.failed_operation == operation:
            self.failed_operation = ""
            self.operation_error = ""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False

    def _read(self, method, *, user_id, limit, query=None):
        """Choose the SDK shape before calling; an internal error is not an API change."""
        parameters = inspect.signature(method).parameters
        modern = "top_k" in parameters or any(
            value.kind == inspect.Parameter.VAR_KEYWORD for value in parameters.values())
        kwargs = ({"filters": {"user_id": str(user_id)}, "top_k": int(limit)}
                  if modern else {"user_id": str(user_id)})
        if not modern and "limit" in parameters:
            kwargs["limit"] = int(limit)
        return method(str(query), **kwargs) if query is not None else method(**kwargs)

    # -- writes ------------------------------------------------------------

    @_serialized
    def add(self, text, *, user_id: str = "owner", embedding=None, embed_model: str = "",
            project: Optional[str] = None, agent_id: Optional[str] = None,
            run_id: Optional[str] = None, categories=None, source: str = "extracted",
            metadata: Optional[Dict[str, Any]] = None) -> Optional[str]:
        mem = self._init_memory()
        if mem is None:
            return None
        text = " ".join(str(text).split())
        if not text:
            return None

        # mem0's own metadata dict already carries project/agent_id/run_id -- the
        # caller's extra keys (repo, source_channel) are merged in rather than
        # replacing it, so both stores end up describing the same memory the
        # same way even though this is mem0's field, not agent8088's.
        full_metadata = {
            "project": project,
            "agent_id": agent_id,
            "run_id": run_id,
            "categories": categories or [],
            "source": source,
        }
        full_metadata.update(metadata or {})
        metadata = full_metadata
        try:
            # Extraction already happened in Agent8088. Never retry with
            # Mem0's default inference after an internal TypeError.
            resp = mem.add(text, user_id=str(user_id), metadata=metadata, infer=False)
            items = []
            if isinstance(resp, dict):
                items = resp.get("results") or []
            elif isinstance(resp, list):
                items = resp
            for item in items:
                if isinstance(item, dict) and item.get("id") and item.get("event") != "DELETE":
                    self._succeeded("add")
                    return str(item["id"])
            self._failed("add", RuntimeError("Mem0 did not confirm a saved memory"))
            return None
        except Exception as exc:
            self._failed("add", exc)
            log.debug("mem0 add failed: %s", exc)
            return None

    @_serialized
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

    @_serialized
    def delete_all(self, *, user_id: str = "owner") -> int:
        mem = self._init_memory()
        if mem is None:
            return 0
        try:
            if hasattr(mem, "delete_all"):
                try:
                    mem.delete_all(filters={"user_id": str(user_id)})
                except (TypeError, ValueError):
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

    @_serialized
    def search(self, query: str, *, user_id: str = "owner", embedding=None, model: str = "",
               limit: int = 5, rrf_k: int = 60, min_score: float = 0.0,
               record_access: bool = True) -> List[Dict[str, Any]]:
        mem = self._init_memory()
        if mem is None:
            return []
        try:
            resp = self._read(mem.search, query=query, user_id=user_id, limit=limit)
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
                    # JSON text, matching native's row["metadata"] shape (a raw
                    # sqlite TEXT column, parsed by the caller) -- until now this
                    # dict was written to mem0 at add() but nothing ever read it
                    # back, so repo/author/source_channel/files_touched were
                    # genuinely stored yet invisible through this adapter.
                    "metadata": json.dumps(item.get("metadata")) if item.get("metadata") else None,
                })
            self._succeeded("search")
            return results
        except Exception as exc:
            self._failed("search", exc)
            log.debug("mem0 search failed: %s", exc)
            return []

    @_serialized
    def get_all(self, *, user_id: str = "owner", limit: int = 200) -> List[Dict[str, Any]]:
        mem = self._init_memory()
        if mem is None:
            return []
        try:
            resp = self._read(mem.get_all, user_id=user_id, limit=limit)
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
                    "metadata": json.dumps(item.get("metadata")) if item.get("metadata") else None,
                })
            self._succeeded("get_all")
            return out
        except Exception as exc:
            self._failed("get_all", exc)
            log.debug("mem0 get_all failed: %s", exc)
            return []

    def recent(self, *, user_id: str = "owner", run_id: Optional[str] = None, limit: int = 20) -> List[str]:
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
            "embedder": self.embedder_info,
            "llm": self.llm_info,
            "error": self.last_error,
            "operation_error": self.operation_error,
            "failed_operation": self.failed_operation,
        }
