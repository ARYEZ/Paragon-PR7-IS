import json
import os
import subprocess
import sys
import time

from pr7is import servicetask


def test_the_service_answers_and_keeps_up(service):
    b, base, proc = service
    st = b.status()
    assert st["service"] and st["state"] == "ready" and st["objects"] > 10
    r = b.search("elseworld")
    assert r["total"] == 2 and {x["name"] for x in r["rows"]} == {"Elseworld shoot", "Elseworld_v3.mp4"}
    assert b.search("cache")["total"] == 0                       # *.tmp left out (PR7IS_LEAVE_OUT)
    page = b.search("", offset=3, limit=4)
    assert len(page["rows"]) == 4 and page["total"] == st["objects"]
    (base / "Docs" / "brand new.txt").write_text("x")
    b.reload()                                                     # a folder index: Options' REBUILD
    end = time.time() + 20
    while time.time() < end and b.search("brand new")["total"] == 0:
        time.sleep(0.2)
    assert b.search("brand new")["total"] == 1


def test_query_from_the_command_line(service, tmp_path):
    b, base, proc = service
    out = tmp_path / "q.json"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = subprocess.run([sys.executable, "-m", "pr7is", "--query", "invoice", str(out)], cwd=root, timeout=60,
                          env=dict(os.environ)).returncode
    got = json.loads(out.read_text())
    assert code == 0 and got["service"] and got["paths"] == [str(base / "Docs" / "Invoice 2026-114.pdf")]


def test_the_scheduled_task_definition():
    import xml.etree.ElementTree as ET
    xml = servicetask.task_xml("C:\\Programs\\PR7-IS\\PR7-IS.exe", "--index-service", "PC\\me & you")
    root = ET.fromstring(xml.replace('encoding="UTF-16"', ""))
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert root.find(".//t:RunLevel", ns).text == "HighestAvailable"          # the rights, without asking
    assert root.find(".//t:LogonTrigger/t:UserId", ns).text == "PC\\me & you"
    assert root.find(".//t:Exec/t:Arguments", ns).text == "--index-service"
    assert root.find(".//t:ExecutionTimeLimit", ns).text == "PT0S" and root.find(".//t:Hidden", ns).text == "true"


def test_query_without_a_service_says_so_at_once(tmp_path, monkeypatch):
    out = tmp_path / "q.json"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PR7IS_ADDRESS=str(tmp_path / "nobody" / "service.json"))
    t = time.time()
    code = subprocess.run([sys.executable, "-m", "pr7is", "--query", "x", str(out)], cwd=root, env=env,
                          timeout=60).returncode
    assert code == 1 and json.loads(out.read_text()) == {"service": False} and time.time() - t < 15


def test_the_service_keeps_a_log(service, tmp_path):
    log = (tmp_path / "svc" / "service.log").read_text(encoding="utf-8")       # the fixture's data folder
    assert "listening on 127.0.0.1:" in log and "built in" in log


def test_only_one_service_runs(service, tmp_path):
    b, base, proc = service
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PR7IS_DATA=str(tmp_path / "svc"), PR7IS_DRIVES=str(base))
    second = subprocess.run([sys.executable, "-m", "pr7is", "--index-service"], cwd=root, env=env, timeout=60)
    assert second.returncode == 1 and proc.poll() is None                # it left; the first one goes on
    assert b.status()["service"]
    assert "running already: leaving" in (tmp_path / "svc" / "service.log").read_text(encoding="utf-8")


def test_the_service_needs_its_token(service, tmp_path):
    import socket
    b, base, proc = service
    where = json.loads((tmp_path / "svc" / "service.json").read_text())
    with socket.create_connection(("127.0.0.1", where["port"]), timeout=5) as s:     # another user's program
        s.sendall(b'{"op": "search", "q": "elseworld", "token": "guess"}\n')
        assert json.loads(s.recv(4096).decode()) == {"error": "token"}
    assert b.search("elseworld")["total"] == 2                                    # the right token still works


def test_a_stale_address_file_means_no_service(tmp_path):
    import socket
    from pr7is import backend
    other = socket.socket(); other.bind(("127.0.0.1", 0)); other.listen(1)        # some other program's port
    f = tmp_path / "service.json"
    f.write_text(json.dumps({"port": other.getsockname()[1], "token": "x"}))
    t = time.time()
    assert not backend.ServiceBackend(str(f)).connect(timeout=1) and time.time() - t < 3
    other.close()


def test_command_line_modes_work_without_a_stdout(tmp_path):
    """The installed exe is windowed: sys.stdout is None. An error there opens a box that waits for a click (CI hung
    on one for 60 s), so --query must neither need a stdout nor let an error escape."""
    out = tmp_path / "q.json"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PR7IS_ADDRESS=str(tmp_path / "none.json"), PR7IS_DATA=str(tmp_path / "data"))
    code = ("import sys; sys.stdout = None; sys.stderr = None\n"
            "from pr7is import app\n"
            f"raise SystemExit(app.main(['--query', 'x', {str(out)!r}]))")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, timeout=60)
    assert r.returncode == 1 and json.loads(out.read_text()) == {"service": False}
    code = ("import sys; sys.stdout = None; sys.stderr = None\n"
            "from pr7is import app\n"
            "raise SystemExit(app.main(['--selftest', '/no/such/folder/at/all', '/no/such/out.json']))")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, timeout=120)
    assert r.returncode in (1, 2)                                  # an error code, never a hang


def test_uninstalling_ends_the_service_without_saving(service, tmp_path):
    b, base, proc = service
    saved = tmp_path / "svc" / "index.bin"
    before = saved.stat().st_mtime_ns if saved.exists() else None
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    t = time.time()
    r = subprocess.run([sys.executable, "-m", "pr7is", "--uninstall-service"], cwd=root, timeout=60,
                       env=dict(os.environ, PR7IS_DATA=str(tmp_path / "svc")))
    assert r.returncode == 0 and proc.wait(5) == 0 and time.time() - t < 15     # gone when the uninstaller goes on
    assert (saved.stat().st_mtime_ns if saved.exists() else None) == before     # not saved on the way out
    assert not (tmp_path / "svc" / "service.json").exists()
