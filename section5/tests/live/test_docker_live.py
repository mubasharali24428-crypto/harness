"""Live Docker backend: real daemon, real container. Skipped when Docker is down."""
import pytest

from harness_sandbox.backends import DockerSandboxRunner, _run, docker_available

pytestmark = pytest.mark.skipif(not docker_available(_run)[0], reason="docker daemon not running")

IMAGE = "python:3.12-slim"


@pytest.fixture
def box(tmp_path, request):
    r = DockerSandboxRunner(
        storage_root=tmp_path, image=IMAGE, allow_network=getattr(request, "param", True)
    )
    h = r.create_task("live1", "repo", "main")
    yield r, h, tmp_path / "live1" / "workspace"
    r.destroy(h)


async def test_runs_as_nobody_and_writes_only_the_workspace(box):
    r, h, ws = box
    who = 'python3 -c "import os; assert os.getuid() == 65534"'
    touch = "python3 -c \"open('out.txt', 'w').close()\""
    assert (await r.exec_structured(h, who)).exit_code == 0
    assert (await r.exec_structured(h, touch)).exit_code == 0
    assert (ws / "out.txt").exists()


async def test_root_filesystem_is_read_only(box):
    r, h, _ = box
    assert (await r.exec_structured(h, "python3 -c \"open('/etc/pwned', 'w')\"")).exit_code != 0


@pytest.mark.parametrize("box", [False], indirect=True)
async def test_no_network_when_disallowed(box):
    r, h, _ = box
    cmd = "python3 -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3)\""
    assert (await r.exec_structured(h, cmd)).exit_code != 0
