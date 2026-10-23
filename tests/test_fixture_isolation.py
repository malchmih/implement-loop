from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from support import SCRIPT, LoopTestCase

_FIXTURE_ATTEMPT_PROBE = r"""
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from support import LoopTestCase

scenario = json.loads(sys.argv[2])
case = LoopTestCase("setUp")
try:
    case.setUp()
    for key, value in scenario.get("local_config", {}).items():
        case.git("config", key, value)

    initial_head = case.git("rev-parse", "HEAD")
    result, runs = case.run_loop(
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

    state = case.load_state()
    final_head = case.git("rev-parse", "HEAD")
    if len(runs) != 1:
        raise RuntimeError(f"expected one run record directory, got {runs!r}")
    if state["tickets"]["2"]["state"] != "closed":
        raise RuntimeError("the fake issue service did not observe ticket closure")
    if [event[0] for event in state["events"]] != ["assign", "close"]:
        raise RuntimeError(f"unexpected fake issue-service events: {state['events']!r}")
    if len(state["copilot_calls"]) != 1 or state["opencode_calls"]:
        raise RuntimeError("the PATH-injected fake Copilot command did not run exactly once")
    if initial_head == final_head:
        raise RuntimeError("the fake harness did not create a commit")
    case.git("merge-base", "--is-ancestor", initial_head, final_head)
    if case.git("status", "--porcelain"):
        raise RuntimeError("the runner left the temporary repository dirty")

    author = case.git("show", "-s", "--format=%an <%ae>", "HEAD")
    expected_author = scenario.get("expected_author")
    if expected_author is not None and author != expected_author:
        raise RuntimeError(f"expected commit author {expected_author!r}, got {author!r}")

    actual_root = Path(case.git("rev-parse", "--show-toplevel")).resolve()
    print(
        json.dumps(
            {
                "repository_matches": actual_root == case.repo.resolve(),
                "ticket_state": state["tickets"]["2"]["state"],
                "events": [event[0] for event in state["events"]],
                "copilot_calls": len(state["copilot_calls"]),
                "opencode_calls": len(state["opencode_calls"]),
                "initial_head": initial_head,
                "final_head": final_head,
                "author": author,
                "clean": True,
            }
        )
    )
finally:
    case.doCleanups()
"""


class FixtureIsolationTests(LoopTestCase):
    def _write_config(self, path: Path, settings: dict[str, str]) -> Path:
        path.touch()
        for key, value in settings.items():
            self.git("config", "--file", str(path), key, value)
        return path

    def _make_hook(self, name: str, *, exit_status: int = 1) -> tuple[Path, Path]:
        hook_directory = self.home / f"{name}-hooks"
        hook_directory.mkdir()
        marker = self.home / f"{name}-hook-invoked"
        hook = hook_directory / "pre-commit"
        hook.write_text(f"#!/bin/sh\n: > {shlex.quote(str(marker))}\nexit {exit_status}\n")
        hook.chmod(0o700)
        return hook_directory, marker

    def _make_failing_gpg(self) -> tuple[Path, Path]:
        gpg_directory = self.home / "gpg-bin"
        gpg_directory.mkdir()
        marker = self.home / "gpg-invoked"
        gpg = gpg_directory / "gpg"
        gpg.write_text(f"#!/bin/sh\n: > {shlex.quote(str(marker))}\nexit 1\n")
        gpg.chmod(0o700)
        return gpg, marker

    def _run_fixture_attempt(
        self,
        inherited_git_environment: dict[str, str],
        *,
        scenario: dict[str, object] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("GIT_")
        }
        environment["HOME"] = str(self.home / "probe-home")
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

    def _successful_attempt_report(
        self,
        result: subprocess.CompletedProcess[str],
    ) -> dict[str, object]:
        self.assertEqual(
            result.returncode,
            0,
            f"fixture/runner probe failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            self.fail(f"fixture/runner probe returned invalid JSON: {error}\n{result.stdout}")
        self.assertTrue(report["repository_matches"])
        self.assertEqual(report["ticket_state"], "closed")
        self.assertEqual(report["events"], ["assign", "close"])
        self.assertEqual(report["copilot_calls"], 1)
        self.assertEqual(report["opencode_calls"], 0)
        self.assertNotEqual(report["initial_head"], report["final_head"])
        self.assertTrue(report["clean"])
        return report

    def test_external_system_and_global_configs_cannot_run_hooks(self) -> None:
        for source, environment_key in (
            ("system", "GIT_CONFIG_SYSTEM"),
            ("global", "GIT_CONFIG_GLOBAL"),
        ):
            with self.subTest(source=source):
                hook_directory, marker = self._make_hook(source)
                config_path = self._write_config(
                    self.home / f"{source}-gitconfig",
                    {"core.hooksPath": str(hook_directory)},
                )
                inherited_environment = {environment_key: str(config_path)}
                if source == "system":
                    inherited_environment["GIT_CONFIG_NOSYSTEM"] = "0"

                result = self._run_fixture_attempt(inherited_environment)

                self.assertFalse(marker.exists(), f"the hostile {source} hook was invoked")
                self._successful_attempt_report(result)

    def test_environment_injected_git_config_cannot_run_hooks(self) -> None:
        hook_directory, marker = self._make_hook("injected-config")
        inherited_environment = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": str(hook_directory),
        }

        result = self._run_fixture_attempt(inherited_environment)

        self.assertFalse(marker.exists(), "the environment-injected hook was invoked")
        self._successful_attempt_report(result)

    def test_external_signing_preferences_do_not_require_credentials(self) -> None:
        gpg, marker = self._make_failing_gpg()
        config_path = self._write_config(
            self.home / "signing-gitconfig",
            {
                "commit.gpgsign": "true",
                "user.signingkey": "missing-test-signing-key",
                "gpg.program": str(gpg),
            },
        )

        result = self._run_fixture_attempt({"GIT_CONFIG_GLOBAL": str(config_path)})

        self.assertFalse(marker.exists(), "the external signing program was invoked")
        self._successful_attempt_report(result)

    def _snapshot_repository(self, repository: Path) -> dict[str, bytes | str]:
        snapshot: dict[str, bytes | str] = {}
        for path in sorted(repository.rglob("*")):
            relative_path = str(path.relative_to(repository))
            if path.is_symlink():
                snapshot[relative_path] = os.readlink(path)
            elif path.is_file():
                snapshot[relative_path] = path.read_bytes()
        return snapshot

    def test_repository_location_overrides_do_not_redirect_or_mutate_a_decoy(self) -> None:
        decoy = self.home / "decoy-repository"
        decoy.mkdir()
        self.git("init", "--quiet", cwd=decoy)
        self.git("config", "user.name", "Decoy Repository", cwd=decoy)
        self.git("config", "user.email", "decoy@example.invalid", cwd=decoy)
        (decoy / "tracked.txt").write_text("leave this repository alone\n")
        self.git("add", "tracked.txt", cwd=decoy)
        self.git("commit", "--quiet", "-m", "Decoy fixture", cwd=decoy)
        decoy_before = self._snapshot_repository(decoy)
        hostile_index = decoy / ".git" / "hostile-index"

        result = self._run_fixture_attempt(
            {
                "GIT_DIR": str(decoy / ".git"),
                "GIT_WORK_TREE": str(decoy),
                "GIT_COMMON_DIR": str(decoy / ".git"),
                "GIT_INDEX_FILE": str(hostile_index),
                "GIT_OBJECT_DIRECTORY": str(decoy / ".git" / "objects"),
            }
        )

        decoy_after = self._snapshot_repository(decoy)
        changed_paths = sorted(
            path
            for path in decoy_before.keys() | decoy_after.keys()
            if decoy_before.get(path) != decoy_after.get(path)
        )
        self.assertEqual(
            changed_paths,
            [],
            "the hostile inherited Git environment changed decoy paths: "
            + ", ".join(changed_paths),
        )
        self.assertFalse(hostile_index.exists())
        self._successful_attempt_report(result)

    def test_repository_local_configuration_remains_effective(self) -> None:
        expected_author = "Repository Local Writer <local@example.invalid>"
        result = self._run_fixture_attempt(
            {},
            scenario={
                "local_config": {
                    "user.name": "Repository Local Writer",
                    "user.email": "local@example.invalid",
                },
                "expected_author": expected_author,
            },
        )

        report = self._successful_attempt_report(result)
        self.assertEqual(report["author"], expected_author)

    def test_explicit_test_git_environment_overrides_reach_fake_harness(self) -> None:
        expected_author = "Per-test Git Override <override@example.invalid>"
        result = self._run_fixture_attempt(
            {},
            scenario={
                "run_env": {
                    "GIT_CONFIG_COUNT": "2",
                    "GIT_CONFIG_KEY_0": "user.name",
                    "GIT_CONFIG_VALUE_0": "Per-test Git Override",
                    "GIT_CONFIG_KEY_1": "user.email",
                    "GIT_CONFIG_VALUE_1": "override@example.invalid",
                },
                "expected_author": expected_author,
            },
        )

        report = self._successful_attempt_report(result)
        self.assertEqual(report["author"], expected_author)
