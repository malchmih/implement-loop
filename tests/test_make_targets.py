from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_FAKE_COMMAND = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

record = {"command": Path(sys.argv[0]).name, "args": sys.argv[1:]}
with Path(os.environ["MAKE_TEST_LOG"]).open("a") as log:
    log.write(json.dumps(record) + "\n")
if os.environ.get("MAKE_TEST_FAILURE") == json.dumps(record):
    sys.exit(23)
"""


def _call(command: str, *arguments: str) -> dict[str, object]:
    return {"command": command, "args": list(arguments)}


class MakeTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory(prefix="make-target-test-")
        self.addCleanup(temporary_directory.cleanup)
        self.home = Path(temporary_directory.name)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.log = self.home / "commands.jsonl"

        for command in ("uv", "shellcheck"):
            executable = self.bin / command
            executable.write_text(_FAKE_COMMAND)
            executable.chmod(0o755)

        self.environment = os.environ.copy()
        for variable in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL"):
            self.environment.pop(variable, None)
        self.environment["PATH"] = os.pathsep.join((str(self.bin), self.environment["PATH"]))
        self.environment["MAKE_TEST_LOG"] = str(self.log)

    def _run_make(
        self,
        target: str,
        *,
        failure: dict[str, object] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = dict(self.environment)
        if failure is not None:
            environment["MAKE_TEST_FAILURE"] = json.dumps(failure)
        return subprocess.run(
            ["make", target],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def _calls(self) -> list[dict[str, object]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_static_target_runs_all_static_checks(self) -> None:
        result = self._run_make("check-static")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self._calls(),
            [
                _call("shellcheck", "install.sh"),
                _call("uv", "run", "--locked", "ruff", "check", "."),
                _call("uv", "run", "--locked", "ruff", "format", "--check", "."),
                _call("uv", "run", "--locked", "mypy"),
            ],
        )

    def test_behavioral_target_runs_the_existing_unittest_suite(self) -> None:
        result = self._run_make("test")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self._calls(),
            [
                _call(
                    "uv",
                    "run",
                    "--locked",
                    "python",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-v",
                )
            ],
        )

    def test_aggregate_syncs_locked_environment_then_runs_both_groups(self) -> None:
        result = self._run_make("check")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self._calls(),
            [
                _call("uv", "sync", "--locked"),
                _call("shellcheck", "install.sh"),
                _call("uv", "run", "--locked", "ruff", "check", "."),
                _call("uv", "run", "--locked", "ruff", "format", "--check", "."),
                _call("uv", "run", "--locked", "mypy"),
                _call(
                    "uv",
                    "run",
                    "--locked",
                    "python",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-v",
                ),
            ],
        )

    def test_static_failure_fails_aggregate_and_skips_remaining_checks(self) -> None:
        failure = _call("uv", "run", "--locked", "ruff", "format", "--check", ".")

        result = self._run_make("check", failure=failure)

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self._calls(),
            [
                _call("uv", "sync", "--locked"),
                _call("shellcheck", "install.sh"),
                _call("uv", "run", "--locked", "ruff", "check", "."),
                failure,
            ],
        )

    def test_behavioral_failure_propagates_from_its_independent_target(self) -> None:
        failure = _call(
            "uv",
            "run",
            "--locked",
            "python",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-v",
        )

        result = self._run_make("test", failure=failure)

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self._calls(), [failure])
