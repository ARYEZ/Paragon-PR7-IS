"""PR7-IS in the notification area (Everything's "run in background"): the key that opens the window from any
program (Ctrl+Alt+Space), and Windows' start-up entry. Closing the window then hides it here."""

from __future__ import annotations

import sys

from PySide6.QtCore import QObject
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import hotkeys, settings

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def start_with_windows(on: bool | None = None) -> bool:
    """Read (on=None) or set the HKCU Run entry that starts PR7-IS in the notification area at sign-in."""
    if sys.platform != "win32":
        return False
    import winreg
    exe = f'"{sys.executable}" --tray' if getattr(sys, "frozen", False) else \
        f'"{sys.executable.replace("python.exe", "pythonw.exe")}" -m pr7is --tray'
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS) as k:
        if on is None:
            try:
                winreg.QueryValueEx(k, "Paragon PR7-IS")
                return True
            except OSError:
                return False
        if on:
            winreg.SetValueEx(k, "Paragon PR7-IS", 0, winreg.REG_SZ, exe)
        else:
            try:
                winreg.DeleteValue(k, "Paragon PR7-IS")
            except OSError:
                pass
        return on


class Tray(QObject):
    def __init__(self, window, show_tray: bool = True):
        super().__init__()
        self.window = window
        self.hotkeys = hotkeys.Hotkeys()
        m = QMenu()
        self.show_act = m.addAction("", self.show_window)
        m.addAction("Options...", lambda: (self.show_window(), window.options()))
        m.addSeparator()
        m.addAction("Exit", self.quit)
        self.menu = m
        self.tray = None
        if show_tray and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(QApplication.windowIcon() or QIcon(), self)
            self.tray.setToolTip("Paragon PR7-IS")
            self.tray.setContextMenu(m)
            self.tray.activated.connect(lambda r: self.show_window()
                                        if r == QSystemTrayIcon.ActivationReason.Trigger else None)
            self.tray.show()
            window.keep_in_tray = True
        self.problems = self.register()

    def register(self) -> dict:
        key = settings.get("key_show", "Ctrl+Alt+Space")
        self.show_act.setText(f"Search\t{key}")
        return self.hotkeys.register({"show": (key, self.show_window)})

    def show_window(self):
        w = self.window
        w.showNormal(); w.raise_(); w.activateWindow()
        w.search.setFocus(); w.search.selectAll()

    def quit(self):
        self.hotkeys.unregister()
        self.window.keep_in_tray = False
        QApplication.instance().quit()
