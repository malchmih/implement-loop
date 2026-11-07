from __future__ import annotations

import os
import shutil

import pytest
from support import LoopFixture


def test_initial_dirty_checkout_is_rejected_before_assignment(loop_fixture: LoopFixture) -> None:
    (loop_fixture.repo / "untracked.txt").write_text("keep me\n")

    result, _ = loop_fixture.run_loop()

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"checkout is not clean" in result.stderr
    assert state["events"] == []
    assert state["opencode_calls"] == []
    assert (loop_fixture.repo / "untracked.txt").is_file()


def test_detached_head_is_rejected_before_github_access(loop_fixture: LoopFixture) -> None:
    loop_fixture.git("checkout", "--quiet", "--detach")

    result, _ = loop_fixture.run_loop()

    assert result.returncode != 0
    assert b"attached branch" in result.stderr
    assert loop_fixture.load_state()["events"] == []
    assert not (loop_fixture.repo / ".git/implement-loop.lock").exists()


def test_unborn_branch_is_rejected_with_recovery_guidance(loop_fixture: LoopFixture) -> None:
    loop_fixture.git("checkout", "--quiet", "--orphan", "unborn")

    result, _ = loop_fixture.run_loop()

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"existing commit" in result.stderr
    assert state["events"] == []
    assert state["opencode_calls"] == []
    assert loop_fixture.git("branch", "--show-current") == "unborn"
    assert not (loop_fixture.repo / ".git/implement-loop.lock").exists()


@pytest.mark.parametrize(
    ("arguments", "failure", "misleading_state"),
    (
        (
            ("symbolic-ref", "--quiet", "--short", "HEAD"),
            "simulated branch read failure",
            "attached branch",
        ),
        (
            ("rev-parse", "--verify", "--quiet", "HEAD^{commit}"),
            "simulated HEAD read failure",
            "unborn branch",
        ),
    ),
)
def test_git_discovery_read_errors_are_not_misreported_as_checkout_state(
    loop_fixture: LoopFixture,
    arguments: tuple[str, ...],
    failure: str,
    misleading_state: str,
) -> None:
    real_git = shutil.which("git")
    assert real_git is not None
    git_bin = loop_fixture.home / f"git-read-failure-{arguments[0]}"
    git_bin.mkdir()
    wrapper = git_bin / "git"
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        f"real_git = {real_git!r}\n"
        f"failed_arguments = {list(arguments)!r}\n"
        "actual_arguments = sys.argv[1:]\n"
        "if actual_arguments == failed_arguments:\n"
        f"    sys.stderr.write({failure!r} + '\\n')\n"
        "    sys.exit(128)\n"
        "os.execv(real_git, [real_git, *actual_arguments])\n"
    )
    wrapper.chmod(0o700)

    result, _ = loop_fixture.run_loop(
        extra_env={"PATH": str(git_bin) + os.pathsep + loop_fixture.env["PATH"]}
    )

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert failure.encode() in result.stderr
    assert misleading_state.encode() not in result.stderr
    assert state["events"] == []
    assert state["opencode_calls"] == []


def test_repository_common_lock_blocks_a_second_worktree(loop_fixture: LoopFixture) -> None:
    worktree = loop_fixture.home / "other-worktree"
    loop_fixture.git("worktree", "add", "--quiet", "-b", "other", str(worktree), "HEAD")
    lock = loop_fixture.repository_lock_path(worktree)
    lock.mkdir()
    (lock / "pid").write_text("another loop\n")

    result, _ = loop_fixture.run_loop(cwd=worktree)

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"stale lock exists" in result.stderr
    assert state["events"] == []
    assert (lock / "pid").read_text() == "another loop\n"


def test_repository_lock_blocks_a_second_run_in_the_same_checkout(
    loop_fixture: LoopFixture,
) -> None:
    lock = loop_fixture.repository_lock_path()
    lock.mkdir()
    (lock / "pid").write_text("another loop\n")

    result, _ = loop_fixture.run_loop()

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"stale lock exists" in result.stderr
    assert state["events"] == []
    assert state["opencode_calls"] == []
    assert (lock / "pid").read_text() == "another loop\n"


def test_conflicting_github_repository_override_is_rejected(loop_fixture: LoopFixture) -> None:
    result, _ = loop_fixture.run_loop(extra_env={"GH_REPO": "someone/else"})

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"GH_REPO conflicts" in result.stderr
    assert state["events"] == []
    assert state["opencode_calls"] == []


def test_matching_github_repository_override_is_allowed(loop_fixture: LoopFixture) -> None:
    result, _ = loop_fixture.run_loop(
        "--max-iterations",
        "1",
        extra_env={"GH_REPO": "test/repo"},
    )

    state = loop_fixture.load_state()
    assert result.returncode == 0, result.stderr.decode()
    assert state["tickets"]["2"]["state"] == "closed"
    assert "repo_view_saw_override" not in state


def test_new_commit_is_required(loop_fixture: LoopFixture) -> None:
    loop_fixture.state["make_commit"] = False
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop("--max-iterations", "1")

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"No new commit was made" in result.stderr
    assert state["tickets"]["2"]["state"] == "open"
    assert [event[0] for event in state["events"]] == ["assign"]


def test_dirty_checkout_after_execution_is_rejected(loop_fixture: LoopFixture) -> None:
    loop_fixture.state["agent_action"] = "dirty"
    loop_fixture.state["make_commit"] = False
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop("--max-iterations", "1")

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"checkout is not clean" in result.stderr
    assert (loop_fixture.repo / "unfinished.txt").is_file()
    assert state["tickets"]["2"]["state"] == "open"
    assert [event[0] for event in state["events"]] == ["assign"]


def test_submodule_changes_after_execution_prevent_closure_and_are_preserved(
    loop_fixture: LoopFixture,
) -> None:
    submodule = loop_fixture.add_submodule()
    loop_fixture.state["agent_action"] = "dirty_submodule"
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop("--max-iterations", "1")

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"checkout is not clean" in result.stderr
    assert state["tickets"]["2"]["state"] == "open"
    assert state["tickets"]["2"]["assignees"] == [{"login": "tester"}]
    assert [event[0] for event in state["events"]] == ["assign"]
    assert (submodule / "uncommitted.txt").read_text() == "submodule work to preserve\n"
    assert "implementation-2.txt" in loop_fixture.git("show", "--pretty=", "--name-only")
    assert (runs[0] / "attempt-1-ticket-2/process.json").is_file()


@pytest.mark.parametrize("action", ("dirty", "change_owner"))
def test_opencode_requires_clean_checkout_and_exclusive_ownership(
    loop_fixture: LoopFixture,
    action: str,
) -> None:
    loop_fixture.state["agent_action"] = action
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop("--max-iterations", "1")

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert len(state["opencode_calls"]) == 1
    assert state["tickets"]["2"]["state"] == "open"
    assert [event[0] for event in state["events"]] == ["assign"]
    assert "implementation-2.txt" in loop_fixture.git("show", "--pretty=", "--name-only")
    assert (runs[0] / "attempt-1-ticket-2/process.json").is_file()
    if action == "dirty":
        assert b"checkout is not clean" in result.stderr
        assert (loop_fixture.repo / "unfinished.txt").is_file()
    else:
        assert b"no longer open, unblocked" in result.stderr
        assert state["tickets"]["2"]["assignees"] == [
            {"login": "tester"},
            {"login": "other"},
        ]


def test_changed_branch_is_rejected(loop_fixture: LoopFixture) -> None:
    loop_fixture.state["agent_action"] = "branch"
    loop_fixture.state["make_commit"] = False
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop("--max-iterations", "1")

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"branch changed from main to agent-branch" in result.stderr
    assert loop_fixture.git("branch", "--show-current") == "agent-branch"
    assert state["tickets"]["2"]["state"] == "open"
    assert [event[0] for event in state["events"]] == ["assign"]


def test_non_descendant_history_is_rejected(loop_fixture: LoopFixture) -> None:
    loop_fixture.add_ticket(3)
    loop_fixture.state["agent_action_by_ticket"] = {"3": "rewrite_history"}
    loop_fixture.save_state()
    initial_head = loop_fixture.git("rev-parse", "HEAD")

    result, _ = loop_fixture.run_loop("--max-iterations", "2")

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"rewrote or replaced existing history" in result.stderr
    assert state["tickets"]["2"]["state"] == "closed"
    assert state["tickets"]["3"]["state"] == "open"
    assert state["tickets"]["3"]["assignees"] == [{"login": "tester"}]
    assert [event[0] for event in state["events"]] == ["assign", "close", "assign"]
    assert loop_fixture.git("branch", "--show-current") == "main"
    assert loop_fixture.git("rev-parse", "HEAD") != initial_head
    assert loop_fixture.git("show", "-s", "--format=%s", "HEAD") == "Rewrite fixture history"
    assert (loop_fixture.repo / "rewritten-history.txt").read_text() == "unrelated commit\n"
    assert loop_fixture.git("status", "--porcelain") == ""
