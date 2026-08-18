"""SQLite store for persistent memory: schema, CRUD, and hybrid retrieval.

Two independent retrieval legs are fused by Reciprocal Rank Fusion:

  BM25 (FTS5)  matches words   -- exact tokens, filenames, flags, error strings
  cosine       matches meaning -- paraphrase, "package manager" finding "uv"

Their scores are not comparable (bm25() returns about -8.4, cosine 0.83), so RRF
throws the scores away and fuses the ranks instead. A memory both legs rank high
wins; a memory only one leg found can still place on its own strength; a memory
ranked 40th by both loses. That property is why the vector leg only has to be
roughly right, and in turn why a 274MB embedder is enough for this corpus.

Nothing here calls a model. Embeddings arrive as plain float lists from
memory.embed, so every ranking decision in this module is testable offline.
"""

import array
import hashlib
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA_VERSION = 1

# The damping constant from the original RRF paper. Large enough that rank 1 does
# not crush rank 2, small enough that rank 50 is nearly worthless.
DEFAULT_RRF_K = 60

# How many rows each leg contributes before fusion. Deeper than the final limit
# on purpose: a memory ranked 30th by words and 3rd by meaning should get the
# chance to win, and it cannot if the word leg only reported its top 5.
LEG_DEPTH = 50

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
  rowid            INTEGER PRIMARY KEY,
  id               TEXT UNIQUE NOT NULL,
  user_id          TEXT NOT NULL,
  agent_id         TEXT,
  run_id           TEXT,
  project          TEXT,
  text             TEXT NOT NULL,
  hash             TEXT NOT NULL,
  categories       TEXT,
  source           TEXT NOT NULL,
  created_at       REAL NOT NULL,
  updated_at       REAL NOT NULL,
  access_count     INTEGER NOT NULL DEFAULT 0,
  last_accessed_at REAL,
  UNIQUE(user_id, hash)
);

CREATE INDEX IF NOT EXISTS memories_scope ON memories(user_id, project);

CREATE TABLE IF NOT EXISTS vectors (
  memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
  model     TEXT NOT NULL,
  dim       INTEGER NOT NULL,
  vec       BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_events (
  id        INTEGER PRIMARY KEY,
  memory_id TEXT NOT NULL,
  event     TEXT NOT NULL,
  old_text  TEXT,
  new_text  TEXT,
  at        REAL NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
  text, content='memories', content_rowid='rowid', tokenize='porter unicode61');

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.rowid, old.text);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.rowid, old.text);
  INSERT INTO memories_fts(rowid, text) VALUES (new.rowid, new.text);
END;
"""


class MemoryStoreError(RuntimeError):
    """Raised for store problems the caller may want to report. Callers in the
    agent loop still swallow it -- memory must never be able to break a turn."""


def text_hash(text: str) -> str:
    """Dedup key for a memory. Normalised so trailing whitespace and case do not
    create a second copy of the same fact."""
    return hashlib.md5(" ".join(text.split()).casefold().encode("utf-8")).hexdigest()


def _pack(vector) -> bytes:
    return array.array("f", vector).tobytes()


def _unpack(blob: bytes):
    out = array.array("f")
    out.frombytes(blob)
    return out


def normalise(vector):
    """L2-normalise so the read path is a dot product rather than a full cosine.

    A zero vector would divide by zero; it is returned untouched and scores 0
    against everything, which is the right answer for "no information".
    """
    magnitude = math.sqrt(sum(component * component for component in vector))
    if not magnitude:
        return list(vector)
    return [component / magnitude for component in vector]


def dot(left, right) -> float:
    return sum(a * b for a, b in zip(left, right))


# Stripped from the keyword leg. Not an optimisation: the tokens are OR-ed so a
# partial match can still rank, which means one shared stopword is enough to make
# any query match any memory. "what is the capital of France" matched a memory
# about uv on the word "the", and on a small store BM25 has nothing better to
# rank, so the irrelevant memory was injected into the prompt. The vector leg
# handles meaning; the keyword leg only needs the words that carry any.
_STOPWORDS = frozenset((
    "a", "about", "all", "also", "am", "an", "and", "any", "are", "as", "at",
    "be", "because", "been", "but", "by", "can", "cannot", "could", "did", "do",
    "does", "doing", "done", "for", "from", "get", "got", "had", "has", "have",
    "how", "however", "i", "if", "in", "into", "is", "it", "its", "just", "me",
    "my", "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other",
    "our", "out", "over", "own", "please", "same", "should", "so", "some",
    "such", "than", "that", "the", "their", "them", "then", "there", "these",
    "they", "this", "those", "to", "too", "under", "until", "up", "us", "very",
    "was", "we", "were", "what", "when", "where", "which", "while", "who",
    "whom", "why", "will", "with", "would", "you", "your",
))


def fts_query(text: str) -> str:
    """Turn arbitrary user text into a safe FTS5 MATCH expression.

    FTS5 treats `"`, `*`, `(`, `:`, `^`, `NEAR` and `OR` as *syntax*, so passing a
    plain question through raises OperationalError on ordinary punctuation -- an
    apostrophe or a stray paren is enough. Every token is therefore stripped to
    word characters and double-quoted as a literal phrase, and the tokens are
    OR-ed so a partial match still ranks.

    Returns "" when nothing survives, which callers read as "skip the BM25 leg".
    A query of nothing but stopwords carries no keyword signal, so skipping is
    the honest answer rather than matching everything.
    """
    tokens = []
    for raw in text.split():
        token = "".join(character for character in raw if character.isalnum() or character in "-_")
        if token.casefold() in _STOPWORDS:
            continue
        if len(token) > 1 or token.isdigit():
            tokens.append(f'"{token}"')
    return " OR ".join(tokens)


class MemoryStore:
    """One SQLite file, one connection per thread.

    Thread-local rather than a single shared connection because sqlite3 objects
    are bound to the thread that created them, and capture deliberately runs on a
    background thread so the user never waits for it. A shared connection raises
    ProgrammingError there -- caught, so the symptom would not be a crash but
    memory silently never being written. WAL mode is what makes the concurrent
    readers safe.
    """

    def __init__(self, path):
        self.path = Path(path).expanduser()
        self._local = threading.local()

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        existing = getattr(self._local, "connection", None)
        if existing is not None:
            return existing
        first_time = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(str(self.path), timeout=5.0)
        except sqlite3.Error as exc:
            raise MemoryStoreError(f"could not open memory store: {exc}") from exc
        if first_time and os.name != "nt":
            # Memories are personal data. 0600 before anything is written to it.
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(_SCHEMA)
        connection.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        connection.commit()
        self._local.connection = connection
        return connection

    def close(self) -> None:
        """Close this thread's connection. Other threads keep theirs, which is why
        a background capture must call this when it finishes."""
        existing = getattr(self._local, "connection", None)
        if existing is not None:
            existing.close()
            self._local.connection = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_exception):
        self.close()
        return False

    # -- writes ------------------------------------------------------------

    def add(self, text, *, user_id, embedding=None, embed_model="", project=None,
            agent_id=None, run_id=None, categories=None, source="extracted"):
        """Insert one memory. Returns its id, or None when it is a duplicate.

        Dedup is the UNIQUE(user_id, hash) constraint rather than a prior SELECT,
        so two concurrent captures of the same fact cannot both win a race.
        """
        text = " ".join(str(text).split())
        if not text:
            return None
        connection = self.connect()
        memory_id = str(uuid.uuid4())
        now = time.time()
        try:
            connection.execute(
                "INSERT INTO memories (id, user_id, agent_id, run_id, project, text,"
                " hash, categories, source, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (memory_id, user_id, agent_id, run_id, project, text, text_hash(text),
                 json.dumps(categories or []), source, now, now),
            )
        except sqlite3.IntegrityError:
            connection.rollback()
            return None
        if embedding:
            vector = normalise(embedding)
            connection.execute(
                "INSERT OR REPLACE INTO vectors (memory_id, model, dim, vec) VALUES (?,?,?,?)",
                (memory_id, embed_model, len(vector), _pack(vector)),
            )
        connection.execute(
            "INSERT INTO memory_events (memory_id, event, old_text, new_text, at)"
            " VALUES (?,?,?,?,?)",
            (memory_id, "ADD", None, text, now),
        )
        connection.commit()
        return memory_id

    def delete(self, memory_id) -> bool:
        connection = self.connect()
        row = connection.execute("SELECT text FROM memories WHERE id=?", (memory_id,)).fetchone()
        if row is None:
            return False
        connection.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        connection.execute(
            "INSERT INTO memory_events (memory_id, event, old_text, new_text, at)"
            " VALUES (?,?,?,?,?)",
            (memory_id, "DELETE", row["text"], None, time.time()),
        )
        connection.commit()
        return True

    def delete_all(self, *, user_id) -> int:
        connection = self.connect()
        rows = connection.execute(
            "SELECT id, text FROM memories WHERE user_id=?", (user_id,)).fetchall()
        now = time.time()
        for row in rows:
            connection.execute(
                "INSERT INTO memory_events (memory_id, event, old_text, new_text, at)"
                " VALUES (?,?,?,?,?)",
                (row["id"], "DELETE", row["text"], None, now),
            )
        connection.execute("DELETE FROM memories WHERE user_id=?", (user_id,))
        connection.commit()
        return len(rows)

    # -- reads -------------------------------------------------------------

    def get_all(self, *, user_id, limit=200):
        connection = self.connect()
        return [dict(row) for row in connection.execute(
            "SELECT * FROM memories WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, int(limit)),
        ).fetchall()]

    def recent(self, *, user_id, run_id=None, limit=20):
        """The dedup reference handed to the extraction prompt."""
        connection = self.connect()
        if run_id:
            rows = connection.execute(
                "SELECT text FROM memories WHERE user_id=? AND run_id=?"
                " ORDER BY created_at DESC LIMIT ?", (user_id, run_id, int(limit))).fetchall()
        else:
            rows = connection.execute(
                "SELECT text FROM memories WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, int(limit))).fetchall()
        return [row["text"] for row in rows]

    def history(self, memory_id):
        connection = self.connect()
        return [dict(row) for row in connection.execute(
            "SELECT * FROM memory_events WHERE memory_id=? ORDER BY at", (memory_id,)).fetchall()]

    def count(self, *, user_id=None) -> int:
        connection = self.connect()
        if user_id is None:
            return connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        return connection.execute(
            "SELECT COUNT(*) FROM memories WHERE user_id=?", (user_id,)).fetchone()[0]

    def stale_vector_count(self, *, model) -> int:
        """Rows embedded by a different model than the active one. They are
        excluded from the vector leg rather than compared across vector spaces."""
        connection = self.connect()
        return connection.execute(
            "SELECT COUNT(*) FROM vectors WHERE model<>?", (model,)).fetchone()[0]

    # -- retrieval ---------------------------------------------------------

    def _bm25_leg(self, query, *, user_id, depth=LEG_DEPTH):
        """Ranked memory ids by keyword match. Empty list on any FTS problem --
        the caller degrades to the vector leg rather than failing the turn."""
        match = fts_query(query)
        if not match:
            return []
        try:
            rows = self.connect().execute(
                "SELECT m.id AS id FROM memories_fts f"
                " JOIN memories m ON m.rowid = f.rowid"
                " WHERE memories_fts MATCH ? AND m.user_id = ?"
                " ORDER BY f.rank LIMIT ?",
                (match, user_id, int(depth)),
            ).fetchall()
        except sqlite3.Error:
            return []
        return [row["id"] for row in rows]

    def _vector_leg(self, embedding, *, user_id, model, depth=LEG_DEPTH):
        """Ranked memory ids by cosine similarity.

        ponytail: a full scan of the user's vectors per recall, O(n) in rows.
        Comfortable to the low tens of thousands on this workload (short notes,
        768 dims); sqlite-vec is the upgrade path if that is ever exceeded.
        """
        if not embedding:
            return []
        query_vector = normalise(embedding)
        rows = self.connect().execute(
            "SELECT v.memory_id AS id, v.vec AS vec, v.dim AS dim FROM vectors v"
            " JOIN memories m ON m.id = v.memory_id"
            " WHERE m.user_id = ? AND v.model = ?",
            (user_id, model),
        ).fetchall()
        scored = []
        for row in rows:
            # A dimension mismatch means the embedder changed under the same
            # name. Skipping is the honest answer; zip() would silently compare
            # truncated vectors and return a confident wrong score.
            if row["dim"] != len(query_vector):
                continue
            similarity = dot(query_vector, _unpack(row["vec"]))
            # Only positive similarity is evidence. Without this the leg reports
            # its top `depth` regardless of whether anything actually matched, so
            # on a small store an unrelated memory takes vector rank 1 and RRF
            # credits it as a real hit. Zero or negative similarity is "no
            # signal", not "the best of a bad set".
            #
            # Deliberately a floor at zero rather than a tuned threshold: real
            # embedders put unrelated text anywhere from 0.2 to 0.6 depending on
            # the model, so any higher constant would encode an assumption about
            # one model. Relevance filtering above this belongs to min_score.
            if similarity <= 0.0:
                continue
            scored.append((similarity, row["id"]))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [memory_id for _score, memory_id in scored[:int(depth)]]

    def search(self, query, *, user_id, embedding=None, model="", limit=5,
               rrf_k=DEFAULT_RRF_K, min_score=0.0, record_access=True):
        """Hybrid BM25 + vector retrieval fused by RRF. Returns memory dicts with
        a `score` and the rank each leg gave them, newest-relevant first."""
        keyword_ranking = self._bm25_leg(query, user_id=user_id)
        vector_ranking = self._vector_leg(embedding, user_id=user_id, model=model)
        if not keyword_ranking and not vector_ranking:
            return []

        fused = {}
        for ranking, leg in ((keyword_ranking, "bm25"), (vector_ranking, "vector")):
            for position, memory_id in enumerate(ranking, start=1):
                entry = fused.setdefault(memory_id, {"score": 0.0, "bm25_rank": None,
                                                     "vector_rank": None})
                entry["score"] += 1.0 / (rrf_k + position)
                entry[f"{leg}_rank"] = position

        placeholders = ",".join("?" * len(fused))
        rows = {
            row["id"]: dict(row)
            for row in self.connect().execute(
                f"SELECT * FROM memories WHERE id IN ({placeholders})", tuple(fused)
            ).fetchall()
        }

        now = time.time()
        results = []
        for memory_id, entry in fused.items():
            row = rows.get(memory_id)
            if row is None:
                continue
            if entry["score"] < min_score:
                continue
            row.update(score=entry["score"], bm25_rank=entry["bm25_rank"],
                       vector_rank=entry["vector_rank"])
            results.append(row)

        # Recency is a tie-break, never a multiplier. Adjacent RRF ranks differ by
        # about 1.6% at k=60, so any boost large enough to be worth having is also
        # large enough to reorder genuinely better matches -- an earlier version
        # scaled by up to 1.4x and let a frequently-read irrelevant memory beat a
        # directly relevant one. As a tie-break it can only decide exact ties,
        # where preferring the newer fact is right: it is the one that supersedes.
        results.sort(key=lambda row: (row["score"], row["created_at"]), reverse=True)
        results = results[:int(limit)]
        if record_access and results:
            self._record_access([row["id"] for row in results], now)
        return results

    def _record_access(self, memory_ids, now):
        connection = self.connect()
        connection.executemany(
            "UPDATE memories SET access_count = access_count + 1, last_accessed_at = ?"
            " WHERE id = ?",
            [(now, memory_id) for memory_id in memory_ids],
        )
        connection.commit()
