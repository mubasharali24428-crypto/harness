"""S-01: the Landlock read allow-list must not grant a forbidden path.

Landlock grants rights and never denies them, so "forbidden path" is
expressed as an allow-list that omits it. These are pure-function tests (no
syscall), so they run on every platform; the real enforcement is proven on
Linux by ``test_landlock_read_isolation.py``.
"""

from harness_sandbox import confinement


def _rules(rules):
    return {path: rights for path, rights in rules}


def test_forbidden_directory_is_never_granted(tmp_path):
    ws = tmp_path / "ws"
    (ws / "payroll").mkdir(parents=True)
    (ws / "payroll" / "data.txt").write_text("CANARY")
    (ws / "src").mkdir()
    (ws / "src" / "main.py").write_text("ok")

    rules = _rules(
        confinement.landlock_read_allowlist(ws, forbidden_paths=["payroll"])
    )
    assert (ws / "payroll").resolve() not in rules
    assert (ws / "payroll" / "data.txt").resolve() not in rules
    # The sibling directory keeps full read access.
    assert (ws / "src").resolve() in rules
    assert rules[(ws / "src").resolve()] & confinement.LL_READ_FILE


def test_directory_holding_a_forbidden_file_is_split_not_denied(tmp_path):
    ws = tmp_path / "ws"
    (ws / "pkg").mkdir(parents=True)
    (ws / "pkg" / "secret.txt").write_text("CANARY")
    (ws / "pkg" / "public.txt").write_text("ok")

    rules = _rules(
        confinement.landlock_read_allowlist(ws, forbidden_paths=["pkg/secret.txt"])
    )
    assert (ws / "pkg" / "secret.txt").resolve() not in rules
    # Listing the directory still works (Seatbelt parity), and the public
    # sibling is still readable.
    assert rules[(ws / "pkg").resolve()] == confinement.LL_READ_DIR
    assert (ws / "pkg" / "public.txt").resolve() in rules
    assert rules[(ws / "pkg" / "public.txt").resolve()] & confinement.LL_READ_FILE


def test_workspace_root_is_listable_but_not_fully_readable_when_split(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_text("x")
    rules = _rules(
        confinement.landlock_read_allowlist(ws, forbidden_paths=["a.txt"])
    )
    assert rules[ws.resolve()] == confinement.LL_READ_DIR
    assert (ws / "a.txt").resolve() not in rules


def test_no_forbidden_path_is_granted_for_any_shape(tmp_path):
    ws = tmp_path / "ws"
    (ws / "a" / "b").mkdir(parents=True)
    (ws / "a" / "b" / "secret.txt").write_text("CANARY")
    (ws / "top.txt").write_text("x")
    for forbidden in (
        ["a/b/secret.txt"],
        ["a/b"],
        ["a"],
        ["top.txt"],
        ["does/not/exist.txt"],
    ):
        rules = _rules(
            confinement.landlock_read_allowlist(ws, forbidden_paths=forbidden)
        )
        target = (ws / forbidden[0]).resolve()
        assert target not in rules, forbidden
        # No rule may cover the forbidden path as a descendant either.
        for path in rules:
            assert not (
                path == target or target in path.parents
            ), (forbidden, path)


def test_symlink_to_forbidden_path_is_not_granted(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "payroll").mkdir()
    (ws / "payroll" / "data.txt").write_text("CANARY")
    (ws / "link").symlink_to(ws / "payroll" / "data.txt")

    rules = _rules(
        confinement.landlock_read_allowlist(ws, forbidden_paths=["payroll"])
    )
    assert (ws / "payroll" / "data.txt").resolve() not in rules
    # The symlink resolves to the forbidden inode, so it must not be granted
    # either — a rule on it would grant the forbidden file.
    assert (ws / "link").resolve() not in rules


def test_sibling_task_workspace_is_not_readable(tmp_path):
    storage = tmp_path / "tasks"
    mine = storage / "t1"
    other = storage / "t2"
    (mine / "workspace").mkdir(parents=True)
    (other / "workspace").mkdir(parents=True)
    (other / "workspace" / "b.txt").write_text("B")

    rules = _rules(confinement.landlock_read_allowlist(mine / "workspace"))
    other_root = (other / "workspace").resolve()
    assert not any(p == other_root or other_root in p.parents for p in rules)


def test_workspace_and_tmpdir_are_readable_roots(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    rules = _rules(
        confinement.landlock_read_allowlist(ws, tmpdir=tmpdir)
    )
    assert ws.resolve() in rules
    assert tmpdir.resolve() in rules
    assert rules[ws.resolve()] & confinement.LL_READ_FILE
    assert rules[tmpdir.resolve()] & confinement.LL_READ_FILE
