# Paragon PR7-IS: notes for Claude

**Paragon PR7-IS** is the owner's personal file search: Python (**PySide6**) that **works the way Everything works**,
in the Paragon family look (PR7-Z ... PR7-Q, PR7-U, PR7-DU, PR7-CB `ARYEZ/Paragon-PR7-CB`). The repo is
Paragon-PR7-IS. Windows 10/11 x64 is the target; it runs on Linux for tests. The owner writes Python; explain the why.

## How it's built

- `pr7is/index.py` (no Qt): `Index` = parallel lists (names, parent, size, mtime, isdir) plus `keys` (volume, MFT
  record) -> entry. Names live casefolded in one `blob` string (one per line); a word is found with `str.find` over
  the blob and mapped back by `bisect` on `_starts`, which is what makes 1M+ names answer in ~10-40 ms. The first
  plain word gives the candidates and isn't checked again. `glob_regex` is ours (Python 3.13 changed
  `fnmatch.translate`). **Changes never rebuild the blob or the name order** (1M+ names take seconds, and the journal
  changes something every second): added / renamed entries go into `_recent` (checked one by one), removed ones are
  just None; `Engine.maintain` rebuilds from a copy outside the lock (`compact_start` / `build` / `compact_finish`,
  `_touched` = what changed meanwhile). Folder contents via the lazily built `_kids` map. `parse` = Everything's syntax (words, `|`, `!`, wildcards, path words, ext:, size:, dm:,
  file:, folder:, quotes). `path()` joins drive roots with `\` on any system.
- `pr7is/mft.py` (no Qt): PR7-DU's NTFS reader as `records()` + `fill(index, recs, volume, skip)` (parents first).
  `pr7is/usn.py`: USN_RECORD_V2 parsing (`parse`, `record` builds test records) and the journal through
  DeviceIoControl (`query`, `read`). Tests use real NTFS images (mkntfs + ntfs-3g, as PR7-DU).
- `pr7is/engine.py` (no Qt): `Engine(drives, leave_out, cache, use_mft)`: `build` (MFT when admin + NTFS + a drive
  root, else the walk), `poll` (the journal) -> `apply` (create / rename / move / delete, folders with their
  contents), `search`, `status`, `save` / `load` (pickle + zlib, `CACHE_VERSION`; rejected when drives or a
  journal id changed). `Skip` = Leave out.
- **The service is a scheduled task, not a Windows service** (`servicetask.py`, "Paragon\PR7-IS Index": logon
  trigger, HighestAvailable, hidden, restart on failure): pywin32 services are fragile when frozen, and a task
  needs Windows' permission only once, at install (`elevated` = ShellExecuteEx "runas"). `backend.run_service`
  is `--index-service`: a loopback TCP port (127.0.0.1) written with a random token to <data>/service.json (only this
  user can read it; not Qt's Windows pipes: their client blocked every Python thread on CI, and an elevated pipe
  refuses a non-elevated window), JSON lines with the token (hello / search / status / reload / quit), one thread per connection; builds on a thread; a second thread reads
  the journal every second, runs `maintain` and saves every 10 minutes. One per user (`service.lock`, a QLockFile).
  `service.log`: start, build, slow answers, errors, and every thread's stack when a request waits 10 s.
- `backend.connect()`: the service when it answers, else `LocalBackend` (the same Engine in the window's process,
  walking unless the window itself is elevated). `PR7IS_ADDRESS`, `PR7IS_DATA`, `PR7IS_DRIVES`, `PR7IS_LEAVE_OUT`
  point tests elsewhere.
- `pr7is/main_window.py`: `Results` (lazy rows, `PAGE`), `SearchBox` (Enter / Ctrl+Enter / arrows), 60 ms debounce,
  `_ask` / `answers` / `said` for tests, `open_path` via PR7-E (`family.launch`) when installed. INSTALL THE INDEX
  SERVICE shows on Windows when only the local backend is there. `OptionsDialog`. `tray.py`: tray, Ctrl+Alt+Space
  (`hotkeys.py`, PR7-CB's), start with Windows (HKCU Run "Paragon PR7-IS" --tray).
- `pr7is/app.py`: one window per user (QLocalSocket hand-off, PR7-R's Windows fix); `--selftest FOLDER OUT.json`,
  `--query TEXT OUT.json` (asks the running service; CI), `--install-service`, `--remove-service`,
  `--uninstall-service`, `--startup on|off`, `--unregister`, `--tray`.
- Installer `installer/pr7is.iss` (per user, AppId {187DC039-E485-4BF2-9C1B-6026AF54C9F8} permanent; tasks startup,
  service (both ticked; service runs `--install-service` elevated), desktopicon). PR7-U installs it with
  `/MERGETASKS=!service` so a batch never stops at UAC; the window then offers the button. CI installs it,
  `tools/verify_install.py installed` checks the task, the service reading C: through its MFT and a new file arriving
  through the journal (starts the service itself if no logon started the task), uninstalls, `... uninstalled`.
- Updates: `pr7is/updates.py` / `update_ui.py` (the family's files, keep them in step; with PR7-U installed,
  update_ui offers it); `family.py`: the family's AppIds (PR7-IS included).

- `pr7is/finish.py` (no Qt): the family's finish (OBSIDIAN / AURORA), one choice for every app: HKCU\Software\Paragon\Family "Finish" (a file elsewhere; PARAGON_FAMILY_FILE in tests, conftest). `finish.start` at start-up, `set_finish` saves it, app.py's 2 s timer `finish.follow`s another app's switch. The same file in every family app: keep them in step.

## The look (family rules)

- Theme is PR7-CB's (OBSIDIAN + AURORA). Colours: the family's only; Inter; labels UPPER CASE; file names keep case.
- The PR7-IS mark: PR7 `Logo.cpp` geometry plus I and S (`widgets.I_SHAPE`, `S_SHAPE`); icon "7IS" by
  `tools/make_icon.py`, wizard art by `tools/make_installer_art.py`. Check visuals with
  `QT_SCALE_FACTOR=2 python tools/screenshots.py OUT` (a made-up 1.6M-object index).

## Rules

- Behaviour questions: do what Everything does (syntax, keys, columns, Options).
- Tests: `python -m pytest` (offscreen) on real folders, real NTFS images and a real service subprocess. Never the
  real settings or data folder (conftest), or, outside CI, the registry, scheduled tasks or global keys.
- Releases: bump `__version__` in `pr7is/__init__.py` and add a `## x.y.z` section to CHANGELOG.md, then push: CI
  publishes that version by itself once every check has passed. Run workflow / `v*` tags still work.
