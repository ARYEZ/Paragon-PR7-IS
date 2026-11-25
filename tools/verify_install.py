"""Check a real install of PR7-IS-Setup.exe (CI, Windows, as administrator): verify_install.py installed|uninstalled

`installed`: the files, the start-up entry, the scheduled task (the index service), the installed exe's --selftest on
a folder made here, and then the service itself: it reads the runner's drives through their MFT, and a file created
now must turn up in a search within a minute (the change journal). `uninstalled`: all of it gone, task included."""
import json
import os
import subprocess
import sys
import tempfile
import time
import winreg
from pathlib import Path

HKCU = winreg.HKEY_CURRENT_USER
APP = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "PR7-IS"
SHORTCUT = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Paragon PR7-IS.lnk"
UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{187DC039-E485-4BF2-9C1B-6026AF54C9F8}_is1"
RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
TASK = "Paragon\\PR7-IS Index"


def value(path, name=""):
    try:
        with winreg.OpenKey(HKCU, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def check(ok, what, p):
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        p.append(what)


def task_exists() -> bool:
    return subprocess.run(["schtasks", "/Query", "/TN", TASK], capture_output=True).returncode == 0


def query(text: str) -> dict:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "q.json"
        t = time.time()
        try:
            subprocess.run([str(APP / "PR7-IS.exe"), "--query", text, str(out)], timeout=60, check=False)
        except subprocess.TimeoutExpired:
            return {"error": "--query itself didn't finish in 60 s"}
        got = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"error": "no output"}
        if got.get("error") or time.time() - t > 5:
            print(f"note  --query {text!r} took {time.time() - t:.1f} s: {got.get('error') or 'ok'}")
        return got


def service_log():
    log = Path(os.environ["LOCALAPPDATA"]) / "Paragon" / "PR7-IS" / "service.log"
    print(f"--- {log}:\n" + (log.read_text(encoding="utf-8", errors="replace") if log.exists() else "(none)"))


def installed():
    p = []
    exe = APP / "PR7-IS.exe"
    for f in (exe, APP / "unins000.exe", APP / "_internal" / "pr7is" / "fonts" / "Inter-Regular.ttf"):
        check(f.is_file(), f"file {f}", p)
    check(SHORTCUT.is_file(), "Start menu shortcut", p)
    check(value(UNINSTALL, "DisplayName") == "Paragon PR7-IS", "Installed apps entry", p)
    run = value(RUN, "Paragon PR7-IS") or ""
    check(str(exe).lower() in run.lower() and "--tray" in run, f"starts with Windows: {run}", p)
    check(task_exists(), "the index service's scheduled task", p)
    with tempfile.TemporaryDirectory() as d:
        base = Path(d) / "known"
        (base / "Docs").mkdir(parents=True)
        (base / "Videos").mkdir()
        (base / "Docs" / "report 2026.pdf").write_bytes(b"x" * 100)
        (base / "Videos" / "clip.mov").write_bytes(b"\0" * 2_000_000)
        out = Path(d) / "out.json"
        subprocess.run([str(exe), "--selftest", str(base), str(out)], timeout=180, check=False)
        got = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        check(got.get("found") == [str(base / "Docs" / "report 2026.pdf")]
              and got.get("big") == [str(base / "Videos" / "clip.mov")] and got.get("fonts") is True,
              f"the window indexes and searches a folder ({got})", p)
    # the service: started by the task (an interactive sign-in starts it; a CI runner may have none, so after a
    # minute it's started here the same way the task would), then reading C: through its MFT takes a little while
    st = {}
    started_here = False
    silent = 0
    first = time.time() + 60
    deadline = time.time() + 360
    while time.time() < deadline:
        if not started_here and time.time() > first and not query("x").get("service"):
            print("note  the task didn't start the service (no interactive session here): starting it directly")
            # detached, as the task starts it: inheriting this step's output pipe would keep the CI step open forever
            subprocess.Popen([str(exe), "--index-service"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True,
                             creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
            started_here = True
        st = query("pr7is-nothing-is-called-this")
        if st.get("service") and st.get("status", {}).get("state") == "ready":
            break
        err = st.get("error", "")
        silent = silent + 1 if any(w in err for w in ("no answer", "didn't answer", "didn't finish")) else 0
        if silent >= 3:                    # it's there but doesn't answer: say why now, not after every loop
            print("FAIL  the service is running but doesn't answer")
            service_log()
            p.append("the service doesn't answer")
            return p
        time.sleep(3)
    status = st.get("status", {})
    modes = {v[-1] for v in status.get("volumes", [])}
    check(st.get("service") is True and status.get("state") == "ready" and "mft" in modes
          and status.get("objects", 0) > 100_000,
          f"the service read the drives through their MFT: {status.get('objects', 0):,} objects ({status})", p)
    # %TEMP% is a short 8.3 path on the runner (C:\Users\RUNNER~1\...); the index has the real names
    marker = Path(os.environ["TEMP"]).resolve() / f"pr7is journal {int(time.time())}.txt"
    marker.write_text("hello")
    found = False
    r = {}
    for _ in range(30):
        r = query(marker.stem)
        if r.get("total") == 1 and [x.lower() for x in r.get("paths", [])] == [str(marker).lower()]:
            found = True
            break
        time.sleep(2)
    check(found, f"a new file turns up through the change journal: {marker} (last answer: {r})", p)
    if p:
        service_log()
    return p


def uninstalled():
    deadline = time.time() + 60
    while True:
        p = []
        check(not (APP / "PR7-IS.exe").exists(), "program files removed", p)
        check(not SHORTCUT.exists(), "Start menu shortcut removed", p)
        check(value(UNINSTALL, "DisplayName") is None, "Installed apps entry removed", p)
        check(value(RUN, "Paragon PR7-IS") is None, "start-with-Windows entry removed", p)
        check(not task_exists(), "the index service's task removed", p)
        if not p or time.time() > deadline:
            return p
        print("... waiting for the uninstaller to finish")
        time.sleep(3)


if __name__ == "__main__":
    problems = {"installed": installed, "uninstalled": uninstalled}[sys.argv[1]]()
    print("\nALL OK" if not problems else f"\n{len(problems)} problem(s)")
    sys.exit(1 if problems else 0)
