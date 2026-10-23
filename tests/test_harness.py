from __future__ import annotations

import json
import os
import resource
import shutil
import signal
import socket
import subprocess
import sys
from pathlib import Path

from support import (
    LIVE_OUTPUT_FRAGMENT,
    LIVE_OUTPUT_TIMEOUT,
    LIVE_READY_TIMEOUT,
    SCRIPT,
    LoopTestCase,
)


class HarnessTests(LoopTestCase):
    def test_opencode_attempts_are_private_and_use_fresh_sessions(self) -> None:
        self.add_ticket(3)
        self.save_state()

        result, runs = self.run_loop("--max-iterations", "2", harness=None)

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([call["ticket"] for call in state["opencode_calls"]], [2, 3])
        self.assertEqual(state["copilot_calls"], [])
        self.assertEqual(
            len({call["attempt_id"] for call in state["opencode_calls"]}),
            2,
        )
        for call in state["opencode_calls"]:
            args = call["args"]
            self.assertEqual(args[0], "run")
            self.assertIn("--standalone", args)
            self.assertNotIn("--continue", args)
            self.assertNotIn("--session", args)
            self.assertNotIn("--fork", args)
            self.assertTrue(call["result_initially_absent"])
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["3"]["state"], "closed")
        self.assertTrue((runs[0] / "attempt-1-ticket-2/agent.log").is_file())
        self.assertTrue((runs[0] / "attempt-2-ticket-3/agent.log").is_file())

    def test_opencode_redirected_output_is_live_before_harness_exit(self) -> None:
        self._assert_live_harness_output("opencode", terminal=False)

    def test_opencode_terminal_output_is_live_before_harness_exit(self) -> None:
        self._assert_live_harness_output("opencode", terminal=True)

    def test_copilot_redirected_output_is_live_before_harness_exit(self) -> None:
        self._assert_live_harness_output("copilot", terminal=False)

    def test_copilot_terminal_output_is_live_before_harness_exit(self) -> None:
        self._assert_live_harness_output("copilot", terminal=True)

    def test_interrupt_reports_existing_run_and_ticket_records(self) -> None:
        ready_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ready_listener.bind(("127.0.0.1", 0))
        ready_listener.listen()
        ready_listener.settimeout(LIVE_READY_TIMEOUT)
        env = dict(self.env)
        env["IMPLEMENT_LOOP_TEST_LIVE_OUTPUT_FRAGMENT"] = LIVE_OUTPUT_FRAGMENT.decode("ascii")
        env["IMPLEMENT_LOOP_TEST_LIVE_OUTPUT_READY_PORT"] = str(ready_listener.getsockname()[1])
        process: subprocess.Popen[bytes] | None = None
        output = bytearray()

        try:
            process = subprocess.Popen(
                [
                    str(SCRIPT),
                    "1",
                    "--max-iterations",
                    "1",
                    "--harness",
                    "copilot",
                ],
                cwd=self.repo,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            with ready_listener.accept()[0] as ready_connection:
                ready_connection.settimeout(LIVE_READY_TIMEOUT)
                self.assertEqual(ready_connection.recv(1), b"R")
            assert process.stdout is not None
            self._read_until_output(
                process.stdout.fileno(),
                output,
                LIVE_OUTPUT_FRAGMENT,
                timeout=LIVE_OUTPUT_TIMEOUT,
            )
            attempt_directories = list(
                (self.home / ".local/state/implement-loop").glob("run-*/attempt-1-ticket-2")
            )
            self.assertEqual(len(attempt_directories), 1)
            run_directory = attempt_directories[0].parent
            attempt_directory = attempt_directories[0]

            process.send_signal(signal.SIGINT)
            assert process.stdin is not None
            process.stdin.write(b"x")
            process.stdin.flush()
            remaining, _ = process.communicate(timeout=LIVE_READY_TIMEOUT)
            output.extend(remaining)

            self.assertEqual(process.returncode, 130, output.decode("utf-8", errors="replace"))
            self.assertIn(b"Interrupted.", output)
            self.assertIn(f"Run records: {run_directory.resolve()}".encode(), output)
            self.assertIn(
                f"Ticket #2 records: {attempt_directory.resolve()}".encode(),
                output,
            )
            self.assertIn(
                b"This output, the records, and release of the local lock do not confirm shutdown",
                output,
            )
            self.assertTrue(run_directory.is_dir())
            self.assertTrue(attempt_directory.is_dir())
            self.assertFalse((run_directory / "active.pid").exists())
            self.assertFalse(self.repository_lock_path().exists())
            self.assertEqual(len(self.load_state()["copilot_calls"]), 1)
        finally:
            if process is not None:
                if process.stdin is not None and not process.stdin.closed:
                    try:
                        process.stdin.write(b"x")
                        process.stdin.flush()
                    except (BrokenPipeError, OSError, ValueError):
                        pass
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
                try:
                    process.wait(timeout=LIVE_READY_TIMEOUT)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)
                if process.stdout is not None:
                    process.stdout.close()
            ready_listener.close()

    def test_transcript_marker_without_result_file_does_not_close_ticket(self) -> None:
        for harness, selected_calls, unselected_calls in (
            (None, "opencode_calls", "copilot_calls"),
            ("copilot", "copilot_calls", "opencode_calls"),
        ):
            with self.subTest(harness=harness or "default"):
                self.state = self.default_state()
                self.save_state()
                result, runs = self.run_loop(
                    "--max-iterations",
                    "1",
                    harness=harness,
                    extra_env={"IMPLEMENT_LOOP_TEST_RESULT_MODE": "missing"},
                )
                self.assertNotEqual(result.returncode, 0)
                state = self.load_state()
                self.assertEqual(state["tickets"]["2"]["state"], "open")
                self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
                self.assertEqual([event[0] for event in state["events"]], ["assign"])
                self.assertEqual(len(state[selected_calls]), 1)
                self.assertEqual(state[unselected_calls], [])
                self.assertEqual(len(runs), 1)
                attempt = runs[0] / "attempt-1-ticket-2"
                self.assertEqual(
                    json.loads((attempt / "completion-evidence.json").read_text())["status"],
                    "missing",
                )
                self.assertIn(b"did not write the required per-attempt result file", result.stderr)
                self.assertIn(b"IMPLEMENT_LOOP_COMPLETE", (attempt / "agent.log").read_bytes())

    def test_result_file_closes_ticket_after_clean_descendant_commit(self) -> None:
        result, runs = self.run_loop(
            "--max-iterations",
            "1",
            extra_env={"COPILOT_TEST_CONFIG": "inherited-setting"},
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        state = self.load_state()
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
        self.assertEqual([event[0] for event in state["events"]], ["assign", "close"])
        self.assertEqual(set(state["api_hosts"]), {"github.com"})
        self.assertEqual(len(runs), 1)
        attempt = runs[0] / "attempt-1-ticket-2"
        metadata = json.loads((attempt / "attempt.json").read_text())
        completion = json.loads((attempt / "completion.json").read_text())
        self.assertEqual(completion["issue"], 2)
        self.assertEqual(completion["attempt_id"], metadata["attempt_id"])
        self.assertTrue(state["copilot_calls"][0]["result_initially_absent"])
        self.assertFalse(Path(metadata["result_path"]).exists())
        self.assertFalse(Path(metadata["result_path"]).is_relative_to(self.repo))
        self.assertEqual(state["copilot_calls"][0]["copilot_config"], "inherited-setting")
        call_args = state["copilot_calls"][0]["args"]
        self.assertEqual(call_args[call_args.index("--model") + 1], "gpt-6-luna")
        self.assertEqual(call_args[call_args.index("--reasoning-effort") + 1], "max")
        self.assertNotIn("--deny-tool", call_args)
        prompt = state["copilot_calls"][0]["prompt"]
        self.assertIn("/implement Implement only ticket #2", prompt)
        self.assertIn("Spec fixture with shared requirements", prompt)
        self.assertIn("Parent comment fixture.", prompt)
        self.assertIn("Ticket comment fixture.", prompt)
        self.assertIn("Second paginated ticket comment.", prompt)
        self.assertIn("Any delegated workers must follow these same scope", prompt)
        self.assertIn("incorporate its results before reporting completion", prompt)
        self.assertIn("branch main", state["events"][1][2])
        self.assertIn("The wrapper did not push or open a PR", state["events"][1][2])
        self.assertEqual((attempt / "commit").read_text().strip(), self.git("rev-parse", "HEAD"))
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertTrue((attempt / "agent.log").is_file())
        self.assertTrue((attempt / "process.json").is_file())
        self.assertTrue((attempt / "commit").is_file())
        self.assertFalse((runs[0] / "active.pid").exists())

    def test_invocation_from_checkout_subdirectory_uses_checkout_root(self) -> None:
        nested = self.repo / "nested"
        nested.mkdir()
        (nested / "tracked.txt").write_text("tracked fixture\n")
        self.git("add", "nested")
        self.git("commit", "--quiet", "-m", "Add nested fixture")

        result, _ = self.run_loop("--max-iterations", "1", cwd=nested)

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(self.git("branch", "--show-current", cwd=nested), "main")
        self.assertTrue((self.repo / "implementation-2.txt").exists())

    def test_result_file_rejections_never_close_or_unassign_ticket(self) -> None:
        modes = (
            "malformed",
            "wrong-root",
            "wrong-ticket",
            "wrong-ticket-number",
            "wrong-attempt",
            "wrong-status",
            "failed-acceptance",
            "failed-validation",
        )
        for mode in modes:
            with self.subTest(mode=mode):
                state = self.load_state()
                state["tickets"]["2"]["state"] = "open"
                state["tickets"]["2"]["assignees"] = []
                state["tickets"]["2"]["issue_dependencies_summary"]["blocked_by"] = 0
                state["events"] = []
                state["make_commit"] = False
                self.state = state
                self.save_state()
                result, runs = self.run_loop(
                    "--max-iterations",
                    "1",
                    "--allow-no-new-commit",
                    "2",
                    extra_env={"IMPLEMENT_LOOP_TEST_RESULT_MODE": mode},
                )
                after = self.load_state()
                self.assertNotEqual(result.returncode, 0, result.stdout.decode())
                self.assertEqual(after["tickets"]["2"]["state"], "open")
                self.assertEqual(after["tickets"]["2"]["assignees"], [{"login": "tester"}])
                self.assertEqual([event[0] for event in after["events"]], ["assign"])
                attempt = runs[-1] / "attempt-1-ticket-2"
                evidence = json.loads((attempt / "completion-evidence.json").read_text())
                self.assertNotEqual(evidence["status"], "valid")
                self.assertTrue((attempt / "completion-result.json").is_file())

    def test_nonzero_harness_exit_rejects_even_valid_result_file(self) -> None:
        for harness, selected_calls, unselected_calls, display_name in (
            (None, "opencode_calls", "copilot_calls", "OpenCode"),
            ("copilot", "copilot_calls", "opencode_calls", "Copilot"),
        ):
            with self.subTest(harness=harness or "default"):
                self.state = self.default_state()
                self.state["agent_exit"] = 7
                self.state["make_commit"] = False
                self.save_state()
                result, runs = self.run_loop(
                    "--max-iterations",
                    "1",
                    "--allow-no-new-commit",
                    "2",
                    harness=harness,
                )
                state = self.load_state()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(state[selected_calls]), 1)
                self.assertEqual(state[unselected_calls], [])
                self.assertEqual(state["tickets"]["2"]["state"], "open")
                self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
                self.assertIn(f"{display_name} exited with status 7".encode(), result.stderr)
                attempt = runs[-1] / "attempt-1-ticket-2"
                self.assertIn(
                    f"Run records: {runs[-1].resolve()}".encode(),
                    result.stderr,
                )
                self.assertIn(
                    f"Ticket #2 records: {attempt.resolve()}".encode(),
                    result.stderr,
                )
                self.assertEqual(json.loads((attempt / "process.json").read_text())["exit_code"], 7)
                self.assertEqual(
                    json.loads((attempt / "completion-evidence.json").read_text())["status"],
                    "valid",
                )

    def test_required_record_persistence_failure_prevents_closure(self) -> None:
        self.state["agent_action"] = "break_records"
        self.state["make_commit"] = False
        self.save_state()

        result, runs = self.run_loop(
            "--max-iterations",
            "1",
            "--allow-no-new-commit",
            "2",
        )

        state = self.load_state()
        attempt = runs[-1] / "attempt-1-ticket-2"
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Cannot persist required run record", result.stderr)
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual([event[0] for event in state["events"]], ["assign"])
        attempt.chmod(0o700)

    def test_agent_log_write_failure_prevents_closure(self) -> None:
        def limit_file_size() -> None:
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (8192, 8192))

        result, runs = self.run_loop(
            "--max-iterations",
            "1",
            extra_env={"IMPLEMENT_LOOP_TEST_OUTPUT": "x" * 20_000},
            preexec_fn=limit_file_size,
        )

        state = self.load_state()
        attempt = runs[-1] / "attempt-1-ticket-2"
        process = json.loads((attempt / "process.json").read_text())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Unable to persist the complete Copilot output log", result.stderr)
        self.assertFalse(process["log_persisted"])
        self.assertLessEqual((attempt / "agent.log").stat().st_size, 8192)
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual([event[0] for event in state["events"]], ["assign"])

    def test_missing_copilot_cli_fails_before_claim(self) -> None:
        limited_bin = self.home / "limited-bin"
        limited_bin.mkdir()
        (limited_bin / "python3").symlink_to(sys.executable)
        (limited_bin / "git").symlink_to(shutil.which("git"))
        (limited_bin / "gh").symlink_to(self.bin / "gh")
        (limited_bin / "opencode").symlink_to(self.bin / "opencode")

        result, runs = self.run_loop(
            harness="copilot",
            extra_env={"PATH": str(limited_bin)},
        )

        self.assert_missing_executable_guidance(result, "copilot", harness="Copilot")
        self.assert_missing_executable_stopped_before_run(runs)

    def test_missing_default_opencode_cli_does_not_fall_back_to_copilot(self) -> None:
        limited_bin = self.home / "limited-bin"
        limited_bin.mkdir()
        for tool, target in (
            ("python3", sys.executable),
            ("git", shutil.which("git")),
            ("gh", self.bin / "gh"),
            ("copilot", self.bin / "copilot"),
        ):
            (limited_bin / tool).symlink_to(target)

        result, runs = self.run_loop(harness=None, extra_env={"PATH": str(limited_bin)})

        self.assert_missing_executable_guidance(result, "opencode", harness="OpenCode")
        self.assert_missing_executable_stopped_before_run(runs)

    def test_default_opencode_runs_without_a_copilot_executable(self) -> None:
        limited_bin = self.home / "limited-bin"
        limited_bin.mkdir()
        for tool, target in (
            ("python3", sys.executable),
            ("git", shutil.which("git")),
            ("gh", self.bin / "gh"),
            ("opencode", self.bin / "opencode"),
        ):
            (limited_bin / tool).symlink_to(target)

        result, _ = self.run_loop(
            "--max-iterations",
            "1",
            harness=None,
            extra_env={"PATH": str(limited_bin)},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(len(state["opencode_calls"]), 1)
        self.assertEqual(state["copilot_calls"], [])

    def test_explicit_copilot_runs_without_an_opencode_executable(self) -> None:
        limited_bin = self.home / "limited-bin"
        limited_bin.mkdir()
        for tool, target in (
            ("python3", sys.executable),
            ("git", shutil.which("git")),
            ("gh", self.bin / "gh"),
            ("copilot", self.bin / "copilot"),
        ):
            (limited_bin / tool).symlink_to(target)

        result, _ = self.run_loop(
            "--max-iterations",
            "1",
            harness="copilot",
            extra_env={"PATH": str(limited_bin)},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(len(state["copilot_calls"]), 1)
        self.assertEqual(state["opencode_calls"], [])

    def test_rejected_model_stops_selected_harness_without_fallback(self) -> None:
        for selected_harness, call_key, other_key in (
            (None, "opencode_calls", "copilot_calls"),
            ("copilot", "copilot_calls", "opencode_calls"),
        ):
            with self.subTest(harness=selected_harness or "default"):
                self.state = self.default_state()
                self.state["model_rejected_for"] = call_key.removesuffix("_calls")
                self.save_state()

                result, runs = self.run_loop(
                    "--max-iterations",
                    "1",
                    harness=selected_harness,
                )

                state = self.load_state()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(state[call_key]), 1)
                self.assertEqual(state[other_key], [])
                self.assertEqual(state["tickets"]["2"]["state"], "open")
                self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
                self.assertEqual([event[0] for event in state["events"]], ["assign"])
                attempt = runs[0] / "attempt-1-ticket-2"
                self.assertEqual(json.loads((attempt / "process.json").read_text())["exit_code"], 1)
                self.assertIn(b"Model unavailable", (attempt / "agent.log").read_bytes())
