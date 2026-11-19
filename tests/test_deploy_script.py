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


# --- smoke test with fake docker/curl ---------------------------------------------

FAKE_DOCKER = """#!/usr/bin/env bash
case "$1" in
  image|pull|ps) exit 0 ;;
  run) echo "services: {}"; exit 0 ;;
  inspect)
    cid="${@: -1}"
    if [[ "$cid" == cid-worker ]]; then echo "$FAKE_WORKER_HEALTH"; else echo healthy; fi ;;
  compose)
    args="$*"
    if [[ "$args" == *"config --services"* ]]; then printf 'db\nmigrate\napi\n%s' "$FAKE_SERVICES_EXTRA"; exit 0; fi
    if [[ "$args" == *" ps -q api"* ]]; then echo cid-api; exit 0; fi
    if [[ "$args" == *" ps -q worker"* ]]; then echo cid-worker; exit 0; fi
    echo "$args" >>"$FAKE_LOG" ;;
esac
exit 0
"""


def _posix(p: Path) -> str:
    text = p.as_posix()
    if len(text) > 1 and text[1] == ":":  # C:/x -> /c/x for Git Bash
        return f"/{text[0].lower()}{text[2:]}"
    return text


def _deploy(tmp_path: Path, worker_health: str, services_extra: str = "worker\n") -> tuple[
    subprocess.CompletedProcess[str], Path
]:
    assert BASH is not None
    app = tmp_path / "app"
    (app / "deploy").mkdir(parents=True)
    script = app / "deploy" / "deploy.sh"
    script.write_bytes(SCRIPT.read_bytes().replace(b"\r\n", b"\n"))
    (app / ".deploy").mkdir()
    (app / ".deploy" / "current").write_text("b" * 40 + "\n", encoding="utf-8", newline="\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("curl", "#!/usr/bin/env bash\nexit 0\n")):
        path = bin_dir / name
        path.write_text(body, encoding="utf-8", newline="\n")
        path.chmod(0o755)
    env = {
        "PATH": f"{_posix(bin_dir)}:/usr/bin:/bin",
        "FAKE_WORKER_HEALTH": worker_health,
        "FAKE_SERVICES_EXTRA": services_extra,
        "FAKE_LOG": _posix(tmp_path / "docker.log"),
        "SMOKE_TIMEOUT_S": "2",
        "SMOKE_INTERVAL_S": "0",
    }
    res = subprocess.run(
        [BASH, _posix(script), "a" * 40],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=60,
    )
    return res, app


def test_smoke_passes_when_api_and_worker_healthy(tmp_path: Path) -> None:
    res, app = _deploy(tmp_path, "healthy")
    assert res.returncode == 0, res.stdout + res.stderr
    assert (app / ".deploy" / "current").read_text(encoding="utf-8").strip() == "a" * 40


def test_smoke_requires_worker_healthy(tmp_path: Path) -> None:
    res, app = _deploy(tmp_path, "unhealthy")
    assert res.returncode == 1
    assert "rollback" in res.stdout
    assert (app / ".deploy" / "current").read_text(encoding="utf-8").strip() == "b" * 40


def test_smoke_skips_worker_for_tags_without_it(tmp_path: Path) -> None:
    res, _ = _deploy(tmp_path, "unhealthy", services_extra="")
    assert res.returncode == 0, res.stdout + res.stderr
