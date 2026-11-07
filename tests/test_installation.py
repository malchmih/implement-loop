from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from support import SCRIPT, LoopTestCase


class SourceCommandIntegrationTests(LoopTestCase):
    def test_installed_source_command_runs_from_a_target_path_with_spaces(self) -> None:
        source = self.copy_source_tree()
        target = self.home / "target repository with spaces"
        self.repo.rename(target)
        self.repo = target

        tool_bin = self.home / "installed tools with spaces"
        tool_dir = self.home / "installed tool environments"
        cache_dir = self.home / "uv cache"
        install_env = dict(
            self.env,
            UV_TOOL_BIN_DIR=str(tool_bin),
            UV_TOOL_DIR=str(tool_dir),
            UV_CACHE_DIR=str(cache_dir),
        )
        uv = shutil.which("uv")
        self.assertIsNotNone(uv, "test environment needs uv")
        assert uv is not None
        install = subprocess.run(
            [
                uv,
                "tool",
                "install",
                "--python",
                sys.executable,
                "--from",
                str(source),
                "implement-loop",
            ],
            cwd=source,
            env=install_env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(install.returncode, 0, install.stdout + install.stderr)

        run_env = dict(install_env)
        run_env["PATH"] = str(tool_bin) + os.pathsep + run_env["PATH"]
        result = subprocess.run(
            ["implement-loop", "1", "--max-iterations", "1"],
            cwd=target,
            env=run_env,
            capture_output=True,
            timeout=30,
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertTrue((target / "implementation-2.txt").is_file())
        self.assertFalse((source / "implementation-2.txt").exists())
        self.assertEqual(self.git("status", "--porcelain", cwd=target), "")

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

    def test_source_opencode_requires_new_commit_for_already_committed_work(self) -> None:
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
            "max_attempts = 2\n\n"
            "[execution.harnesses.opencode]\n"
            'model = "provider/custom-opencode"\n'
            'variant = "high"\n\n'
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
            executable=SCRIPT,
            extra_env={"XDG_CONFIG_HOME": str(config_home)},
        )

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"No new commit was made", result.stderr)
        self.assertEqual([call["ticket"] for call in state["opencode_calls"]], [2])
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

        for call in state["opencode_calls"]:
            arguments = call["args"]
            self.assertEqual(
                arguments[arguments.index("--model") + 1],
                "provider/custom-opencode#high",
            )
            self.assertTrue(call["result_initially_absent"])

        run_directories = sorted(records_dir.glob("run-*"))
        self.assertEqual(len(run_directories), 1)
        run_dir = run_directories[0]
        run_metadata = json.loads((run_dir / "run.json").read_text())
        self.assertEqual(run_metadata["harness"], "opencode")
        self.assertEqual(run_metadata["model"], "provider/custom-opencode")
        self.assertEqual(run_metadata["max_iterations"], 2)
        self.assertEqual(run_metadata["retention_days"], 30)

        attempt = run_dir / "attempt-1-ticket-2"
        prompt = state["opencode_calls"][0]["prompt"]
        self.assertIn("requires a new local descendant commit", prompt)
        self.assertIn("Human approval received.", prompt)
        self.assertTrue((attempt / "completion.json").is_file())
        self.assertFalse((attempt / "checks.json").exists())
        self.assertEqual(
            json.loads((attempt / "completion-evidence.json").read_text())["status"],
            "valid",
        )
        self.assertFalse((run_dir / "attempt-2-ticket-3").exists())
