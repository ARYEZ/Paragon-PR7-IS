"""Start PR7-IS.

    PR7-IS.exe [search]           the window (in the running PR7-IS if there is one), searching for that
    PR7-IS.exe --tray             start in the notification area (Windows' start-up entry)
    PR7-IS.exe --index-service    the index service (the scheduled task starts it, with administrator rights)
    PR7-IS.exe --install-service | --remove-service    create / delete that task (needs administrator rights)
    PR7-IS.exe --startup on|off   start with Windows or not;  --unregister  removes that (the uninstaller)
    PR7-IS.exe --query TEXT OUT.json   search through the running service and write what it found (CI)
    PR7-IS.exe --selftest FOLDER OUT.json   index FOLDER in the window, offscreen, search it, write what came out (CI)
    PR7-IS.exe --version
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import sys

TIMEOUT_MS = 1500


def server_name() -> str:
    try:
        u = getpass.getuser()
    except Exception:  # noqa: BLE001
        u = "user"
    return "Paragon-PR7-IS-" + hashlib.sha1(u.encode()).hexdigest()[:12]


def hand_over(text: str | None) -> bool:
    from PySide6.QtNetwork import QLocalSocket
    s = QLocalSocket()
    s.connectToServer(server_name())
    if not s.waitForConnected(TIMEOUT_MS):
        return False
    s.write(json.dumps({"search": text}).encode() + b"\n")
    s.flush()
    s.waitForBytesWritten(TIMEOUT_MS)
    if not s.waitForDisconnected(TIMEOUT_MS):
        s.disconnectFromServer()
    return True


def listen(tray):
    """Later starts hand over to this PR7-IS (PR7-R's hand-off, with its Windows fix)."""
    from PySide6.QtNetwork import QLocalServer
    server = QLocalServer(tray)
    QLocalServer.removeServer(server_name())
    if not server.listen(server_name()):
        return None

    def connection():
        sock = server.nextPendingConnection()
        if sock is None:
            return
        buf = bytearray()
        done = []

        def read():
            buf.extend(bytes(sock.readAll()))
            if buf.endswith(b"\n") and not done:
                done.append(True)
                try:
                    msg = json.loads(buf.decode())
                except ValueError:
                    return
                tray.show_window()
                if msg.get("search") is not None:
                    tray.window.search.setText(msg["search"])
                sock.disconnectFromServer()
        sock.readyRead.connect(read)
        sock.disconnected.connect(read)
        if sock.bytesAvailable():
            read()
    server.newConnection.connect(connection)
    tray.instance_server = server
    return server


def query(text: str, out: str) -> int:
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    from . import backend
    s = backend.ServiceBackend()
    result = {"service": False}
    if s.connect():
        st = s.status()
        r = s.search(text, limit=50)
        result = {"service": True, "status": st, "total": r["total"],
                  "paths": [x["path"] for x in r["rows"]]}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    return 0 if result["service"] else 1


def selftest(folder: str, out: str) -> int:
    import tempfile
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["PR7IS_DATA"] = tempfile.mkdtemp()
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
    result = {}
    try:
        from . import __version__, backend, theme
        from .main_window import MainWindow
        app = QApplication.instance() or QApplication(sys.argv[:1])
        theme.load_fonts()
        from PySide6.QtGui import QFontDatabase
        b = backend.LocalBackend([folder], [], sync=True)
        b.start()
        w = MainWindow(b)
        w.show()
        w.search.setText("report")
        w.refresh()
        found = [w.model.path(i) for i in range(w.model.total)]
        w.search.setText("ext:mov size:>1mb")
        w.refresh()
        big = [w.model.path(i) for i in range(w.model.total)]
        result = {"version": __version__, "objects": w.model.objects, "found": found, "big": big,
                  "fonts": "Inter" in QFontDatabase.families()}
        w.close()
    except Exception as e:  # noqa: BLE001 - reported in the output
        result = {"error": f"{type(e).__name__}: {e}"}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    return 0 if "objects" in result else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--version"]:
        from . import __version__
        print(__version__)
        return 0
    if argv[:1] == ["--selftest"] and len(argv) == 3:
        return selftest(argv[1], argv[2])
    if argv[:1] == ["--query"] and len(argv) == 3:
        return query(argv[1], argv[2])
    if argv[:1] == ["--index-service"]:
        from .backend import run_service
        return run_service()
    if argv[:1] == ["--install-service"]:
        from . import servicetask
        problem = servicetask.install()
        return 0 if not problem else 1
    if argv[:1] == ["--remove-service"]:
        from . import servicetask
        return 0 if not servicetask.remove() else 1
    if argv[:1] == ["--uninstall-service"]:              # the uninstaller: asks for the rights only if there's a task
        from . import backend, servicetask
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
        s = backend.ServiceBackend()
        if s.connect():
            s.quit()
        if servicetask.installed() and servicetask.remove():
            servicetask.elevated("--remove-service")
        return 0
    if argv[:1] == ["--startup"] and len(argv) == 2:
        from .tray import start_with_windows
        start_with_windows(argv[1].lower() in ("on", "1", "yes"))
        return 0
    if argv[:1] == ["--unregister"]:
        from .tray import start_with_windows
        start_with_windows(False)
        return 0
    quiet = "--tray" in argv
    text = " ".join(a for a in argv if not a.startswith("--")) or None
    from PySide6.QtWidgets import QApplication

    from . import backend, settings, theme
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setOrganizationName("Paragon")
    app.setApplicationName("PR7-IS")
    app.setQuitOnLastWindowClosed(False)
    if hand_over(text if not quiet else None) if not quiet else hand_over_quiet():
        return 0
    theme.set_finish(settings.get("finish", "obsidian"))
    theme.load_fonts()
    from PySide6.QtGui import QIcon
    icon = os.path.join(os.path.dirname(__file__), "icons", "app.ico")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    from .main_window import MainWindow
    from .tray import Tray
    w = MainWindow(backend.connect())
    tray = Tray(w)
    listen(tray)
    if not tray.tray:
        app.setQuitOnLastWindowClosed(True)
    if not quiet:
        tray.show_window()
        if text:
            w.search.setText(text)
        w.refresh()
    from . import update_ui
    update_ui.start(w)
    return app.exec()


def hand_over_quiet() -> bool:
    """A second --tray start (Windows' start-up entry while one runs): nothing to do."""
    from PySide6.QtNetwork import QLocalSocket
    s = QLocalSocket()
    s.connectToServer(server_name())
    return s.waitForConnected(300)


if __name__ == "__main__":
    sys.exit(main())
