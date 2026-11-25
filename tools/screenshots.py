"""Screenshots of the real PR7-IS window on a made-up index (nothing on disk is read):
QT_SCALE_FACTOR=2 python tools/screenshots.py OUT_DIR"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PR7IS_DATA"] = tempfile.mkdtemp()
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())

from pr7is import theme  # noqa: E402
from pr7is.engine import Engine, Volume  # noqa: E402
from pr7is.main_window import MainWindow, OptionsDialog  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots")
out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
theme.load_fonts()
now = time.time()
MB, GB = 1024 ** 2, 1024 ** 3
FILES = [
    ("C:\\Users\\me\\Videos\\Elseworld shoot", 0, 12), ("C:\\Users\\me\\Videos\\Exports\\Elseworld_v3.mp4", 1.2 * GB, 4),
    ("C:\\Users\\me\\Videos\\Exports\\Elseworld_v2.mp4", 1.1 * GB, 7),
    ("C:\\Users\\me\\Videos\\Exports\\Elseworld_v1.mp4", 986 * MB, 10),
    ("C:\\Users\\me\\Documents\\Elseworld\\Elseworld credits.txt", 4200, 4),
    ("C:\\Users\\me\\Documents\\Elseworld\\Elseworld edit.drp", 38 * MB, 0.1),
    ("C:\\Users\\me\\Pictures\\Edits\\Elseworld poster.png", 8.7 * MB, 6),
    ("D:\\Projects\\Paragon-Productions\\elseworld_parallax.mp4", 14.8 * MB, 3),
    ("D:\\Archive\\Elseworld shoot RAW.7z", 61 * GB, 11), ("D:\\Audio\\Mixes\\Elseworld stems", 0, 4),
    ("D:\\Audio\\Mixes\\Elseworld stems\\Elseworld_mix_v4.wav", 412 * MB, 1.2),
    ("D:\\Audio\\Mixes\\Elseworld stems\\Elseworld_mix_v4.flac", 201 * MB, 1.2),
    ("C:\\Users\\me\\Documents\\Invoices\\Invoice 2026-114 Static Bloom.pdf", 12.4 * MB, 1),
    ("C:\\Users\\me\\Documents\\Contracts\\Label contract 2026.pdf", 18.9 * MB, 2),
    ("C:\\Users\\me\\Downloads\\DaVinci Resolve 20 Manual.pdf", 96.2 * MB, 3),
    ("C:\\Users\\me\\Downloads\\Blender 5.0 Reference.pdf", 44.1 * MB, 4),
    ("D:\\Audio\\Docs\\Mastering notes (scanned).pdf", 27.5 * MB, 5),
    ("C:\\Users\\me\\Documents\\notes.pdf", 0.2 * MB, 2),
]

e = Engine([], use_mft=False)
ix = e.index
roots = {}
folders = {}


def folder(path: str) -> int:
    if path in folders:
        return folders[path]
    head, _, name = path.rpartition("\\")
    if not head:
        i = ix.add(path, -1, is_dir=True)
        roots[path] = i
    else:
        i = ix.add(name, folder(head), 0, now - 86400 * 30, True)
    folders[path] = i
    return i


for path, size, days in FILES:
    head, _, name = path.rpartition("\\")
    if size == 0:
        f = folder(path)
        ix.mtime[f] = now - days * 86400
    else:
        ix.add(name, folder(head), int(size), now - days * 86400)
for n in range(1_604_118 - len(ix.names)):                   # the rest of two well-used drives
    ix.add(f"f{n}.dll", roots["C:"], 1, 0)
e.volumes = [Volume("C:\\", "C:", "NTFS", "mft", roots["C:"]), Volume("D:\\", "D:", "NTFS", "mft", roots["D:"])]
e.state = "ready"


class Backend:
    name = "service"

    def search(self, *a, **k):
        return e.search(*a, **k)

    def status(self):
        return e.status() | {"service": True}

    def reload(self):
        pass


w = MainWindow(Backend())
w.resize(1180, 720)
w.show()


def shot(name, query, kind="everything"):
    w.filter.setCurrentIndex(w.filter.findData(kind))
    w.search.setText(query)
    w.refresh()
    w.table.selectRow(1)
    app.processEvents()
    w.grab().save(str(out / name))
    print(out / name)


shot("search.png", "elseworld")
shot("syntax.png", "ext:pdf size:>10mb dm:thismonth", "document")
theme.set_finish("aurora")
w.restyle()
# the Options of the made-up computer: NTFS C: and D:, an exFAT USB stick, the service installed and running
from pr7is import main_window, servicetask, settings  # noqa: E402
main_window.drive_info = lambda r: {"C:\\": "NTFS", "D:\\": "NTFS"}.get(r, "exFAT")
OptionsDialog._all_drives = staticmethod(lambda: ["C:\\", "D:\\", "E:\\"])
servicetask.installed = lambda: True
settings.set("drives", ["C:\\", "D:\\"])
d = OptionsDialog(w)
d.show(); app.processEvents()
d.grab().save(str(out / "options_aurora.png"))
print(out / "options_aurora.png")
theme.set_finish("obsidian")
