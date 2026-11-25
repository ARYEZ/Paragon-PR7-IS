import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp(tmp_path_factory):
    # Tests never write the real settings or index (QSettings -> INI files in a temp folder, PR7IS_DATA too).
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from pr7is import theme
    os.environ["PR7IS_DATA"] = str(tmp_path_factory.mktemp("data"))
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path_factory.mktemp("settings")))
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    theme.load_fonts()
    yield app
    for w in app.topLevelWidgets():
        w.close()
    app.clipboard().clear()          # PySide's offscreen clipboard crashes at exit while it holds data (PR7-CB)
    app.processEvents()


@pytest.fixture
def clean_settings(qapp):
    from PySide6.QtCore import QSettings
    s = QSettings(QSettings.defaultFormat(), QSettings.Scope.UserScope, "Paragon", "PR7-IS")
    s.clear(); s.sync()
    yield
    s.clear(); s.sync()


FILES = {
    "Videos/Elseworld shoot/A001_C0006.mov": 3_000_000, "Videos/Exports/Elseworld_v3.mp4": 1_200_000,
    "Docs/Invoice 2026-114.pdf": 12_000, "Docs/notes.txt": 120, "Docs/deep/a/b/leaf.bin": 7_777,
    "Music/Last Light.flac": 600_000, "setup.exe": 800_000, "Temp/cache.tmp": 50,
}


def make_tree(base: Path, files=FILES) -> dict:
    for name, size in files.items():
        p = base / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\0" * size)
    return files


@pytest.fixture
def tree(tmp_path):
    base = tmp_path / "disk"
    base.mkdir()
    return base, make_tree(base)


def _ntfs_tools() -> bool:
    return (sys.platform.startswith("linux") and all(shutil.which(t) for t in ("mkntfs", "ntfs-3g"))
            and os.path.exists("/dev/fuse") and os.geteuid() == 0)


@pytest.fixture
def ntfs_image(tmp_path):
    """A real NTFS volume (mkntfs) holding FILES, written through ntfs-3g, unmounted."""
    if not _ntfs_tools():
        pytest.skip("needs mkntfs, ntfs-3g and FUSE (Linux)")
    img, mnt = tmp_path / "vol.img", tmp_path / "mnt"
    mnt.mkdir()
    with open(img, "wb") as fh:
        fh.truncate(64 << 20)
    subprocess.run(["mkntfs", "-F", "-q", "-f", str(img)], check=True, capture_output=True)
    subprocess.run(["ntfs-3g", str(img), str(mnt)], check=True, capture_output=True)
    try:
        make_tree(mnt)
    finally:
        subprocess.run(["fusermount", "-u", str(mnt)], check=False, capture_output=True)
    return img


@pytest.fixture
def service(tmp_path, tree):
    """A real index service (its own process, its own address file) over the test tree."""
    base, files = tree
    env = dict(os.environ, PR7IS_DATA=str(tmp_path / "svc"), PR7IS_DRIVES=str(base), PR7IS_LEAVE_OUT="*.tmp",
               PR7IS_ADDRESS=str(tmp_path / "svc" / "service.json"))
    proc = subprocess.Popen([sys.executable, "-m", "pr7is", "--index-service"], cwd=str(ROOT), env=env)
    os.environ["PR7IS_ADDRESS"] = env["PR7IS_ADDRESS"]
    from pr7is import backend
    b = backend.ServiceBackend()
    end = time.time() + 30
    while time.time() < end and not b.connect():
        time.sleep(0.1)
    while time.time() < end and b.status().get("state") != "ready":
        time.sleep(0.1)
    yield b, base, proc
    b.quit()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
    os.environ.pop("PR7IS_ADDRESS", None)


@pytest.fixture
def window(qapp, clean_settings, tree):
    from pr7is import backend
    from pr7is.main_window import MainWindow
    base, files = tree
    b = backend.LocalBackend([str(base)], [], sync=True)
    b.start()
    w = MainWindow(b)
    w.said, w.opened = [], []
    w.answers["say"] = lambda kind, text: w.said.append((kind, text))
    w.answers["open"] = w.opened.append
    w.show()
    w.refresh()
    yield w
    w.close()
