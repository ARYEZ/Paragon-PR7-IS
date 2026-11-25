"""Mock-up pictures of PR7-IS, drawn offscreen with real Qt widgets: QT_SCALE_FACTOR=2 python tools/mockup.py OUT"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QGridLayout, QHBoxLayout,  # noqa
                               QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from pr7is import theme, widgets  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/mockup")
out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
theme.load_fonts()

RESULTS_1 = [
    ("Elseworld shoot", "C:\\Users\\me\\Videos", "", "2026-11-13 18:48", True),
    ("Elseworld_v3.mp4", "C:\\Users\\me\\Videos\\Exports", "1.2 GB", "2026-11-20 22:10", False),
    ("Elseworld_v2.mp4", "C:\\Users\\me\\Videos\\Exports", "1.1 GB", "2026-11-18 16:42", False),
    ("Elseworld_v1.mp4", "C:\\Users\\me\\Videos\\Exports", "986.4 MB", "2026-11-15 11:03", False),
    ("Elseworld credits.txt", "C:\\Users\\me\\Documents\\Elseworld", "4.1 KB", "2026-11-20 21:55", False),
    ("Elseworld edit.drp", "C:\\Users\\me\\Documents\\Elseworld", "38.2 MB", "2026-11-24 23:12", False),
    ("Elseworld poster.png", "C:\\Users\\me\\Pictures\\Edits", "8.7 MB", "2026-11-19 14:31", False),
    ("elseworld_parallax.mp4", "D:\\Projects\\Paragon-Productions", "14.8 MB", "2026-11-22 03:14", False),
    ("Elseworld shoot RAW.7z", "D:\\Archive", "61.0 GB", "2026-11-14 09:20", False),
    ("Elseworld stems", "D:\\Audio\\Mixes", "", "2026-11-21 17:05", True),
    ("Elseworld_mix_v4.wav", "D:\\Audio\\Mixes\\Elseworld stems", "412.6 MB", "2026-11-23 20:48", False),
    ("Elseworld_mix_v4.flac", "D:\\Audio\\Mixes\\Elseworld stems", "201.3 MB", "2026-11-23 20:51", False),
]
RESULTS_2 = [
    ("Invoice 2026-114 Static Bloom.pdf", "C:\\Users\\me\\Documents\\Invoices", "12.4 MB", "2026-11-24 10:02", False),
    ("Label contract 2026.pdf", "C:\\Users\\me\\Documents\\Contracts", "18.9 MB", "2026-11-23 15:40", False),
    ("DaVinci Resolve 20 Manual.pdf", "C:\\Users\\me\\Downloads", "96.2 MB", "2026-11-22 08:17", False),
    ("Blender 5.0 Reference.pdf", "C:\\Users\\me\\Downloads", "44.1 MB", "2026-11-21 19:33", False),
    ("Mastering notes (scanned).pdf", "D:\\Audio\\Docs", "27.5 MB", "2026-11-20 12:06", False),
]


def table(rows, query_words):
    t = QTableWidget(len(rows), 4)
    t.setHorizontalHeaderLabels(["NAME", "PATH", "SIZE", "DATE MODIFIED"])
    t.verticalHeader().hide()
    t.setShowGrid(False)
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.verticalHeader().setDefaultSectionSize(28)
    for r, (name, path, size, date, is_dir) in enumerate(rows):
        it = QTableWidgetItem(name)
        it.setIcon(widgets.file_icon(widgets.kind_of(name, is_dir), 18))
        t.setItem(r, 0, it)
        t.setItem(r, 1, QTableWidgetItem(path))
        s = QTableWidgetItem(size); s.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        t.setItem(r, 2, s)
        t.setItem(r, 3, QTableWidgetItem(date))
    h = t.horizontalHeader()
    h.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive); t.setColumnWidth(0, 330)
    h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    for c, w in ((2, 100), (3, 150)):
        h.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed); t.setColumnWidth(c, w)
    t.selectRow(1)
    return t


def window(query, rows, total, filt="Everything", finish="obsidian"):
    theme.set_finish(finish)
    app.setPalette(theme.palette())
    w = QWidget(); w.resize(1180, 720)
    w.setPalette(theme.palette()); w.setAutoFillBackground(True)
    w.setStyleSheet(theme.stylesheet() + f"""
QTableWidget::item:selected {{ background: {theme.ACCENT_DIM}; color: #FFFFFF; }}
QTableWidget {{ alternate-background-color: {theme.ALT_ROW}; }}
QLineEdit#search {{ font-size: 16px; padding: 8px 12px; }}""")
    v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
    v.addWidget(widgets.Header("SEARCH"))
    bar = QHBoxLayout(); bar.setContentsMargins(16, 12, 16, 12); bar.setSpacing(10)
    s = QLineEdit(query); s.setObjectName("search"); s.setPlaceholderText("Search every file and folder")
    bar.addWidget(s, 1)
    c = QComboBox()
    for f in ("Everything", "Audio", "Compressed", "Document", "Executable", "Folder", "Picture", "Video"):
        c.addItem(f.upper())
    c.setCurrentText(filt.upper()); c.setMinimumWidth(150)
    bar.addWidget(c)
    bar.addWidget(widgets.SmallKey("more", "Options"))
    v.addLayout(bar)
    v.addWidget(table(rows, query.split()), 1)
    st = QHBoxLayout(); st.setContentsMargins(16, 6, 16, 8)
    lab = QLabel(f"{len(rows)} of {total:,} objects"); st.addWidget(lab)
    st.addStretch(1)
    d = QLabel("C:  D:  indexed from the drives' own index  ·  kept up to date as files change")
    d.setProperty("role", "dim"); st.addWidget(d)
    v.addLayout(st)
    w.show(); app.processEvents()
    return w


def shoot(w, name):
    w.grab().save(str(out / name))
    print(out / name)
    w.close()


shoot(window("elseworld", RESULTS_1, 1_604_118), "1_search.png")
shoot(window("ext:pdf size:>10mb dm:thismonth", RESULTS_2, 1_604_118, "Document"), "2_syntax.png")


def options():
    theme.set_finish("aurora")
    app.setPalette(theme.palette())
    d = QWidget(); d.setPalette(theme.palette()); d.setAutoFillBackground(True); d.resize(600, 660)
    d.setStyleSheet(theme.stylesheet() + f"""QPushButton[kind="primary"] {{ background: {theme.qss_gradient()};
        color: #FFFFFF; border: none; font-weight: 600; padding: 7px 22px; }}""")
    v = QVBoxLayout(d); v.setContentsMargins(0, 0, 0, 16); v.setSpacing(0)
    v.addWidget(widgets.Header("SEARCH"))
    g = QVBoxLayout(); g.setContentsMargins(22, 18, 22, 6); g.setSpacing(10)

    def head(text):
        lab = QLabel(text); f = lab.font(); f.setWeight(QFont.Weight.DemiBold); lab.setFont(f); g.addWidget(lab)

    def note(text):
        lab = QLabel(text); lab.setProperty("role", "dim"); lab.setWordWrap(True); g.addWidget(lab)
    head("DRIVES")
    for text, on in (("C:  WINDOWS  ·  NTFS  ·  the drive's own index", True),
                     ("D:  DATA  ·  NTFS  ·  the drive's own index", True),
                     ("E:  USB STICK  ·  exFAT  ·  folders walked (no index on exFAT)", False)):
        cb = QCheckBox(text); cb.setChecked(on); g.addWidget(cb)
    head("INDEX SERVICE")
    note("Reading a drive's index needs administrator rights. PR7-IS installs a small background service once "
         "(Windows asks for permission one time), as Everything does; after that, searching never asks again.")
    row = QHBoxLayout()
    row.addWidget(QLabel("Running  ·  1,604,118 files and folders  ·  updated 2 s ago"))
    row.addStretch(1)
    row.addWidget(QPushButton("REBUILD INDEX"))
    g.addLayout(row)
    head("LEAVE OUT")
    g.addWidget(QLineEdit("C:\\Windows\\WinSxS;  *.tmp;  $Recycle.Bin"))
    head("KEY TO OPEN")
    g.addWidget(QLineEdit("Ctrl+Alt+Space"))
    for text, on in (("Open folders in PR7-E", True), ("Start PR7-IS with Windows (in the notification area)", True),
                     ("Check for updates automatically", True)):
        cb = QCheckBox(text); cb.setChecked(on); g.addWidget(cb)
    v.addLayout(g)
    v.addStretch(1)
    b = QHBoxLayout(); b.setContentsMargins(22, 0, 22, 0); b.addStretch(1)
    ok = QPushButton("OK"); ok.setProperty("kind", "primary"); b.addWidget(ok); b.addWidget(QPushButton("Cancel"))
    v.addLayout(b)
    d.show(); app.processEvents()
    shoot(d, "3_options_aurora.png")


options()
