from __future__ import annotations

from support import LoopTestCase


class QueueTests(LoopTestCase):
    def test_queue_skips_blockers_and_assignments_and_ignores_labels(self) -> None:
        self.state["tickets"]["2"]["issue_dependencies_summary"]["blocked_by"] = 1
        self.state["tickets"]["2"]["blocking_issues"] = [3]
        self.add_ticket(3, assignees=["tester"])
        self.add_ticket(4, assignees=["someone-else"])
        self.add_ticket(5, blocked=1, labels=["wontfix"])
        self.state["tickets"]["5"]["blocking_issues"] = [99]
        self.add_ticket(6, labels=["wontfix"])
        self.add_ticket(99)
        self.state["children"] = [
            {"number": 2},
            {"number": 3},
            {"number": 4},
            {"number": 5},
            {"number": 6},
        ]
        self.save_state()

        result, _ = self.run_loop("--max-iterations", "1")

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [6])
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        self.assertEqual(state["tickets"]["4"]["state"], "open")
        self.assertEqual(state["tickets"]["5"]["state"], "open")
        self.assertEqual(state["tickets"]["6"]["state"], "closed")
        self.assertNotIn(99, [child["number"] for child in state["children"]])

    def test_queue_refreshes_after_closure_and_preserves_returned_order(self) -> None:
        self.add_ticket(3)
        self.state["children"] = [{"number": 3}, {"number": 2}]
        self.state["sub_issue_page_size"] = 1
        self.save_state()

        result, runs = self.run_loop("--max-iterations", "2")

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [3, 2])
        self.assertEqual(
            len({call["attempt_id"] for call in state["copilot_calls"]}),
            2,
        )
        for call in state["copilot_calls"]:
            self.assertNotIn("--continue", call["args"])
            self.assertNotIn("--resume", call["args"])
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["3"]["state"], "closed")
        self.assertTrue((runs[0] / "attempt-1-ticket-3/completion-result.json").is_file())
        self.assertTrue((runs[0] / "attempt-2-ticket-2/completion-result.json").is_file())

    def test_no_eligible_children_exits_successfully_without_harness(self) -> None:
        self.state["tickets"]["2"]["assignees"] = [{"login": "tester"}]
        self.add_ticket(3, blocked=2)
        self.save_state()

        result, _ = self.run_loop()

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["copilot_calls"], [])
        self.assertEqual(state["events"], [])
        self.assertIn(b"No eligible children", result.stdout)

    def test_attempt_limit_refreshes_queue_but_does_not_claim_next_ticket(self) -> None:
        self.add_ticket(3)
        self.save_state()

        result, _ = self.run_loop("--max-iterations", "1")

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [2])
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        self.assertEqual(state["tickets"]["3"]["assignees"], [])
        self.assertIn(b"Attempt limit reached", result.stdout)

    def test_default_limit_is_five_harness_invocations(self) -> None:
        for number in range(3, 8):
            self.add_ticket(number)
        self.save_state()

        result, _ = self.run_loop(harness=None)

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(state["opencode_calls"]), 5)
        self.assertEqual(state["copilot_calls"], [])
        self.assertEqual(state["tickets"]["7"]["state"], "open")
        self.assertIn(b"Attempt limit reached after 5 invocation(s)", result.stdout)
