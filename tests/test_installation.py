from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from support import SCRIPT, LoopTestCase, TemporaryHomeTestCase


class MakeInstallCommandTests(TemporaryHomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.make = shutil.which("make")
        self.assertIsNotNone(self.make, "test environment needs make")
        assert self.make is not None

        self.source = self.copy_source_tree()
        self.build_tools = self.home / "build tools"
        self.build_tools.mkdir()
        self.build_log = self.home / "build invocation.log"
        self.env.pop("PREFIX", None)
        self.env["IMPLEMENT_LOOP_TEST_BUILD_LOG"] = str(self.build_log)
        self.env["IMPLEMENT_LOOP_TEST_BUILD_PAYLOAD"] = "fake executable\n"
        self.env["IMPLEMENT_LOOP_TEST_PYTHON"] = sys.executable
        self.env["PATH"] = str(self.build_tools) + os.pathsep + self.env["PATH"]

        self._write_command(
            "uname",
            "#!/bin/sh\n"
            'case "$1" in\n'
            "  -s) printf 'Darwin\\n' ;;\n"
            "  -m) printf 'arm64\\n' ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n",
        )
        self._write_command(
            "uv",
            "#!/bin/sh\n"
            "set -eu\n"
            'printf \'%s\\n\' "$*" >> "$IMPLEMENT_LOOP_TEST_BUILD_LOG"\n'
            'if [ "$1" = run ] && [ "$3" = python ]; then\n'
            '  if [ "${IMPLEMENT_LOOP_TEST_REPLACEMENT_FAIL:-}" = 1 ]; then\n'
            "    printf 'simulated replacement failure\\n' >&2\n"
            "    exit 73\n"
            "  fi\n"
            '  if [ -n "${IMPLEMENT_LOOP_TEST_RACE_DESTINATION:-}" ]; then\n'
            '    ln -s "$IMPLEMENT_LOOP_TEST_RACE_TARGET" "$IMPLEMENT_LOOP_TEST_RACE_DESTINATION"\n'
            "  fi\n"
            "  shift 2\n"
            "  shift\n"
            '  exec "$IMPLEMENT_LOOP_TEST_PYTHON" "$@"\n'
            "fi\n"
            'if [ "${IMPLEMENT_LOOP_TEST_BUILD_FAIL:-}" = 1 ]; then\n'
            "  printf 'simulated build failure\\n' >&2\n"
            "  exit 73\n"
            "fi\n"
            "mkdir -p dist\n"
            "printf '%s' \"$IMPLEMENT_LOOP_TEST_BUILD_PAYLOAD\" > dist/implement-loop\n"
            "chmod 755 dist/implement-loop\n",
        )

    def _write_command(self, name: str, contents: str) -> Path:
        command = self.build_tools / name
        command.write_text(contents)
        command.chmod(0o700)
        return command

    def _run_make_install(
        self,
        *,
        prefix: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [self.make, "install"]
        if prefix is not None:
            command.append(f"PREFIX={prefix}")
        return subprocess.run(
            command,
            cwd=self.source,
            env=env if env is not None else self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def _write_existing_executable(self, prefix: Path) -> Path:
        installed = prefix / "bin/implement-loop"
        installed.parent.mkdir(parents=True)
        installed.write_text("previous working executable\n")
        installed.chmod(0o755)
        return installed

    def test_make_install_builds_and_installs_default_user_executable(self) -> None:
        result = self._run_make_install()

        installed = self.home / ".local/bin/implement-loop"
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(installed.is_file())
        self.assertFalse(installed.is_symlink())
        self.assertTrue(os.access(installed, os.X_OK))
        self.assertEqual(installed.read_text(), "fake executable\n")
        self.assertIn("run --locked --group build pyinstaller", self.build_log.read_text())

    def test_make_install_uses_custom_prefix_and_replaces_regular_executable(self) -> None:
        prefix = self.home / "chosen install prefix"
        first = self._run_make_install(prefix=prefix)

        installed = prefix / "bin/implement-loop"
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(installed.read_text(), "fake executable\n")
        self.assertFalse(installed.is_symlink())

        upgrade_env = dict(self.env)
        upgrade_env["IMPLEMENT_LOOP_TEST_BUILD_PAYLOAD"] = "upgraded executable\n"
        second = self._run_make_install(prefix=prefix, env=upgrade_env)

        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(installed.read_text(), "upgraded executable\n")
        self.assertTrue(os.access(installed, os.X_OK))

    def test_make_install_refuses_symlink_and_directory_destinations(self) -> None:
        prefix = self.home / "collision prefix"
        bin_directory = prefix / "bin"
        bin_directory.mkdir(parents=True)
        destination = bin_directory / "implement-loop"
        target = self.home / "preserved target"
        target.write_text("keep this target\n")

        destination.symlink_to(target)
        symlink_result = self._run_make_install(prefix=prefix)

        self.assertNotEqual(symlink_result.returncode, 0, symlink_result.stdout)
        self.assertIn("refusing to replace", symlink_result.stderr)
        self.assertTrue(destination.is_symlink())
        self.assertEqual(os.readlink(destination), str(target))
        self.assertEqual(target.read_text(), "keep this target\n")

        destination.unlink()
        destination.mkdir()
        marker = destination / "keep.txt"
        marker.write_text("keep this directory\n")
        directory_result = self._run_make_install(prefix=prefix)

        self.assertNotEqual(directory_result.returncode, 0, directory_result.stdout)
        self.assertIn("refusing to replace", directory_result.stderr)
        self.assertTrue(destination.is_dir())
        self.assertEqual(marker.read_text(), "keep this directory\n")

        marker.unlink()
        destination.rmdir()
        missing_target = self.home / "missing executable target"
        destination.symlink_to(missing_target)
        dangling_result = self._run_make_install(prefix=prefix)

        self.assertNotEqual(dangling_result.returncode, 0, dangling_result.stdout)
        self.assertIn("refusing to replace", dangling_result.stderr)
        self.assertTrue(destination.is_symlink())
        self.assertEqual(os.readlink(destination), str(missing_target))
        self.assertFalse(missing_target.exists())

    def test_make_install_build_failure_preserves_existing_executable(self) -> None:
        prefix = self.home / "existing install prefix"
        installed = self._write_existing_executable(prefix)
        failed_build_env = dict(self.env)
        failed_build_env["IMPLEMENT_LOOP_TEST_BUILD_FAIL"] = "1"

        result = self._run_make_install(prefix=prefix, env=failed_build_env)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("simulated build failure", result.stderr)
        self.assertEqual(installed.read_text(), "previous working executable\n")
        self.assertTrue(os.access(installed, os.X_OK))

    def test_make_install_replacement_failure_preserves_existing_executable(self) -> None:
        prefix = self.home / "existing install prefix"
        installed = self._write_existing_executable(prefix)
        failed_replacement_env = dict(self.env)
        failed_replacement_env["IMPLEMENT_LOOP_TEST_REPLACEMENT_FAIL"] = "1"

        result = self._run_make_install(prefix=prefix, env=failed_replacement_env)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("cannot replace the installed executable", result.stderr)
        self.assertEqual(installed.read_text(), "previous working executable\n")
        self.assertTrue(os.access(installed, os.X_OK))
        self.assertEqual(list(installed.parent.glob(".implement-loop.*")), [])

    def test_make_install_does_not_follow_a_symlink_created_before_replacement(self) -> None:
        prefix = self.home / "racing install prefix"
        installed = prefix / "bin/implement-loop"
        installed.parent.mkdir(parents=True)
        target_directory = self.home / "symlink target"
        target_directory.mkdir()
        race_env = dict(self.env)
        race_env["IMPLEMENT_LOOP_TEST_RACE_DESTINATION"] = str(installed)
        race_env["IMPLEMENT_LOOP_TEST_RACE_TARGET"] = str(target_directory)

        result = self._run_make_install(prefix=prefix, env=race_env)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("refusing to replace symlink", result.stderr)
        self.assertTrue(installed.is_symlink())
        self.assertEqual(os.readlink(installed), str(target_directory))
        self.assertEqual(list(target_directory.iterdir()), [])
        self.assertEqual(list(installed.parent.glob(".implement-loop.*")), [])


class SourceCommandIntegrationTests(LoopTestCase):
    def test_source_command_runs_from_a_target_checkout_subdirectory(self) -> None:
        source = SCRIPT.parent
        nested = self.repo / "nested"
        nested.mkdir()
        (nested / "tracked.txt").write_text("target checkout fixture\n")
        self.git("add", "nested")
        self.git("commit", "--quiet", "-m", "Add target subdirectory")

        result, _ = self.run_loop(
            "--max-iterations",
            "1",
            harness=None,
            cwd=nested,
            executable=SCRIPT,
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertTrue((self.repo / "implementation-2.txt").is_file())
        self.assertFalse((source / "implementation-2.txt").exists())
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_source_default_opencode_honors_config_and_progresses_returned_queue(self) -> None:
        source = SCRIPT.parent

        records_dir = self.home / "configured private records"
        config_home = self.home / "configured user settings"
        config_file = config_home / "implement-loop/config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            "[execution]\n"
            "max_attempts = 2\n\n"
            "[execution.harnesses.opencode]\n"
            'model = "provider/custom-opencode"\n'
            'variant = "high"\n\n'
            "[records]\n"
            f'directory = "{records_dir}"\n'
            "retention_days = 14\n"
        )

        self.add_ticket(3)
        self.add_ticket(4)
        self.state["children"] = [{"number": 3}, {"number": 2}, {"number": 4}]
        self.state["records_dir"] = str(records_dir)
        self.save_state()

        nested = self.repo / "nested"
        nested.mkdir()
        (nested / "target.txt").write_text("target repository, not source checkout\n")
        self.git("add", "nested")
        self.git("commit", "--quiet", "-m", "Add target subdirectory")
        state_home = self.home / "configured XDG state"

        result, _ = self.run_loop(
            harness=None,
            cwd=nested,
            executable=SCRIPT,
            extra_env={
                "XDG_CONFIG_HOME": str(config_home),
                "XDG_STATE_HOME": str(state_home),
            },
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([call["ticket"] for call in state["opencode_calls"]], [3, 2])
        self.assertEqual(state["copilot_calls"], [])
        self.assertEqual(state["tickets"]["3"]["state"], "closed")
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["4"]["state"], "open")
        self.assertEqual(state["tickets"]["4"]["assignees"], [])
        self.assertIn(b"Attempt limit reached after 2 invocation(s)", result.stdout)
        self.assertEqual(result.stdout.count(b"Agent output"), 2)

        for call in state["opencode_calls"]:
            arguments = call["args"]
            self.assertEqual(arguments[0], "run")
            self.assertIn("--standalone", arguments)
            self.assertNotIn("--continue", arguments)
            self.assertNotIn("--session", arguments)
            self.assertNotIn("--fork", arguments)
            self.assertEqual(
                arguments[arguments.index("--model") + 1],
                "provider/custom-opencode#high",
            )
            self.assertTrue(call["result_initially_absent"])
            self.assertTrue(
                call["prompt"].startswith(f"@implement Implement only ticket #{call['ticket']}")
            )

        run_directories = sorted(records_dir.glob("run-*"))
        self.assertEqual(len(run_directories), 1)
        run_dir = run_directories[0]
        self.assertEqual(stat.S_IMODE(records_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(run_dir.stat().st_mode), 0o700)
        run_metadata = json.loads((run_dir / "run.json").read_text())
        self.assertEqual(run_metadata["harness"], "opencode")
        self.assertEqual(run_metadata["model"], "provider/custom-opencode")
        self.assertEqual(run_metadata["model_variant"], "high")
        self.assertEqual(run_metadata["max_iterations"], 2)
        self.assertEqual(run_metadata["configuration_file"], str(config_file.resolve()))
        self.assertEqual(run_metadata["records_directory"], str(records_dir.resolve()))
        self.assertEqual(run_metadata["retention_days"], 14)
        self.assertFalse((state_home / "implement-loop").exists())

        for attempt_number, ticket_number in enumerate((3, 2), start=1):
            attempt = run_dir / f"attempt-{attempt_number}-ticket-{ticket_number}"
            attempt_metadata = json.loads((attempt / "attempt.json").read_text())
            self.assertEqual(attempt_metadata["harness"], "opencode")
            self.assertEqual(
                json.loads((attempt / "completion-evidence.json").read_text())["status"],
                "valid",
            )
            self.assertTrue((attempt / "completion.json").is_file())
            self.assertIn("Agent output", (attempt / "agent.log").read_text())
            result_path = Path(attempt_metadata["result_path"])
            self.assertFalse(result_path.exists())
            self.assertFalse(result_path.is_relative_to(self.repo))
            self.assertEqual(stat.S_IMODE((attempt / "agent.log").stat().st_mode), 0o600)

        closure_comments = [event[2] for event in state["events"] if event[0] == "close"]
        self.assertEqual(len(closure_comments), 2)
        for comment in closure_comments:
            self.assertIn("branch main at commit ", comment)
            self.assertIn("new descendant commit was produced and verified", comment)
            self.assertIn("The wrapper did not push or open a PR", comment)
        for ticket_number in (2, 3):
            self.assertTrue((self.repo / f"implementation-{ticket_number}.txt").is_file())
            self.assertFalse((source / f"implementation-{ticket_number}.txt").exists())
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_source_copilot_requires_new_commit_for_already_committed_work(self) -> None:
        existing_implementation = self.repo / "implementation-2.txt"
        existing_implementation.write_text("implementation committed before approval\n")
        self.git("add", existing_implementation.name)
        self.git("commit", "--quiet", "-m", "Existing implementation for ticket 2")
        existing_head = self.git("rev-parse", "HEAD")

        records_dir = self.home / "finalization private records"
        config_home = self.home / "finalization user settings"
        config_file = config_home / "implement-loop/config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            "[execution]\n"
            'harness = "opencode"\n'
            "max_attempts = 2\n\n"
            "[execution.harnesses.copilot]\n"
            'model = "provider/custom-copilot"\n'
            'reasoning_effort = "high"\n\n'
            "[records]\n"
            f'directory = "{records_dir}"\n'
            "retention_days = 30\n"
        )
        self.add_ticket(3)
        self.state["comments"]["2"].append(
            {"user": {"login": "reviewer"}, "body": "Human approval received."}
        )
        self.state["make_commit"] = False
        self.state["records_dir"] = str(records_dir)
        self.save_state()

        result, _ = self.run_loop(
            harness="copilot",
            executable=SCRIPT,
            extra_env={"XDG_CONFIG_HOME": str(config_home)},
        )

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"No new commit was made", result.stderr)
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [2])
        self.assertEqual(state["opencode_calls"], [])
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        self.assertEqual(state["tickets"]["3"]["assignees"], [])
        self.assertEqual(
            [event[:2] for event in state["events"]],
            [["assign", 2]],
        )
        self.assertEqual(result.stdout.count(b"Agent output"), 1)
        self.assertEqual(self.git("rev-parse", "HEAD"), existing_head)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(
            existing_implementation.read_text(), "implementation committed before approval\n"
        )

        for call in state["copilot_calls"]:
            arguments = call["args"]
            self.assertEqual(
                arguments[arguments.index("--model") + 1],
                "provider/custom-copilot",
            )
            self.assertEqual(
                arguments[arguments.index("--reasoning-effort") + 1],
                "high",
            )
            self.assertTrue(call["result_initially_absent"])

        run_directories = sorted(records_dir.glob("run-*"))
        self.assertEqual(len(run_directories), 1)
        run_dir = run_directories[0]
        run_metadata = json.loads((run_dir / "run.json").read_text())
        self.assertEqual(run_metadata["harness"], "copilot")
        self.assertEqual(run_metadata["model"], "provider/custom-copilot")
        self.assertEqual(run_metadata["reasoning_effort"], "high")
        self.assertEqual(run_metadata["max_iterations"], 2)
        self.assertEqual(run_metadata["retention_days"], 30)

        attempt = run_dir / "attempt-1-ticket-2"
        self.assertIn("requires a new local descendant commit", state["copilot_calls"][0]["prompt"])
        self.assertIn("Human approval received.", state["copilot_calls"][0]["prompt"])
        self.assertTrue((attempt / "completion.json").is_file())
        self.assertFalse((attempt / "checks.json").exists())
        self.assertEqual(
            json.loads((attempt / "completion-evidence.json").read_text())["status"],
            "valid",
        )
        self.assertFalse((run_dir / "attempt-2-ticket-3").exists())
