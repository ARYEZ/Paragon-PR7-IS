"""Where searches go: the index service (when it runs), else an index inside the window.

The service (`run_service`, started by the scheduled task with administrator rights) keeps the index and answers on
a local socket named per user: one JSON line in ({"op": "search", ...} / "status" / "reload" / "quit"), one JSON line
out, as Everything's window talks to its service. The socket is open to every local user (WorldAccessOption):
the service runs elevated, the window doesn't, and Windows otherwise keeps the two apart.

Without the service, `LocalBackend` builds the same index in the window's process (a folder walk, or the MFT when
PR7-IS itself runs as administrator), saves it, and keeps it up to date by rebuilding in the background."""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import sys
import threading
import time

from . import settings
from .engine import Engine, fixed_drives

TIMEOUT_MS = 4000


def pipe_name() -> str:
    if os.environ.get("PR7IS_PIPE"):                 # tests: their own service, never the real one
        return os.environ["PR7IS_PIPE"]
    try:
        u = getpass.getuser()
    except Exception:  # noqa: BLE001
        u = "user"
    return "Paragon-PR7-IS-index-" + hashlib.sha1(u.encode()).hexdigest()[:12]


def data_folder() -> str:
    env = os.environ.get("PR7IS_DATA")
    if env:
        return env
    base = os.environ.get("LOCALAPPDATA") if sys.platform == "win32" else os.path.join(os.path.expanduser("~"),
                                                                                         ".local", "share")
    return os.path.join(base or os.path.expanduser("~"), "Paragon", "PR7-IS")


def options() -> tuple[list[str], list[str]]:
    """(drives, leave out) from Options; every fixed drive when none were chosen. PR7IS_DRIVES / PR7IS_LEAVE_OUT
    (os.pathsep-separated) override them for tests and CI."""
    if os.environ.get("PR7IS_DRIVES"):
        return (os.environ["PR7IS_DRIVES"].split(os.pathsep),
                [x for x in os.environ.get("PR7IS_LEAVE_OUT", "").split(os.pathsep) if x])
    drives = settings.get_list("drives") or fixed_drives()
    leave = settings.get_list("leave_out") or [r"C:\Windows\WinSxS", "*.tmp", "$Recycle.Bin"]
    return drives, leave


class ServiceBackend:
    """The running service, through its socket. `connect()` False: no service."""

    name = "service"

    def __init__(self, pipe: str | None = None):
        self.pipe = pipe or pipe_name()
        self.sock = None

    def connect(self) -> bool:
        from PySide6.QtNetwork import QLocalSocket
        s = QLocalSocket()
        s.connectToServer(self.pipe)
        if not s.waitForConnected(500):
            return False
        self.sock = s
        return True

    def _ask(self, msg: dict) -> dict:
        s = self.sock
        if s is None:
            raise ConnectionError("Not connected to the index service.")
        s.write(json.dumps(msg).encode() + b"\n")
        s.flush()
        buf = bytearray()
        end = time.monotonic() + TIMEOUT_MS / 1000
        while not buf.endswith(b"\n"):
            if not s.waitForReadyRead(int(max(1, (end - time.monotonic()) * 1000))):
                raise ConnectionError("The index service didn't answer.")
            buf.extend(bytes(s.readAll()))
        return json.loads(buf.decode())

    def search(self, query, kind="everything", sort="name", descending=False, offset=0, limit=200) -> dict:
        return self._ask({"op": "search", "q": query, "kind": kind, "sort": sort, "desc": descending,
                          "offset": offset, "limit": limit})

    def status(self) -> dict:
        return self._ask({"op": "status"})

    def reload(self):
        self._ask({"op": "reload"})

    def quit(self):
        try:
            self._ask({"op": "quit"})
        except ConnectionError:
            pass


class LocalBackend:
    """The index in this process: loaded from its cache at once, built (or rebuilt) on a thread."""

    name = "local"

    def __init__(self, drives=None, leave_out=None, cache=None, sync=False):
        d, l = options()
        self.engine = Engine(drives if drives is not None else d, leave_out if leave_out is not None else l,
                             cache or os.path.join(data_folder(), "index-local.bin"))
        self.sync = sync
        self.progress = None
        self._thread = None

    def start(self):
        if not self.engine.load():
            self.rebuild()

    def rebuild(self):
        def work():
            self.engine.build(lambda p: setattr(self, "progress", p))
            self.engine.save()
        if self.sync:
            work()
        else:
            self._thread = threading.Thread(target=work, daemon=True)
            self._thread.start()

    def search(self, query, kind="everything", sort="name", descending=False, offset=0, limit=200) -> dict:
        return self.engine.search(query, kind, sort, descending, offset, limit)

    def status(self) -> dict:
        return self.engine.status()

    def reload(self):
        d, l = options()
        from .engine import Skip
        self.engine.drives, self.engine.skip = d, Skip(l)
        self.rebuild()

    def quit(self):
        pass


def connect(local_ok: bool = True, **local_args):
    """The service when it runs, else (local_ok) an index in this process."""
    s = ServiceBackend()
    if s.connect():
        return s
    if not local_ok:
        return None
    b = LocalBackend(**local_args)
    b.start()
    return b


# -------------------------------------------------------------------- the service itself
def run_service(argv=None) -> int:
    """PR7-IS.exe --index-service: keep the index (MFT + change journals when allowed), answer the window."""
    from PySide6.QtCore import QCoreApplication, QTimer
    from PySide6.QtNetwork import QLocalServer

    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    app.setOrganizationName("Paragon")
    app.setApplicationName("PR7-IS")
    drives, leave = options()
    engine = Engine(drives, leave, os.path.join(data_folder(), "index.bin"))
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.WorldAccessOption)
    QLocalServer.removeServer(pipe_name())
    if not server.listen(pipe_name()):
        return 1                                               # one is running already
    building = threading.Event()

    def build():
        building.set()
        try:
            engine.build()
            engine.save()
        finally:
            building.clear()
    if not engine.load():
        threading.Thread(target=build, daemon=True).start()

    def answer(msg: dict) -> dict:
        op = msg.get("op")
        if op == "search":
            return engine.search(msg.get("q", ""), msg.get("kind", "everything"), msg.get("sort", "name"),
                                 bool(msg.get("desc")), int(msg.get("offset", 0)), int(msg.get("limit", 200)))
        if op == "status":
            return engine.status() | {"service": True, "building": building.is_set()}
        if op == "reload":
            from .engine import Skip
            settings.sync()
            engine.drives, leave_ = options()
            engine.skip = Skip(leave_)
            if not building.is_set():
                threading.Thread(target=build, daemon=True).start()
            return {"ok": True}
        if op == "quit":
            QTimer.singleShot(100, app.quit)
            return {"ok": True}
        return {"error": f"unknown op {op!r}"}

    def connection():
        sock = server.nextPendingConnection()
        if sock is None:
            return
        buf = bytearray()

        def read():
            buf.extend(bytes(sock.readAll()))
            while b"\n" in buf:
                line, _, rest = bytes(buf).partition(b"\n")
                buf[:] = rest
                try:
                    reply = answer(json.loads(line))
                except Exception as e:  # noqa: BLE001 - the window shows it
                    reply = {"error": str(e)}
                sock.write(json.dumps(reply).encode() + b"\n")
                sock.flush()
        sock.readyRead.connect(read)
        if sock.bytesAvailable():
            read()
    server.newConnection.connect(connection)

    poll = QTimer()
    poll.timeout.connect(lambda: engine.poll() if engine.state == "ready" and not building.is_set() else None)
    poll.start(1000)
    saver = QTimer()
    last = [time.time()]

    def save():
        if engine.state == "ready" and engine.changed > last[0]:
            engine.save()
            last[0] = time.time()
    saver.timeout.connect(save)
    saver.start(600_000)
    app.aboutToQuit.connect(save)
    return app.exec()
