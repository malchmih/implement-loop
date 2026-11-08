from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from support import SCRIPT, LoopFixture, _without_inherited_git_environment

_FIXTURE_ATTEMPT_PROBE = r"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from support import LoopFixture

scenario = json.loads(sys.argv[2])
fixture = LoopFixture()
try:
    for key, value in scenario.get("local_config", {}).items():
        fixture.git("config", key, value)

    initial_head = fixture.git("rev-parse", "HEAD")
    result, runs = fixture.run_loop(
        "--max-iterations",
        "1",
        extra_env=scenario.get("run_env"),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"runner exited {result.returncode}\n"
            f"stdout: {result.stdout.decode(errors='replace')}\n"
            f"stderr: {result.stderr.decode(errors='replace')}"
        )

    state = fixture.load_state()
    final_head = fixture.git("rev-parse", "HEAD")
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", initial_head, final_head],
        cwd=fixture.repo,
        env=fixture.env,
        check=False,
        capture_output=True,
    )
    author = fixture.git("show", "-s", "--format=%an <%ae>", "HEAD")
    actual_root = Path(fixture.git("rev-parse", "--show-toplevel")).resolve()
    print(
        json.dumps(
            {
                "repository_matches": actual_root == fixture.repo.resolve(),
                "ticket_state": state["tickets"]["2"]["state"],
                "events": [event[0] for event in state["events"]],
                "opencode_calls": len(state["opencode_calls"]),
                "run_count": len(runs),
                "initial_head": initial_head,
                "final_head": final_head,
                "descendant": ancestry.returncode == 0,
                "author": author,
                "clean": not fixture.git("status", "--porcelain"),
            }
        )
    )
finally:
    fixture.cleanup()
"""


def _write_failing_marker_program(executable: Path, marker: Path) -> None:
    executable.write_text(f"#!/bin/sh\n: > {shlex.quote(str(marker))}\nexit 1\n")
    executable.chmod(0o700)


def _make_hook(fixture: LoopFixture, name: str) -> tuple[Path, Path]:
    hook_directory = fixture.home / f"{name}-hooks"
    hook_directory.mkdir()
    marker = fixture.home / f"{name}-hook-invoked"
    _write_failing_marker_program(hook_directory / "pre-commit", marker)
    return hook_directory, marker


def _make_failing_gpg(fixture: LoopFixture) -> tuple[Path, Path]:
    gpg_directory = fixture.home / "gpg-bin"
    gpg_directory.mkdir()
    marker = fixture.home / "gpg-invoked"
    gpg = gpg_directory / "gpg"
    _write_failing_marker_program(gpg, marker)
    return gpg, marker


def _run_fixture_attempt(
    fixture: LoopFixture,
    inherited_git_environment: dict[str, str],
    *,
    scenario: dict[str, object] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = _without_inherited_git_environment(os.environ)
    environment["HOME"] = str(fixture.home / "probe-home")
    environment.update(inherited_git_environment)
    return subprocess.run(
        [
            sys.executable,
            "-c",
            _FIXTURE_ATTEMPT_PROBE,
            str(Path(__file__).resolve().parent),
            json.dumps(scenario or {}),
        ],
        cwd=SCRIPT.parent,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _successful_attempt_report(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert result.returncode == 0, (
        f"fixture/runner probe failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    report = json.loads(result.stdout)
    assert report["repository_matches"]
    assert report["ticket_state"] == "closed"
    assert report["events"] == ["assign", "close"]
    assert report["opencode_calls"] == 1
    assert report["run_count"] == 1
    assert report["initial_head"] != report["final_head"]
    assert report["descendant"]
    assert report["clean"]
    return report


def _assert_external_program_was_not_run(
    result: subprocess.CompletedProcess[str],
    marker: Path,
    description: str,
) -> None:
    assert not marker.exists(), f"{description} was invoked"
    _successful_attempt_report(result)


def _write_config(fixture: LoopFixture, path: Path, settings: dict[str, str]) -> Path:
    path.touch()
    for key, value in settings.items():
        fixture.git("config", "--file", str(path), key, value)
    return path


def _snapshot_repository(repository: Path) -> dict[str, bytes | str]:
    snapshot: dict[str, bytes | str] = {}
    for path in sorted(repository.rglob("*")):
        relative_path = str(path.relative_to(repository))
        if path.is_symlink():
            snapshot[relative_path] = os.readlink(path)
        elif path.is_file():
            snapshot[relative_path] = path.read_bytes()
    return snapshot


@pytest.mark.parametrize(
    ("source", "environment_key"),
    [("system", "GIT_CONFIG_SYSTEM"), ("global", "GIT_CONFIG_GLOBAL")],
)
def test_external_git_configs_cannot_run_hooks(
    loop_fixture: LoopFixture,
    source: str,
    environment_key: str,
) -> None:
    hook_directory, marker = _make_hook(loop_fixture, source)
    config_path = _write_config(
        loop_fixture,
        loop_fixture.home / f"{source}-gitconfig",
        {"core.hooksPath": str(hook_directory)},
    )
    inherited_environment = {environment_key: str(config_path)}
    if source == "system":
        inherited_environment["GIT_CONFIG_NOSYSTEM"] = "0"

    result = _run_fixture_attempt(loop_fixture, inherited_environment)

    _assert_external_program_was_not_run(result, marker, f"the hostile {source} hook")


def test_environment_injected_git_config_cannot_run_hooks(loop_fixture: LoopFixture) -> None:
    hook_directory, marker = _make_hook(loop_fixture, "injected-config")
    inherited_environment = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": str(hook_directory),
    }

    result = _run_fixture_attempt(loop_fixture, inherited_environment)

    _assert_external_program_was_not_run(result, marker, "the environment-injected hook")


def test_external_signing_preferences_do_not_require_credentials(loop_fixture: LoopFixture) -> None:
    gpg, marker = _make_failing_gpg(loop_fixture)
    config_path = _write_config(
        loop_fixture,
        loop_fixture.home / "signing-gitconfig",
        {
            "commit.gpgsign": "true",
            "user.signingkey": "missing-test-signing-key",
            "gpg.program": str(gpg),
        },
    )

    result = _run_fixture_attempt(loop_fixture, {"GIT_CONFIG_GLOBAL": str(config_path)})

    _assert_external_program_was_not_run(result, marker, "the external signing program")


def test_repository_location_overrides_do_not_redirect_or_mutate_a_decoy(
    loop_fixture: LoopFixture,
) -> None:
    decoy = loop_fixture.home / "decoy-repository"
    decoy.mkdir()
    loop_fixture.git("init", "--quiet", cwd=decoy)
    loop_fixture.git("config", "user.name", "Decoy Repository", cwd=decoy)
    loop_fixture.git("config", "user.email", "decoy@example.invalid", cwd=decoy)
    (decoy / "tracked.txt").write_text("leave this repository alone\n")
    loop_fixture.git("add", "tracked.txt", cwd=decoy)
    loop_fixture.git("commit", "--quiet", "-m", "Decoy fixture", cwd=decoy)
    decoy_before = _snapshot_repository(decoy)
    hostile_index = decoy / ".git" / "hostile-index"

    result = _run_fixture_attempt(
        loop_fixture,
        {
            "GIT_DIR": str(decoy / ".git"),
            "GIT_WORK_TREE": str(decoy),
            "GIT_COMMON_DIR": str(decoy / ".git"),
            "GIT_INDEX_FILE": str(hostile_index),
            "GIT_OBJECT_DIRECTORY": str(decoy / ".git" / "objects"),
        },
    )

    decoy_after = _snapshot_repository(decoy)
    changed_paths = sorted(
        path
        for path in decoy_before.keys() | decoy_after.keys()
        if decoy_before.get(path) != decoy_after.get(path)
    )
    assert not changed_paths, (
        "the hostile inherited Git environment changed decoy paths: " + ", ".join(changed_paths)
    )
    assert not hostile_index.exists()
    _successful_attempt_report(result)


def test_repository_local_configuration_remains_effective(loop_fixture: LoopFixture) -> None:
    expected_author = "Repository Local Writer <local@example.invalid>"
    result = _run_fixture_attempt(
        loop_fixture,
        {},
        scenario={
            "local_config": {
                "user.name": "Repository Local Writer",
                "user.email": "local@example.invalid",
            },
        },
    )

    report = _successful_attempt_report(result)
    assert report["author"] == expected_author


def test_explicit_test_git_environment_overrides_reach_fake_harness(
    loop_fixture: LoopFixture,
) -> None:
    expected_author = "Per-test Git Override <override@example.invalid>"
    result = _run_fixture_attempt(
        loop_fixture,
        {},
        scenario={
            "run_env": {
                "GIT_CONFIG_COUNT": "2",
                "GIT_CONFIG_KEY_0": "user.name",
                "GIT_CONFIG_VALUE_0": "Per-test Git Override",
                "GIT_CONFIG_KEY_1": "user.email",
                "GIT_CONFIG_VALUE_1": "override@example.invalid",
            },
        },
    )

    report = _successful_attempt_report(result)
    assert report["author"] == expected_author
