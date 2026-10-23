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
        self.assertIn(b"--harness", result.stdout)
        self.assertIn(b"--allow-no-new-commit TICKET_NUMBER", result.stdout)
        self.assertIn(b"--config-file", result.stdout)
        self.assertIn(b"--opencode-model", result.stdout)
        self.assertIn(b"--copilot-reasoning-effort", result.stdout)
        self.assertIn(b"--records-dir", result.stdout)
        self.assertIn(b"--retention-days", result.stdout)
        self.assertIn(b"--keep-records-forever", result.stdout)
        self.assertIn(b"opencode", result.stdout)
        self.assertIn(b"copilot", result.stdout)
        self.assertFalse((self.home / ".git").exists())
        self.assertFalse((self.home / ".git/implement-loop.lock").exists())
        self.assertFalse((self.home / ".local/state/implement-loop").exists())
        self.assert_commands_not_invoked("git", "gh", "opencode", "copilot")


class ConfigurationTests(LoopTestCase):
    def test_default_selection_uses_opencode_and_native_skill_reference(self) -> None:
        result, runs = self.run_loop(
            "--max-iterations",
            "1",
            harness=None,
            extra_env={"OPENCODE_TEST_CONFIG": "inherited-setting"},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["copilot_calls"], [])
        self.assertEqual(len(state["opencode_calls"]), 1)
        call = state["opencode_calls"][0]
        args = call["args"]
        self.assertEqual(args[0], "run")
        self.assertIn("--standalone", args)
        self.assertIn("--auto", args)
        self.assertEqual(args[args.index("--model") + 1], "github-copilot/gpt-6-luna#max")
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
        self.assertIsNone(run_metadata["reasoning_effort"])
        self.assertIsNone(run_metadata["configuration_file"])
        self.assertEqual(
            run_metadata["records_directory"],
            str((self.home / ".local/state/implement-loop").resolve()),
        )
        self.assertEqual(run_metadata["retention_days"], 7)
        self.assertFalse(run_metadata["keep_records_forever"])
        self.assertTrue((attempt / "agent.log").is_file())
        self.assertIn("successful OpenCode exit", state["events"][1][2])

    def test_project_configuration_is_not_loaded_automatically(self) -> None:
        (self.repo / ".implement-loop.toml").write_text("[execution\nharness = 'copilot'\n")
        self.git("add", ".implement-loop.toml")
        self.git("commit", "--quiet", "-m", "Add unselected project config fixture")

        result, _ = self.run_loop("--max-iterations", "1", harness=None)

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(state["opencode_calls"]), 1)
        self.assertEqual(state["copilot_calls"], [])

    def test_user_config_selects_harness_models_and_attempt_limit(self) -> None:
        self.add_ticket(3)
        self.save_state()
        config_home = self.home / "custom-config"
        config_file = config_home / "implement-loop/config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            '[execution]\nharness = "copilot"\nmax_attempts = 1\n\n'
            "[execution.harnesses.copilot]\n"
            'model = "custom-copilot-model"\nreasoning_effort = "high"\n'
        )

        result, runs = self.run_loop(
            harness=None,
            extra_env={"XDG_CONFIG_HOME": str(config_home)},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(state["copilot_calls"]), 1)
        self.assertEqual(state["opencode_calls"], [])
        self.assertEqual(
            state["copilot_calls"][0]["args"][
                state["copilot_calls"][0]["args"].index("--model") + 1
            ],
            "custom-copilot-model",
        )
        self.assertEqual(
            state["copilot_calls"][0]["args"][
                state["copilot_calls"][0]["args"].index("--reasoning-effort") + 1
            ],
            "high",
        )
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        self.assertEqual(len(runs), 1)

    def test_conventional_user_config_path_is_discovered(self) -> None:
        config_file = self.home / ".config/implement-loop/config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text('[execution]\nharness = "copilot"\n')

        result, runs = self.run_loop(harness=None)

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(state["copilot_calls"]), 1)
        self.assertEqual(state["opencode_calls"], [])
        metadata = json.loads((runs[0] / "run.json").read_text())
        self.assertEqual(metadata["configuration_file"], str(config_file))

    def test_user_config_overrides_opencode_model_and_variant(self) -> None:
        config_home = self.home / "opencode-config"
        config_file = config_home / "implement-loop/config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            "[execution.harnesses.opencode]\n"
            'model = "custom-provider/custom-model"\nvariant = "high"\n'
        )

        result, runs = self.run_loop(
            "--max-iterations",
            "1",
            harness=None,
            extra_env={"XDG_CONFIG_HOME": str(config_home)},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["copilot_calls"], [])
        self.assertEqual(len(state["opencode_calls"]), 1)
        args = state["opencode_calls"][0]["args"]
        self.assertEqual(args[args.index("--model") + 1], "custom-provider/custom-model#high")
        metadata = json.loads((runs[0] / "run.json").read_text())
        self.assertEqual(metadata["model"], "custom-provider/custom-model")
        self.assertEqual(metadata["model_variant"], "high")

    def test_cli_model_and_reasoning_values_override_user_config(self) -> None:
        config_home = self.home / "cli-model-config"
        config_file = config_home / "implement-loop/config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            '[execution]\nharness = "copilot"\n\n'
            "[execution.harnesses.copilot]\n"
            'model = "configured-model"\nreasoning_effort = "low"\n'
        )

        result, _ = self.run_loop(
            "--max-iterations",
            "1",
            "--copilot-model",
            "cli-selected-model",
            "--copilot-reasoning-effort",
            "high",
            harness=None,
            extra_env={"XDG_CONFIG_HOME": str(config_home)},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        args = state["copilot_calls"][0]["args"]
        self.assertEqual(args[args.index("--model") + 1], "cli-selected-model")
        self.assertEqual(args[args.index("--reasoning-effort") + 1], "high")

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
            harness=None,
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
            '[execution]\nharness = "copilot"\nmax_attempts = 5\n\n'
            '[execution.harnesses.copilot]\nmodel = "discovered-model"\n'
            'reasoning_effort = "low"\n'
        )
        explicit_file = self.home / "explicit.toml"
        explicit_file.write_text(
            '[execution]\nharness = "opencode"\nmax_attempts = 2\n\n'
            '[execution.harnesses.copilot]\nmodel = "explicit-model"\n'
            'reasoning_effort = "high"\n'
        )

        result, runs = self.run_loop(
            "--config-file",
            str(explicit_file),
            "--harness",
            "copilot",
            "--max-iterations",
            "1",
            harness=None,
            extra_env={"XDG_CONFIG_HOME": str(config_home)},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(state["copilot_calls"]), 1)
        self.assertEqual(state["opencode_calls"], [])
        call_args = state["copilot_calls"][0]["args"]
        self.assertEqual(call_args[call_args.index("--model") + 1], "explicit-model")
        self.assertEqual(call_args[call_args.index("--reasoning-effort") + 1], "high")
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        run_metadata = json.loads((runs[0] / "run.json").read_text())
        self.assertEqual(run_metadata["configuration_file"], str(explicit_file.resolve()))

    def test_invalid_configuration_fails_before_claiming_or_invoking_a_harness(self) -> None:
        cases = (
            ("malformed", "[execution\nharness = 'copilot'\n", None, b"Invalid TOML"),
            ("invalid-encoding", b"\xff", None, b"not valid UTF-8"),
            (
                "unknown-key",
                '[execution.harnesses.opencode]\nreasoning_effort = "max"\n',
                None,
                b"Unknown configuration key",
            ),
            (
                "non-persistent-commit-exception",
                "[execution]\nallow_no_new_commit_ticket = 2\n",
                None,
                b"Unknown configuration key execution.allow_no_new_commit_ticket",
            ),
            (
                "invalid-type",
                "[execution]\nmax_attempts = true\n",
                None,
                b"execution.max_attempts must be a positive integer",
            ),
            (
                "invalid-reasoning-effort",
                '[execution]\nharness = "copilot"\n\n'
                '[execution.harnesses.copilot]\nreasoning_effort = "bogus"\n',
                None,
                b"reasoning_effort must be one of",
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

                result, runs = self.run_loop(
                    *args,
                    harness=None,
                    extra_env={"XDG_CONFIG_HOME": str(config_home)},
                )

                state = self.load_state()
                self.assertEqual(result.returncode, 2, result.stderr.decode())
                self.assertIn(diagnostic, result.stderr)
                self.assertEqual(state["events"], [])
                self.assertEqual(state["opencode_calls"], [])
                self.assertEqual(state["copilot_calls"], [])
                self.assertEqual(runs, [])
                self.assertFalse(state_dir.exists())
                self.assertFalse((self.repo / ".git/implement-loop.lock").exists())

    def test_run_records_selected_configuration_before_github_discovery(self) -> None:
        config_home = self.home / "early-record-config"
        config_file = config_home / "implement-loop/config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            '[execution]\nharness = "copilot"\nmax_attempts = 2\n\n'
            "[execution.harnesses.copilot]\n"
            'model = "configured-model"\nreasoning_effort = "high"\n'
        )
        self.state["repository_view_failure"] = True
        self.save_state()

        result, runs = self.run_loop(
            harness=None,
            extra_env={"XDG_CONFIG_HOME": str(config_home)},
        )

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"simulated repository discovery failure", result.stderr)
        self.assertEqual(len(runs), 1)
        self.assertIn(f"Run records: {runs[0].resolve()}".encode(), result.stderr)
        self.assertEqual(state["events"], [])
        self.assertEqual(state["copilot_calls"], [])
        self.assertEqual(state["opencode_calls"], [])
        run_metadata = json.loads((runs[0] / "run.json").read_text())
        self.assertEqual(run_metadata["configuration_file"], str(config_file.resolve()))
        self.assertEqual(run_metadata["harness"], "copilot")
        self.assertEqual(run_metadata["model"], "configured-model")
        self.assertEqual(run_metadata["reasoning_effort"], "high")

    def test_missing_git_fails_before_checkout_discovery_or_side_effects(self) -> None:
        minimal_bin = self.controlled_path_directory("python3", "gh", "opencode", "copilot")

        result, runs = self.run_loop(
            harness=None,
            extra_env={"PATH": str(minimal_bin)},
        )

        self.assertEqual(result.returncode, 1, result.stderr.decode())
        self.assert_missing_executable_guidance(result, "git")
        self.assert_missing_executable_stopped_before_run(runs)

    def test_failure_before_record_creation_does_not_print_record_paths(self) -> None:
        minimal_bin = self.controlled_path_directory("python3", "git", "opencode", "copilot")

        result, runs = self.run_loop(
            harness="copilot",
            extra_env={"PATH": str(minimal_bin)},
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, result.stderr.decode())
        self.assert_missing_executable_guidance(result, "gh")
        self.assertNotIn(b"Run records:", output)
        self.assertNotIn(b"Ticket #", output)
        self.assert_missing_executable_stopped_before_run(runs)

    def test_invalid_cli_reasoning_effort_is_rejected_before_side_effects(self) -> None:
        result, runs = self.run_loop(
            "--harness",
            "copilot",
            "--copilot-reasoning-effort",
            "bogus",
            harness=None,
        )

        self.assertEqual(result.returncode, 2, result.stderr.decode())
        self.assertIn(b"reasoning effort must be one of", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertEqual(self.load_state()["copilot_calls"], [])
        self.assertEqual(runs, [])
        self.assertFalse((self.home / ".local/state/implement-loop").exists())
        self.assertFalse((self.repo / ".git/implement-loop.lock").exists())

    def test_empty_configuration_and_record_paths_are_rejected_early(self) -> None:
        for option in ("--config-file", "--records-dir"):
            with self.subTest(option=option):
                self.state = self.default_state()
                self.save_state()

                result, runs = self.run_loop(option, "", harness=None)

                self.assertEqual(result.returncode, 2, result.stderr.decode())
                self.assertIn(b"non-empty path", result.stderr)
                self.assertEqual(self.load_state()["events"], [])
                self.assertEqual(self.load_state()["opencode_calls"], [])
                self.assertEqual(self.load_state()["copilot_calls"], [])
                self.assertEqual(runs, [])
                self.assertFalse((self.home / ".local/state/implement-loop").exists())
                self.assertFalse((self.repo / ".git/implement-loop.lock").exists())

    def test_explicit_copilot_selection_preserves_native_skill_and_model(self) -> None:
        result, runs = self.run_loop("--max-iterations", "1", harness="copilot")

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["opencode_calls"], [])
        self.assertEqual(len(state["copilot_calls"]), 1)
        call = state["copilot_calls"][0]
        args = call["args"]
        self.assertIn("--trust-folder", args)
        self.assertEqual(args[args.index("--model") + 1], "gpt-6-luna")
        self.assertEqual(args[args.index("--reasoning-effort") + 1], "max")
        self.assertNotIn("--continue", args)
        self.assertNotIn("--resume", args)
        self.assertTrue(call["prompt"].startswith("/implement Implement only ticket #2"))
        self.assertEqual(len(runs), 1)
        attempt = runs[0] / "attempt-1-ticket-2"
        metadata = json.loads((attempt / "attempt.json").read_text())
        self.assertEqual(metadata["harness"], "copilot")
        self.assertEqual(metadata["model"], "gpt-6-luna")
        self.assertEqual(metadata["reasoning_effort"], "max")

    def test_unknown_harness_is_rejected_before_side_effects(self) -> None:
        result, _ = self.run_loop("--harness", "unknown", harness=None)

        self.assertEqual(result.returncode, 2)
        self.assertIn(b"invalid choice: 'unknown'", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertEqual(self.load_state()["opencode_calls"], [])
        self.assertEqual(self.load_state()["copilot_calls"], [])
        self.assertFalse((self.home / ".local/state/implement-loop").exists())

    def test_invalid_attempt_limit_is_rejected_before_side_effects(self) -> None:
        result, _ = self.run_loop("--max-iterations", "0")

        self.assertEqual(result.returncode, 2)
        self.assertIn(b"positive integer", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertFalse((self.home / ".local/state/implement-loop").exists())
