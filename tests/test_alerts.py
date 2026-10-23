from __future__ import annotations

import json
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

    def test_linux_zero_attempt_no_eligible_children_uses_complete_theme_event(self) -> None:
        self._make_queue_ineligible()
        self.set_repository_identity("--owner_name/--repo.with-special_chars")

        result, _ = self.run_loop(
            harness="copilot",
            extra_env=self.platform_test_environment("linux"),
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b"No eligible children", result.stdout)
        self.assertEqual(self.load_state()["copilot_calls"], [])
        calls = self.alert_calls()
        self.assertEqual(
            [call["tool"] for call in calls],
            ["notify-send", "canberra-gtk-play"],
        )
        self.assertEqual(
            calls[0]["args"],
            [
                "--hint=boolean:suppress-sound:true",
                "--",
                "--owner_name/--repo.with-special_chars",
                "No eligible children",
            ],
        )
        self.assertEqual(calls[1]["args"], ["--id=complete"])
        for call in calls:
            self.assertFalse(call["lock_exists"])
            self.assertEqual(call["active_run_markers"], [])

    def test_linux_queue_empty_at_attempt_limit_reports_no_eligible_children(self) -> None:
        result, _ = self.run_loop(
            "--max-iterations",
            "1",
            harness="copilot",
            extra_env=self.platform_test_environment("linux"),
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b"No eligible children", result.stdout)
        self.assertEqual(len(self.load_state()["copilot_calls"]), 1)
        calls = self.alert_calls()
        self.assertEqual(
            [call["tool"] for call in calls],
            ["notify-send", "canberra-gtk-play"],
        )
        self.assertEqual(calls[0]["args"][-2:], ["test/repo", "No eligible children"])
        self.assertEqual(calls[1]["args"], ["--id=complete"])

    def test_linux_multi_attempt_limit_alerts_once_with_complete_theme_event(self) -> None:
        self.add_ticket(3)
        self.add_ticket(4)
        self.save_state()

        result, _ = self.run_loop(
            "--max-iterations",
            "2",
            harness="copilot",
            extra_env=self.platform_test_environment("linux"),
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [2, 3])
        self.assertEqual(state["tickets"]["4"]["state"], "open")
        self.assertEqual(state["tickets"]["4"]["assignees"], [])
        self.assertIn(b"Attempt limit reached", result.stdout)
        calls = self.alert_calls()
        self.assertEqual(
            [call["tool"] for call in calls],
            ["notify-send", "canberra-gtk-play"],
        )
        self.assertEqual(
            calls[0]["args"],
            [
                "--hint=boolean:suppress-sound:true",
                "--",
                "test/repo",
                "Attempt limit reached",
            ],
        )
        self.assertEqual(calls[1]["args"], ["--id=complete"])
        for private_value in (
            "Ticket fixture: implement the requested behavior.",
            "IMPLEMENT_LOOP_COMPLETE",
            str(self.repo),
            "implementation-2.txt",
        ):
            self.assertNotIn(private_value, calls[0]["args"])

    def test_linux_failure_before_attempt_uses_generic_identity_and_error_theme_event(self) -> None:
        self.state["repository_view_failure"] = True
        self.save_state()

        result, _ = self.run_loop(
            harness="copilot",
            extra_env=self.platform_test_environment("linux"),
        )

        self.assertEqual(result.returncode, 1, result.stderr.decode())
        self.assertIn(b"simulated repository discovery failure", result.stderr)
        self.assertEqual(self.load_state()["copilot_calls"], [])
        calls = self.alert_calls()
        self.assertEqual(
            [call["tool"] for call in calls],
            ["notify-send", "canberra-gtk-play"],
        )
        self.assertEqual(
            calls[0]["args"],
            ["--hint=boolean:suppress-sound:true", "--", "Implement Loop", "Run failed"],
        )
        self.assertEqual(calls[1]["args"], ["--id=dialog-error"])
        for call in calls:
            self.assertFalse(call["lock_exists"])
            self.assertEqual(call["active_run_markers"], [])
        for private_value in (
            str(self.repo),
            "test/repo",
            "simulated repository discovery failure",
            "Ticket fixture: implement the requested behavior.",
            "IMPLEMENT_LOOP_COMPLETE",
        ):
            self.assertNotIn(private_value, calls[0]["args"])

    def test_linux_multi_attempt_handled_failure_alerts_once_after_cleanup(self) -> None:
        self.add_ticket(3)
        self.state["agent_exit_by_ticket"] = {"3": 7}
        self.save_state()

        result, runs = self.run_loop(
            "--max-iterations",
            "2",
            harness="copilot",
            extra_env=self.platform_test_environment("linux"),
        )

        state = self.load_state()
        attempt = runs[0] / "attempt-2-ticket-3"
        self.assertEqual(result.returncode, 1, result.stderr.decode())
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [2, 3])
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        self.assertEqual(state["tickets"]["3"]["assignees"], [{"login": "tester"}])
        self.assertIn(b"Copilot exited with status 7", result.stderr)
        self.assertIn(f"Ticket #3 records: {attempt.resolve()}".encode(), result.stderr)
        self.assertEqual(json.loads((attempt / "process.json").read_text())["exit_code"], 7)
        calls = self.alert_calls()
        self.assertEqual(
            [call["tool"] for call in calls],
            ["notify-send", "canberra-gtk-play"],
        )
        self.assertEqual(calls[0]["args"][-2:], ["test/repo", "Run failed"])
        self.assertEqual(calls[1]["args"], ["--id=dialog-error"])
        for call in calls:
            self.assertFalse(call["lock_exists"])
            self.assertEqual(call["active_run_markers"], [])
        for private_value in (
            str(self.repo),
            "Ticket fixture: implement the requested behavior.",
            "IMPLEMENT_LOOP_COMPLETE",
            "Copilot exited with status 7",
            "implementation-3.txt",
        ):
            self.assertNotIn(private_value, calls[0]["args"])

    def test_linux_failed_channel_does_not_prevent_the_other_or_change_result(self) -> None:
        self._make_queue_ineligible()

        for failing_tool in ("notify-send", "canberra-gtk-play"):
            with self.subTest(failing_tool=failing_tool):
                self.clear_alert_calls()
                mode_key = f"IMPLEMENT_LOOP_TEST_{failing_tool.upper().replace('-', '_')}_MODE"
                result, _ = self.run_loop(
                    harness="copilot",
                    extra_env={
                        **self.platform_test_environment("linux"),
                        mode_key: "fail",
                    },
                )

                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertNotIn(b"simulated alert", result.stdout + result.stderr)
                self.assertEqual(
                    [call["tool"] for call in self.alert_calls()],
                    ["notify-send", "canberra-gtk-play"],
                )

    def test_linux_missing_tools_are_skipped_without_player_fallbacks(self) -> None:
        self._make_queue_ineligible()

        for missing_tool in ("notify-send", "canberra-gtk-play"):
            with self.subTest(missing_tool=missing_tool):
                self.clear_alert_calls()
                if missing_tool == "notify-send":
                    available_tools = ["canberra-gtk-play"]
                else:
                    available_tools = ["notify-send", "afplay"]
                path = self.controlled_path_directory(
                    "python3", "git", "gh", "copilot", *available_tools
                )
                result, _ = self.run_loop(
                    harness="copilot",
                    extra_env={
                        **self.platform_test_environment("linux"),
                        "PATH": str(path),
                    },
                )

                self.assertEqual(result.returncode, 0, result.stderr.decode())
                expected_tools = (
                    ["canberra-gtk-play"] if missing_tool == "notify-send" else ["notify-send"]
                )
                self.assertEqual(
                    [call["tool"] for call in self.alert_calls()],
                    expected_tools,
                )

    def test_linux_headless_or_unavailable_services_fail_quietly(self) -> None:
        self._make_queue_ineligible()

        result, _ = self.run_loop(
            harness="copilot",
            extra_env={
                **self.platform_test_environment("linux"),
                "DISPLAY": "",
                "WAYLAND_DISPLAY": "",
                "DBUS_SESSION_BUS_ADDRESS": "",
                "IMPLEMENT_LOOP_TEST_NOTIFY_SEND_MODE": "fail",
                "IMPLEMENT_LOOP_TEST_CANBERRA_GTK_PLAY_MODE": "fail",
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertNotIn(b"simulated alert", result.stdout + result.stderr)
        self.assertEqual(
            [call["tool"] for call in self.alert_calls()],
            ["notify-send", "canberra-gtk-play"],
        )

    def test_linux_hung_delivery_commands_are_bounded_and_independent(self) -> None:
        self._make_queue_ineligible()

        for hanging_tool in ("notify-send", "canberra-gtk-play"):
            with self.subTest(hanging_tool=hanging_tool):
                self.clear_alert_calls()
                started = time.monotonic()
                mode_key = f"IMPLEMENT_LOOP_TEST_{hanging_tool.upper().replace('-', '_')}_MODE"
                result, _ = self.run_loop(
                    harness="copilot",
                    extra_env={
                        **self.platform_test_environment("linux"),
                        mode_key: "hang",
                    },
                )
                elapsed = time.monotonic() - started

                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertLess(elapsed, 5.0)
                self.assertNotIn(b"simulated alert", result.stdout + result.stderr)
                self.assertEqual(
                    [call["tool"] for call in self.alert_calls()],
                    ["notify-send", "canberra-gtk-play"],
                )

    def test_unsupported_platforms_do_not_invoke_delivery_tools(self) -> None:
        self._make_queue_ineligible()

        result, _ = self.run_loop(
            harness="copilot",
            extra_env=self.platform_test_environment("freebsd"),
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.alert_calls(), [])

    def test_failure_before_attempt_uses_generic_identity_and_basso_after_cleanup(self) -> None:
        self.state["repository_view_failure"] = True
        self.save_state()

        result, runs = self.run_loop(
            harness="copilot",
            extra_env=self.platform_test_environment("darwin"),
        )

        self.assertEqual(result.returncode, 1, result.stderr.decode())
        self.assertIn(b"simulated repository discovery failure", result.stderr)
        self.assertIn(
            b"Before rerunning, verify that any potentially running harness",
            result.stderr,
        )
        self.assertEqual(len(runs), 1)
        self.assertEqual(self.load_state()["copilot_calls"], [])

        calls = self.alert_calls()
        self.assertEqual([call["tool"] for call in calls], ["osascript", "afplay"])
        self.assertEqual(calls[0]["args"][-2:], ["Implement Loop", "Run failed"])
        self.assertEqual(calls[1]["args"], ["/System/Library/Sounds/Basso.aiff"])
        for call in calls:
            self.assertFalse(call["lock_exists"])
            self.assertEqual(call["active_run_markers"], [])
        for private_value in (
            str(self.repo),
            "test/repo",
            "simulated repository discovery failure",
            "Ticket fixture: implement the requested behavior.",
            "IMPLEMENT_LOOP_COMPLETE",
        ):
            self.assertNotIn(private_value, calls[0]["args"])

    def test_multi_attempt_run_alerts_once_for_handled_failure_after_attempt(self) -> None:
        self.add_ticket(3)
        self.state["agent_exit_by_ticket"] = {"3": 7}
        self.save_state()

        result, runs = self.run_loop(
            "--max-iterations",
            "2",
            harness="copilot",
            extra_env=self.platform_test_environment("darwin"),
        )

        state = self.load_state()
        attempt = runs[0] / "attempt-2-ticket-3"
        self.assertEqual(result.returncode, 1, result.stderr.decode())
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [2, 3])
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        self.assertEqual(state["tickets"]["3"]["assignees"], [{"login": "tester"}])
        self.assertIn(b"Copilot exited with status 7", result.stderr)
        self.assertIn(f"Run records: {runs[0].resolve()}".encode(), result.stderr)
        self.assertIn(f"Ticket #3 records: {attempt.resolve()}".encode(), result.stderr)
        self.assertIn(
            b"Before rerunning, verify that any potentially running harness",
            result.stderr,
        )
        self.assertEqual(json.loads((attempt / "process.json").read_text())["exit_code"], 7)

        calls = self.alert_calls()
        self.assertEqual([call["tool"] for call in calls], ["osascript", "afplay"])
        self.assertEqual(calls[0]["args"][-2:], ["test/repo", "Run failed"])
        self.assertEqual(calls[1]["args"], ["/System/Library/Sounds/Basso.aiff"])
        for call in calls:
            self.assertFalse(call["lock_exists"])
            self.assertEqual(call["active_run_markers"], [])
        for private_value in (
            str(self.repo),
            "Ticket fixture: implement the requested behavior.",
            "IMPLEMENT_LOOP_COMPLETE",
            "Copilot exited with status 7",
            "implementation-3.txt",
        ):
            self.assertNotIn(private_value, calls[0]["args"])

    def test_failed_or_timed_out_delivery_preserves_handled_failure_status(self) -> None:
        self.state["repository_view_failure"] = True
        self.save_state()

        for failing_tool, mode in (
            ("osascript", "fail"),
            ("afplay", "fail"),
            ("osascript", "hang"),
            ("afplay", "hang"),
        ):
            with self.subTest(failing_tool=failing_tool, mode=mode):
                self.clear_alert_calls()
                started = time.monotonic()
                result, _ = self.run_loop(
                    harness="copilot",
                    extra_env={
                        **self.platform_test_environment("darwin"),
                        f"IMPLEMENT_LOOP_TEST_{failing_tool.upper()}_MODE": mode,
                    },
                )
                elapsed = time.monotonic() - started

                self.assertEqual(result.returncode, 1, result.stderr.decode())
                self.assertIn(b"simulated repository discovery failure", result.stderr)
                self.assertNotIn(b"simulated alert", result.stdout + result.stderr)
                if mode == "hang":
                    self.assertLess(elapsed, 5.0)
                calls = self.alert_calls()
                self.assertEqual([call["tool"] for call in calls], ["osascript", "afplay"])
                self.assertEqual(calls[0]["args"][-2:], ["Implement Loop", "Run failed"])
                self.assertEqual(calls[1]["args"], ["/System/Library/Sounds/Basso.aiff"])

    def test_help_and_invalid_cli_or_configuration_do_not_alert(self) -> None:
        bad_config = self.home / "invalid-config.toml"
        bad_config.write_text("[execution\n")

        for platform in ("darwin", "linux"):
            with self.subTest(platform=platform):
                self.clear_alert_calls()
                platform_environment = self.platform_test_environment(platform)
                environment = dict(self.env)
                environment.update(platform_environment)
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
                    extra_env=platform_environment,
                )
                self.assertEqual(bad_argument.returncode, 2)
                self.assertEqual(self.alert_calls(), [])

                bad_configuration, _ = self.run_loop(
                    "--config-file",
                    str(bad_config),
                    harness="copilot",
                    extra_env=platform_environment,
                )
                self.assertEqual(bad_configuration.returncode, 2)
                self.assertEqual(self.alert_calls(), [])
