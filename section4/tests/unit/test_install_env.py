"""W4 slice 4: the worker only gets a proxy route when the operator asked for installs."""

from __future__ import annotations

from harness_orchestrator import confine


def test_no_flag_no_proxy_env():
    assert confine.install_env("container", {}) == {}
    assert confine.install_env("container", {confine.INSTALLS_ENV: "0"}) == {}


def test_the_flag_without_the_container_boundary_stays_off():
    # seatbelt confines reads and writes, not sockets: installs there are unbounded
    assert confine.install_env("seatbelt", {confine.INSTALLS_ENV: "1"}) == {}
    assert confine.install_env("none", {confine.INSTALLS_ENV: "1"}) == {}


def test_flag_plus_container_routes_through_the_egress_alias():
    env = confine.install_env("container", {confine.INSTALLS_ENV: "1"})
    assert env["HTTPS_PROXY"] == confine.EGRESS_SANDBOX_URL_DEFAULT
    assert env["HTTP_PROXY"] == env["HTTPS_PROXY"]
    assert "harness-egress" in env["HTTPS_PROXY"]
    assert env[confine.INSTALLS_ENV] == "1"
    assert "127.0.0.1" in env["NO_PROXY"]


def test_wrap_command_passes_routing_vars_by_value(tmp_path):
    ws, sess, tmp = tmp_path / "ws", tmp_path / "s", tmp_path / "t"
    for d in (ws, sess, tmp):
        d.mkdir()
    argv = confine.wrap_command(
        ["python", "-m", "harness_orchestrator.engine_worker"],
        mode="container", roots=[str(tmp_path)], workspace=str(ws),
        session_dir=str(sess), tmp_dir=str(tmp),
        container_name="c1", network="n1",
        env={"HTTPS_PROXY": "http://harness-egress:3128", "DSH_ALLOW_INSTALLS": "1"},
    )
    # by value: `--env KEY` would resolve from the daemon's env, which has neither
    assert "HTTPS_PROXY=http://harness-egress:3128" in argv
    assert "HTTPS_PROXY" not in argv
    assert "DSH_ALLOW_INSTALLS" in argv  # non-secret DSH_ vars still pass by name
