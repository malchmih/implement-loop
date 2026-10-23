from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from support import LoopTestCase


class IssueLifecycleTests(LoopTestCase):
    def test_missing_native_blocker_metadata_fails_closed(self) -> None:
        self.state["tickets"]["2"].pop("issue_dependencies_summary")
        self.save_state()

        result, _ = self.run_loop()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"missing readable native dependency data", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertEqual(self.load_state()["copilot_calls"], [])

    def test_malformed_issue_metadata_fails_closed_before_claim(self) -> None:
        cases = ("missing_parent_repository", "non_integer_blocker_count", "boolean_child_number")
        for malformed_case in cases:
            with self.subTest(malformed_case=malformed_case):
                self.state = self.default_state()
                if malformed_case == "missing_parent_repository":
                    self.state["spec"].pop("repository_url")
                elif malformed_case == "non_integer_blocker_count":
                    self.state["tickets"]["2"]["issue_dependencies_summary"]["blocked_by"] = True
                else:
                    self.state["children"][0]["number"] = True
                self.save_state()

                result, _ = self.run_loop()

                state = self.load_state()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(state["tickets"]["2"]["assignees"], [])
                self.assertEqual(state["events"], [])
                self.assertEqual(state["copilot_calls"], [])

    def test_failed_required_queue_read_stops_before_claim(self) -> None:
        self.add_ticket(3)
        self.state["api_failure_endpoint"] = "repos/test/repo/issues/1/sub_issues?per_page=100"
        self.save_state()

        result, _ = self.run_loop("--max-iterations", "2")

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"simulated required GitHub API read failure", result.stderr)
        self.assertEqual(state["events"], [])
        self.assertEqual(state["copilot_calls"], [])
        self.assertEqual(state["tickets"]["2"]["assignees"], [])
        self.assertEqual(state["tickets"]["3"]["assignees"], [])

    def test_preparation_failure_reports_ticket_records_before_metadata_exists(self) -> None:
        self.state["api_failure_endpoint"] = "repos/test/repo/issues/2/comments?per_page=100"
        self.save_state()

        result, runs = self.run_loop()

        attempt = runs[0] / "attempt-1-ticket-2"
        self.assertEqual(result.returncode, 1, result.stderr.decode())
        self.assertIn(b"simulated required GitHub API read failure", result.stderr)
        self.assertNotIn(b"may already be closed", result.stderr)
        self.assertIn(f"Run records: {runs[0].resolve()}".encode(), result.stderr)
        self.assertIn(
            f"Ticket #2 records (no harness invocation started): {attempt.resolve()}".encode(),
            result.stderr,
        )
        self.assertTrue(attempt.is_dir())
        self.assertEqual(list(attempt.iterdir()), [])
        self.assertEqual(self.load_state()["events"], [])
        self.assertEqual(self.load_state()["copilot_calls"], [])

    def test_harness_log_creation_failure_reports_no_invocation_started(self) -> None:
        self.state["precreate_agent_log_before_invocation"] = True
        self.save_state()

        result, runs = self.run_loop()

        attempt = runs[0] / "attempt-1-ticket-2"
        self.assertEqual(result.returncode, 1, result.stderr.decode())
        self.assertIn(b"Cannot create private Copilot output log", result.stderr)
        self.assertIn(f"Run records: {runs[0].resolve()}".encode(), result.stderr)
        self.assertIn(
            f"Ticket #2 records (no harness invocation started): {attempt.resolve()}".encode(),
            result.stderr,
        )
        self.assertEqual(self.load_state()["copilot_calls"], [])
        self.assertEqual(self.load_state()["tickets"]["2"]["assignees"], [{"login": "tester"}])
        self.assertEqual((attempt / "agent.log").read_text(), "preexisting log record\n")

    def test_harness_spawn_failure_reports_no_invocation_started(self) -> None:
        self.state["remove_copilot_before_invocation"] = True
        self.save_state()
        spawn_bin = self.home / "spawn-failure-bin"
        spawn_bin.mkdir()
        for name, target in (
            ("python3", Path(sys.executable)),
            ("git", Path(shutil.which("git", path=self.env["PATH"]) or "")),
            ("gh", self.bin / "gh"),
            ("copilot", self.bin / "copilot"),
        ):
            self.assertTrue(target.is_file(), f"missing fixture executable: {target}")
            (spawn_bin / name).symlink_to(target)

        result, runs = self.run_loop(extra_env={"PATH": str(spawn_bin)})

        attempt = runs[0] / "attempt-1-ticket-2"
        self.assertEqual(result.returncode, 1, result.stderr.decode())
        self.assertIn(b"Copilot could not be started", result.stderr)
        self.assertIn(f"Run records: {runs[0].resolve()}".encode(), result.stderr)
        self.assertIn(
            f"Ticket #2 records (no harness invocation started): {attempt.resolve()}".encode(),
            result.stderr,
        )
        self.assertEqual(self.load_state()["copilot_calls"], [])
        self.assertEqual(self.load_state()["tickets"]["2"]["assignees"], [{"login": "tester"}])
        process = json.loads((attempt / "process.json").read_text())
        self.assertIsNone(process["exit_code"])
        self.assertIn("could not be started", process["spawn_error"])

    def test_queue_refresh_failure_does_not_report_completed_ticket_records(self) -> None:
        self.add_ticket(3)
        self.state["fail_queue_refresh_after_close"] = True
        self.save_state()

        result, runs = self.run_loop("--max-iterations", "2")

        state = self.load_state()
        self.assertEqual(result.returncode, 1, result.stderr.decode())
        self.assertIn(b"simulated required GitHub API read failure", result.stderr)
        self.assertIn(f"Run records: {runs[0].resolve()}".encode(), result.stderr)
        self.assertNotIn(b"Ticket #2 records", result.stderr)
        self.assertNotIn(b"Ticket #3 records", result.stderr)
        self.assertTrue((runs[0] / "attempt-1-ticket-2").is_dir())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["3"]["assignees"], [])
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [2])

    def test_failed_preclosure_read_preserves_work_and_stops_the_queue(self) -> None:
        self.add_ticket(3)
        self.state["fail_preclosure_read_after_harness"] = True
        self.save_state()

        result, runs = self.run_loop("--max-iterations", "2")

        state = self.load_state()
        attempt = runs[0] / "attempt-1-ticket-2"
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"simulated required issue read failure", result.stderr)
        self.assertNotIn(b"may already be closed", result.stderr)
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [2])
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
        self.assertEqual(state["tickets"]["3"]["assignees"], [])
        self.assertEqual([event[0] for event in state["events"]], ["assign"])
        self.assertTrue((attempt / "completion.json").is_file())
        self.assertTrue((attempt / "process.json").is_file())
        self.assertIn("implementation-2.txt", self.git("show", "--pretty=", "--name-only"))

    def test_failed_closure_confirmation_does_not_start_another_ticket(self) -> None:
        self.add_ticket(3)
        self.state["fail_confirmation_after_close"] = True
        self.state["issue_read_failure_detail"] = (
            "simulated required issue read failure\nadditional issue-read stderr detail"
        )
        self.save_state()

        result, runs = self.run_loop("--max-iterations", "2")

        state = self.load_state()
        attempt = runs[0] / "attempt-1-ticket-2"
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            b"simulated required issue read failure\nadditional issue-read stderr detail",
            result.stderr,
        )
        self.assertIn(
            b"Ticket #2 may already be closed. "
            b"Inspect ticket #2's live GitHub state before rerunning.",
            result.stderr,
        )
        self.assertNotIn(b"The closure comment or closure", result.stderr)
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [2])
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        self.assertEqual(state["tickets"]["3"]["assignees"], [])
        self.assertEqual([event[0] for event in state["events"]], ["assign", "close"])
        self.assertTrue((attempt / "closure-comment.txt").is_file())
        self.assertFalse((attempt / "closed-ticket.json").exists())

    def test_cross_repository_child_fails_closed(self) -> None:
        self.state["children"][0]["repository_url"] = "https://api.github.com/repos/other/repo"
        self.save_state()

        result, _ = self.run_loop()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"cross-repository children are not supported", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertEqual(self.load_state()["copilot_calls"], [])

    def test_pull_requests_cannot_be_used_as_parent_specs_or_children(self) -> None:
        for name in ("parent", "child"):
            with self.subTest(name=name):
                self.state = self.default_state()
                if name == "parent":
                    self.state["spec"]["pull_request"] = {"url": "pull/1"}
                else:
                    self.state["children"][0]["pull_request"] = {"url": "pull/2"}
                self.save_state()

                result, _ = self.run_loop()

                state = self.load_state()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"pull request, not an issue", result.stderr)
                self.assertEqual(state["events"], [])
                self.assertEqual(state["copilot_calls"], [])

    def test_claim_is_rechecked_for_exclusive_ownership_and_blockers(self) -> None:
        self.state["claim_conflict"] = True
        self.save_state()

        result, _ = self.run_loop()

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"no longer open, unblocked, and exclusively assigned", result.stderr)
        self.assertEqual(
            state["tickets"]["2"]["assignees"],
            [{"login": "tester"}, {"login": "other"}],
        )
        self.assertEqual(state["copilot_calls"], [])
        self.assertEqual([event[0] for event in state["events"]], ["assign"])

    def test_failed_claim_writes_stop_without_rolling_back_partial_assignment(self) -> None:
        for failure_mode in ("before_write", "after_write"):
            with self.subTest(failure_mode=failure_mode):
                self.state = self.default_state()
                self.add_ticket(3)
                self.state["claim_failure"] = failure_mode
                self.state["claim_failure_detail"] = (
                    f"simulated GitHub claim failure {failure_mode.replace('_', ' ')}\n"
                    "additional stderr detail"
                )
                self.save_state()

                result, runs = self.run_loop("--max-iterations", "2")

                state = self.load_state()
                attempt = runs[0] / "attempt-1-ticket-2"
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    (
                        f"simulated GitHub claim failure {failure_mode.replace('_', ' ')}\n"
                        "additional stderr detail"
                    ).encode(),
                    result.stderr,
                )
                self.assertIn(
                    b"Assignment of ticket #2 may have taken effect. "
                    b"Inspect ticket #2's live GitHub state before rerunning.",
                    result.stderr,
                )
                self.assertIn(f"Run records: {runs[0].resolve()}".encode(), result.stderr)
                self.assertIn(
                    (
                        f"Ticket #2 records (no harness invocation started): {attempt.resolve()}"
                    ).encode(),
                    result.stderr,
                )
                self.assertIn(
                    b"Any existing checkout changes, commits, and assignments were left in place",
                    result.stderr,
                )
                self.assertIn(
                    b"Before rerunning, verify that any potentially running harness",
                    result.stderr,
                )
                self.assertEqual(state["copilot_calls"], [])
                self.assertEqual(state["tickets"]["2"]["state"], "open")
                self.assertEqual(state["tickets"]["3"]["state"], "open")
                self.assertEqual(state["tickets"]["3"]["assignees"], [])
                self.assertEqual(
                    state["tickets"]["2"]["assignees"],
                    [] if failure_mode == "before_write" else [{"login": "tester"}],
                )
                self.assertEqual(
                    [event[0] for event in state["events"]],
                    [] if failure_mode == "before_write" else ["assign"],
                )
                self.assertTrue((attempt / "attempt.json").is_file())
                self.assertFalse((attempt / "process.json").exists())

    def test_blocker_appearing_after_claim_prevents_harness_invocation(self) -> None:
        self.state["claim_blocker"] = True
        self.save_state()

        result, _ = self.run_loop()

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"no longer open, unblocked", result.stderr)
        self.assertEqual(state["tickets"]["2"]["issue_dependencies_summary"]["blocked_by"], 1)
        self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
        self.assertEqual(state["copilot_calls"], [])

    def test_claim_is_rechecked_for_direct_child_membership(self) -> None:
        self.state["claim_remove_child"] = True
        self.save_state()

        result, _ = self.run_loop()

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"no longer an open direct child", result.stderr)
        self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
        self.assertEqual(state["copilot_calls"], [])

    def test_claim_recheck_refuses_a_closed_ticket_or_parent(self) -> None:
        for changed_issue in ("ticket", "parent"):
            with self.subTest(changed_issue=changed_issue):
                self.state = self.default_state()
                claim_option = "claim_close" if changed_issue == "ticket" else "claim_close_parent"
                self.state[claim_option] = True
                self.save_state()

                result, runs = self.run_loop()

                state = self.load_state()
                attempt = runs[0] / "attempt-1-ticket-2"
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(state["copilot_calls"], [])
                self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
                self.assertEqual([event[0] for event in state["events"]], ["assign"])
                self.assertFalse((attempt / "process.json").exists())
                self.assertEqual(
                    state["tickets"]["2"]["state"],
                    "closed" if changed_issue == "ticket" else "open",
                )
                self.assertEqual(
                    state["spec"]["state"],
                    "closed" if changed_issue == "parent" else "open",
                )

    def test_ownership_change_after_harness_is_rejected(self) -> None:
        self.state["agent_action"] = "change_owner"
        self.save_state()
        existing_head = self.git("rev-parse", "HEAD")

        result, _ = self.run_loop("--max-iterations", "1")

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"no longer open, unblocked, and exclusively assigned", result.stderr)
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual(
            state["tickets"]["2"]["assignees"],
            [{"login": "tester"}, {"login": "other"}],
        )
        self.assertNotEqual(self.git("rev-parse", "HEAD"), existing_head)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual([event[0] for event in state["events"]], ["assign"])

    def test_ticket_blocker_and_membership_are_rechecked_after_harness(self) -> None:
        cases = (
            ("add_blocker", b"no longer open, unblocked"),
            ("remove_child", b"no longer an open direct child"),
        )
        for action, diagnostic in cases:
            with self.subTest(action=action):
                self.state = self.default_state()
                self.state["agent_action"] = action
                self.save_state()

                result, _ = self.run_loop("--max-iterations", "1")

                state = self.load_state()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(diagnostic, result.stderr)
                self.assertEqual(state["tickets"]["2"]["state"], "open")
                self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
                self.assertEqual([event[0] for event in state["events"]], ["assign"])

    def test_parent_must_remain_open_after_harness(self) -> None:
        self.state["agent_action"] = "close_parent"
        self.save_state()

        result, _ = self.run_loop("--max-iterations", "1")

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["spec"]["state"], "closed")
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual([event[0] for event in state["events"]], ["assign"])

    def test_ticket_closed_during_execution_is_not_closed_again_or_followed(self) -> None:
        self.add_ticket(3)
        self.state["agent_action"] = "close_ticket"
        self.save_state()

        result, runs = self.run_loop("--max-iterations", "2")

        state = self.load_state()
        attempt = runs[0] / "attempt-1-ticket-2"
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"no longer open", result.stderr)
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [2])
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        self.assertEqual(state["tickets"]["3"]["assignees"], [])
        self.assertEqual([event[0] for event in state["events"]], ["assign"])
        self.assertFalse((attempt / "closed-ticket.json").exists())
        self.assertIn("implementation-2.txt", self.git("show", "--pretty=", "--name-only"))

    def test_github_close_failure_preserves_assignment_and_local_commit(self) -> None:
        self.add_ticket(3)
        self.state["close_failure"] = True
        self.save_state()

        result, _ = self.run_loop("--max-iterations", "2")

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"GitHub CLI failed during closing ticket", result.stderr)
        self.assertIn(b"simulated GitHub closure failure", result.stderr)
        self.assertIn(
            b"The closure comment or closure of ticket #2 may have taken effect. "
            b"Inspect ticket #2's live GitHub state before rerunning.",
            result.stderr,
        )
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [2])
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        self.assertEqual(state["tickets"]["3"]["assignees"], [])
        self.assertIn("implementation-2.txt", self.git("show", "--pretty=", "--name-only"))
        self.assertEqual([event[0] for event in state["events"]], ["assign"])

    def test_partial_closure_outcomes_preserve_work_and_stop_before_the_next_ticket(self) -> None:
        cases = (
            ("close_comment_failure_after_write", "comment"),
            ("close_failure_after_write", "close"),
        )
        for failure_setting, expected_final_event in cases:
            with self.subTest(failure_setting=failure_setting):
                self.state = self.default_state()
                self.add_ticket(3)
                self.state[failure_setting] = True
                self.state["close_failure_detail"] = (
                    "simulated GitHub closure failure detail\nadditional closure stderr detail"
                )
                self.save_state()

                result, runs = self.run_loop("--max-iterations", "2")

                state = self.load_state()
                attempt = runs[0] / "attempt-1-ticket-2"
                closure_comment = (attempt / "closure-comment.txt").read_text().strip()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    b"simulated GitHub closure failure detail\nadditional closure stderr detail",
                    result.stderr,
                )
                self.assertIn(
                    b"The closure comment or closure of ticket #2 may have taken effect. "
                    b"Inspect ticket #2's live GitHub state before rerunning.",
                    result.stderr,
                )
                self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [2])
                self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
                self.assertEqual(state["tickets"]["3"]["state"], "open")
                self.assertEqual(state["tickets"]["3"]["assignees"], [])
                self.assertIn("implementation-2.txt", self.git("show", "--pretty=", "--name-only"))
                self.assertEqual(
                    [event[0] for event in state["events"]],
                    ["assign", expected_final_event],
                )
                if expected_final_event == "comment":
                    self.assertEqual(state["tickets"]["2"]["state"], "open")
                    self.assertEqual(state["comments"]["2"][-1]["body"], closure_comment)
                else:
                    self.assertEqual(state["tickets"]["2"]["state"], "closed")
                self.assertTrue((attempt / "checks.json").is_file())
                self.assertTrue((attempt / "closure-comment.txt").is_file())
                self.assertFalse((attempt / "closed-ticket.json").exists())

    def test_oversized_context_fails_before_claim_without_truncation(self) -> None:
        self.state["tickets"]["2"]["body"] = "x" * 101_000
        self.save_state()

        result, _ = self.run_loop()

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"safe 100 KB command-line budget", result.stderr)
        self.assertEqual(state["tickets"]["2"]["assignees"], [])
        self.assertEqual(state["events"], [])
        self.assertEqual(state["copilot_calls"], [])
