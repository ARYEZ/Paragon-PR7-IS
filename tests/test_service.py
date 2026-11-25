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
    env = dict(os.environ, PR7IS_PIPE=f"pr7is-nobody-{os.getpid()}")
    t = time.time()
    code = subprocess.run([sys.executable, "-m", "pr7is", "--query", "x", str(out)], cwd=root, env=env,
                          timeout=60).returncode
    assert code == 1 and json.loads(out.read_text()) == {"service": False} and time.time() - t < 15


def test_the_service_keeps_a_log(service, tmp_path):
    log = (tmp_path / "svc" / "service.log").read_text(encoding="utf-8")       # the fixture's data folder
    assert "listening on" in log and "built in" in log
