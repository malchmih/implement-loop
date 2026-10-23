from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from support import SCRIPT, LoopTestCase, TemporaryHomeTestCase


class InstallationOnlyTests(TemporaryHomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        for utility in ("dirname", "mkdir", "ln", "readlink", "git"):
            target = shutil.which(utility)
            self.assertIsNotNone(target, f"test environment needs {utility}")
            (self.bin / utility).symlink_to(target)
        (self.bin / "python3").symlink_to(sys.executable)
        for tool in ("gh", "opencode", "copilot"):
            self.add_invocation_marker(tool)
        self.env["PATH"] = str(self.bin)

    def test_installer_custom_destination_is_created_and_repeat_safe(self) -> None:
        source = self.copy_source_tree()
        destination = self.home / "chosen executable directory" / "nested"

        first = self.run_installer(source, destination)
        installed = destination / "implement-loop"
        self.assertEqual(first.returncode, 0, first.stderr.decode())
        self.assertTrue(installed.is_symlink())
        self.assertEqual(os.readlink(installed), str(source.resolve() / SCRIPT.name))

        path_env = dict(self.env)
        path_env["PATH"] = str(destination.resolve()) + os.pathsep + path_env["PATH"]
        second = self.run_installer(source, destination, env=path_env)
        self.assertEqual(second.returncode, 0, second.stderr.decode())
        self.assertIn(b"PATH already includes", second.stdout)
        self.assertTrue(installed.is_symlink())
        self.assertEqual(os.readlink(installed), str(source.resolve() / SCRIPT.name))
        self.assert_commands_not_invoked("gh", "opencode", "copilot")

    def test_installer_refuses_unrelated_file_directory_and_link_collisions(self) -> None:
        source = self.copy_source_tree()
        unrelated_target = self.home / "unrelated executable"
        unrelated_target.write_text("keep this target\n")

        for kind in ("file", "directory", "symlink", "dangling symlink"):
            with self.subTest(kind=kind):
                destination = self.home / f"collision destination {kind}"
                destination.mkdir()
                link = destination / "implement-loop"
                marker = destination / "keep.txt"
                if kind == "file":
                    link.write_text("keep this file\n")
                elif kind == "directory":
                    link.mkdir()
                    marker.write_text("keep this directory\n")
                elif kind == "symlink":
                    link.symlink_to(unrelated_target)
                else:
                    link.symlink_to(self.home / "missing unrelated target")
                original_link = os.readlink(link) if link.is_symlink() else None

                result = self.run_installer(source, destination)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(str(link).encode(), result.stderr)
                self.assertIn(b"Choose another destination", result.stderr)
                self.assertIn(b"manually", result.stderr)
                if kind == "file":
                    self.assertEqual(link.read_text(), "keep this file\n")
                elif kind == "directory":
                    self.assertTrue(link.is_dir())
                    self.assertEqual(marker.read_text(), "keep this directory\n")
                else:
                    self.assertTrue(link.is_symlink())
                    self.assertEqual(os.readlink(link), original_link)
                self.assert_commands_not_invoked("gh", "opencode", "copilot")

    def test_installer_reports_prerequisites_and_path_without_mutating_user_settings(self) -> None:
        source = self.copy_source_tree()
        profile_contents = {
            self.home / ".bashrc": b"# keep shell settings\n",
            self.home / ".zshrc": b"# keep shell settings\n",
            self.home / ".config/opencode/opencode.json": b"keep opencode settings\n",
            self.home / ".config/github-copilot/config.json": b"keep copilot settings\n",
        }
        for path, contents in profile_contents.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)

        limited_path = self.home / "installer-prerequisites"
        limited_path.mkdir()
        utilities = ("dirname", "mkdir", "ln", "readlink", "git")
        for utility in utilities:
            (limited_path / utility).symlink_to(self.bin / utility)
        for tool in ("python3", "gh", "opencode"):
            (limited_path / tool).symlink_to(self.bin / tool)

        env = dict(self.env, PATH=str(limited_path))
        existing_entries = {path.relative_to(self.home) for path in self.home.rglob("*")}

        result = self.run_installer(source, env=env)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        output = result.stdout.decode()
        for prerequisite in ("Python 3.11", "git", "gh", "opencode", "copilot"):
            self.assertIn(prerequisite, output)
        self.assertIn("PATH", output)
        self.assertIn("PATH does not include", output)
        self.assertIn(str(self.home / ".local/bin"), output)
        self.assertIn("optional", output.lower())
        self.assertIn("Missing: copilot", output)
        self.assertNotIn("uv", output.lower())
        self.assertEqual(
            {path.relative_to(self.home) for path in self.home.rglob("*")} - existing_entries,
            {Path(".local"), Path(".local/bin"), Path(".local/bin/implement-loop")},
        )
        for path, contents in profile_contents.items():
            self.assertEqual(path.read_bytes(), contents)
        self.assert_commands_not_invoked("gh", "opencode", "copilot")

    def test_installer_requires_home_when_destination_is_omitted(self) -> None:
        source = self.copy_source_tree()
        env = dict(self.env)
        env.pop("HOME")
        existing_entries = {path.relative_to(self.home) for path in self.home.rglob("*")}

        result = self.run_installer(source, env=env)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr,
            b"Error: HOME is not set; pass a destination directory explicitly.\n",
        )
        self.assertEqual(result.stdout, b"")
        self.assertEqual(
            {path.relative_to(self.home) for path in self.home.rglob("*")} - existing_entries,
            set(),
        )
        self.assertFalse((self.home / ".local/bin/implement-loop").exists())
        self.assert_commands_not_invoked("gh", "opencode", "copilot")

    def test_installer_rejects_an_empty_destination_without_side_effects(self) -> None:
        source = self.copy_source_tree()
        existing_entries = {path.relative_to(self.home) for path in self.home.rglob("*")}

        result = self.run_installer(source, "")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, b"Error: destination must be a non-empty directory path.\n")
        self.assertEqual(result.stdout, b"")
        self.assertEqual(
            {path.relative_to(self.home) for path in self.home.rglob("*")} - existing_entries,
            set(),
        )
        self.assertFalse((self.home / "implement-loop").exists())
        self.assert_commands_not_invoked("gh", "opencode", "copilot")


class InstalledCommandIntegrationTests(LoopTestCase):
    def test_installed_command_runs_from_a_target_checkout_subdirectory(self) -> None:
        source, installed = self.install_test_command()
        self.assertTrue(installed.is_symlink())
        self.assertEqual(os.readlink(installed), str(source.resolve() / SCRIPT.name))

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
            executable=installed,
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertTrue((self.repo / "implementation-2.txt").is_file())
        self.assertFalse((source / "implementation-2.txt").exists())
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_installed_default_opencode_honors_config_and_progresses_returned_queue(self) -> None:
        source, installed = self.install_test_command()

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
            executable=installed,
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
            self.assertFalse(attempt_metadata["new_commit_requirement_waived"])
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

    def test_installed_copilot_finalizes_after_manual_unassignment_then_requires_new_commit(
        self,
    ) -> None:
        source, installed = self.install_test_command()

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
        self.state["tickets"]["2"]["assignees"] = [{"login": "tester"}]
        self.state["comments"]["2"].append(
            {"user": {"login": "reviewer"}, "body": "Human approval received."}
        )
        self.state["make_commit_by_ticket"] = {"2": False, "3": False}
        self.state["records_dir"] = str(records_dir)
        self.save_state()

        unassign = subprocess.run(
            [str(self.bin / "gh"), "issue", "edit", "2", "--remove-assignee", "@me"],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(unassign.returncode, 0, unassign.stderr.decode())
        self.assertEqual(self.load_state()["tickets"]["2"]["assignees"], [])

        result, _ = self.run_loop(
            "--allow-no-new-commit",
            "2",
            harness="copilot",
            executable=installed,
            extra_env={"XDG_CONFIG_HOME": str(config_home)},
        )

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"No new commit was made", result.stderr)
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [2, 3])
        self.assertEqual(state["opencode_calls"], [])
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        self.assertEqual(state["tickets"]["3"]["assignees"], [{"login": "tester"}])
        self.assertEqual(
            [event[:2] for event in state["events"]],
            [["unassign", 2], ["assign", 2], ["close", 2], ["assign", 3]],
        )
        self.assertEqual(result.stdout.count(b"Agent output"), 2)
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

        first_comment = next(event[2] for event in state["events"] if event[0] == "close")
        self.assertIn(f"branch main at commit {existing_head}", first_comment)
        self.assertIn("new-commit requirement was waived for ticket #2", first_comment)
        self.assertIn(f"HEAD remained at the pre-attempt commit {existing_head}", first_comment)
        self.assertNotIn("a new descendant commit was produced", first_comment)

        run_directories = sorted(records_dir.glob("run-*"))
        self.assertEqual(len(run_directories), 1)
        run_dir = run_directories[0]
        run_metadata = json.loads((run_dir / "run.json").read_text())
        self.assertEqual(run_metadata["harness"], "copilot")
        self.assertEqual(run_metadata["model"], "provider/custom-copilot")
        self.assertEqual(run_metadata["reasoning_effort"], "high")
        self.assertEqual(run_metadata["max_iterations"], 2)
        self.assertEqual(run_metadata["retention_days"], 30)

        finalized_attempt = run_dir / "attempt-1-ticket-2"
        finalized_metadata = json.loads((finalized_attempt / "attempt.json").read_text())
        self.assertTrue(finalized_metadata["new_commit_requirement_waived"])
        self.assertIn(
            "assess the existing committed implementation", state["copilot_calls"][0]["prompt"]
        )
        self.assertIn("Human approval received.", state["copilot_calls"][0]["prompt"])
        self.assertTrue((finalized_attempt / "completion.json").is_file())
        self.assertTrue((finalized_attempt / "checks.json").is_file())
        self.assertEqual(
            json.loads((finalized_attempt / "completion-evidence.json").read_text())["status"],
            "valid",
        )

        later_attempt = run_dir / "attempt-2-ticket-3"
        later_metadata = json.loads((later_attempt / "attempt.json").read_text())
        self.assertFalse(later_metadata["new_commit_requirement_waived"])
        self.assertIn(
            "requires a new local descendant commit",
            state["copilot_calls"][1]["prompt"],
        )
        self.assertTrue((later_attempt / "completion.json").is_file())
        self.assertFalse((later_attempt / "checks.json").exists())
