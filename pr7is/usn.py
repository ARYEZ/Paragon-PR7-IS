"""An NTFS drive's change journal (USN journal): what was created, deleted, renamed or written since a point, so the
index keeps up without walking anything (Everything's way). Windows writes one record per change; asking for
"only on close" gives one record per file once it's closed, with the final name and folder (a rename's old and new
names fold into one).

`parse(buffer)` reads FSCTL_READ_USN_JOURNAL's output (pure Python, tested anywhere): the next USN (8 bytes), then
USN_RECORD_V2s: RecordLength u32, MajorVersion u16, MinorVersion u16, FileReferenceNumber u64,
ParentFileReferenceNumber u64, Usn i64, TimeStamp i64, Reason u32, SourceInfo u32, SecurityId u32,
FileAttributes u32, FileNameLength u16, FileNameOffset u16, then the name (UTF-16). `query` and `read` are the Windows
calls (administrator rights: the drive is opened as \\\\.\\C:)."""

from __future__ import annotations

import struct
import sys
from dataclasses import dataclass

FILE_CREATE, FILE_DELETE = 0x100, 0x200
RENAME_OLD, RENAME_NEW = 0x1000, 0x2000
DATA_OVERWRITE, DATA_EXTEND, DATA_TRUNCATION = 0x1, 0x2, 0x4
CLOSE = 0x80000000
ATTR_DIRECTORY = 0x10
RECORD_MASK = (1 << 48) - 1                      # a file reference = record number (48 bits) + sequence (16)
FSCTL_QUERY_USN_JOURNAL = 0x000900F4
FSCTL_READ_USN_JOURNAL = 0x000900BB
_HEAD = struct.Struct("<IHHQQqqIIIIHH")          # USN_RECORD_V2 up to the name (60 bytes)


@dataclass
class Change:
    record: int          # record number (sequence stripped): the same key as the MFT's
    parent: int
    name: str
    reason: int
    is_dir: bool
    usn: int

    @property
    def deleted(self) -> bool:
        return bool(self.reason & FILE_DELETE)


def parse(buf: bytes) -> tuple[int, list[Change]]:
    """(the USN to read from next time, the changes in this buffer)."""
    if len(buf) < 8:
        return 0, []
    next_usn = struct.unpack_from("<q", buf, 0)[0]
    out = []
    pos = 8
    while pos + _HEAD.size <= len(buf):
        (length, major, _minor, frn, parent, usn, _ts, reason, _src, _sec, attrs, nlen,
         noff) = _HEAD.unpack_from(buf, pos)
        if length == 0 or pos + length > len(buf):
            break
        if major == 2:
            name = buf[pos + noff:pos + noff + nlen].decode("utf-16-le", "replace")
            out.append(Change(frn & RECORD_MASK, parent & RECORD_MASK, name, reason, bool(attrs & ATTR_DIRECTORY),
                              usn))
        pos += length
    return next_usn, out


def record(frn: int, parent: int, name: str, reason: int, is_dir: bool = False, usn: int = 0) -> bytes:
    """One USN_RECORD_V2 as Windows writes it (tests build journals with it)."""
    raw = name.encode("utf-16-le")
    length = (_HEAD.size + len(raw) + 7) // 8 * 8
    head = _HEAD.pack(length, 2, 0, frn, parent, usn, 0, reason, 0, 0, ATTR_DIRECTORY if is_dir else 0x20, len(raw),
                      _HEAD.size)
    return (head + raw).ljust(length, b"\0")


# -------------------------------------------------------------------- Windows
def _open(volume: str):
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    k.CreateFileW.restype = wintypes.HANDLE
    h = k.CreateFileW(f"\\\\.\\{volume.rstrip(chr(92))}", 0x80000000, 0x3, None, 3, 0, None)
    if h in (None, wintypes.HANDLE(-1).value):
        raise OSError(f"{volume} couldn't be opened (administrator rights are needed).")
    return h


def _ioctl(h, code: int, inbuf: bytes, size: int) -> bytes:
    import ctypes
    from ctypes import wintypes
    out = ctypes.create_string_buffer(size)
    got = wintypes.DWORD()
    inb = ctypes.create_string_buffer(inbuf, len(inbuf)) if inbuf else None
    if not ctypes.windll.kernel32.DeviceIoControl(h, code, inb, len(inbuf), out, size, ctypes.byref(got), None):
        raise OSError(ctypes.get_last_error() or ctypes.windll.kernel32.GetLastError(), "DeviceIoControl failed")
    return out.raw[:got.value]


def query(volume: str) -> tuple[int, int]:
    """(journal id, next USN) of the drive's change journal ("C:")."""
    if sys.platform != "win32":
        raise OSError("The change journal is an NTFS (Windows) feature.")
    import ctypes
    h = _open(volume)
    try:
        data = _ioctl(h, FSCTL_QUERY_USN_JOURNAL, b"", 80)
        journal_id, _first, next_usn = struct.unpack_from("<Qqq", data, 0)
        return journal_id, next_usn
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def read(volume: str, start: int, journal_id: int, max_bytes: int = 1 << 20) -> tuple[int, list[Change]]:
    """Every change since `start` (closed files only), reading until the journal is caught up."""
    if sys.platform != "win32":
        raise OSError("The change journal is an NTFS (Windows) feature.")
    import ctypes
    h = _open(volume)
    changes: list[Change] = []
    try:
        while True:
            req = struct.pack("<qIIQQQ", start, 0xFFFFFFFF, 1, 0, 0, journal_id)
            buf = _ioctl(h, FSCTL_READ_USN_JOURNAL, req, max_bytes)
            nxt, got = parse(buf)
            changes += got
            if nxt == start or not got and nxt <= start:
                return nxt, changes
            start = nxt
            if not got:
                return start, changes
    finally:
        ctypes.windll.kernel32.CloseHandle(h)
