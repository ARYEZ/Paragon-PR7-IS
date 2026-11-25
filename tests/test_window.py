import json
import os

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from pr7is import __version__, app, kinds, settings
from pr7is.main_window import PAGE


def names(w):
    return [w.model.row(i)["name"] for i in range(w.model.total)]


def test_typing_searches_and_shows_the_count(window, tree):
    base, files = tree
    w = window
    QTest.keyClicks(w.search, "elseworld")
    QTest.qWait(150)                                             # 60 ms after the last key
    assert sorted(names(w)) == ["Elseworld shoot", "Elseworld_v3.mp4"]
    assert w.count.text() == f"2 of {w.model.objects:,} objects" and "folders walked" in w.drives_text.text()
    w.search.setText("")
    w.filter.setCurrentIndex(w.filter.findData("audio"))
    assert names(w) == ["Last Light.flac"]
    w.filter.setCurrentIndex(0)
    w.search.setText("file:")
    w.refresh()
    w._sort_by(2)                                                 # SIZE: biggest first
    assert names(w)[0] == "A001_C0006.mov" and w.sort == "size" and w.descending


def test_many_results_come_a_page_at_a_time(window, tree, tmp_path):
    base, _ = tree
    many = base / "Many"
    many.mkdir()
    for i in range(PAGE * 2 + 50):
        (many / f"frame {i:04d}.png").write_bytes(b"x")
    window.backend.rebuild()
    window.search.setText("frame")
    window.refresh()
    m = window.model
    assert m.total == PAGE * 2 + 50 and list(m.pages) == [0]
    assert m.row(PAGE * 2 + 10)["name"] == f"frame {PAGE * 2 + 10:04d}.png" and sorted(m.pages) == [0, 2]


def test_open_open_path_and_copy(window, tree, qapp):
    base, _ = tree
    w = window
    w.search.setText("invoice")
    w.refresh()
    QTest.keyClick(w.search, Qt.Key.Key_Return)
    assert w.opened == [str(base / "Docs" / "Invoice 2026-114.pdf")]
    launched = []
    w.answers["launch"] = lambda appname, args: launched.append((appname, args)) or True
    QTest.keyClick(w.search, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)   # Everything's open path
    assert launched == [("PR7-E", [str(base / "Docs")])]
    w.search.setText("elseworld")
    w.refresh()
    QTest.keyClick(w.search, Qt.Key.Key_Down)                     # the arrows move through the results
    QTest.keyClick(w.search, Qt.Key.Key_Return)
    assert w.opened[-1] == w.model.path(1) and w.opened[-1] != w.model.path(0)
    w.search.setText("invoice")
    w.refresh()
    launched.clear()
    w.open_path(0)
    assert launched == [("PR7-E", [str(base / "Docs")])]
    m = w.row_menu(0)
    assert [a.text() for a in m.actions() if a.text()] == ["Open", "Open path", "Copy full path", "Copy name"]
    [a for a in m.actions() if a.text() == "Copy full path"][0].trigger()
    assert qapp.clipboard().text() == str(base / "Docs" / "Invoice 2026-114.pdf")



def test_results_open_in_the_family_app_for_their_type(window, tree):
    """A PDF -> PR7-R, text -> PR7-W, a video -> PR7-M; nothing for a program, nor for an app not installed."""
    base, _ = tree
    w = window
    installed = {"PR7-R", "PR7-W", "PR7-M"}
    w.answers["find"] = lambda appname: appname in installed
    launched = []
    w.answers["launch"] = lambda appname, args: launched.append((appname, args)) or True

    def item(query):
        w.search.setText(query)
        w.refresh()
        assert w.model.total == 1
        extra = [a for a in w.row_menu(0).actions() if " in PR7-" in a.text()]
        return extra[0] if extra else None

    for query, text, app_name, path in [
            ("invoice", "Open in PR7-R", "PR7-R", base / "Docs" / "Invoice 2026-114.pdf"),
            ("notes.txt", "Edit in PR7-W", "PR7-W", base / "Docs" / "notes.txt"),
            ("elseworld_v3", "Play in PR7-M", "PR7-M", base / "Videos" / "Exports" / "Elseworld_v3.mp4")]:
        a = item(query)
        assert a is not None and a.text() == text
        a.trigger()
        assert launched[-1] == (app_name, [str(path)])
    assert item("setup.exe") is None                       # a program: Windows runs it, no family app
    installed.discard("PR7-M")
    assert item("elseworld_v3") is None                    # PR7-M not installed: no item
    assert kinds.family_app("Stems.7z") == ("PR7-Z", "Open in PR7-Z")
    assert kinds.family_app("IMG_2041.JPG") == ("PR7-P", "View in PR7-P")
    assert kinds.family_app("Music", is_dir=True) is None


def test_a_folder_handed_over_by_pr7e_searches_inside_it(window, tree):
    """PR7-E's "Search with PR7-IS" starts PR7-IS with the folder as one quoted path word ("<folder>\\"): every
    result is inside that folder, and a word typed after it narrows them."""
    base, _ = tree
    w = window
    word = '"' + str(base / "Docs") + os.sep + '"'                # exactly what PR7-E passes
    w.search.setText(word)
    w.refresh()
    found = sorted(w.model.path(n) for n in range(w.model.total))
    assert found and all(p.startswith(str(base / "Docs") + os.sep) for p in found)
    assert str(base / "Docs" / "notes.txt") in found and str(base / "setup.exe") not in found
    w.search.setText(word + " notes")
    w.refresh()
    assert [w.model.path(n) for n in range(w.model.total)] == [str(base / "Docs" / "notes.txt")]

def test_options_rebuild_with_what_was_chosen(window, tree):
    base, _ = tree
    w = window
    settings.set("drives", [str(base)])
    w.answers["options"] = lambda d: (d.leave.setText("*.flac; *.tmp"), True)[-1]
    w.backend.reload = lambda: (w.backend.engine.__setattr__("skip", __import__("pr7is.engine").engine.Skip(
        settings.get_list("leave_out"))), w.backend.rebuild())
    w.options()
    assert settings.get_list("leave_out") == ["*.flac", "*.tmp"]
    w.search.setText("last light")
    w.refresh()
    assert w.model.total == 0


def test_installing_the_service_when_windows_says_no(window):
    window.answers["elevated"] = lambda arg: False
    assert not window.install_service() and "declined" in window.said[-1][1]


def test_version(capsys):
    assert app.main(["--version"]) == 0 and capsys.readouterr().out.strip() == __version__


def test_selftest(tree, tmp_path):
    import subprocess
    import sys
    base, _ = tree
    (base / "Docs" / "report 2026.pdf").write_bytes(b"x" * 10)
    out = tmp_path / "out.json"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = subprocess.run([sys.executable, "-m", "pr7is", "--selftest", str(base), str(out)], cwd=root,
                          timeout=120).returncode
    got = json.loads(out.read_text())
    assert code == 0 and got["found"] == [str(base / "Docs" / "report 2026.pdf")]
    assert got["big"] == [str(base / "Videos" / "Elseworld shoot" / "A001_C0006.mov")] and got["fonts"]
