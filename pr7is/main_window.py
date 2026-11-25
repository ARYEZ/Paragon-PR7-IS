"""PR7-IS's window, Everything's: the search box, the filter, the results, the count.

The results model is lazy: it knows the total and fetches rows from the backend a page (200) at a time as they're
shown, so a million matches cost nothing until scrolled to. Typing searches 60 ms after the last key. Column
headers sort (name, path, size, date modified), as in Everything. `answers` replaces dialogs and launches in tests."""

from __future__ import annotations

import os
import subprocess
import sys
import time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton,
                               QTableView, QVBoxLayout, QWidget)

from . import __version__, backend, family, servicetask, settings, theme, widgets
from .engine import drive_info, fixed_drives

PAGE = 200
FILTERS = ["everything", "audio", "compressed", "document", "executable", "folder", "picture", "video"]
SORTS = ["name", "path", "size", "dm"]
RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


def human(n: int) -> str:
    for unit, k in (("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= k:
            return f"{n / k:,.1f} {unit}"
    return f"{n:,} B" if n else "0 KB"


class Results(QAbstractTableModel):
    COLUMNS = ["NAME", "PATH", "SIZE", "DATE MODIFIED"]

    def __init__(self):
        super().__init__()
        self.backend = None
        self.args = {}
        self.total = 0
        self.objects = 0
        self.pages: dict[int, list[dict]] = {}

    def run(self, backend, query: str, kind: str, sort: str, descending: bool) -> dict:
        self.beginResetModel()
        self.backend = backend
        self.args = {"query": query, "kind": kind, "sort": sort, "descending": descending}
        r = backend.search(query, kind, sort, descending, 0, PAGE)
        self.total = r.get("total", 0)
        self.objects = r.get("objects", 0)
        self.pages = {0: r.get("rows", [])}
        self.endResetModel()
        return r

    def row(self, n: int) -> dict | None:
        page = n // PAGE
        if page not in self.pages:
            r = self.backend.search(**self.args, offset=page * PAGE, limit=PAGE)
            self.pages[page] = r.get("rows", [])
        rows = self.pages[page]
        k = n - page * PAGE
        return rows[k] if k < len(rows) else None

    def path(self, n: int) -> str:
        r = self.row(n)
        return r["path"] if r is not None else ""

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.total

    def columnCount(self, parent=QModelIndex()):
        return 4

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r = self.row(index.row())
        if r is None:
            return None
        c = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if c == 0:
                return r["name"]
            if c == 1:
                return r["folder"]
            if c == 2:
                return "" if r["dir"] else human(r["size"])
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(r["mtime"])) if r["mtime"] else ""
        if role == Qt.ItemDataRole.DecorationRole and c == 0:
            return widgets.file_icon(widgets.kind_of(r["name"], r["dir"]), 18)
        if role == Qt.ItemDataRole.TextAlignmentRole and c == 2:
            return RIGHT
        if role == Qt.ItemDataRole.ToolTipRole:
            return self.path(index.row())
        return None


def stylesheet() -> str:
    return theme.stylesheet() + f"""
QTableView::item:selected {{ background: {theme.ACCENT_DIM}; color: #FFFFFF; }}
QTableView {{ alternate-background-color: {theme.ALT_ROW}; }}
QLineEdit#search {{ font-size: 16px; padding: 8px 12px; }}
QPushButton[kind="primary"] {{ background: {theme.qss_gradient()}; color: #FFFFFF; border: none; font-weight: 600;
    padding: 7px 22px; }}
"""


class SearchBox(QLineEdit):
    """Everything's search box: Enter opens the result, Ctrl+Enter its folder, Up / Down move through the results."""
    enter = Signal(bool)
    step = Signal(int)

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.enter.emit(bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier))
        elif k in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
            self.step.emit({Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1, Qt.Key.Key_PageDown: 20,
                            Qt.Key.Key_PageUp: -20}[k])
        else:
            super().keyPressEvent(e)


class MainWindow(QMainWindow):
    hidden_to_tray = Signal()

    def __init__(self, backend_=None):
        super().__init__()
        self.answers: dict = {}
        self.backend = backend_
        self.sort, self.descending = "name", False
        self.keep_in_tray = False
        self.setWindowTitle("Paragon PR7-IS")
        self.resize(1180, 720)
        body = QWidget(); self.setCentralWidget(body)
        v = QVBoxLayout(body); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        self.header = widgets.Header("SEARCH")
        v.addWidget(self.header)
        bar = QHBoxLayout(); bar.setContentsMargins(16, 12, 16, 12); bar.setSpacing(10)
        self.search = SearchBox(); self.search.setObjectName("search")
        self.search.setPlaceholderText("Search every file and folder")
        self.search.textChanged.connect(lambda _: self._timer.start())
        self.search.enter.connect(lambda ctrl: (self.open_path if ctrl else self.open_row)(self._current()))
        self.search.step.connect(self._step)
        bar.addWidget(self.search, 1)
        self.filter = QComboBox()
        for f in FILTERS:
            self.filter.addItem(f.upper(), f)
        self.filter.setMinimumWidth(150)
        self.filter.currentIndexChanged.connect(lambda _: self.refresh())
        bar.addWidget(self.filter)
        self.more = widgets.SmallKey("more", "Options")
        self.more.clicked.connect(lambda: self.main_menu(self.more.mapToGlobal(self.more.rect().bottomLeft())))
        bar.addWidget(self.more)
        v.addLayout(bar)
        self.model = Results()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.doubleClicked.connect(lambda idx: self.open_row(idx.row()))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._row_menu)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive); self.table.setColumnWidth(0, 330)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for c, w in ((2, 100), (3, 150)):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive); self.table.setColumnWidth(c, w)
        h.setSectionsClickable(True)
        h.setSortIndicatorShown(True)
        h.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        h.sectionClicked.connect(self._sort_by)
        v.addWidget(self.table, 1)
        st = QHBoxLayout(); st.setContentsMargins(16, 6, 16, 8)
        self.count = QLabel("")
        st.addWidget(self.count)
        st.addStretch(1)
        self.service_button = QPushButton("INSTALL THE INDEX SERVICE")
        self.service_button.setToolTip("Reads your drives' own index, as Everything's service does: searches find "
                                       "everything at once and stay up to date. Windows asks for permission once.")
        self.service_button.clicked.connect(self.install_service)
        self.service_button.hide()
        st.addWidget(self.service_button)
        self.drives_text = QLabel(""); self.drives_text.setProperty("role", "dim")
        st.addWidget(self.drives_text)
        v.addLayout(st)
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(60)
        self._timer.timeout.connect(self.refresh)
        self._status_timer = QTimer(self); self._status_timer.timeout.connect(self._status)
        self._status_timer.start(2000)
        for keys, fn in (("Ctrl+F", self.search.setFocus), ("Escape", self._escape), ("F5", self.refresh)):
            a = QAction(self); a.setShortcut(QKeySequence(keys)); a.triggered.connect(fn); self.addAction(a)
        self.restyle()

    def restyle(self):
        QApplication.instance().setPalette(theme.palette())
        self.setStyleSheet(stylesheet())
        self.header.update()

    # ---------------------------------------------------------------- searching
    def refresh(self):
        if self.backend is None:
            return
        try:
            r = self.model.run(self.backend, self.search.text(), self.filter.currentData(), self.sort, self.descending)
        except ConnectionError:
            self.backend = backend.connect()            # the service went away: search here instead
            r = self.model.run(self.backend, self.search.text(), self.filter.currentData(), self.sort, self.descending)
        if self.model.total:
            self.table.setCurrentIndex(self.model.index(0, 0))
        self._show_count(r)

    def _show_count(self, r: dict):
        state = r.get("state", "")
        if state == "indexing" or (state == "empty" and not self.model.objects):
            self.count.setText("Indexing your drives ...")
        else:
            self.count.setText(f"{self.model.total:,} of {self.model.objects:,} objects")
        vols = r.get("volumes") or []
        how = {"mft": "the drive's own index", "walk": "folders walked"}
        if vols:
            names = "  ".join(v[0] for v in vols)
            modes = {v[-1] for v in vols}
            text = f"{names}  indexed from " + (" and ".join(how[m] for m in sorted(modes)))
            if self.backend is not None and self.backend.name == "service" and "mft" in modes:
                text += "  ·  kept up to date as files change"
            self.drives_text.setText(text)
        self.service_button.setVisible(sys.platform == "win32" and getattr(self.backend, "name", "") == "local"
                                       and not servicetask.installed())

    def _status(self):
        if self.backend is None or not self.isVisible():
            return
        try:
            s = self.backend.status()
        except ConnectionError:
            return
        if s.get("objects") != self.model.objects or s.get("changed", 0) > getattr(self, "_seen", 0):
            self._seen = s.get("changed", 0)
            if not self.search.hasFocus() or not self.search.text():
                self.refresh()
            else:
                self._show_count(s | {"rows": []})

    def _sort_by(self, column: int):
        by = SORTS[column]
        self.descending = (not self.descending) if by == self.sort else by in ("size", "dm")
        self.sort = by
        self.table.horizontalHeader().setSortIndicator(
            column, Qt.SortOrder.DescendingOrder if self.descending else Qt.SortOrder.AscendingOrder)
        self.refresh()

    def _current(self) -> int:
        return max(self.table.currentIndex().row(), 0)

    def _step(self, by: int):
        if self.model.total:
            n = min(max(self.table.currentIndex().row() + by, 0), self.model.total - 1)
            self.table.selectRow(n)

    def _escape(self):
        if self.search.text():
            self.search.clear()
        elif self.keep_in_tray:
            self.hide()

    # ---------------------------------------------------------------- what can be done with a result
    def open_row(self, n: int):
        if n < 0 or n >= self.model.total:
            return
        path = self.model.path(n)
        if "open" in self.answers:
            self.answers["open"](path)
        elif sys.platform == "win32":
            os.startfile(path)                                       # noqa: S606 - the file the user chose
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def open_path(self, n: int):
        """The result's folder: PR7-E (Options) when installed, else Explorer with the result selected."""
        r = self.model.row(n)
        if r is None:
            return
        path = self.model.path(n)
        folder = r["folder"] or path
        launch = self.answers.get("launch", family.launch)
        if settings.get_bool("open_in_pr7e", True) and launch("PR7-E", [folder]):
            return
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", path])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _row_menu(self, pos):
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        self.table.setCurrentIndex(idx)
        m = self.row_menu(idx.row())
        m.exec(self.table.viewport().mapToGlobal(pos))

    def row_menu(self, n: int) -> QMenu:
        m = QMenu(self)
        m.addAction("Open", lambda: self.open_row(n))
        m.addAction("Open path", lambda: self.open_path(n))
        m.addSeparator()
        m.addAction("Copy full path", lambda: QApplication.clipboard().setText(self.model.path(n)))
        m.addAction("Copy name", lambda: QApplication.clipboard().setText((self.model.row(n) or {}).get("name", "")))
        self.last_menu = m
        return m

    def main_menu(self, at=None) -> QMenu:
        m = QMenu(self)
        for name in ("obsidian", "aurora"):
            a = m.addAction(name.upper())
            a.setCheckable(True); a.setChecked(theme.FINISH == name)
            a.triggered.connect(lambda _=False, n=name: self.set_finish(n))
        m.addSeparator()
        m.addAction("Options...", self.options)
        m.addAction("Check for updates...", self.check_updates)
        m.addAction("About PR7-IS", lambda: self._say("info", f"Paragon PR7-IS {__version__}\nInstant file search, "
                                                              "the way Everything finds files."))
        self.last_menu = m
        if at is not None and "menu" not in self.answers:
            m.exec(at)
        return m

    def set_finish(self, name: str):
        theme.set_finish(name)
        settings.set("finish", name)
        self.restyle()

    def check_updates(self):
        from . import update_ui
        update_ui.check(self, manual=True)

    # ---------------------------------------------------------------- the service
    def install_service(self) -> bool:
        ok = self.answers["elevated"]("--install-service") if "elevated" in self.answers else \
            servicetask.elevated("--install-service")
        if not ok:
            self._say("warning", "The index service wasn't installed (Windows' permission was declined).")
            return False
        for _ in range(40):                                      # it starts in a moment
            s = backend.ServiceBackend()
            if s.connect():
                self.backend = s
                self.refresh()
                return True
            QApplication.processEvents()
            time.sleep(0.25)
        return True

    def remove_service(self) -> bool:
        s = backend.ServiceBackend()
        if s.connect():
            s.quit()
        ok = self.answers["elevated"]("--remove-service") if "elevated" in self.answers else \
            servicetask.elevated("--remove-service")
        self.backend = backend.connect()
        self.refresh()
        return ok

    # ---------------------------------------------------------------- Options
    def options(self):
        d = OptionsDialog(self)
        self.options_dialog = d
        ok = self.answers["options"](d) if "options" in self.answers else d.exec() == QDialog.DialogCode.Accepted
        if ok:
            d.save()
            try:
                self.backend.reload()
            except ConnectionError:
                pass
            self.refresh()

    # ---------------------------------------------------------------- questions
    def _say(self, kind: str, text: str):
        if "say" in self.answers:
            self.answers["say"](kind, text)
            return
        (QMessageBox.warning if kind == "warning" else QMessageBox.information)(self, "Paragon PR7-IS", text)

    def closeEvent(self, e):
        if self.keep_in_tray:
            e.ignore()
            self.hide()
            self.hidden_to_tray.emit()
            return
        e.accept()


class OptionsDialog(QDialog):
    def __init__(self, win: MainWindow):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("PR7-IS Options")
        self.setStyleSheet(stylesheet())
        self.resize(600, 660)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 16); v.setSpacing(0)
        v.addWidget(widgets.Header("SEARCH"))
        g = QVBoxLayout(); g.setContentsMargins(22, 18, 22, 6); g.setSpacing(10)

        def head(text):
            lab = QLabel(text); f = lab.font(); f.setWeight(f.Weight.DemiBold); lab.setFont(f); g.addWidget(lab)

        def note(text):
            lab = QLabel(text); lab.setProperty("role", "dim"); lab.setWordWrap(True); g.addWidget(lab)
            return lab
        head("DRIVES")
        chosen = set(settings.get_list("drives")) or set(fixed_drives())
        self.drives = {}
        for root in self._all_drives():
            fs = drive_info(root)
            how = "the drive's own index" if fs == "NTFS" else f"folders walked (no index on {fs or 'this drive'})"
            cb = QCheckBox(f"{root[:2]}  ·  {fs or 'drive'}  ·  {how}")
            cb.setChecked(root in chosen)
            g.addWidget(cb)
            self.drives[root] = cb
        head("INDEX SERVICE")
        note("Reading a drive's index needs administrator rights. PR7-IS installs a small background service once "
             "(Windows asks for permission one time), as Everything does; after that, searching never asks again.")
        row = QHBoxLayout()
        self.service_state = QLabel(self._service_text())
        row.addWidget(self.service_state, 1)
        installed = servicetask.installed()
        self.service_button = QPushButton("REMOVE SERVICE" if installed else "INSTALL SERVICE")
        self.service_button.clicked.connect(self._toggle_service)
        self.service_button.setEnabled(sys.platform == "win32")
        row.addWidget(self.service_button)
        self.rebuild = QPushButton("REBUILD INDEX")
        self.rebuild.clicked.connect(lambda: self.win.backend.reload())
        row.addWidget(self.rebuild)
        g.addLayout(row)
        head("LEAVE OUT")
        self.leave = QLineEdit(";  ".join(settings.get_list("leave_out") or
                                          [r"C:\Windows\WinSxS", "*.tmp", "$Recycle.Bin"]))
        g.addWidget(self.leave)
        head("KEY TO OPEN")
        self.key = QLineEdit(settings.get("key_show", "Ctrl+Alt+Space"))
        g.addWidget(self.key)
        from .tray import start_with_windows
        from . import update_ui
        self.pr7e = QCheckBox("Open folders in PR7-E"); self.pr7e.setChecked(settings.get_bool("open_in_pr7e", True))
        self.startup = QCheckBox("Start PR7-IS with Windows (in the notification area)")
        self.startup.setChecked(start_with_windows()); self.startup.setEnabled(sys.platform == "win32")
        self.updates = QCheckBox("Check for updates automatically"); self.updates.setChecked(update_ui.auto_enabled())
        for c in (self.pr7e, self.startup, self.updates):
            g.addWidget(c)
        v.addLayout(g)
        v.addStretch(1)
        b = QHBoxLayout(); b.setContentsMargins(22, 0, 22, 0); b.addStretch(1)
        ok = QPushButton("OK"); ok.setProperty("kind", "primary"); ok.setDefault(True); ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        b.addWidget(ok); b.addWidget(cancel)
        v.addLayout(b)

    @staticmethod
    def _all_drives() -> list[str]:
        if sys.platform != "win32":
            return settings.get_list("drives") or []
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        return [f"{chr(65 + n)}:\\" for n in range(26) if mask & (1 << n)
                and ctypes.windll.kernel32.GetDriveTypeW(f"{chr(65 + n)}:\\") in (2, 3)]     # removable, fixed

    def _service_text(self) -> str:
        b = self.win.backend
        if getattr(b, "name", "") == "service":
            try:
                s = b.status()
                return f"Running  ·  {s.get('objects', 0):,} files and folders"
            except ConnectionError:
                pass
        return "Installed, not running" if servicetask.installed() else "Not installed: searching walks folders"

    def _toggle_service(self):
        if servicetask.installed():
            self.win.remove_service()
        else:
            self.win.install_service()
        self.service_state.setText(self._service_text())
        self.service_button.setText("REMOVE SERVICE" if servicetask.installed() else "INSTALL SERVICE")

    def save(self):
        settings.set("drives", [root for root, cb in self.drives.items() if cb.isChecked()])
        settings.set("leave_out", [p.strip() for p in self.leave.text().split(";") if p.strip()])
        settings.set("key_show", self.key.text().strip() or "Ctrl+Alt+Space")
        settings.set("open_in_pr7e", self.pr7e.isChecked())
        from .tray import start_with_windows
        from . import update_ui
        if sys.platform == "win32" and self.startup.isChecked() != start_with_windows():
            start_with_windows(self.startup.isChecked())
        update_ui.set_auto(self.updates.isChecked())
