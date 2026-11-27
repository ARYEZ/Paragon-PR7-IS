"""Keeping a walked index up to date on Linux (no Qt): inotify, the kernel's change notices, standing in for NTFS's
change journal. Every indexed folder gets a watch; `process()` reads what happened since last time (non-blocking)
and applies it to the index:

    created / moved in      added (a new folder is walked and watched too, since files can land before its watch)
    deleted / moved out     removed with everything inside
    moved within            renamed and/or re-parented (the pair is matched by inotify's cookie)
    written / attributes    size and time updated

Linux limits the number of watches (fs.inotify.max_user_watches): when they run out the rest of the folders aren't
watched (`full`), and the engine's periodic rebuild catches what they miss. A queue overflow sets `overflowed`, after
which the engine rebuilds. ctypes only: the standard library has no inotify.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import struct

IN_MODIFY, IN_ATTRIB, IN_CLOSE_WRITE = 0x2, 0x4, 0x8
IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE = 0x40, 0x80, 0x100, 0x200
IN_DELETE_SELF, IN_MOVE_SELF, IN_Q_OVERFLOW, IN_IGNORED = 0x400, 0x800, 0x4000, 0x8000
IN_ONLYDIR, IN_DONT_FOLLOW, IN_EXCL_UNLINK, IN_ISDIR = 0x01000000, 0x02000000, 0x04000000, 0x40000000
IN_NONBLOCK, IN_CLOEXEC = 0o4000, 0o2000000
MASK = (IN_ATTRIB | IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE | IN_ONLYDIR |
        IN_DONT_FOLLOW | IN_EXCL_UNLINK)
EVENT = struct.Struct("iIII")


def available() -> bool:
    return hasattr(os, "uname") and os.uname().sysname == "Linux"


class Watcher:
    def __init__(self, engine):
        self.engine = engine
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        self._add = libc.inotify_add_watch
        self._add.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        self.fd = libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1 failed")
        self.wd: dict[int, int] = {}          # watch -> the folder's index entry
        self.full = False                     # the watch limit was reached
        self.overflowed = False

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    # ---------------------------------------------------------------- watching
    def watch(self, i: int, path: str) -> bool:
        if self.full:
            return False
        wd = self._add(self.fd, os.fsencode(path), MASK)
        if wd < 0:
            if ctypes.get_errno() == errno.ENOSPC:
                self.full = True
            return False
        self.wd[wd] = i
        return True

    def watch_tree(self, i: int):
        """The folder entry `i` and every folder inside it."""
        ix = self.engine.index
        for j in [i] + ix.descendants(i):
            if ix.isdir[j] and ix.names[j] is not None and not self.watch(j, ix.path(j)):
                if self.full:
                    return

    # ---------------------------------------------------------------- reading
    def _events(self) -> list[tuple[int, int, int, str]]:
        out = []
        while True:
            try:
                data = os.read(self.fd, 1 << 16)
            except BlockingIOError:
                return out
            except OSError:
                return out
            if not data:
                return out
            pos = 0
            while pos + EVENT.size <= len(data):
                wd, mask, cookie, length = EVENT.unpack_from(data, pos)
                name = data[pos + EVENT.size:pos + EVENT.size + length].split(b"\0", 1)[0]
                out.append((wd, mask, cookie, os.fsdecode(name)))
                pos += EVENT.size + length

    def process(self) -> int:
        """Apply what changed since last time; how many changes were applied. Call it under the engine's lock."""
        events = self._events()
        if not events:
            return 0
        ix, eng = self.engine.index, self.engine
        n = 0
        moved: dict[int, int] = {}            # cookie -> the entry moved out (waiting for its moved-in half)
        for wd, mask, cookie, name in events:
            if mask & IN_Q_OVERFLOW:
                self.overflowed = True
                continue
            if mask & IN_IGNORED:
                self.wd.pop(wd, None)
                continue
            parent = self.wd.get(wd)
            if parent is None or ix.names[parent] is None or not name:
                continue
            is_dir = bool(mask & IN_ISDIR)
            if mask & IN_MOVED_FROM:
                i = ix.child(parent, name)
                if i is not None:
                    moved[cookie] = i
                continue
            if mask & IN_MOVED_TO and cookie in moved:
                i = moved.pop(cookie)
                old = ix.child(parent, name)          # a move over an existing name replaces it
                if old is not None and old != i:
                    ix.remove(old)
                ix.rename(i, name, parent)
                if eng.skip and eng.skip(ix.path(i)):
                    ix.remove(i)
                n += 1
                continue
            if mask & (IN_CREATE | IN_MOVED_TO):
                if ix.child(parent, name) is not None:
                    continue
                path = os.path.join(ix.path(parent), name)
                if eng.skip and eng.skip(path):
                    continue
                try:
                    st = os.stat(path, follow_symlinks=False)
                except OSError:
                    continue
                if os.path.islink(path):
                    continue
                i = ix.add(name, parent, 0 if is_dir else st.st_size, st.st_mtime, is_dir)
                n += 1
                if is_dir:                            # files can land before its watch: walk it, then watch it all
                    n += eng._walk_into(ix, i, path)
                    self.watch_tree(i)
                continue
            if mask & IN_DELETE:
                i = ix.child(parent, name)
                if i is not None:
                    ix.remove(i)
                    n += 1
                continue
            if mask & (IN_CLOSE_WRITE | IN_ATTRIB):
                i = ix.child(parent, name)
                if i is not None and not ix.isdir[i]:
                    try:
                        st = os.stat(ix.path(i), follow_symlinks=False)
                        ix.update(i, st.st_size, st.st_mtime)
                        n += 1
                    except OSError:
                        pass
        for i in moved.values():                      # moved out of every watched folder: gone from the index
            if ix.names[i] is not None:
                ix.remove(i)
                n += 1
        return n
