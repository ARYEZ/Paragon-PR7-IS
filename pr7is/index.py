"""The file index and its search (no Qt), Everything's way.

The index is flat: entry i has a name, a parent entry (-1 for a drive), a size, a modified time and a folder flag.
Paths are rebuilt from the parent links, so renaming a folder renames everything inside it at once.

Searching 1.6 million names on every key press can't loop in Python, so every name, lower-cased, sits in one long
string, a name per line (`blob`): Python's `str.find` and `re` scan it at C speed, and a match's position becomes an
entry through a sorted table of line starts (bisect). Only the candidates that survive the first word are checked
against the rest in Python.

Everything's syntax: words (all must match the name, any order), "quoted phrases", a|b (either), !word (not),
* and ? (wildcards: they match the whole name), a word with \\ matches the path, and the functions ext:pdf;docx,
size:>10mb (<, >=, <=, =, a..b, empty, tiny/small/medium/large/huge/gigantic), dm:today / yesterday / thisweek /
lastweek / thismonth / thisyear / 2026-11-24 / >2026-11-01 / 2026-11-01..2026-11-20, file:, folder:."""

from __future__ import annotations

import bisect
import heapq
import datetime
import fnmatch
import re
import time
from dataclasses import dataclass, field

from . import kinds

UNITS = {"": 1, "b": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3, "tb": 1024 ** 4}
SIZE_WORDS = {"empty": (0, 0), "tiny": (1, 10 * 1024), "small": (10 * 1024 + 1, 100 * 1024),
              "medium": (100 * 1024 + 1, 1024 ** 2), "large": (1024 ** 2 + 1, 16 * 1024 ** 2),
              "huge": (16 * 1024 ** 2 + 1, 128 * 1024 ** 2), "gigantic": (128 * 1024 ** 2 + 1, None)}


class Index:
    def __init__(self):
        self.names: list[str | None] = []      # None: removed
        self.parent: list[int] = []
        self.size: list[int] = []
        self.mtime: list[float] = []
        self.isdir = bytearray()
        self.keys: dict = {}                   # (volume, record) -> entry, for the change journal
        self._key_of: dict[int, tuple] = {}
        # The search text and name order cover entries [0, _built); changes never rebuild them (1M+ names take
        # seconds): new and renamed entries go into _recent and are checked one by one until compact() catches up.
        self._blob: str | None = None
        self._starts: list[int] = []
        self._rank: list[int] | None = None
        self._built = 0
        self._recent: set[int] = set()
        self._touched: list[int] | None = None   # while a compact() builds: what changed meanwhile
        self._kids: dict[int, set[int]] | None = None
        self.removed = 0

    # ---------------------------------------------------------------- changing
    def add(self, name: str, parent: int, size: int = 0, mtime: float = 0.0, is_dir: bool = False, key=None) -> int:
        i = len(self.names)
        self.names.append(name)
        self.parent.append(parent)
        self.size.append(size)
        self.mtime.append(mtime)
        self.isdir.append(1 if is_dir else 0)
        if key is not None:
            self.keys[key] = i
            self._key_of[i] = key
        if self._kids is not None and parent >= 0:
            self._kids.setdefault(parent, set()).add(i)
        self._changed(i)
        return i

    def remove(self, i: int):
        """An entry and everything inside it (a deleted folder)."""
        todo = {i}
        if self.isdir[i]:
            todo |= set(self.descendants(i))
        for j in todo:
            if self.names[j] is not None:
                self.names[j] = None
                self.removed += 1
                k = self._key_of.pop(j, None)
                if k is not None and self.keys.get(k) == j:
                    del self.keys[k]
        # removed entries stay in the search text: every search drops entries whose name is None

    def rename(self, i: int, name: str | None = None, parent: int | None = None):
        if name is not None:
            self.names[i] = name
        if parent is not None and parent != self.parent[i]:
            if self._kids is not None:
                self._kids.get(self.parent[i], set()).discard(i)
                self._kids.setdefault(parent, set()).add(i)
            self.parent[i] = parent
        self._changed(i)

    def update(self, i: int, size: int | None = None, mtime: float | None = None):
        if size is not None:
            self.size[i] = size
        if mtime is not None:
            self.mtime[i] = mtime

    def _changed(self, i: int):
        if self._touched is not None:              # a compact is building from a copy: it doesn't know this
            self._touched.append(i)
        if self._blob is not None:
            self._recent.add(i)

    def descendants(self, i: int) -> list[int]:
        if self._kids is None:                     # built once, then kept up to date by add / rename
            kids: dict[int, set[int]] = {}
            for j, p in enumerate(self.parent):
                if p >= 0:
                    kids.setdefault(p, set()).add(j)
            self._kids = kids
        out, stack = [], [i]
        while stack:
            for c in self._kids.get(stack.pop(), ()):
                if self.names[c] is not None:
                    out.append(c)
                    stack.append(c)
        return out

    def drop_volume(self, root: int):
        self.remove(root)

    # ---------------------------------------------------------------- reading
    def __len__(self) -> int:
        return len(self.names) - self.removed

    def path(self, i: int) -> str:
        """The full path: "C:\\Users\\me\\a.txt". Drives are entries named "C:"."""
        parts = []
        seen = 0
        while i >= 0 and seen < 4096:
            parts.append(self.names[i] or "")
            i = self.parent[i]
            seen += 1
        parts.reverse()
        root = parts[0]
        drive = root.endswith(":")                    # "C:" -> C:\...
        if len(parts) == 1:
            return root + "\\" if drive else root
        # a Windows folder root ("C:\\Users\\me") joins with "\\" too; only a Linux one ("/home/me") keeps "/"
        sep = "\\" if drive or "\\" in root else "/"
        return root.rstrip("\\/") + sep + sep.join(parts[1:])

    def folder(self, i: int) -> str:
        p = self.parent[i]
        return self.path(p) if p >= 0 else ""

    def alive(self, i: int) -> bool:
        if self.names[i] is None:
            return False
        seen = 0
        while i >= 0 and seen < 4096:                # a folder removed above it removes it too
            if self.names[i] is None:
                return False
            i = self.parent[i]
            seen += 1
        return True

    @staticmethod
    def build(names: list) -> tuple[str, list[int], list[int]]:
        """(search text, where each name starts in it, each entry's place in name order) for these names. Takes
        seconds for a million names: compact() runs it on a copy, outside the engine's lock."""
        parts, starts, pos = [], [], 0
        for n in names:
            s = (n or "\0").casefold()                # removed entries can't match anything
            starts.append(pos)
            parts.append(s)
            pos += len(s) + 1
        order = sorted(range(len(names)), key=lambda i: (parts[i], i))
        rank = [0] * len(order)
        for r, i in enumerate(order):
            rank[i] = r
        return "\n".join(parts), starts, rank

    def compact_start(self) -> list:
        """The names to build from (a copy); changes from now on are remembered for compact_finish."""
        self._touched = []
        return list(self.names)

    def compact_finish(self, names: list, built: tuple):
        self._blob, self._starts, self._rank = built
        self._built = len(names)
        self._recent = {i for i in self._touched if i < self._built} | set(range(self._built, len(self.names)))
        self._touched = None

    def compact(self):
        """Build the search text and name order again now (in one go: tests, and right after a full build)."""
        names = self.compact_start()
        self.compact_finish(names, self.build(names))

    def _ensure(self):
        if self._blob is None:
            self.compact()

    @property
    def blob(self) -> str:
        self._ensure()
        return self._blob

    @property
    def recent(self) -> int:
        """How many entries are checked one by one (added or renamed since the last compact)."""
        return len(self._recent)

    def rank(self) -> list[int]:
        """Each entry's place in name order (folders and files together, as Everything sorts) at the last compact."""
        self._ensure()
        return self._rank

    def _entry_at(self, pos: int) -> int:
        return bisect.bisect_right(self._starts, pos) - 1

    # ---------------------------------------------------------------- searching
    def find_word(self, word: str) -> set[int]:
        """Entries whose name holds `word` (already case-folded): the search text, then the recent entries."""
        blob = self.blob
        out = set()
        start = 0
        while True:
            at = blob.find(word, start)
            if at < 0:
                break
            out.add(self._entry_at(at))
            start = at + 1
        return self._with_recent(out, lambda n: word in n)

    def find_glob(self, pattern: str) -> set[int]:
        """Entries whose whole name matches a wildcard pattern (* ?)."""
        blob = self.blob
        rx = glob_regex(pattern)
        out = {self._entry_at(m.start()) for m in re.finditer(rx, blob, re.M)}
        whole = re.compile(rx)
        return self._with_recent(out, lambda n: whole.match(n) is not None)

    def _with_recent(self, found: set[int], test) -> set[int]:
        if not self._recent:
            return found
        found -= self._recent                          # their text in the blob may be an old name
        names = self.names
        found.update(i for i in self._recent if names[i] is not None and test(names[i].casefold()))
        return found

    def search(self, query: str, kind: str = "everything", sort: str = "name", descending: bool = False) -> list[int]:
        q = parse(query)
        if kind and kind != "everything":
            q.kinds = kinds.CATEGORIES.get(kind)
            if kind == "folder":
                q.only = "folder"
        ids, used = self._candidates(q)
        if used is not None:                          # its matches are the candidates: no need to check it again
            q.words = q.words[:used] + q.words[used + 1:]
        names = self.names
        if q.words or q.exts is not None or q.kinds is not None or q.size or q.dm or q.only:
            out = [i for i in ids if self._keep(i, q)]
        else:
            out = [i for i in ids if names[i] is not None]
        return self.sort(out, sort, descending)

    def _candidates(self, q: "Query"):
        for n, alts in enumerate(q.words):            # the first plain word narrows by blob scan
            if all(not a.startswith("!") for a in alts):
                found = set()
                for a in alts:
                    found |= self._match_term(a)
                exact = all("\\" not in a and "/" not in a for a in alts)   # path words still need their check
                return found, (n if exact else None)
        return range(len(self.names)), None

    def _match_term(self, term: str) -> set[int]:
        if "\\" in term or "/" in term:
            last = [p for p in re.split(r"[\\/]", term) if p]
            return self.find_word(last[-1].replace("*", "").replace("?", "")) if last else set(range(len(self.names)))
        if "*" in term or "?" in term:
            return self.find_glob(term)
        return self.find_word(term)

    def _term_ok(self, i: int, term: str) -> bool:
        name = (self.names[i] or "").casefold()
        if "\\" in term or "/" in term:
            path = self.path(i).casefold().replace("/", "\\")
            t = term.replace("/", "\\")
            if "*" in t or "?" in t:
                return fnmatch.fnmatchcase(path, "*" + t + "*")
            return t in path
        if "*" in term or "?" in term:
            return fnmatch.fnmatchcase(name, term)
        return term in name

    def _keep(self, i: int, q: "Query") -> bool:
        if self.names[i] is None:                     # remove() marks everything inside a removed folder too
            return False
        d = self.isdir[i]
        if q.only == "file" and d or q.only == "folder" and not d:
            return False
        for alts in q.words:
            hit = False
            for a in alts:
                if a.startswith("!"):
                    if not self._term_ok(i, a[1:]):
                        hit = True
                        break
                elif self._term_ok(i, a):
                    hit = True
                    break
            if not hit:
                return False
        if q.exts is not None or q.kinds is not None:
            if d:
                return False
            ext = kinds.ext_of(self.names[i])
            if q.exts is not None and ext not in q.exts:
                return False
            if q.kinds is not None and ext not in q.kinds:
                return False
        if q.size is not None:
            if d:
                return False
            lo, hi = q.size
            s = self.size[i]
            if (lo is not None and s < lo) or (hi is not None and s > hi):
                return False
        if q.dm is not None:
            lo, hi = q.dm
            t = self.mtime[i]
            if (lo is not None and t < lo) or (hi is not None and t >= hi):
                return False
        return True

    def sort(self, ids: list[int], by: str = "name", descending: bool = False) -> list[int]:
        if by == "size":
            key = self.size.__getitem__
        elif by == "dm":
            key = self.mtime.__getitem__
        elif by == "path":
            key = (lambda i: (self.folder(i).casefold(), (self.names[i] or "").casefold(), i))
        else:
            return self._by_name(ids, descending)
        return sorted(ids, key=key, reverse=descending)

    def _by_name(self, ids: list[int], descending: bool) -> list[int]:
        """Name order: the order kept at the last compact; recent entries (whose place it doesn't know) merged in."""
        rank = self.rank()
        recent = self._recent
        names = self.names

        def by_text(i):
            return ((names[i] or "").casefold(), i)
        if not recent or len(ids) <= 20_000:
            if not recent or not any(i in recent for i in ids):
                return sorted(ids, key=rank.__getitem__, reverse=descending)
            return sorted(ids, key=by_text, reverse=descending)
        old = sorted((i for i in ids if i not in recent), key=rank.__getitem__)
        new = sorted((i for i in ids if i in recent), key=by_text)
        out = list(heapq.merge(old, new, key=by_text))
        return out[::-1] if descending else out

    def row(self, i: int) -> dict:
        return {"name": self.names[i], "folder": self.folder(i), "path": self.path(i), "size": self.size[i],
                "mtime": self.mtime[i], "dir": bool(self.isdir[i])}


def glob_regex(pattern: str) -> str:
    """A wildcard pattern as a regex for one blob line: * and ? never cross into the next name. (Written here, not
    fnmatch.translate: its output changed in Python 3.13.)"""
    out = []
    for ch in pattern:
        out.append("[^\n]*" if ch == "*" else "[^\n]" if ch == "?" else re.escape(ch))
    return "^" + "".join(out) + "$"


# -------------------------------------------------------------------- the query language
@dataclass
class Query:
    words: list[list[str]] = field(default_factory=list)     # AND of ORs; "!x" is NOT x
    exts: set[str] | None = None
    kinds: set[str] | None = None
    size: tuple | None = None
    dm: tuple | None = None
    only: str = ""                                             # "file" | "folder" | ""


def _tokens(text: str) -> list[str]:
    """Split on spaces, keeping "quoted phrases" (with their prefix: !"a b", ext:"x") together."""
    out, cur, quoted = [], "", False
    for ch in text:
        if ch == '"':
            quoted = not quoted
        elif ch.isspace() and not quoted:
            if cur:
                out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def _bytes(text: str) -> int | None:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(b|kb|mb|gb|tb)?", text.strip().lower())
    if not m:
        return None
    return int(float(m.group(1)) * UNITS[m.group(2) or ""])


def parse_size(text: str) -> tuple | None:
    t = text.lower()
    if t in SIZE_WORDS:
        return SIZE_WORDS[t]
    if ".." in t:
        a, b = t.split("..", 1)
        return _bytes(a), _bytes(b)
    for op in (">=", "<=", ">", "<", "="):
        if t.startswith(op):
            n = _bytes(t[len(op):])
            if n is None:
                return None
            return {">=": (n, None), "<=": (None, n), ">": (n + 1, None), "<": (None, n - 1), "=": (n, n)}[op]
    n = _bytes(t)
    return (n, n) if n is not None else None


def _day(d: datetime.date) -> float:
    return time.mktime(d.timetuple())


def parse_date(text: str, now: float | None = None) -> tuple | None:
    """dm:today ... -> (from, to) in seconds (to excluded)."""
    today = datetime.date.fromtimestamp(now if now is not None else time.time())
    t = text.lower()
    one = datetime.timedelta(days=1)
    week0 = today - datetime.timedelta(days=today.weekday())
    named = {"today": (today, today + one), "yesterday": (today - one, today),
             "thisweek": (week0, week0 + 7 * one), "lastweek": (week0 - 7 * one, week0),
             "thismonth": (today.replace(day=1), (today.replace(day=28) + 4 * one).replace(day=1)),
             "thisyear": (today.replace(month=1, day=1), today.replace(year=today.year + 1, month=1, day=1))}
    if t in named:
        a, b = named[t]
        return _day(a), _day(b)

    def date(s):
        try:
            return datetime.date.fromisoformat(s)
        except ValueError:
            return None
    if ".." in t:
        a, b = (date(x) for x in t.split("..", 1))
        return (_day(a) if a else None, _day(b + one) if b else None)
    for op in (">=", "<=", ">", "<"):
        if t.startswith(op):
            d = date(t[len(op):])
            if d is None:
                return None
            return {">=": (_day(d), None), ">": (_day(d + one), None), "<=": (None, _day(d + one)),
                    "<": (None, _day(d))}[op]
    d = date(t)
    return (_day(d), _day(d + one)) if d else None


def parse(text: str, now: float | None = None) -> Query:
    q = Query()
    for tok in _tokens(text):
        low = tok.casefold()
        if low.startswith("ext:"):
            exts = {e.strip(". ") for e in low[4:].split(";") if e.strip(". ")}
            q.exts = exts if q.exts is None else q.exts & exts
        elif low.startswith("size:"):
            q.size = parse_size(low[5:])
        elif low.startswith(("dm:", "datemodified:")):
            q.dm = parse_date(low.split(":", 1)[1], now)
        elif low in ("file:", "files:"):
            q.only = "file"
        elif low in ("folder:", "folders:"):
            q.only = "folder"
        else:
            alts = [a for a in low.split("|") if a and a != "!"]
            if alts:
                q.words.append(alts)
    return q
