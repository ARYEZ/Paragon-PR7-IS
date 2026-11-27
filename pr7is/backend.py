"""Where searches go: the index service (when it runs), else an index inside the window.

The service (`run_service`, started by the scheduled task with administrator rights) keeps the index and answers on
a loopback TCP port (127.0.0.1 only): one JSON line in ({"op": "search", ...} / "status" / "reload" / "quit", each
with the token), one JSON line out, as Everything's window talks to its service. The port and a random token are in
<data>/service.json, which only this user can read: that keeps other users out, and an elevated service and the
window (not elevated) reach each other without the access rules of Windows' named pipes. Plain sockets also give real
timeouts (Qt's Windows pipe client blocked every Python thread on the CI runner).

Without the service, `LocalBackend` builds the same index in the window's process (a folder walk, or the MFT when
PR7-IS itself runs as administrator), saves it, and keeps it up to date by rebuilding in the background."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import sys
import threading
import time

from . import settings
from .engine import Engine, fixed_drives

TIMEOUT = 4.0


def address_file() -> str:
    """Where the running service says how to reach it. PR7IS_ADDRESS (tests): their own service, never the real one."""
    if os.environ.get("PR7IS_ADDRESS"):
        return os.environ["PR7IS_ADDRESS"]
    return os.path.join(data_folder(), "service.json")


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
    default_leave = [r"C:\Windows\WinSxS", "*.tmp", "$Recycle.Bin"] if sys.platform == "win32" else \
        [os.path.join(os.path.expanduser("~"), ".local", "share", "Trash"), ".cache", "*.tmp"]
    leave = settings.get_list("leave_out") or default_leave
    return drives, leave


class ServiceBackend:
    """The running service, through its port. `connect()` False: no service."""

    name = "service"

    def __init__(self, address: str | None = None):
        self.address = address or address_file()
        self.sock: socket.socket | None = None
        self.token = ""
        self._buf = b""
        self._timeout = TIMEOUT

    def connect(self, timeout: float = 2.0) -> bool:
        try:
            with open(self.address, encoding="utf-8") as fh:
                where = json.load(fh)
            self.sock = socket.create_connection(("127.0.0.1", int(where["port"])), timeout=timeout)
            self.token = where["token"]
            self._buf = b""
            self._timeout = timeout                     # a quick hello: a stale file may point at someone else's port
            ok = self._ask({"op": "hello"}).get("hello") == "PR7-IS"
            self._timeout = TIMEOUT
            if not ok:
                self.close()
            return ok
        except (OSError, ValueError, KeyError, ConnectionError):
            self.close()
            return False

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def _ask(self, msg: dict) -> dict:
        s = self.sock
        if s is None:
            raise ConnectionError("Not connected to the index service.")
        try:
            s.settimeout(self._timeout)
            s.sendall(json.dumps(msg | {"token": self.token}).encode() + b"\n")
            while b"\n" not in self._buf:
                chunk = s.recv(1 << 20)
                if not chunk:
                    raise ConnectionError("The index service closed the connection.")
                self._buf += chunk
        except OSError as e:                          # socket.timeout included
            self.close()
            raise ConnectionError(f"The index service didn't answer ({e}).") from None
        line, _, self._buf = self._buf.partition(b"\n")
        reply = json.loads(line.decode())
        if reply.get("error") == "token":
            raise ConnectionError("The index service refused this connection.")
        return reply

    def search(self, query, kind="everything", sort="name", descending=False, offset=0, limit=200) -> dict:
        return self._ask({"op": "search", "q": query, "kind": kind, "sort": sort, "desc": descending,
                          "offset": offset, "limit": limit})

    def status(self) -> dict:
        return self._ask({"op": "status"})

    def reload(self):
        self._ask({"op": "reload"})

    def quit(self, save: bool = True):
        try:
            self._ask({"op": "quit", "save": save})
        except ConnectionError:
            pass


def service_pid(address: str | None = None) -> int | None:
    try:
        with open(address or address_file(), encoding="utf-8") as fh:
            return int(json.load(fh)["pid"])
    except (OSError, ValueError, KeyError):
        return None


def wait_exit(pid: int, timeout: float, kill: bool = False) -> bool:
    """True when process `pid` has ended within `timeout` s (then, with `kill`, ended by force if it could be).
    Windows: OpenProcess + WaitForSingleObject (os.kill(pid, 0) there would *end* the process, not test it)."""
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        h = k32.OpenProcess(0x00100000 | 0x0001, False, pid)     # SYNCHRONIZE | PROCESS_TERMINATE
        if not h:
            h = k32.OpenProcess(0x00100000, False, pid)
            if not h:
                return True                                     # no such process (or not ours to see)
        try:
            if k32.WaitForSingleObject(ctypes.c_void_p(h), int(timeout * 1000)) == 0:
                return True
            if kill and k32.TerminateProcess(ctypes.c_void_p(h), 1):
                return k32.WaitForSingleObject(ctypes.c_void_p(h), 5000) == 0
            return False
        finally:
            k32.CloseHandle(ctypes.c_void_p(h))
    end = time.time() + timeout
    while time.time() < end:
        try:
            os.kill(pid, 0)
            with open(f"/proc/{pid}/stat") as fh:           # ended but not yet collected by its parent
                if fh.read().rsplit(")", 1)[-1].split()[0] == "Z":
                    return True
        except ProcessLookupError:
            return True
        except (PermissionError, OSError):
            pass
        time.sleep(0.1)
    if kill:
        import signal
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return True
        return wait_exit(pid, 5)
    return False


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
def log(text: str):
    """A line in <data>/service.log (kept under 1 MB): what the service did, for when something goes wrong."""
    try:
        path = os.path.join(data_folder(), "service.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
            os.replace(path, path + ".old")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
    except OSError:
        pass


def run_service(argv=None) -> int:
    """PR7-IS.exe --index-service: keep the index (MFT + change journals when allowed), answer the window."""
    from PySide6.QtCore import QLockFile

    drives, leave = options()
    engine = Engine(drives, leave, os.path.join(data_folder(), "index.bin"))
    log(f"starting: drives {drives}, leave out {leave}")
    # one service per user, decided by a lock file (released when the process ends, however it ends)
    os.makedirs(data_folder(), exist_ok=True)
    only = QLockFile(os.path.join(data_folder(), "service.lock"))
    only.setStaleLockTime(0)
    if not only.tryLock(0):
        log("another index service is running already: leaving")
        return 1
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":                    # nobody else may take the port over while it's ours
        server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(16)
    token = secrets.token_hex(16)
    where = address_file()
    os.makedirs(os.path.dirname(where) or ".", exist_ok=True)
    # only this user may read the token (Windows' user folders are private already; Linux's default is 0644)
    fd = os.open(where + ".part", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"port": server.getsockname()[1], "token": token, "pid": os.getpid()}, fh)
    os.replace(where + ".part", where)
    log(f"listening on 127.0.0.1:{server.getsockname()[1]}")
    building = threading.Event()
    stop = threading.Event()
    skip_save = threading.Event()

    def build():
        building.set()
        t = time.time()
        log("building the index")
        try:
            engine.build()
            log(f"built in {time.time() - t:.1f} s: {len(engine.index):,} objects, "
                f"{[(v.name, v.mode) for v in engine.volumes]}")
            building.clear()                            # live now: the journal goes on while it's saved
            engine.save()
        except Exception as e:  # noqa: BLE001 - logged; the window shows the state
            log(f"build failed: {type(e).__name__}: {e}")
        finally:
            building.clear()
    if engine.load():
        log(f"loaded the saved index: {len(engine.index):,} objects")
        if sys.platform != "win32":     # Linux has no journal to replay: walk again (answers go on from the saved one)
            threading.Thread(target=build, daemon=True).start()
    else:
        threading.Thread(target=build, daemon=True).start()

    def answer(msg: dict) -> dict:
        if not hmac.compare_digest(str(msg.get("token", "")), token):
            return {"error": "token"}
        op = msg.get("op")
        if op == "hello":
            return {"hello": "PR7-IS"}
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
            if not msg.get("save", True):                 # the uninstaller: the index goes with the program
                skip_save.set()
            stop.set()
            return {"ok": True}
        return {"error": f"unknown op {op!r}"}

    busy: dict[int, tuple[float, bytes]] = {}       # requests being answered: for the stall watchdog

    def serve(conn: socket.socket):
        buf = b""
        conn.settimeout(None)
        with conn:
            while not stop.is_set():
                try:
                    chunk = conn.recv(1 << 16)
                except OSError:
                    return
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    t = time.time()
                    me = threading.get_ident()
                    busy[me] = (t, line[:200])
                    try:
                        reply = answer(json.loads(line))
                    except Exception as e:  # noqa: BLE001 - the window shows it
                        reply = {"error": str(e)}
                        log(f"error answering {line[:200]!r}: {e}")
                    finally:
                        busy.pop(me, None)
                    if time.time() - t > 1:
                        log(f"slow answer ({time.time() - t:.1f} s): {line[:200]!r}")
                    try:
                        conn.sendall(json.dumps(reply).encode() + b"\n")
                    except OSError:
                        return

    def accept():
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except OSError:
                return
            threading.Thread(target=serve, args=(conn,), daemon=True).start()
    threading.Thread(target=accept, daemon=True).start()

    def watch():                       # a request unanswered for 10 s: every thread's stack goes into the log
        import faulthandler
        reported = set()
        while not stop.is_set():
            time.sleep(2)
            for ident, (t, line) in list(busy.items()):
                if time.time() - t > 10 and (ident, t) not in reported:
                    reported.add((ident, t))
                    log(f"no answer after {time.time() - t:.0f} s to {line!r}; every thread's stack:")
                    try:
                        with open(os.path.join(data_folder(), "service.log"), "a", encoding="utf-8") as fh:
                            faulthandler.dump_traceback(fh, all_threads=True)
                    except OSError:
                        pass
    threading.Thread(target=watch, daemon=True).start()

    last = [time.time()]

    def save():
        if engine.state == "ready" and engine.changed > last[0]:
            engine.save()
            last[0] = time.time()

    def keep_up():                     # the journal, the background rebuild, and saving every 10 minutes
        while not stop.is_set():
            time.sleep(1)
            if engine.state != "ready" or building.is_set():
                continue
            try:
                if engine.wants_rebuild:        # Linux: inotify's queue overflowed, so walk again
                    log("change notices overflowed: rebuilding")
                    threading.Thread(target=build, daemon=True).start()
                    continue
                t = time.time()
                n = engine.poll()
                if time.time() - t > 1:
                    log(f"slow journal read ({time.time() - t:.1f} s, {n} changes)")
                t = time.time()
                if engine.maintain():
                    log(f"search text rebuilt in {time.time() - t:.1f} s")
                if time.time() - last[0] > 600:
                    save()
            except Exception as e:  # noqa: BLE001 - logged; the next second tries again
                log(f"keeping up failed: {type(e).__name__}: {e}")
    threading.Thread(target=keep_up, daemon=True).start()

    stop.wait()
    log("quitting")
    server.close()
    try:
        if not skip_save.is_set():
            save()
    finally:
        try:
            os.remove(where)
        except OSError:
            pass
        only.unlock()
    return 0
