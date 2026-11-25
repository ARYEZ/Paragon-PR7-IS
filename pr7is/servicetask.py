"""The index service's start-up: a Task Scheduler task, "Paragon\\PR7-IS Index", that starts PR7-IS.exe
--index-service at sign-in with the user's highest rights, hidden, with no time limit (and again if it fails).

Everything installs a Windows service for the same reason: reading a drive's own index needs administrator rights,
and asking for them at every search would be absurd. A task "run with highest privileges" gives the same thing
without a Windows service framework (pywin32 services are fragile in a packaged .exe): creating it asks once
(administrator rights are needed to create such a task); afterwards Windows starts it silently at every sign-in.
For a standard (non-administrator) account it simply runs without the rights, and the service walks folders."""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape

TASK = "Paragon\\PR7-IS Index"


def command() -> tuple[str, str]:
    """(program, arguments) that start the service."""
    if getattr(sys, "frozen", False):
        return sys.executable, "--index-service"
    return sys.executable.replace("python.exe", "pythonw.exe"), "-m pr7is --index-service"


def user() -> str:
    domain = os.environ.get("USERDOMAIN", "")
    name = getpass.getuser()
    return f"{domain}\\{name}" if domain else name


def task_xml(program: str, arguments: str, account: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Paragon PR7-IS index service: reads the drives' own index, so searching needs no administrator rights.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{escape(account)}</UserId></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(account)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <Hidden>true</Hidden>
    <RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(program)}</Command><Arguments>{escape(arguments)}</Arguments></Exec>
  </Actions>
</Task>
"""


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, check=False,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def installed() -> bool:
    return sys.platform == "win32" and _schtasks("/Query", "/TN", TASK).returncode == 0


def install(start: bool = True) -> str:
    """Create (or replace) the task and start it now; "" on success, else what went wrong. Needs the rights."""
    if sys.platform != "win32":
        return "The index service is a Windows feature."
    program, arguments = command()
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as fh:
        fh.write(task_xml(program, arguments, user()))
        xml = fh.name
    try:
        r = _schtasks("/Create", "/TN", TASK, "/XML", xml, "/F")
    finally:
        os.remove(xml)
    if r.returncode != 0:
        return (r.stderr or r.stdout).strip() or "The task couldn't be created."
    if start:
        _schtasks("/Run", "/TN", TASK)
    return ""


def remove() -> str:
    if sys.platform != "win32":
        return ""
    _schtasks("/End", "/TN", TASK)
    r = _schtasks("/Delete", "/TN", TASK, "/F")
    return "" if r.returncode == 0 or not installed() else (r.stderr or r.stdout).strip()


def elevated(argument: str) -> bool:
    """Run PR7-IS once more with administrator rights (Windows asks) to install or remove the task; waits for it."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]
    program = sys.executable
    params = argument if getattr(sys, "frozen", False) else f"-m pr7is {argument}"
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40                                          # SEE_MASK_NOCLOSEPROCESS: wait for it
    info.lpVerb, info.lpFile, info.lpParameters, info.nShow = "runas", program, params, 0
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        return False                                           # declined
    ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 60000)
    code = wintypes.DWORD()
    ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(info.hProcess)
    return code.value == 0
