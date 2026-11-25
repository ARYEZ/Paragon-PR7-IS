"""The index's keeper (no Qt): builds it, keeps it up to date, answers searches in pages, and saves it.

- NTFS drives, with administrator rights (the service has them): the MFT (mft.py) in one long read, then the drive's
  change journal (usn.py) every second. The journal id and next USN are saved with the index, so a restart catches
  up from where it stopped instead of reading the whole MFT again (unless the journal was reset).
- Other drives (exFAT, FAT, network) and any drive without the rights: a folder walk (os.scandir; junctions and
  links not followed), refreshed by `rebuild`.
- "Leave out" patterns (Options): a folder path (C:\\Windows\\WinSxS), a name or a wildcard (*.tmp, $Recycle.Bin).

`search(...)` returns {"total", "rows" (the asked-for page), "objects", "state", "volumes"}: the service sends it as
JSON, and the window shows the rows as it scrolls."""

from __future__ import annotations

import fnmatch
import os
import pickle
import stat
import sys
import threading
import time
import zlib
from dataclasses import dataclass, field

from . import mft, usn
from .index import Index

CACHE_VERSION = 2


@dataclass
class Volume:
    path: str                    # "C:\\" (or a folder, for tests and non-drive roots)
    name: str = ""               # the index's root entry: "C:" (or the folder path)
    fs: str = ""
    mode: str = ""               # "mft" | "walk"
    root: int = -1
    journal: int = 0
    next_usn: int = 0
    built: float = 0.0


def drive_info(path: str) -> str:
    """The file system of a drive ("NTFS", "exFAT" ...), "" when unknown."""
    if sys.platform != "win32":
        return ""
    import ctypes
    buf = ctypes.create_unicode_buffer(64)
    if ctypes.windll.kernel32.GetVolumeInformationW(path, None, 0, None, None, None, buf, 64):
        return buf.value
    return ""


def is_admin() -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def fixed_drives() -> list[str]:
    """The local hard drives Everything indexes by default (C:\\, D:\\ ...); not removable, network or CD."""
    if sys.platform != "win32":
        return []
    import ctypes
    out = []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for n in range(26):
        if mask & (1 << n):
            root = f"{chr(65 + n)}:\\"
            if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:          # DRIVE_FIXED
                out.append(root)
    return out


class Skip:
    """The "Leave out" list as a test on paths."""

    def __init__(self, patterns: list[str]):
        self.folders = []
        self.globs = []
        for p in (x.strip() for x in patterns):
            if not p:
                continue
            if ("\\" in p or "/" in p) and not any(c in p for c in "*?"):
                self.folders.append(p.rstrip("\\/").casefold().replace("/", "\\"))
            else:
                self.globs.append(p.casefold())

    def __call__(self, path: str) -> bool:
        low = path.casefold().replace("/", "\\")
        for f in self.folders:
            if low == f or low.startswith(f + "\\"):
                return True
        name = low.rstrip("\\").rsplit("\\", 1)[-1]
        return any(fnmatch.fnmatchcase(name, g) for g in self.globs)

    def __bool__(self):
        return bool(self.folders or self.globs)


class Engine:
    def __init__(self, drives: list[str] | None = None, leave_out: list[str] | None = None, cache: str | None = None,
                 use_mft: bool | None = None):
        self.drives = list(drives) if drives is not None else fixed_drives()
        self.skip = Skip(leave_out or [])
        self.cache = cache
        self.use_mft = is_admin() if use_mft is None else use_mft
        self.index = Index()
        self.volumes: list[Volume] = []
        self.state = "empty"                     # empty | indexing | ready
        self.lock = threading.RLock()
        self.changed = 0.0

    # ---------------------------------------------------------------- building
    def build(self, progress=None, cancelled=lambda: False):
        """The whole index again, from every drive."""
        self.state = "indexing"
        index = Index()
        volumes = []
        for path in self.drives:
            v = self._fill(index, path, progress, cancelled)
            if v is not None:
                volumes.append(v)
        index.rank()
        _ = index.blob
        with self.lock:
            self.index, self.volumes = index, volumes
            self.state = "ready"
            self.changed = time.time()

    def _fill(self, index: Index, path: str, progress, cancelled) -> Volume | None:
        drive = os.path.splitdrive(path)[0]
        v = Volume(path, drive if drive and len(path.rstrip("\\/")) <= 2 else os.path.abspath(path).rstrip("\\/") or path)
        v.fs = drive_info(path)
        v.built = time.time()
        if self.use_mft and v.fs == "NTFS" and v.name.endswith(":"):
            try:
                journal, next_usn = usn.query(v.name)
                recs = mft.records("\\\\.\\" + v.name, progress, cancelled)
                v.root = mft.fill(index, recs, v.name, self.skip if self.skip else None)
                v.mode, v.journal, v.next_usn = "mft", journal, next_usn
                return v
            except mft.Cancelled:
                raise
            except Exception:  # noqa: BLE001 - no MFT for this drive after all: walk it
                pass
        v.mode = "walk"
        v.root = self._walk(index, path, v.name, progress, cancelled)
        return v

    def _walk(self, index: Index, path: str, name: str, progress, cancelled) -> int:
        root = index.add(name, -1, is_dir=True)
        stack = [(root, path)]
        n = 0
        while stack:
            if cancelled():
                raise mft.Cancelled()
            parent, folder = stack.pop()
            try:
                it = os.scandir(folder)
            except OSError:
                continue
            with it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        st = e.stat(follow_symlinks=False)
                        if getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                            continue
                        if self.skip and self.skip(e.path):
                            continue
                        d = e.is_dir(follow_symlinks=False)
                        i = index.add(e.name, parent, 0 if d else st.st_size, st.st_mtime, d)
                        n += 1
                        if d:
                            stack.append((i, e.path))
                    except OSError:
                        continue
            if progress is not None and n % 2000 < 50:
                progress(mft.Progress(n, 0, folder))
        return root

    # ---------------------------------------------------------------- keeping up
    def poll(self) -> int:
        """Apply what the change journals say happened since last time; returns how many changes were applied."""
        total = 0
        for v in self.volumes:
            if v.mode != "mft":
                continue
            try:
                nxt, changes = usn.read(v.name, v.next_usn, v.journal)
            except OSError:
                continue
            with self.lock:
                total += self.apply(v, changes)
                v.next_usn = nxt
        if total:
            self.changed = time.time()
        return total

    def apply(self, v: Volume, changes: list[usn.Change], stat_file=os.stat) -> int:
        ix = self.index
        n = 0
        for c in changes:
            key = (v.name, c.record)
            i = ix.keys.get(key)
            if c.deleted:
                if i is not None:
                    ix.remove(i)
                    n += 1
                continue
            p = ix.keys.get((v.name, c.parent))
            if p is None or ix.names[p] is None:
                continue                               # its folder isn't indexed (left out, or not yet known)
            if i is None or ix.names[i] is None:
                i = ix.add(c.name, p, 0, 0.0, c.is_dir, key=key)
            elif ix.names[i] != c.name or ix.parent[i] != p:
                ix.rename(i, c.name, p)
            path = ix.path(i)
            if self.skip and self.skip(path):
                ix.remove(i)
                continue
            if not c.is_dir:
                try:
                    st = stat_file(path)
                    ix.update(i, st.st_size, st.st_mtime)
                except OSError:
                    pass
            n += 1
        return n

    # ---------------------------------------------------------------- answering
    def search(self, query: str, kind: str = "everything", sort: str = "name", descending: bool = False,
               offset: int = 0, limit: int = 200) -> dict:
        with self.lock:
            ids = self.index.search(query, kind, sort, descending)
            rows = [self.index.row(i) for i in ids[offset:offset + limit]]
            return {"total": len(ids), "rows": rows, "objects": len(self.index), "state": self.state,
                    "volumes": [(v.name, v.mode) for v in self.volumes], "changed": self.changed}

    def status(self) -> dict:
        return {"objects": len(self.index), "state": self.state, "changed": self.changed,
                "volumes": [(v.name, v.fs, v.mode) for v in self.volumes]}

    # ---------------------------------------------------------------- saving
    def save(self, path: str | None = None):
        path = path or self.cache
        if not path:
            return
        with self.lock:
            ix = self.index
            data = {"version": CACHE_VERSION, "drives": self.drives, "volumes": self.volumes,
                    "names": ix.names, "parent": ix.parent, "size": ix.size, "mtime": ix.mtime,
                    "isdir": bytes(ix.isdir), "keys": ix.keys, "removed": ix.removed}
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            fh.write(zlib.compress(pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL), 1))
        os.replace(tmp, path)

    def load(self, path: str | None = None) -> bool:
        """The saved index, if it's for the same drives; then the journals bring it up to date (poll)."""
        path = path or self.cache
        try:
            with open(path, "rb") as fh:
                data = pickle.loads(zlib.decompress(fh.read()))
        except (OSError, ValueError, pickle.UnpicklingError, zlib.error, EOFError):
            return False
        if data.get("version") != CACHE_VERSION or data.get("drives") != self.drives:
            return False
        ix = Index()
        ix.names, ix.parent, ix.size, ix.mtime = data["names"], data["parent"], data["size"], data["mtime"]
        ix.isdir = bytearray(data["isdir"])
        ix.keys = data["keys"]
        ix._key_of = {i: k for k, i in ix.keys.items()}
        ix.removed = data["removed"]
        with self.lock:
            self.index, self.volumes, self.state = ix, data["volumes"], "ready"
            self.changed = time.time()
        for v in self.volumes:                          # a reset journal (or no rights now): that drive again
            if v.mode == "mft":
                try:
                    if usn.query(v.name)[0] != v.journal:
                        return False
                except OSError:
                    return False
        return True
