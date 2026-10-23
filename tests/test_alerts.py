from __future__ import annotations

import subprocess
import time

from support import SCRIPT, LoopTestCase


class AlertTests(LoopTestCase):
    def _make_queue_ineligible(self) -> None:
        self.state["tickets"]["2"]["assignees"] = [{"login": "tester"}]
        self.save_state()

    def test_zero_attempt_no_eligible_children_delivers_one_macos_alert_after_cleanup(self) -> None:
        self._make_queue_ineligible()
        self.set_repository_identity("test/repo.with-special_chars")

        result, _ = self.run_loop(
            harness="copilot",
            extra_env=self.platform_test_environment("darwin"),
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b"No eligible children", result.stdout)
        self.assertEqual(self.load_state()["copilot_calls"], [])
        calls = self.alert_calls()
        self.assertEqual([call["tool"] for call in calls], ["osascript", "afplay"])
        self.assertEqual(
            calls[0]["args"][-2:],
            ["test/repo.with-special_chars", "No eligible children"],
        )
        self.assertEqual(calls[1]["args"], ["/System/Library/Sounds/Glass.aiff"])
        self.assertFalse(calls[0]["lock_exists"])
        self.assertEqual(calls[0]["active_run_markers"], [])

    def test_failure_or_denial_in_one_channel_does_not_prevent_the_other(self) -> None:
        self._make_queue_ineligible()

        for failing_tool in ("osascript", "afplay"):
            with self.subTest(failing_tool=failing_tool):
                self.clear_alert_calls()
                result, _ = self.run_loop(
                    harness="copilot",
                    extra_env={
                        **self.platform_test_environment("darwin"),
                        f"IMPLEMENT_LOOP_TEST_{failing_tool.upper()}_MODE": "fail",
                    },
                )

                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertNotIn(b"simulated alert", result.stdout + result.stderr)
                calls = self.alert_calls()
                self.assertEqual([call["tool"] for call in calls], ["osascript", "afplay"])

    def test_missing_delivery_tool_does_not_prevent_the_available_channel(self) -> None:
        self._make_queue_ineligible()

        for missing_tool in ("osascript", "afplay"):
            with self.subTest(missing_tool=missing_tool):
                self.clear_alert_calls()
                available_tool = "afplay" if missing_tool == "osascript" else "osascript"
                command_names = ["python3", "git", "gh", "copilot", available_tool]
                path = self.controlled_path_directory(*command_names)
                result, _ = self.run_loop(
                    harness="copilot",
                    extra_env={
                        **self.platform_test_environment("darwin"),
                        "PATH": str(path),
                    },
                )

                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual([call["tool"] for call in self.alert_calls()], [available_tool])

    def test_hung_delivery_commands_are_bounded_quiet_and_independent(self) -> None:
        self._make_queue_ineligible()

        for hanging_tool in ("osascript", "afplay"):
            with self.subTest(hanging_tool=hanging_tool):
                self.clear_alert_calls()
                started = time.monotonic()
                result, _ = self.run_loop(
                    harness="copilot",
                    extra_env={
                        **self.platform_test_environment("darwin"),
                        f"IMPLEMENT_LOOP_TEST_{hanging_tool.upper()}_MODE": "hang",
                    },
                )
                elapsed = time.monotonic() - started

                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertLess(elapsed, 5.0)
                self.assertNotIn(b"simulated alert", result.stdout + result.stderr)
                self.assertEqual(
                    [call["tool"] for call in self.alert_calls()],
                    ["osascript", "afplay"],
                )

    def test_multi_attempt_run_with_empty_queue_at_limit_alerts_once_as_no_eligible(self) -> None:
        self.add_ticket(3)
        self.save_state()

        result, _ = self.run_loop(
            "--max-iterations",
            "2",
            harness="copilot",
            extra_env=self.platform_test_environment("darwin"),
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(
            [call["ticket"] for call in self.load_state()["copilot_calls"]],
            [2, 3],
        )
        calls = self.alert_calls()
        self.assertEqual([call["tool"] for call in calls], ["osascript", "afplay"])
        self.assertEqual(calls[0]["args"][-2:], ["test/repo", "No eligible children"])
        self.assertEqual(calls[1]["args"], ["/System/Library/Sounds/Glass.aiff"])

    def test_multi_attempt_limit_alerts_once_without_claiming_remaining_work(self) -> None:
        self.add_ticket(3)
        self.add_ticket(4)
        self.save_state()

        result, _ = self.run_loop(
            "--max-iterations",
            "2",
            harness="copilot",
            extra_env=self.platform_test_environment("darwin"),
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [2, 3])
        self.assertEqual(state["tickets"]["4"]["state"], "open")
        self.assertEqual(state["tickets"]["4"]["assignees"], [])
        self.assertIn(b"Attempt limit reached", result.stdout)
        calls = self.alert_calls()
        self.assertEqual([call["tool"] for call in calls], ["osascript", "afplay"])
        self.assertEqual(calls[0]["args"][-2:], ["test/repo", "Attempt limit reached"])
        self.assertEqual(calls[1]["args"], ["/System/Library/Sounds/Glass.aiff"])
        notification_arguments = calls[0]["args"]
        for private_value in (
            "Ticket fixture: implement the requested behavior.",
            "IMPLEMENT_LOOP_COMPLETE",
            str(self.repo),
            "implementation-2.txt",
        ):
            self.assertNotIn(private_value, notification_arguments)

    def test_unsupported_platforms_do_not_invoke_macos_delivery_tools(self) -> None:
        self._make_queue_ineligible()

        result, _ = self.run_loop(
            harness="copilot",
            extra_env=self.platform_test_environment("linux"),
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.alert_calls(), [])

    def test_handled_execution_failure_is_silent_in_this_slice(self) -> None:
        self.state["agent_exit"] = 7
        self.save_state()

        result, _ = self.run_loop(
            "--max-iterations",
            "1",
            harness="copilot",
            extra_env=self.platform_test_environment("darwin"),
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn(b"Copilot exited with status 7", result.stderr)
        self.assertEqual(self.alert_calls(), [])

    def test_help_and_invalid_cli_or_configuration_do_not_alert(self) -> None:
        environment = dict(self.env)
        environment.update(self.platform_test_environment("darwin"))
        help_result = subprocess.run(
            [str(SCRIPT), "--help"],
            cwd=self.repo,
            env=environment,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr.decode())
        self.assertEqual(self.alert_calls(), [])

        bad_argument, _ = self.run_loop(
            "--max-iterations",
            "0",
            harness="copilot",
            extra_env=self.platform_test_environment("darwin"),
        )
        self.assertEqual(bad_argument.returncode, 2)
        self.assertEqual(self.alert_calls(), [])

        self.clear_alert_calls()
        bad_config = self.home / "invalid-config.toml"
        bad_config.write_text("[execution\n")
        bad_configuration, _ = self.run_loop(
            "--config-file",
            str(bad_config),
            harness="copilot",
            extra_env=self.platform_test_environment("darwin"),
        )
        self.assertEqual(bad_configuration.returncode, 2)
        self.assertEqual(self.alert_calls(), [])
