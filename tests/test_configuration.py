from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from support import SCRIPT, LoopTestCase, TemporaryHomeTestCase


class HelpSmokeTests(TemporaryHomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        (self.bin / "python3").symlink_to(sys.executable)
        for tool in ("git", "gh", "opencode", "copilot"):
            self.add_invocation_marker(tool)
        self.env["PATH"] = str(self.bin)

    def test_help_works_without_locking_or_contacting_services(self) -> None:
        result = subprocess.run(
            [str(SCRIPT), "--help"],
            cwd=self.home,
            env=self.env,
            capture_output=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b"implement-loop", result.stdout)
        self.assertIn(b"--max-iterations", result.stdout)
        self.assertIn(b"--config-file", result.stdout)
        self.assertIn(b"--opencode-model", result.stdout)
        self.assertIn(b"--records-dir", result.stdout)
        self.assertIn(b"--retention-days", result.stdout)
        self.assertIn(b"--keep-records-forever", result.stdout)
        self.assertIn(b"opencode", result.stdout.lower())
        self.assertNotIn(b"--harness", result.stdout)
        self.assertNotIn(b"copilot", result.stdout.lower())
        self.assertFalse((self.home / ".git").exists())
        self.assertFalse((self.home / ".git/implement-loop.lock").exists())
        self.assertFalse((self.home / ".local/state/implement-loop").exists())
        self.assert_commands_not_invoked("git", "gh", "opencode", "copilot")


class ConfigurationTests(LoopTestCase):
    def test_default_selection_uses_opencode_and_native_skill_reference(self) -> None:
        result, runs = self.run_loop(
            "--max-iterations",
            "1",
            extra_env={"OPENCODE_TEST_CONFIG": "inherited-setting"},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(len(state["opencode_calls"]), 1)
        call = state["opencode_calls"][0]
        args = call["args"]
        self.assertEqual(args[0], "run")
        self.assertIn("--standalone", args)
        self.assertIn("--auto", args)
        self.assertEqual(args[args.index("--model") + 1], "github-copilot/gpt-6-luna#max")
        self.assertEqual(args[args.index("--format") + 1], "json")
        self.assertNotIn("--continue", args)
        self.assertNotIn("--session", args)
        self.assertTrue(call["result_initially_absent"])
        self.assertTrue(call["prompt"].startswith("@implement Implement only ticket #2"))
        self.assertEqual(call["opencode_config"], "inherited-setting")
        self.assertEqual(call["no_color"], "1")
        self.assertEqual(call["force_color"], "0")
        self.assertEqual(len(runs), 1)
        attempt = runs[0] / "attempt-1-ticket-2"
        attempt_metadata = json.loads((attempt / "attempt.json").read_text())
        self.assertEqual(attempt_metadata["harness"], "opencode")
        self.assertEqual(attempt_metadata["attempt_id"], call["attempt_id"])
        self.assertIn(attempt_metadata["result_path"], call["prompt"])
        self.assertFalse(Path(attempt_metadata["result_path"]).exists())
        completion = json.loads((attempt / "completion.json").read_text())
        self.assertEqual(completion["attempt_id"], call["attempt_id"])
        run_metadata = json.loads((runs[0] / "run.json").read_text())
        self.assertEqual(run_metadata["model"], "github-copilot/gpt-6-luna")
        self.assertEqual(run_metadata["model_variant"], "max")
        self.assertIsNone(run_metadata["configuration_file"])
        self.assertEqual(
            run_metadata["records_directory"],
            str((self.home / ".local/state/implement-loop").resolve()),
        )
        self.assertEqual(run_metadata["retention_days"], 7)
        self.assertFalse(run_metadata["keep_records_forever"])
        agent_log = attempt / "agent.log"
        self.assertTrue(agent_log.is_file())
        self.assertEqual(json.loads(agent_log.read_text())["type"], "text")
        self.assertIn("successful OpenCode exit", state["events"][1][2])

    def test_project_configuration_is_not_loaded_automatically(self) -> None:
        (self.repo / ".implement-loop.toml").write_text("[execution\n")
        self.git("add", ".implement-loop.toml")
        self.git("commit", "--quiet", "-m", "Add unselected project config fixture")

        result, _ = self.run_loop("--max-iterations", "1")

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(self.load_state()["opencode_calls"]), 1)

    def test_user_config_selects_opencode_model_variant_and_attempt_limit(self) -> None:
        self.add_ticket(3)
        self.save_state()
        config_home = self.home / "custom-config"
        config_file = config_home / "implement-loop/config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            "[execution]\nmax_attempts = 1\n\n"
            "[execution.harnesses.opencode]\n"
            'model = "custom-provider/custom-model"\nvariant = "high"\n'
        )

        result, runs = self.run_loop(extra_env={"XDG_CONFIG_HOME": str(config_home)})

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(state["opencode_calls"]), 1)
        args = state["opencode_calls"][0]["args"]
        self.assertEqual(args[args.index("--model") + 1], "custom-provider/custom-model#high")
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        metadata = json.loads((runs[0] / "run.json").read_text())
        self.assertEqual(metadata["configuration_file"], str(config_file))

    def test_cli_opencode_model_and_variant_override_user_config(self) -> None:
        config_home = self.home / "cli-opencode-config"
        config_file = config_home / "implement-loop/config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            "[execution.harnesses.opencode]\n"
            'model = "configured-provider/configured-model"\nvariant = "low"\n'
        )

        result, _ = self.run_loop(
            "--max-iterations",
            "1",
            "--opencode-model",
            "cli-provider/cli-model",
            "--opencode-variant",
            "medium",
            extra_env={"XDG_CONFIG_HOME": str(config_home)},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        args = state["opencode_calls"][0]["args"]
        self.assertEqual(args[args.index("--model") + 1], "cli-provider/cli-model#medium")

    def test_explicit_config_replaces_discovery_and_cli_values_take_precedence(self) -> None:
        self.add_ticket(3)
        self.save_state()
        config_home = self.home / "custom-config"
        discovered_file = config_home / "implement-loop/config.toml"
        discovered_file.parent.mkdir(parents=True)
        discovered_file.write_text(
            "[execution]\nmax_attempts = 5\n\n"
            '[execution.harnesses.opencode]\nmodel = "discovered-model"\n'
        )
        explicit_file = self.home / "explicit.toml"
        explicit_file.write_text(
            "[execution]\nmax_attempts = 2\n\n"
            '[execution.harnesses.opencode]\nmodel = "explicit-model"\n'
        )

        result, runs = self.run_loop(
            "--config-file",
            str(explicit_file),
            "--max-iterations",
            "1",
            extra_env={"XDG_CONFIG_HOME": str(config_home)},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(state["opencode_calls"]), 1)
        call_args = state["opencode_calls"][0]["args"]
        self.assertEqual(call_args[call_args.index("--model") + 1], "explicit-model#max")
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        run_metadata = json.loads((runs[0] / "run.json").read_text())
        self.assertEqual(run_metadata["configuration_file"], str(explicit_file.resolve()))

    def test_invalid_configuration_fails_before_claiming_or_invoking_opencode(self) -> None:
        cases = (
            ("malformed", "[execution\n", None, b"Invalid TOML"),
            ("invalid-encoding", b"\xff", None, b"not valid UTF-8"),
            (
                "unknown-key",
                '[execution.harnesses.opencode]\nreasoning_effort = "max"\n',
                None,
                b"Unknown configuration key",
            ),
            (
                "obsolete-harness-key",
                '[execution]\nharness = "copilot"\n',
                None,
                b"Unknown configuration key execution.harness",
            ),
            (
                "obsolete-copilot-settings",
                '[execution.harnesses.copilot]\nmodel = "old-model"\n',
                None,
                b"Unknown configuration key execution.harnesses.copilot",
            ),
            (
                "invalid-type",
                "[execution]\nmax_attempts = true\n",
                None,
                b"execution.max_attempts must be a positive integer",
            ),
            (
                "nul-model",
                '[execution.harnesses.opencode]\nmodel = "\\u0000"\n',
                None,
                b"without whitespace or NUL",
            ),
            ("missing-explicit", None, "missing.toml", b"Cannot read explicitly selected"),
        )
        for name, contents, explicit_path, diagnostic in cases:
            with self.subTest(name=name):
                state_dir = self.home / ".local/state/implement-loop"
                if state_dir.exists():
                    shutil.rmtree(state_dir)
                self.state = self.default_state()
                self.save_state()
                config_home = self.home / f"config-{name}"
                args: tuple[str, ...] = ()
                if explicit_path is None:
                    config_file = config_home / "implement-loop/config.toml"
                    config_file.parent.mkdir(parents=True)
                    assert contents is not None
                    if isinstance(contents, bytes):
                        config_file.write_bytes(contents)
                    else:
                        config_file.write_text(contents)
                else:
                    args = ("--config-file", str(config_home / explicit_path))

                result, runs = self.run_loop(*args, extra_env={"XDG_CONFIG_HOME": str(config_home)})

                state = self.load_state()
                self.assertEqual(result.returncode, 2, result.stderr.decode())
                self.assertIn(diagnostic, result.stderr)
                self.assertEqual(state["events"], [])
                self.assertEqual(state["opencode_calls"], [])
                self.assertEqual(runs, [])
                self.assertFalse(state_dir.exists())
                self.assertFalse((self.repo / ".git/implement-loop.lock").exists())

    def test_run_records_selected_configuration_before_github_discovery(self) -> None:
        config_home = self.home / "early-record-config"
        config_file = config_home / "implement-loop/config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            "[execution]\nmax_attempts = 2\n\n"
            '[execution.harnesses.opencode]\nmodel = "configured-model"\nvariant = "high"\n'
        )
        self.state["repository_view_failure"] = True
        self.save_state()

        result, runs = self.run_loop(extra_env={"XDG_CONFIG_HOME": str(config_home)})

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"simulated repository discovery failure", result.stderr)
        self.assertEqual(len(runs), 1)
        self.assertIn(f"Run records: {runs[0].resolve()}".encode(), result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertEqual(self.load_state()["opencode_calls"], [])
        run_metadata = json.loads((runs[0] / "run.json").read_text())
        self.assertEqual(run_metadata["configuration_file"], str(config_file.resolve()))
        self.assertEqual(run_metadata["harness"], "opencode")
        self.assertEqual(run_metadata["model"], "configured-model")

    def test_missing_git_fails_before_checkout_discovery_or_side_effects(self) -> None:
        minimal_bin = self.controlled_path_directory("python3", "gh", "opencode")

        result, runs = self.run_loop(extra_env={"PATH": str(minimal_bin)})

        self.assertEqual(result.returncode, 1, result.stderr.decode())
        self.assert_missing_executable_guidance(result, "git")
        self.assert_missing_executable_stopped_before_run(runs)

    def test_failure_before_record_creation_does_not_print_record_paths(self) -> None:
        minimal_bin = self.controlled_path_directory("python3", "git", "opencode")

        result, runs = self.run_loop(extra_env={"PATH": str(minimal_bin)})

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, result.stderr.decode())
        self.assert_missing_executable_guidance(result, "gh")
        self.assertNotIn(b"Run records:", output)
        self.assertNotIn(b"Ticket #", output)
        self.assert_missing_executable_stopped_before_run(runs)

    def test_harness_selection_option_is_rejected_before_side_effects(self) -> None:
        result, runs = self.run_loop("--harness", "copilot")

        self.assertEqual(result.returncode, 2, result.stderr.decode())
        self.assertIn(b"unrecognized arguments: --harness copilot", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertEqual(self.load_state()["opencode_calls"], [])
        self.assertEqual(runs, [])
        self.assertFalse((self.home / ".local/state/implement-loop").exists())
        self.assertFalse((self.repo / ".git/implement-loop.lock").exists())

    def test_empty_configuration_and_record_paths_are_rejected_early(self) -> None:
        for option in ("--config-file", "--records-dir"):
            with self.subTest(option=option):
                self.state = self.default_state()
                self.save_state()

                result, runs = self.run_loop(option, "")

                self.assertEqual(result.returncode, 2, result.stderr.decode())
                self.assertIn(b"non-empty path", result.stderr)
                self.assertEqual(self.load_state()["events"], [])
                self.assertEqual(self.load_state()["opencode_calls"], [])
                self.assertEqual(runs, [])
                self.assertFalse((self.home / ".local/state/implement-loop").exists())
                self.assertFalse((self.repo / ".git/implement-loop.lock").exists())

    def test_invalid_attempt_limit_is_rejected_before_side_effects(self) -> None:
        result, _ = self.run_loop("--max-iterations", "0")

        self.assertEqual(result.returncode, 2)
        self.assertIn(b"positive integer", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertFalse((self.home / ".local/state/implement-loop").exists())
