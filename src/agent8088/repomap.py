"""A ranked map of the working repository: what is defined where, and what matters.

The shape of this feature -- tree-sitter tag queries, a graph of references
between files, PageRank over that graph, and a token budget -- follows Aider's
`repomap.py` (Apache-2.0, https://github.com/Aider-AI/aider). The tag query
files under `queries/` are vendored from Aider and from Continue
(Apache-2.0, https://github.com/continuedev/continue); see queries/NOTICE.

Three things are deliberately not Aider's:

Ranking runs on a few dozen lines of power iteration rather than networkx.
`nx.pagerank` needs numpy and scipy, which is 132 MB of wheels to compute an
eigenvector over a graph that has one node per source file. The graphs here are
small enough that the naive loop converges in milliseconds.

The map is produced on demand, for a tool call, rather than injected into every
prompt. Aider has no tool loop and must decide what the model sees up front; a
tool-using agent can ask when it wants to orient. It is also the variant that
other projects abandoned -- OpenHands withdrew its always-on port (PR #2248,
"does not help much") and Zed deleted its symbol ranking -- so the always-on
version is the one with a track record of not paying for itself.

A definition counts as distinctive when exactly one file defines it, not when it
merely looks deliberate. See `_distinctive`.
"""
from __future__ import annotations

import fnmatch
import os
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

QUERIES = Path(__file__).resolve().parent / "queries"

# One entry per vendored query file. A suffix absent here is not mapped rather
# than guessed: a wrong grammar parses to a tree of errors and yields noise.
SUFFIX_LANGUAGE = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".sh": "bash", ".bash": "bash",
    ".php": "php",
}

# tsx has its own grammar but the same node names as typescript.
QUERY_ALIAS = {"tsx": "typescript"}

DIRECTORY_SKIPS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".idea", ".vscode",
    "site-packages", "vendor", "third_party",
})

# A type is the thing you orient by. Query files disagree on what to call one,
# so match on the set rather than on any single grammar's vocabulary.
TYPE_SYMBOLS = frozenset({
    "class", "struct", "interface", "type", "enum", "trait", "protocol", "module",
})

# Tree-sitter tag queries distinguish these reference kinds. PageRank's damping
# already decays rank over successive hops; these weights say which direct link
# is stronger.
REFERENCE_WEIGHTS = {
    "call": 1.0,
    "send": 1.0,
    "method": 1.0,
    "inherit": 0.9,
    "implementation": 0.9,
    "module": 0.5,
}
DETAIL_LEVELS = frozenset({"minimal", "standard"})

MAX_SOURCE_BYTES = 1024 * 1024
MAX_FILES = 5000
LINE_WIDTH = 100


@dataclass(frozen=True)
class Tag:
    rel: str
    line: int
    name: str
    kind: str  # "def" or "ref"
    symbol: str = ""  # for definitions: "class", "function", "constant", ...
    relation: str = ""  # for references: "call", "module", "inherit", ...


class RepoMapUnavailable(RuntimeError):
    """tree-sitter is not installed, so no map can be built."""


def language_for(path):
    return SUFFIX_LANGUAGE.get(Path(path).suffix.lower())


@lru_cache(maxsize=None)
def _parser_and_query(language):
    try:
        from tree_sitter import Query, QueryCursor
        from tree_sitter_language_pack import get_language, get_parser
    except ImportError as exc:
        raise RepoMapUnavailable(
            "repo_map needs tree-sitter. Install agent8088[repomap] and restart."
        ) from exc
    scm = QUERIES / f"{QUERY_ALIAS.get(language, language)}-tags.scm"
    if not scm.is_file():
        return None
    try:
        return (get_parser(language), Query(get_language(language), scm.read_text(encoding="utf-8")),
                QueryCursor)
    except Exception:
        # A grammar the pack ships but whose nodes the vendored query does not
        # name: one language goes dark, the rest of the map is still worth having.
        return None


def extract_tags(rel, source):
    """Definitions and references in one file, in source order."""
    language = language_for(rel)
    if not language:
        return []
    loaded = _parser_and_query(language)
    if loaded is None:
        return []
    parser, query, cursor = loaded
    try:
        tree = parser.parse(source.encode("utf-8", errors="replace"))
        captures = cursor(query).captures(tree.root_node)
    except (RecursionError, ValueError):
        return []
    tags = []
    for label, nodes in captures.items():
        if label.startswith("name.definition"):
            kind = "def"
        elif label.startswith("name.reference"):
            kind = "ref"
        else:
            continue
        label_kind = label.rpartition(".")[2]
        symbol = label_kind if kind == "def" else ""
        relation = label_kind if kind == "ref" else ""
        for node in nodes:
            try:
                name = node.text.decode("utf-8", errors="replace")
            except AttributeError:
                continue
            if name:
                tags.append(Tag(rel, node.start_point[0] + 1, name, kind, symbol, relation))
    tags.sort(key=lambda t: (t.line, t.kind, t.name))
    return tags


def _ignore_rules(root):
    """Directory-scoped .gitignore patterns, enough for the common cases.

    Not a gitignore implementation: no negation, no `**`. It exists so a
    generated `build/` or `dist/` does not crowd real code out of the budget,
    and being approximate costs a stray entry, never a wrong answer. Repositories
    under git never reach it -- git itself does the filtering there.
    """
    rules = []
    ignore = root / ".gitignore"
    if not ignore.is_file():
        return rules
    try:
        lines = ignore.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return rules
    for line in lines:
        line = line.strip()
        if line and not line.startswith(("#", "!")):
            rules.append(line.rstrip("/"))
    return rules


def _ignored(name, rules):
    return any(fnmatch.fnmatchcase(name, rule) for rule in rules)


def _git_tracked(root):
    """The repository's own files, per git -- the only authority that gets
    .gitignore exactly right, including nested and global ignore files."""
    try:
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--other", "--exclude-standard", "-z"],
            capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    if listed.returncode != 0:
        return None
    names = listed.stdout.decode("utf-8", errors="replace").split("\0")
    return [root / name for name in names if name]


def _is_virtualenv(path):
    return (path / "pyvenv.cfg").is_file()


def _vendored(relative):
    """Whether a path lies inside a dependency tree rather than the project.

    git lists untracked-but-not-ignored files, so a virtualenv nobody thought to
    add to .gitignore still arrives here and would otherwise bury the project's
    own code under its dependencies'.
    """
    return any(part in DIRECTORY_SKIPS for part in relative.parts)


def iter_source_files(root):
    """Source files under root, skipping vendored, generated and hidden trees."""
    root = Path(root)
    tracked = _git_tracked(root)
    if tracked is not None:
        found = []
        for path in tracked:
            if not language_for(path.name) or _vendored(path.relative_to(root)):
                continue
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
                    continue
            except OSError:
                continue
            found.append(path)
            if len(found) >= MAX_FILES:
                break
        return sorted(found)

    rules = _ignore_rules(root)
    found = []
    for parent, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(
            d for d in directories
            if d not in DIRECTORY_SKIPS and not _ignored(d, rules)
            and not _is_virtualenv(Path(parent) / d)
        )
        for name in sorted(files):
            if not language_for(name) or _ignored(name, rules):
                continue
            path = Path(parent) / name
            try:
                if path.is_symlink() or path.stat().st_size > MAX_SOURCE_BYTES:
                    continue
            except OSError:
                continue
            found.append(path)
            if len(found) >= MAX_FILES:
                return found
    return found


def collect_tags(root):
    root = Path(root)
    tags = []
    for path in iter_source_files(root):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        tags.extend(extract_tags(path.relative_to(root).as_posix(), source))
    return tags


def pagerank(nodes, edges, personalization=None, damping=0.85, iterations=40):
    """Power iteration over a weighted directed graph.

    Replaces `nx.pagerank`, whose only implementation pulls in numpy and scipy.
    """
    nodes = list(dict.fromkeys(nodes))
    if not nodes:
        return {}
    index = set(nodes)
    outgoing = defaultdict(list)
    out_weight = defaultdict(float)
    for source, target, weight in edges:
        if source not in index or target not in index or weight <= 0:
            continue
        outgoing[source].append((target, weight))
        out_weight[source] += weight

    if personalization:
        total = sum(max(0.0, personalization.get(n, 0.0)) for n in nodes)
    else:
        total = 0.0
    if total > 0:
        seed = {n: max(0.0, personalization.get(n, 0.0)) / total for n in nodes}
    else:
        seed = {n: 1.0 / len(nodes) for n in nodes}

    rank = dict(seed)
    for _ in range(iterations):
        nxt = {n: 0.0 for n in nodes}
        dangling = sum(rank[n] for n in nodes if out_weight[n] <= 0)
        for node, targets in outgoing.items():
            share = rank[node] / out_weight[node]
            for target, weight in targets:
                nxt[target] += share * weight
        delta = 0.0
        for node in nodes:
            updated = (1.0 - damping) * seed[node] + damping * (nxt[node] + dangling * seed[node])
            delta += abs(updated - rank[node])
            nxt[node] = updated
        rank = nxt
        if delta < 1e-9:
            break
    total = sum(rank.values())
    return {n: rank[n] / total for n in nodes} if total else rank


def _is_test_path(rel):
    lowered = rel.lower()
    name = lowered.rpartition("/")[2]
    return (name.startswith("test_") or "_test." in name or ".spec." in name
            or any(part in ("tests", "test", "__tests__", "spec")
                   for part in lowered.split("/")[:-1]))


def _distinctive(name, definer_count):
    """A name that identifies one thing in this repository.

    Aider counts any long snake/camelCase name as distinctive. That reads as a
    signal in a mixed corpus and as noise in Python, where snake_case IS the
    convention -- it fired on `on_token` and `run_agent` and pushed callbacks to
    the top of the map. Requiring the name to be defined exactly once restores
    what the rule was reaching for: not "looks deliberate" but "means one thing".
    """
    return definer_count == 1 and len(name) >= 8


def rank_symbols(tags, focus=(), mentioned=()):
    """Every definition in the repository, most worth showing first.

    Returns (path, name) pairs. Definitions nothing references still appear --
    they rank below referenced ones, but a symbol absent from the map reads as a
    symbol absent from the repository.
    """
    focus = {str(f) for f in focus}
    mentioned = {str(m) for m in mentioned}
    definers = defaultdict(set)
    referencers = defaultdict(lambda: defaultdict(float))
    symbol_kinds = {}
    files = set()
    for tag in tags:
        files.add(tag.rel)
        if tag.kind == "def":
            definers[tag.name].add(tag.rel)
            symbol_kinds.setdefault((tag.rel, tag.name), tag.symbol)
        else:
            referencers[tag.name][tag.rel] += REFERENCE_WEIGHTS.get(tag.relation, 1.0)
    if not files:
        return []

    # One weight per (referencer, definer, name), used both to rank files and to
    # score individual symbols. Computing it once is what keeps the two
    # consistent: weighting the graph but not the symbols lets a name every file
    # defines -- `get`, `append` -- win the map back on raw reference count.
    links = []
    edges = []
    for name, defining in definers.items():
        multiplier = 1.0
        if name in mentioned:
            multiplier *= 10.0
        if _distinctive(name, len(defining)):
            multiplier *= 10.0
        if any(symbol_kinds.get((rel, name)) in TYPE_SYMBOLS for rel in defining):
            multiplier *= 3.0
        if name.startswith("_"):
            multiplier *= 0.1
        if len(defining) > 5:
            # A name almost every file defines carries no information about which
            # file matters -- `run`, `get`, `handler`.
            multiplier *= 0.1
        uses = referencers.get(name, {})
        for referencer, reference_weight in uses.items():
            weight = multiplier * (reference_weight ** 0.5)
            if referencer in focus:
                weight *= 50.0
            for definer in defining:
                if definer != referencer:
                    links.append((referencer, definer, name, weight))
                    edges.append((referencer, definer, weight))
        if not uses:
            for definer in defining:
                edges.append((definer, definer, 0.1))

    nodes = sorted(files)
    personalization = {n: (100.0 if n in focus else 1.0) for n in nodes}
    ranks = pagerank(nodes, edges, personalization)

    # Spread each file's rank over the identifiers it is consulted for, which is
    # what turns a ranking of files into a ranking of symbols.
    #
    # Tests are worth mapping but not worth leading with: a test double named
    # `run_agent` is not what someone orienting in the repository is looking for.
    # Naming a test file in `focus` puts it back on equal footing.
    scores = defaultdict(float)
    for referencer, definer, name, weight in links:
        if _is_test_path(definer) and definer not in focus:
            weight *= 0.3
        scores[(definer, name)] += ranks.get(referencer, 0.0) * weight

    ranked = sorted(scores, key=lambda key: (-scores[key], key[0], key[1]))
    seen = set(ranked)
    rest = sorted(
        ((rel, name) for name, defining in definers.items() for rel in defining
         if (rel, name) not in seen),
        key=lambda key: (-ranks.get(key[0], 0.0), key[0], key[1]),
    )
    return ranked + rest


def build_map(root, budget_tokens=1024, focus=(), mentioned=(), detail_level="standard"):
    """A budget-fitting outline of the repository, most important symbols first."""
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"{root} is not a directory")
    detail_level = str(detail_level).strip().lower()
    if detail_level not in DETAIL_LEVELS:
        raise ValueError("detail_level must be 'minimal' or 'standard'")
    tags = collect_tags(root)
    ranked = rank_symbols(tags, focus=focus, mentioned=mentioned)
    if not ranked:
        return "Repository map: no source files found in a language this map understands."

    lines_by_file = {}

    def source_line(rel, number):
        if rel not in lines_by_file:
            try:
                lines_by_file[rel] = (root / rel).read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                lines_by_file[rel] = []
        body = lines_by_file[rel]
        return body[number - 1].rstrip() if 0 < number <= len(body) else ""

    definition_lines = defaultdict(dict)
    for tag in tags:
        if tag.kind == "def":
            definition_lines[tag.rel].setdefault(tag.name, tag.line)

    # Grow the map one symbol at a time in rank order and stop at the budget,
    # rather than binary-searching a count as Aider does: rendering is cheap and
    # this way the budget is an exact ceiling instead of an approximation.
    budget_chars = max(0, int(budget_tokens)) * 4
    chosen = defaultdict(list)
    rendered = ""
    for rel, name in ranked:
        if detail_level == "minimal" and rel in chosen:
            continue
        line = definition_lines.get(rel, {}).get(name)
        if line is None:
            continue
        text = source_line(rel, line).strip()
        if not text:
            continue
        candidate = dict(chosen)
        candidate[rel] = sorted(set(chosen[rel]) | {(line, text[:LINE_WIDTH])})
        attempt = _render(candidate)
        if len(attempt) > budget_chars:
            break
        chosen[rel] = candidate[rel]
        rendered = attempt
    return rendered or "Repository map: token budget too small to show any symbol."


def _render(chosen):
    blocks = []
    for rel in sorted(chosen):
        entries = sorted(chosen[rel])
        if not entries:
            continue
        body = [f"{rel}:"]
        previous = 0
        for line, text in entries:
            if previous and line > previous + 1:
                body.append("  ...")
            body.append(f"  {line}: {text}")
            previous = line
        blocks.append("\n".join(body))
    return "\n\n".join(blocks)
