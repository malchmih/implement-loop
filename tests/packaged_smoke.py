from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

from support import LoopTestCase

ROOT = Path(__file__).resolve().parents[1]
PACKAGED_EXECUTABLE = ROOT / "dist" / "implement-loop"


class PackagedExecutableTests(LoopTestCase):
    def setUp(self) -> None:
        if sys.platform != "darwin" or platform.machine() != "arm64":
            self.skipTest("the packaged executable is supported on macOS arm64 only")
        if not PACKAGED_EXECUTABLE.is_file():
            self.fail("run `make build` before the packaged executable smoke tests")

        super().setUp()
        external_directory = self.home / "outside source checkout"
        external_directory.mkdir()
        self.executable = external_directory / "implement-loop"
        shutil.copy2(PACKAGED_EXECUTABLE, self.executable)
        self.executable.chmod(0o755)
        self.temporary_directory = self.home / "temporary files"
        self.temporary_directory.mkdir()
        self.empty_path = self.home / "empty path"
        self.empty_path.mkdir()

    def _runtime_environment(self, *, no_external_tools: bool) -> dict[str, str]:
        environment = dict(self.env)
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment["TMPDIR"] = str(self.temporary_directory)
        if no_external_tools:
            environment["PATH"] = str(self.empty_path)
        return environment

    def _run_binary_without_external_tools(
        self, *arguments: str
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [str(self.executable), *arguments],
            cwd=self.home,
            env=self._runtime_environment(no_external_tools=True),
            capture_output=True,
            timeout=60,
        )

    def test_help_runs_outside_checkout_without_python_on_path(self) -> None:
        environment = self._runtime_environment(no_external_tools=True)
        self.assertIsNone(shutil.which("python3", path=environment["PATH"]))

        result = self._run_binary_without_external_tools("--help")

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b"usage: implement-loop", result.stdout)
        self.assertEqual(result.stderr, b"")

    def test_invalid_argument_and_configuration_exit_before_external_commands(self) -> None:
        invalid_argument = self._run_binary_without_external_tools("not-a-number")
        missing_config = self._run_binary_without_external_tools(
            "1", "--config-file", str(self.home / "missing.toml")
        )

        self.assertEqual(invalid_argument.returncode, 2)
        self.assertIn(b"positive integer", invalid_argument.stderr)
        self.assertEqual(missing_config.returncode, 2)
        self.assertIn(b"Cannot read explicitly selected configuration file", missing_config.stderr)
        state = self.load_state()
        self.assertEqual(state["events"], [])
        self.assertEqual(state["copilot_calls"], [])
        self.assertEqual(state["opencode_calls"], [])

    def test_one_file_binary_completes_a_controlled_workflow(self) -> None:
        result, _ = self.run_loop(harness="copilot", executable=self.executable)

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual([event[0] for event in state["events"]], ["assign", "close"])
        self.assertEqual(len(state["copilot_calls"]), 1)
        self.assertEqual(state["opencode_calls"], [])
        self.assertTrue((self.repo / "implementation-2.txt").is_file())
        self.assertEqual(self.git("status", "--porcelain"), "")
