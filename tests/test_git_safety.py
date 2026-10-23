from __future__ import annotations

import os
import shutil

from support import LoopTestCase


class GitSafetyTests(LoopTestCase):
    def test_initial_dirty_checkout_is_rejected_before_assignment(self) -> None:
        (self.repo / "untracked.txt").write_text("keep me\n")

        result, _ = self.run_loop()

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"checkout is not clean", result.stderr)
        self.assertEqual(state["events"], [])
        self.assertEqual(state["copilot_calls"], [])
        self.assertTrue((self.repo / "untracked.txt").is_file())

    def test_detached_head_is_rejected_before_github_access(self) -> None:
        self.git("checkout", "--quiet", "--detach")

        result, _ = self.run_loop()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"attached branch", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertFalse((self.repo / ".git/implement-loop.lock").exists())

    def test_unborn_branch_is_rejected_with_recovery_guidance(self) -> None:
        self.git("checkout", "--quiet", "--orphan", "unborn")

        result, _ = self.run_loop()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"existing commit", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertEqual(self.load_state()["copilot_calls"], [])
        self.assertEqual(self.git("branch", "--show-current"), "unborn")
        self.assertFalse((self.repo / ".git/implement-loop.lock").exists())

    def test_git_discovery_read_errors_are_not_misreported_as_checkout_state(self) -> None:
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git)
        cases = (
            (
                ["symbolic-ref", "--quiet", "--short", "HEAD"],
                "simulated branch read failure",
                "attached branch",
            ),
            (
                ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"],
                "simulated HEAD read failure",
                "unborn branch",
            ),
        )
        for arguments, failure, misleading_state in cases:
            with self.subTest(arguments=arguments):
                git_bin = self.home / f"git-read-failure-{arguments[0]}"
                git_bin.mkdir()
                wrapper = git_bin / "git"
                wrapper.write_text(
                    "#!/usr/bin/env python3\n"
                    "import os, sys\n"
                    f"real_git = {real_git!r}\n"
                    f"failed_arguments = {arguments!r}\n"
                    "actual_arguments = sys.argv[1:]\n"
                    "if actual_arguments == failed_arguments:\n"
                    f"    sys.stderr.write({failure!r} + '\\n')\n"
                    "    sys.exit(128)\n"
                    "os.execv(real_git, [real_git, *actual_arguments])\n"
                )
                wrapper.chmod(0o700)

                result, _ = self.run_loop(
                    extra_env={"PATH": str(git_bin) + os.pathsep + self.env["PATH"]}
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(failure.encode(), result.stderr)
                self.assertNotIn(misleading_state.encode(), result.stderr)
                self.assertEqual(self.load_state()["events"], [])
                self.assertEqual(self.load_state()["copilot_calls"], [])

    def test_repository_common_lock_blocks_a_second_worktree(self) -> None:
        worktree = self.home / "other-worktree"
        self.git("worktree", "add", "--quiet", "-b", "other", str(worktree), "HEAD")
        lock = self.repository_lock_path(worktree)
        lock.mkdir()
        (lock / "pid").write_text("another loop\n")

        result, _ = self.run_loop(cwd=worktree)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"stale lock exists", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertEqual((lock / "pid").read_text(), "another loop\n")

    def test_repository_lock_blocks_a_second_run_in_the_same_checkout(self) -> None:
        lock = self.repository_lock_path()
        lock.mkdir()
        (lock / "pid").write_text("another loop\n")

        result, _ = self.run_loop()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"stale lock exists", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertEqual(self.load_state()["copilot_calls"], [])
        self.assertEqual((lock / "pid").read_text(), "another loop\n")

    def test_conflicting_github_repository_override_is_rejected(self) -> None:
        result, _ = self.run_loop(extra_env={"GH_REPO": "someone/else"})

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"GH_REPO conflicts", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertEqual(self.load_state()["copilot_calls"], [])

    def test_matching_github_repository_override_is_allowed(self) -> None:
        result, _ = self.run_loop(
            "--max-iterations",
            "1",
            extra_env={"GH_REPO": "test/repo"},
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        state = self.load_state()
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertNotIn("repo_view_saw_override", state)

    def test_new_commit_is_required(self) -> None:
        self.state["make_commit"] = False
        self.save_state()

        result, _ = self.run_loop("--max-iterations", "1")

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"No new commit was made", result.stderr)
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual([event[0] for event in state["events"]], ["assign"])

    def test_dirty_checkout_after_execution_is_rejected(self) -> None:
        self.state["agent_action"] = "dirty"
        self.state["make_commit"] = False
        self.save_state()

        result, _ = self.run_loop("--max-iterations", "1")

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"checkout is not clean", result.stderr)
        self.assertTrue((self.repo / "unfinished.txt").is_file())
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual([event[0] for event in state["events"]], ["assign"])

    def test_submodule_changes_after_execution_prevent_closure_and_are_preserved(self) -> None:
        submodule = self.add_submodule()
        self.state["agent_action"] = "dirty_submodule"
        self.save_state()

        result, runs = self.run_loop("--max-iterations", "1")

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"checkout is not clean", result.stderr)
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
        self.assertEqual([event[0] for event in state["events"]], ["assign"])
        self.assertEqual(
            (submodule / "uncommitted.txt").read_text(), "submodule work to preserve\n"
        )
        self.assertIn("implementation-2.txt", self.git("show", "--pretty=", "--name-only"))
        self.assertTrue((runs[0] / "attempt-1-ticket-2/process.json").is_file())

    def test_both_harnesses_share_git_and_ownership_completion_checks(self) -> None:
        for harness, selected_calls, unselected_calls in (
            (None, "opencode_calls", "copilot_calls"),
            ("copilot", "copilot_calls", "opencode_calls"),
        ):
            for action in ("dirty", "change_owner"):
                with self.subTest(harness=harness or "default", action=action):
                    self.state = self.default_state()
                    self.state["agent_action"] = action
                    self.save_state()

                    result, runs = self.run_loop(
                        "--max-iterations",
                        "1",
                        harness=harness,
                    )

                    state = self.load_state()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(len(state[selected_calls]), 1)
                    self.assertEqual(state[unselected_calls], [])
                    self.assertEqual(state["tickets"]["2"]["state"], "open")
                    self.assertEqual([event[0] for event in state["events"]], ["assign"])
                    self.assertIn(
                        "implementation-2.txt",
                        self.git("show", "--pretty=", "--name-only"),
                    )
                    self.assertTrue((runs[0] / "attempt-1-ticket-2/process.json").is_file())
                    if action == "dirty":
                        self.assertIn(b"checkout is not clean", result.stderr)
                        self.assertTrue((self.repo / "unfinished.txt").is_file())
                        (self.repo / "unfinished.txt").unlink()
                    else:
                        self.assertIn(b"no longer open, unblocked", result.stderr)
                        self.assertEqual(
                            state["tickets"]["2"]["assignees"],
                            [{"login": "tester"}, {"login": "other"}],
                        )

    def test_changed_branch_is_rejected(self) -> None:
        self.state["agent_action"] = "branch"
        self.state["make_commit"] = False
        self.save_state()

        result, _ = self.run_loop("--max-iterations", "1")

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"branch changed from main to agent-branch", result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "agent-branch")
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual([event[0] for event in state["events"]], ["assign"])

    def test_non_descendant_history_is_rejected(self) -> None:
        self.add_ticket(3)
        self.state["agent_action_by_ticket"] = {"3": "rewrite_history"}
        self.save_state()
        initial_head = self.git("rev-parse", "HEAD")

        result, _ = self.run_loop("--max-iterations", "2")

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"rewrote or replaced existing history", result.stderr)
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        self.assertEqual(state["tickets"]["3"]["assignees"], [{"login": "tester"}])
        self.assertEqual([event[0] for event in state["events"]], ["assign", "close", "assign"])
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertNotEqual(self.git("rev-parse", "HEAD"), initial_head)
        self.assertEqual(self.git("show", "-s", "--format=%s", "HEAD"), "Rewrite fixture history")
        self.assertEqual((self.repo / "rewritten-history.txt").read_text(), "unrelated commit\n")
        self.assertEqual(self.git("status", "--porcelain"), "")
