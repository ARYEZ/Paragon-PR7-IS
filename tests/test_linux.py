"""PR7-IS on Linux (PR7-OS): the walked index kept up to date by inotify (real folders, real change notices), the
places indexed by default, and the service's address file readable by its user only."""
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from pr7is import engine as engine_mod
from pr7is.engine import Engine

linux = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux's inotify")


def names(e: Engine, query: str) -> list[str]:
    return sorted(r["name"] for r in e.search(query, limit=1000)["rows"])


def settle(e: Engine, rounds: int = 20) -> int:
    """Poll until the change notices stop coming (they arrive within milliseconds on a local disk)."""
    total = 0
    for _ in range(rounds):
        time.sleep(0.05)
        n = e.poll()
        total += n
        if not n and total:
            break
    return total


@pytest.fixture
def live(tmp_path):
    root = tmp_path / "home"
    (root / "Documents/Projects").mkdir(parents=True)
    (root / "Documents/notes.txt").write_text("a")
    (root / "Music").mkdir()
    (root / "Music/song.flac").write_bytes(b"x" * 10)
    e = Engine([str(root)], ["*.tmp", ".cache"], use_mft=False)
    e.build()
    assert e.watcher is not None and len(e.watcher.wd) == 4      # home, Documents, Projects, Music
    yield e, root
    e.watcher.close()


@linux
def test_new_files_and_folders_appear(live):
    e, root = live
    (root / "Documents/report.pdf").write_bytes(b"%PDF" + b"0" * 100)
    settle(e)
    assert names(e, "report") == ["report.pdf"]
    assert e.search("report", limit=1)["rows"][0]["size"] == 104
    # a folder that arrives with files already in it (copied, unpacked): walked, then watched itself
    new = root / "Downloads_tmp_holder"
    new.mkdir()
    (new / "inner").mkdir()
    (new / "inner/deep.txt").write_text("deep")
    settle(e)
    assert names(e, "deep") == ["deep.txt"]
    (new / "inner/later.txt").write_text("later")                 # inside the new folder, after the fact
    settle(e)
    assert names(e, "later") == ["later.txt"]


@linux
def test_renames_moves_and_deletes(live):
    e, root = live
    (root / "Documents/notes.txt").rename(root / "Documents/minutes.txt")
    settle(e)
    assert names(e, "notes") == [] and names(e, "minutes") == ["minutes.txt"]
    (root / "Documents/minutes.txt").rename(root / "Music/minutes.txt")     # another folder: same entry, new parent
    settle(e)
    hit = e.search("minutes", limit=1)["rows"][0]
    assert hit["path"].endswith("/Music/minutes.txt") or hit.get("folder", "").endswith("/Music")
    (root / "Documents/Projects").rename(root / "Music/Projects")          # a whole folder moves
    (root / "Music/Projects/plan.md").write_text("plan")                   # its watch moved with it
    settle(e)
    assert names(e, "plan") == ["plan.md"]
    outside = root.parent / "elsewhere"
    outside.mkdir()
    (root / "Music/song.flac").rename(outside / "song.flac")               # moved out of everything watched
    settle(e)
    assert names(e, "song") == []
    import shutil
    shutil.rmtree(root / "Music")                                          # a folder with contents, deleted
    settle(e)
    assert names(e, "Music") == [] and names(e, "plan") == [] and names(e, "minutes") == []


@linux
def test_writes_update_the_size_and_leave_out_is_kept(live):
    e, root = live
    with open(root / "Documents/notes.txt", "a") as fh:
        fh.write("b" * 999)
    settle(e)
    assert e.search("notes", limit=1)["rows"][0]["size"] == 1000
    (root / "Documents/scratch.tmp").write_text("x")
    (root / ".cache").mkdir()
    (root / ".cache/thumb.png").write_bytes(b"x")
    settle(e)
    assert names(e, "scratch") == [] and names(e, "thumb") == []


@linux
def test_an_overflow_asks_for_a_rebuild(live):
    e, root = live
    from pr7is import linuxwatch
    e.watcher.overflowed = True                     # what an IN_Q_OVERFLOW notice sets
    (root / "x.txt").write_text("x")
    e.poll()
    assert e.wants_rebuild
    e.build()                                      # what the service then does
    assert not e.wants_rebuild and names(e, "x.txt") == ["x.txt"]


def test_the_places_indexed_by_default(tmp_path):
    mounts = tmp_path / "mounts"
    mounts.write_text("sysfs /sys sysfs rw 0 0\nproc /proc proc rw 0 0\n/dev/nvme0n1p2 / ext4 rw 0 0\n"
                      "/dev/sda1 /media/ari/Photos\\040Drive exfat rw 0 0\ntmpfs /media/ari/ram tmpfs rw 0 0\n"
                      "/dev/sdb1 /mnt/backup ext4 rw 0 0\n/dev/sdc1 /home/ari/inside btrfs rw 0 0\n")
    assert engine_mod.linux_places(str(mounts), home="/home/ari") == [
        "/home/ari", "/media/ari/Photos Drive", "/mnt/backup"]     # no /proc, /sys, RAM disks; home's mounts in it


@linux
def test_the_address_file_is_private(service, tmp_path):
    where = Path(os.environ["PR7IS_ADDRESS"])
    assert stat.S_IMODE(where.stat().st_mode) == 0o600              # the token: this user's only
