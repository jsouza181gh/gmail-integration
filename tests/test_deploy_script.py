import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "deploy.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


def run(args: list[str], ssh_command: str | None = None) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    env = {"PATH": "/nonexistent"}  # any docker/curl call would fail loudly
    if ssh_command is not None:
        env["SSH_ORIGINAL_COMMAND"] = ssh_command
    return subprocess.run(
        [BASH, str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8", env=env, timeout=30
    )


@pytest.mark.parametrize(
    "arg",
    ["", "abc", "a" * 39, "A" * 40, "g" * 40, "a" * 40 + "; rm -rf /", "$(id)"],
)
def test_rejects_anything_but_a_full_sha(arg: str) -> None:
    res = run([arg])
    assert res.returncode == 2
    assert "SHA inválido" in res.stderr


def test_rejects_unexpected_ssh_command() -> None:
    res = run([], ssh_command="bash -i")
    assert res.returncode == 2


def test_rejects_extra_words_in_ssh_command() -> None:
    res = run([], ssh_command="deploy " + "a" * 40 + " extra")
    assert res.returncode == 2
