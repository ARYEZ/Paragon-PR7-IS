import os
import sys

import pytest

from pr7is import engine, mft, usn
from pr7is.index import Index


def paths(e, q):
    return sorted(r["path"] for r in e.search(q, limit=1000)["rows"])


def test_a_walked_folder_with_left_out_things(tree):
    base, files = tree
    e = engine.Engine([str(base)], leave_out=["*.tmp", str(base / "Music")])
    e.build()
    assert paths(e, "elseworld") == [str(base / "Videos" / "Elseworld shoot"),
                                     str(base / "Videos" / "Exports" / "Elseworld_v3.mp4")]
    assert paths(e, "cache") == [] and paths(e, "last light") == []          # left out
    r = e.search("", limit=0)
    assert r["state"] == "ready" and r["objects"] == r["total"] and r["volumes"] == [(str(base), "walk")]


def test_the_cache_is_used_only_for_the_same_drives(tree, tmp_path):
    base, _ = tree
    cache = str(tmp_path / "index.bin")
    e = engine.Engine([str(base)], cache=cache)
    e.build(); e.save()
    again = engine.Engine([str(base)], cache=cache)
    assert again.load() and paths(again, "invoice") == [str(base / "Docs" / "Invoice 2026-114.pdf")]
    other = engine.Engine([str(tmp_path)], cache=cache)
    assert not other.load()                                   # other drives: build again


def test_the_mft_of_a_real_ntfs_volume(ntfs_image):
    from conftest import FILES
    ix = Index()
    mft.fill(ix, mft.records(str(ntfs_image)), "T:", skip=engine.Skip(["*.tmp"]))
    got = {ix.path(i): ix.size[i] for i in ix.search("file:") if not ix.names[i].startswith("$")}
    want = {"T:\\" + k.replace("/", "\\"): v for k, v in FILES.items() if not k.endswith(".tmp")}
    assert got == want                                          # every file, its folder and its exact size


def test_the_change_journal_keeps_the_index_up_to_date(ntfs_image):
    ix = Index()
    root = mft.fill(ix, mft.records(str(ntfs_image)), "T:")
    e = engine.Engine([], use_mft=False)
    e.index = ix
    v = engine.Volume("T:\\", "T:", "NTFS", "mft", root)
    docs = next(k for k, i in ix.keys.items() if ix.names[i] == "Docs")[1]
    sizes = {"T:\\Docs\\cut v1.mov": 111, "T:\\Docs\\final cut.mov": 222}
    fake_stat = lambda p: os.stat_result((0, 0, 0, 0, 0, 0, sizes.get(p, 0), 0, 1_700_000_000, 0))  # noqa: E731
    journal = b"\0" * 8 + usn.record(900, docs, "cut v1.mov", usn.FILE_CREATE | usn.CLOSE, usn=10) \
        + usn.record(901, docs, "Stems", usn.FILE_CREATE | usn.CLOSE, is_dir=True, usn=11)
    nxt, changes = usn.parse(journal)
    assert [c.name for c in changes] == ["cut v1.mov", "Stems"] and changes[1].is_dir
    assert e.apply(v, changes, stat_file=fake_stat) == 2
    assert paths(e, "cut") == ["T:\\Docs\\cut v1.mov"] and e.search("cut")["rows"][0]["size"] == 111
    stems = next(k for k, i in ix.keys.items() if ix.names[i] == "Stems")[1]
    _, changes = usn.parse(b"\0" * 8 + usn.record(900, docs, "final cut.mov", usn.RENAME_NEW | usn.CLOSE))
    e.apply(v, changes, stat_file=fake_stat)
    assert paths(e, "cut") == ["T:\\Docs\\final cut.mov"] and e.search("cut")["rows"][0]["size"] == 222
    _, changes = usn.parse(b"\0" * 8 + usn.record(900, stems, "final cut.mov", usn.RENAME_NEW | usn.CLOSE))
    e.apply(v, changes, stat_file=fake_stat)                    # moved into Stems
    assert paths(e, "cut") == ["T:\\Docs\\Stems\\final cut.mov"]
    _, changes = usn.parse(b"\0" * 8 + usn.record(901, docs, "Stems", usn.FILE_DELETE | usn.CLOSE, True))
    e.apply(v, changes)
    assert paths(e, "cut") == [] and paths(e, "stems") == []      # the folder and what was in it


def test_journal_records_round_trip():
    raw = usn.record((7 << 48) | 1234, (3 << 48) | 5, "ünïcode name.txt", usn.FILE_CREATE | usn.CLOSE, usn=99)
    nxt, ch = usn.parse(b"\x10\0\0\0\0\0\0\0" + raw + raw)
    assert nxt == 16 and len(ch) == 2
    assert (ch[0].record, ch[0].parent, ch[0].name, ch[0].usn) == (1234, 5, "ünïcode name.txt", 99)


@pytest.mark.skipif(sys.platform != "win32" or not engine.is_admin(), reason="Windows with administrator rights (CI)")
def test_the_real_journal_sees_a_new_file(tmp_path):
    drive = os.path.splitdrive(str(tmp_path))[0]
    journal, start = usn.query(drive)
    f = tmp_path / "pr7is journal test.txt"
    f.write_text("hello")
    nxt, changes = usn.read(drive, start, journal)
    assert nxt > start and any(c.name == f.name for c in changes)
