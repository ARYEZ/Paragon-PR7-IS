import time

import pytest

from pr7is.index import Index, parse, parse_date, parse_size

NOW = time.time()


@pytest.fixture
def ix():
    i = Index()
    c = i.add("C:", -1, is_dir=True)
    users = i.add("Users", c, is_dir=True)
    shoot = i.add("Elseworld shoot", users, is_dir=True, mtime=NOW)
    i.add("A001_C0006.mov", shoot, 3_000_000, NOW)
    i.add("B002.MOV", shoot, 1_000, NOW - 40 * 86400)
    docs = i.add("Docs", users, is_dir=True)
    i.add("Invoice 2026-114.pdf", docs, 12 * 1024 ** 2, NOW)
    i.add("elseworld credits.txt", docs, 4_100, NOW)
    i.add("über-mix.flac", docs, 600_000, NOW)
    return i


def found(ix, q, **kw):
    return sorted(ix.path(i) for i in ix.search(q, **kw))


def test_words_match_names_in_any_order_any_case(ix):
    assert found(ix, "else") == ["C:\\Users\\Docs\\elseworld credits.txt", "C:\\Users\\Elseworld shoot"]
    assert found(ix, "credits else") == ["C:\\Users\\Docs\\elseworld credits.txt"]
    assert found(ix, "ÜBER") == ["C:\\Users\\Docs\\über-mix.flac"]
    assert found(ix, "else mov") == []                       # names only, like Everything
    assert found(ix, '"world shoot"') == ["C:\\Users\\Elseworld shoot"]


def test_or_not_wildcards_and_paths(ix):
    assert found(ix, "pdf|flac") == ["C:\\Users\\Docs\\Invoice 2026-114.pdf", "C:\\Users\\Docs\\über-mix.flac"]
    assert found(ix, "*.mov") == ["C:\\Users\\Elseworld shoot\\A001_C0006.mov", "C:\\Users\\Elseworld shoot\\B002.MOV"]
    assert found(ix, "*.mov !b0") == ["C:\\Users\\Elseworld shoot\\A001_C0006.mov"]
    assert found(ix, "a00?_*") == ["C:\\Users\\Elseworld shoot\\A001_C0006.mov"]
    assert found(ix, "shoot\\a001") == ["C:\\Users\\Elseworld shoot\\A001_C0006.mov"]
    docs = ["C:\\Users\\Docs\\Invoice 2026-114.pdf", "C:\\Users\\Docs\\elseworld credits.txt",
            "C:\\Users\\Docs\\über-mix.flac"]
    assert found(ix, "users\\docs\\") == docs                # a folder path ending in \\: what's in it (Everything)
    assert found(ix, '"C:\\Users\\Docs\\"') == docs          # the whole path, quoted (PR7-E's Search with PR7-IS)
    assert found(ix, "c:\\users\\") == sorted(docs + ["C:\\Users\\Docs", "C:\\Users\\Elseworld shoot",
                                                    "C:\\Users\\Elseworld shoot\\A001_C0006.mov",
                                                    "C:\\Users\\Elseworld shoot\\B002.MOV"])   # sub-folders too
    assert found(ix, "users\\docs\\ !pdf") == docs[1:]
    assert found(ix, "docs\\inv") == ["C:\\Users\\Docs\\Invoice 2026-114.pdf"]


def test_functions(ix):
    assert found(ix, "ext:pdf;flac") == ["C:\\Users\\Docs\\Invoice 2026-114.pdf", "C:\\Users\\Docs\\über-mix.flac"]
    assert found(ix, "size:>10mb") == ["C:\\Users\\Docs\\Invoice 2026-114.pdf"]
    assert found(ix, "size:1kb..5kb") == ["C:\\Users\\Docs\\elseworld credits.txt"]
    assert found(ix, "dm:thismonth *.mov") == ["C:\\Users\\Elseworld shoot\\A001_C0006.mov"] or \
        time.localtime(NOW - 40 * 86400).tm_mon == time.localtime(NOW).tm_mon
    assert found(ix, "folder: else") == ["C:\\Users\\Elseworld shoot"]
    assert found(ix, "file: else") == ["C:\\Users\\Docs\\elseworld credits.txt"]
    assert found(ix, "", kind="audio") == ["C:\\Users\\Docs\\über-mix.flac"]
    assert len(found(ix, "", kind="folder")) == 4


def test_parsing():
    assert parse_size(">10mb") == (10 * 1024 ** 2 + 1, None) and parse_size("<=1kb") == (None, 1024)
    assert parse_size("empty") == (0, 0) and parse_size("1.5gb") == (int(1.5 * 1024 ** 3),) * 2
    day = 86400
    a, b = parse_date("today", NOW)
    assert a <= NOW < b and b - a in (day - 3600, day, day + 3600)          # summer time days
    a, b = parse_date("2026-11-01..2026-11-20", NOW)
    assert b - a >= 19 * day
    q = parse('ext:pdf size:>1mb "two words" a|b !c folder:')
    assert q.exts == {"pdf"} and q.only == "folder" and q.words == [["two words"], ["a", "b"], ["!c"]]


def test_renaming_a_folder_moves_everything_inside_and_removing_it_too(ix):
    shoot = ix.search("folder: elseworld")[0]
    ix.rename(shoot, "Final shoot")
    assert found(ix, "a001") == ["C:\\Users\\Final shoot\\A001_C0006.mov"]
    docs = ix.search("folder: docs")[0]
    ix.rename(shoot, parent=docs)
    assert found(ix, "a001") == ["C:\\Users\\Docs\\Final shoot\\A001_C0006.mov"]
    before = len(ix)
    ix.remove(docs)
    assert found(ix, "a001") == [] and found(ix, "pdf") == [] and len(ix) == before - 7


def test_sorting(ix):
    by_name = [ix.names[i] for i in ix.search("*.*")]
    assert by_name == sorted(by_name, key=str.casefold)
    by_size = [ix.size[i] for i in ix.search("file:", sort="size", descending=True)]
    assert by_size == sorted(by_size, reverse=True)
    rows = ix.row(ix.search("invoice")[0])
    assert rows["folder"] == "C:\\Users\\Docs" and rows["path"].endswith("Invoice 2026-114.pdf") and not rows["dir"]


def test_a_million_names_search_quickly():
    import random
    import string
    big = Index()
    r = big.add("C:", -1, is_dir=True)
    rnd = random.Random(1)
    for i in range(300_000):
        big.add("".join(rnd.choices(string.ascii_lowercase, k=12)) + ".txt", r, i)
    big.rank(); _ = big.blob                                    # what the service does once after building
    t = time.perf_counter()
    for q in ("abc", "ab cd", "zz ext:txt", "*q*.txt"):
        big.search(q)
    assert time.perf_counter() - t < 2.0


def test_paths_use_the_roots_own_separator():
    ix = Index()
    win = ix.add("C:\\Users\\me\\disk", -1, is_dir=True)          # a walked Windows folder, not a whole drive
    docs = ix.add("Docs", win, is_dir=True)
    f = ix.add("a.pdf", docs, 1, 0)
    lin = ix.add("/home/me", -1, is_dir=True)
    g = ix.add("b.pdf", lin, 1, 0)
    drive = ix.add("C:", -1, is_dir=True)
    h = ix.add("c.pdf", drive, 1, 0)
    assert ix.path(f) == "C:\\Users\\me\\disk\\Docs\\a.pdf" and ix.path(g) == "/home/me/b.pdf"
    assert ix.path(h) == "C:\\c.pdf" and ix.path(drive) == "C:\\"


def _brute(ix, word):
    return {i for i, n in enumerate(ix.names) if n is not None and word in n.casefold()}


def test_changes_after_building_never_rebuild_and_stay_right():
    import random
    rnd = random.Random(7)
    ix = Index()
    root = ix.add("C:", -1, is_dir=True)
    dirs = [root] + [ix.add(f"Folder {d}", root, is_dir=True) for d in range(20)]
    for n in range(3000):
        ix.add(f"{rnd.choice(['clip', 'mix', 'Edit', 'stem'])} {n}.wav", rnd.choice(dirs), n, n)
    ix.compact()
    blob = ix.blob
    for step in range(600):                                     # what the change journal does all day
        op = rnd.random()
        alive = [i for i, n in enumerate(ix.names) if n is not None and i != root]
        if op < .4:
            ix.add(f"{rnd.choice(['clip', 'new', 'MIX'])} n{step}.wav", rnd.choice(dirs), step, step)
        elif op < .7:
            ix.rename(rnd.choice(alive), f"renamed {rnd.choice(['clip', 'zz'])} {step}")
        elif op < .85:
            i = rnd.choice(alive)
            ix.rename(i, parent=rnd.choice([d for d in dirs if d != i]))
        else:
            ix.remove(rnd.choice(alive))
        if step == 300:                                         # a background compact, with changes meanwhile
            names = ix.compact_start()
            built = Index.build(names)
            ix.rename(rnd.choice([i for i, n in enumerate(ix.names) if n and i != root]), "during the build clip")
            ix.compact_finish(names, built)
            blob = ix.blob
    assert ix.blob is blob and ix.recent > 0                     # nothing was rebuilt by the changes
    for word in ("clip", "mix", "zz", "during", "nothing-like-this"):
        assert set(ix.search(word)) == _brute(ix, word), word
    every = ix.search("")
    want = sorted(every, key=lambda i: (ix.names[i].casefold(), i))
    assert ix.sort(every) == want and ix.sort(every, descending=True) == want[::-1]
    small = ix.search("clip")
    assert ix.sort(small) == sorted(small, key=lambda i: (ix.names[i].casefold(), i))
    ix.compact()
    assert ix.recent == 0 and set(ix.search("clip")) == _brute(ix, "clip") and ix.sort(every) == want


def test_a_million_names_stay_fast_while_they_change():
    import time
    ix = Index()
    root = ix.add("C:", -1, is_dir=True)
    folders = [ix.add(f"dir{d}", root, is_dir=True) for d in range(2000)]
    for n in range(1_250_000):
        ix.add(f"file{n}.dll", folders[n % 2000], 1, 0)
    ix.compact()
    temp = ix.add("Temp", root, is_dir=True)
    for n in range(500):                                         # a burst of journal changes
        ix.add(f"tmp{n}.log", temp, 1, 0)
        ix.rename(folders[0] + 1 + n * 7, f"renamed{n}.dll")
    t = time.perf_counter()
    ix.remove(folders[5])                                        # a folder deleted with what's inside
    first = time.perf_counter() - t
    t = time.perf_counter()
    ix.remove(folders[6])
    again = time.perf_counter() - t
    t = time.perf_counter()
    assert ix.search("pr7is-nothing-is-called-this") == []
    assert len(ix.search("renamed4")) == 111                       # renamed4, renamed40..49, renamed400..499
    took = time.perf_counter() - t
    assert took < 0.5, f"searching after changes took {took:.2f} s: something rebuilt"
    assert again < 0.05, f"a second folder removal took {again:.2f} s (first, which builds the map: {first:.2f} s)"
