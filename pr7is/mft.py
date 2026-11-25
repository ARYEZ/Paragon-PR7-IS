"""Read an NTFS drive's Master File Table directly (no Qt): WizTree's way of scanning in seconds.

Every file and folder on NTFS has a 1 KB record in the MFT. Reading the MFT front to back in large sequential reads
gives every name, size and parent folder without asking Windows about each folder. Needs administrator rights (the
drive is opened as \\\\.\\C:). The format (all little-endian):

- Boot sector: bytes per sector (0x0B), sectors per cluster (0x0D), the MFT's first cluster (0x30), clusters per
  record (0x40; negative n means 2**-n bytes).
- A record: "FILE", the update sequence (fixups: the last two bytes of each sector are stored aside and must be put
  back), flags (0x01 in use, 0x02 folder), the base record (records too full spill attributes into extension records
  that point back at it), then attributes until 0xFFFFFFFF.
- Attributes used: 0x10 STANDARD_INFORMATION (modified time), 0x30 FILE_NAME (parent folder, name, namespace: the
  DOS 8.3 short name is skipped when a long one exists), 0x80 unnamed DATA (resident: in the record; non-resident:
  real and allocated sizes, and for the MFT itself the run list saying where it lies on the drive).

`records(source)` takes a device path or, in tests, an NTFS image file; `fill` puts them in the index with their
record numbers as keys, which the change journal (usn.py) refers to. (PR7-DU's reader, split.)"""

from __future__ import annotations

import struct
import time

from dataclasses import dataclass


class Cancelled(Exception):
    pass


@dataclass
class Progress:
    files: int = 0
    bytes: int = 0
    current: str = ""

ROOT_RECORD = 5
CHUNK = 4 << 20                       # read 4 MB at a time (whole records)
_u16 = struct.Struct("<H").unpack_from
_u32 = struct.Struct("<I").unpack_from
_u64 = struct.Struct("<Q").unpack_from
EPOCH_DIFF = 11644473600              # seconds from 1601 (FILETIME) to 1970


class NotNTFS(Exception):
    pass


def boot(sector: bytes) -> tuple[int, int, int, int]:
    """(bytes per sector, cluster size, MFT first cluster, record size) from the boot sector."""
    if sector[3:11] != b"NTFS    ":
        raise NotNTFS("Not an NTFS volume.")
    bps = _u16(sector, 0x0B)[0]
    spc = sector[0x0D]
    if spc > 0x80:                    # big clusters are stored as a negative power of two
        spc = 1 << (256 - spc)
    cluster = bps * spc
    mft_lcn = _u64(sector, 0x30)[0]
    cpr = struct.unpack_from("<b", sector, 0x40)[0]
    record = cluster * cpr if cpr > 0 else 1 << (-cpr)
    return bps, cluster, mft_lcn, record


def fixup(rec: bytearray, bps: int) -> bool:
    """Put back the bytes the update sequence moved aside; False when the record is torn (half written)."""
    off = _u16(rec, 4)[0]
    count = _u16(rec, 6)[0]
    if count == 0 or off + 2 * count > len(rec):
        return False
    usn = rec[off:off + 2]
    for i in range(1, count):
        end = i * bps
        if end > len(rec):
            break
        if rec[end - 2:end] != usn:
            return False
        rec[end - 2:end] = rec[off + 2 * i:off + 2 * i + 2]
    return True


def runs(data: bytes, pos: int) -> list[tuple[int, int]]:
    """A run list -> [(first cluster, cluster count)]; sparse runs (no clusters on disk) are left out."""
    out = []
    lcn = 0
    while pos < len(data):
        head = data[pos]
        if head == 0:
            break
        nlen, noff = head & 0x0F, head >> 4
        pos += 1
        length = int.from_bytes(data[pos:pos + nlen], "little")
        pos += nlen
        if noff:
            lcn += int.from_bytes(data[pos:pos + noff], "little", signed=True)
            out.append((lcn, length))
        pos += noff
    return out


def parse(rec: bytes) -> tuple | None:
    """One fixed-up record -> (base, is_dir, names, data_size, data_alloc, mtime, mft_runs) or None if unused.
    names: [(parent, namespace, name)]; data_* are None when this record holds no unnamed DATA."""
    if rec[:4] != b"FILE":
        return None
    flags = _u16(rec, 0x16)[0]
    if not flags & 0x01:
        return None
    base = _u64(rec, 0x20)[0] & 0xFFFFFFFFFFFF
    pos = _u16(rec, 0x14)[0]
    names = []
    size = alloc = None
    mtime = None
    mft_runs = None
    n = len(rec)
    while pos + 8 <= n:
        atype = _u32(rec, pos)[0]
        if atype == 0xFFFFFFFF:
            break
        alen = _u32(rec, pos + 4)[0]
        if alen < 16 or pos + alen > n:
            break
        nonres = rec[pos + 8]
        name_len = rec[pos + 9]
        if atype == 0x30 and not nonres:
            v = pos + _u16(rec, pos + 0x14)[0]
            parent = _u64(rec, v)[0] & 0xFFFFFFFFFFFF
            ln = rec[v + 0x40]
            ns = rec[v + 0x41]
            names.append((parent, ns, rec[v + 0x42:v + 0x42 + 2 * ln].decode("utf-16-le", "replace")))
        elif atype == 0x80 and name_len == 0:
            if nonres:
                if _u64(rec, pos + 0x10)[0] == 0:          # the first piece carries the sizes
                    alloc = _u64(rec, pos + 0x28)[0]
                    size = _u64(rec, pos + 0x30)[0]
                    mft_runs = runs(rec, pos + _u16(rec, pos + 0x20)[0])
            else:
                size = _u32(rec, pos + 0x10)[0]
                alloc = 0                                  # stored inside the MFT record itself
        elif atype == 0x10 and not nonres:
            v = pos + _u16(rec, pos + 0x14)[0]
            mtime = _u64(rec, v + 0x08)[0] / 1e7 - EPOCH_DIFF
        pos += alen
    return base, bool(flags & 0x02), names, size, alloc, mtime, mft_runs


def _best(names: list) -> tuple | None:
    """The long name (POSIX 0, Win32 1, Win32+DOS 3) over the DOS 8.3 short one (2)."""
    best = None
    for item in names:
        if item[1] != 2:
            return item
        best = best or item
    return best


def _open(source: str):
    return open(source, "rb", buffering=0)  # noqa: SIM115 - closed by read()


def records(source: str, progress=None, cancelled=lambda: False, every: float = 0.1) -> dict:
    """Every record in use on the volume at `source`: {record number: [is_dir, names, size, alloc, mtime]}."""
    with _open(source) as fh:
        first = fh.read(512)
        bps, cluster, mft_lcn, rsize = boot(first)
        fh.seek(mft_lcn * cluster)
        head = bytearray(fh.read(max(rsize, bps)))[:rsize]
        if not fixup(head, bps):
            raise NotNTFS("The MFT's first record is damaged.")
        info = parse(bytes(head))
        if info is None or not info[6]:
            raise NotNTFS("The MFT's location couldn't be read.")
        mft_size = info[3]
        extents = info[6]
        # record number -> [is_dir, (parent, ns, name) or None, size, alloc, mtime]
        recs: dict[int, list] = {}
        p = Progress()
        last = 0.0
        number = 0
        total = mft_size // rsize
        for lcn, count in extents:
            todo = count * cluster
            fh.seek(lcn * cluster)
            while todo > 0 and number < total:
                if cancelled():
                    raise Cancelled()
                want = min(CHUNK, todo)
                buf = fh.read(want)
                if not buf:
                    break
                todo -= len(buf)
                for off in range(0, len(buf) - rsize + 1, rsize):
                    if number >= total:
                        break
                    rec = buf[off:off + rsize]
                    me = number
                    number += 1
                    if rec[:4] != b"FILE":
                        continue
                    rec = bytearray(rec)
                    if not fixup(rec, bps):
                        continue
                    got = parse(bytes(rec))
                    if got is None:
                        continue
                    base, is_dir, names, size, alloc, mtime, _ = got
                    key = base or me
                    r = recs.get(key)
                    if r is None:
                        r = recs[key] = [False, [], None, None, 0.0]
                    if not base:
                        r[0] = is_dir
                    r[1].extend(names)
                    if size is not None:
                        r[2], r[3] = size, alloc
                    if mtime is not None:
                        r[4] = mtime
                    if size and not is_dir:
                        p.files += 1
                        p.bytes += size
                if progress is not None:
                    now = time.monotonic()
                    if now - last >= every:
                        last = now
                        p.current = f"{number:,} of {total:,} records"
                        progress(p)
    return recs


def fill(index, recs: dict, volume: str, skip=None) -> int:
    """Records -> index entries under a drive entry named `volume` ("C:"); returns the drive's entry. Keys are
    (volume, record number). Anything whose folder is missing hangs off the drive; `skip(path)` leaves things out."""
    root = index.add(volume, -1, is_dir=True, key=(volume, ROOT_RECORD))
    entry: dict[int, int] = {ROOT_RECORD: root}
    pending = {}
    for num, (is_dir, names, size, alloc, mtime) in recs.items():
        if num == ROOT_RECORD:
            continue
        best = _best(names)
        if best is None:
            continue
        pending[num] = (best[0], best[2], is_dir, size or 0, mtime or 0.0)
    # parents before children: add in waves (a folder's record can come after its files')
    while pending:
        added = 0
        for num in list(pending):
            parent, name, is_dir, size, mtime = pending[num]
            p = entry.get(parent)
            if p is None and parent in pending:
                continue
            if p is None:
                p = root                               # its folder is gone (or never existed): hang it on the drive
            entry[num] = index.add(name, p, 0 if is_dir else size, mtime, is_dir, key=(volume, num))
            del pending[num]
            added += 1
        if not added:                                  # a loop of folders (a damaged MFT): hang the rest on the drive
            for num, (parent, name, is_dir, size, mtime) in list(pending.items()):
                entry[num] = index.add(name, root, 0 if is_dir else size, mtime, is_dir, key=(volume, num))
                del pending[num]
    if skip is not None:
        for num, i in list(entry.items()):
            if num != ROOT_RECORD and index.names[i] is not None and skip(index.path(i)):
                index.remove(i)
    return root
