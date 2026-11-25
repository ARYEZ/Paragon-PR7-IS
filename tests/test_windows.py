"""What only real Windows can show (CI runs these, as administrator): the scheduled task and its service."""
import os
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32" or not os.environ.get("CI"),
                                reason="Windows CI only (creates a real scheduled task)")


def test_the_task_starts_the_service_and_goes_away():
    from pr7is import servicetask
    xml_task = servicetask.TASK
    try:
        assert servicetask.install(start=False) == "" and servicetask.installed()
        q = subprocess.run(["schtasks", "/Query", "/TN", xml_task, "/XML"], capture_output=True, text=True)
        assert "HighestAvailable" in q.stdout and "--index-service" in q.stdout
    finally:
        assert servicetask.remove() == ""
    assert not servicetask.installed()
