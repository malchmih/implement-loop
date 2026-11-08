from __future__ import annotations

import json

import pytest
from support import LoopFixture


def test_missing_native_blocker_metadata_fails_closed(loop_fixture: LoopFixture) -> None:
    loop_fixture.state["tickets"]["2"].pop("issue_dependencies_summary")
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop()

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"missing readable native dependency data" in result.stderr
    assert state["events"] == []
    assert state["opencode_calls"] == []


@pytest.mark.parametrize(
    "malformed_case",
    ("missing_parent_repository", "non_integer_blocker_count", "boolean_child_number"),
)
def test_malformed_issue_metadata_fails_closed_before_claim(
    loop_fixture: LoopFixture,
    malformed_case: str,
) -> None:
    if malformed_case == "missing_parent_repository":
        loop_fixture.state["spec"].pop("repository_url")
    elif malformed_case == "non_integer_blocker_count":
        loop_fixture.state["tickets"]["2"]["issue_dependencies_summary"]["blocked_by"] = True
    else:
        loop_fixture.state["children"][0]["number"] = True
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop()

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert state["tickets"]["2"]["assignees"] == []
    assert state["events"] == []
    assert state["opencode_calls"] == []


def test_failed_required_queue_read_stops_before_claim(loop_fixture: LoopFixture) -> None:
    loop_fixture.add_ticket(3)
    loop_fixture.state["api_failure_endpoint"] = "repos/test/repo/issues/1/sub_issues?per_page=100"
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop("--max-iterations", "2")

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"simulated required GitHub API read failure" in result.stderr
    assert state["events"] == []
    assert state["opencode_calls"] == []
    assert state["tickets"]["2"]["assignees"] == []
    assert state["tickets"]["3"]["assignees"] == []


def test_preparation_failure_reports_ticket_records_before_metadata_exists(
    loop_fixture: LoopFixture,
) -> None:
    loop_fixture.state["api_failure_endpoint"] = "repos/test/repo/issues/2/comments?per_page=100"
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop()

    attempt = runs[0] / "attempt-1-ticket-2"
    assert result.returncode == 1, result.stderr.decode()
    assert b"simulated required GitHub API read failure" in result.stderr
    assert b"may already be closed" not in result.stderr
    assert f"Run records: {runs[0].resolve()}".encode() in result.stderr
    assert (
        f"Ticket #2 records (no harness invocation started): {attempt.resolve()}".encode()
        in result.stderr
    )
    assert attempt.is_dir()
    assert list(attempt.iterdir()) == []
    state = loop_fixture.load_state()
    assert state["events"] == []
    assert state["opencode_calls"] == []


def test_opencode_log_creation_failure_reports_no_invocation_started(
    loop_fixture: LoopFixture,
) -> None:
    loop_fixture.state["precreate_agent_log_before_invocation"] = True
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop()

    attempt = runs[0] / "attempt-1-ticket-2"
    assert result.returncode == 1, result.stderr.decode()
    assert b"Cannot create private OpenCode output log" in result.stderr
    assert f"Run records: {runs[0].resolve()}".encode() in result.stderr
    assert (
        f"Ticket #2 records (no harness invocation started): {attempt.resolve()}".encode()
        in result.stderr
    )
    state = loop_fixture.load_state()
    assert state["opencode_calls"] == []
    assert state["tickets"]["2"]["assignees"] == [{"login": "tester"}]
    assert (attempt / "agent.log").read_text() == "preexisting log record\n"


def test_opencode_spawn_failure_reports_no_invocation_started(loop_fixture: LoopFixture) -> None:
    loop_fixture.state["remove_opencode_before_invocation"] = True
    loop_fixture.save_state()
    spawn_bin = loop_fixture.controlled_path_directory("python3", "git", "gh", "opencode")

    result, runs = loop_fixture.run_loop(extra_env={"PATH": str(spawn_bin)})

    attempt = runs[0] / "attempt-1-ticket-2"
    assert result.returncode == 1, result.stderr.decode()
    assert b"OpenCode could not be started" in result.stderr
    assert f"Run records: {runs[0].resolve()}".encode() in result.stderr
    assert (
        f"Ticket #2 records (no harness invocation started): {attempt.resolve()}".encode()
        in result.stderr
    )
    state = loop_fixture.load_state()
    assert state["opencode_calls"] == []
    assert state["tickets"]["2"]["assignees"] == [{"login": "tester"}]
    outcome = json.loads((attempt / "outcome.json").read_text())
    assert outcome["status"] == "failed"
    assert outcome["process"]["exit_code"] is None
    assert "could not be started" in outcome["process"]["spawn_error"]


def test_queue_refresh_failure_does_not_report_completed_ticket_records(
    loop_fixture: LoopFixture,
) -> None:
    loop_fixture.add_ticket(3)
    loop_fixture.state["fail_queue_refresh_after_close"] = True
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop("--max-iterations", "2")

    state = loop_fixture.load_state()
    assert result.returncode == 1, result.stderr.decode()
    assert b"simulated required GitHub API read failure" in result.stderr
    assert f"Run records: {runs[0].resolve()}".encode() in result.stderr
    assert b"Ticket #2 records" not in result.stderr
    assert b"Ticket #3 records" not in result.stderr
    assert (runs[0] / "attempt-1-ticket-2").is_dir()
    assert state["tickets"]["2"]["state"] == "closed"
    assert state["tickets"]["3"]["assignees"] == []
    assert [call["ticket"] for call in state["opencode_calls"]] == [2]


def test_failed_preclosure_read_preserves_work_and_stops_the_queue(
    loop_fixture: LoopFixture,
) -> None:
    loop_fixture.add_ticket(3)
    loop_fixture.state["fail_preclosure_read_after_harness"] = True
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop("--max-iterations", "2")

    state = loop_fixture.load_state()
    attempt = runs[0] / "attempt-1-ticket-2"
    assert result.returncode != 0
    assert b"simulated required issue read failure" in result.stderr
    assert b"may already be closed" not in result.stderr
    assert [call["ticket"] for call in state["opencode_calls"]] == [2]
    assert state["tickets"]["2"]["state"] == "open"
    assert state["tickets"]["2"]["assignees"] == [{"login": "tester"}]
    assert state["tickets"]["3"]["assignees"] == []
    assert [event[0] for event in state["events"]] == ["assign"]
    outcome = json.loads((attempt / "outcome.json").read_text())
    assert outcome["status"] == "failed"
    assert outcome["process"]["exit_code"] == 0
    assert outcome["report"]["status"] == "valid"
    assert outcome["checks"]["status"] == "failed"
    assert "simulated required issue read failure" in outcome["checks"]["diagnostic"]
    assert "implementation-2.txt" in loop_fixture.git("show", "--pretty=", "--name-only")


def test_failed_closure_confirmation_does_not_start_another_ticket(
    loop_fixture: LoopFixture,
) -> None:
    loop_fixture.add_ticket(3)
    loop_fixture.state["fail_confirmation_after_close"] = True
    loop_fixture.state["issue_read_failure_detail"] = (
        "simulated required issue read failure\nadditional issue-read stderr detail"
    )
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop("--max-iterations", "2")

    state = loop_fixture.load_state()
    attempt = runs[0] / "attempt-1-ticket-2"
    assert result.returncode != 0
    assert (
        b"simulated required issue read failure\nadditional issue-read stderr detail"
        in result.stderr
    )
    assert (
        b"Ticket #2 may already be closed. "
        b"Inspect ticket #2's live GitHub state before rerunning." in result.stderr
    )
    assert b"The closure comment or closure" not in result.stderr
    assert [call["ticket"] for call in state["opencode_calls"]] == [2]
    assert state["tickets"]["2"]["state"] == "closed"
    assert state["tickets"]["2"]["assignees"] == [{"login": "tester"}]
    assert state["tickets"]["3"]["state"] == "open"
    assert state["tickets"]["3"]["assignees"] == []
    assert [event[0] for event in state["events"]] == ["assign", "close"]
    outcome = json.loads((attempt / "outcome.json").read_text())
    assert outcome["closure"]["status"] == "uncertain"
    assert "Implemented locally by implement-loop" in outcome["closure"]["comment"]


def test_cross_repository_child_fails_closed(loop_fixture: LoopFixture) -> None:
    loop_fixture.state["children"][0]["repository_url"] = "https://api.github.com/repos/other/repo"
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop()

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"cross-repository children are not supported" in result.stderr
    assert state["events"] == []
    assert state["opencode_calls"] == []


@pytest.mark.parametrize("name", ("parent", "child"))
def test_pull_requests_cannot_be_used_as_parent_specs_or_children(
    loop_fixture: LoopFixture,
    name: str,
) -> None:
    if name == "parent":
        loop_fixture.state["spec"]["pull_request"] = {"url": "pull/1"}
    else:
        loop_fixture.state["children"][0]["pull_request"] = {"url": "pull/2"}
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop()

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"pull request, not an issue" in result.stderr
    assert state["events"] == []
    assert state["opencode_calls"] == []


def test_claim_is_rechecked_for_exclusive_ownership_and_blockers(
    loop_fixture: LoopFixture,
) -> None:
    loop_fixture.state["claim_conflict"] = True
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop()

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"no longer open, unblocked, and exclusively assigned" in result.stderr
    assert state["tickets"]["2"]["assignees"] == [
        {"login": "tester"},
        {"login": "other"},
    ]
    assert state["opencode_calls"] == []
    assert [event[0] for event in state["events"]] == ["assign"]


@pytest.mark.parametrize("failure_mode", ("before_write", "after_write"))
def test_failed_claim_writes_stop_without_rolling_back_partial_assignment(
    loop_fixture: LoopFixture,
    failure_mode: str,
) -> None:
    loop_fixture.add_ticket(3)
    failure_detail = (
        f"simulated GitHub claim failure {failure_mode.replace('_', ' ')}\nadditional stderr detail"
    )
    loop_fixture.state["claim_failure"] = failure_mode
    loop_fixture.state["claim_failure_detail"] = failure_detail
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop("--max-iterations", "2")

    state = loop_fixture.load_state()
    attempt = runs[0] / "attempt-1-ticket-2"
    assert result.returncode != 0
    assert failure_detail.encode() in result.stderr
    assert (
        b"Assignment of ticket #2 may have taken effect. "
        b"Inspect ticket #2's live GitHub state before rerunning." in result.stderr
    )
    assert f"Run records: {runs[0].resolve()}".encode() in result.stderr
    assert (
        f"Ticket #2 records (no harness invocation started): {attempt.resolve()}".encode()
        in result.stderr
    )
    assert (
        b"Any existing checkout changes, commits, and assignments were left in place"
        in result.stderr
    )
    assert b"Before rerunning, verify that any potentially running harness" in result.stderr
    assert state["opencode_calls"] == []
    assert state["tickets"]["2"]["state"] == "open"
    assert state["tickets"]["3"]["state"] == "open"
    assert state["tickets"]["3"]["assignees"] == []
    assert state["tickets"]["2"]["assignees"] == (
        [] if failure_mode == "before_write" else [{"login": "tester"}]
    )
    assert [event[0] for event in state["events"]] == (
        [] if failure_mode == "before_write" else ["assign"]
    )
    assert (attempt / "attempt.json").is_file()


def test_blocker_appearing_after_claim_prevents_harness_invocation(
    loop_fixture: LoopFixture,
) -> None:
    loop_fixture.state["claim_blocker"] = True
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop()

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"no longer open, unblocked" in result.stderr
    assert state["tickets"]["2"]["issue_dependencies_summary"]["blocked_by"] == 1
    assert state["tickets"]["2"]["assignees"] == [{"login": "tester"}]
    assert state["opencode_calls"] == []


def test_claim_is_rechecked_for_direct_child_membership(loop_fixture: LoopFixture) -> None:
    loop_fixture.state["claim_remove_child"] = True
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop()

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"no longer an open direct child" in result.stderr
    assert state["tickets"]["2"]["assignees"] == [{"login": "tester"}]
    assert state["opencode_calls"] == []


@pytest.mark.parametrize(
    ("changed_issue", "claim_option"),
    (("ticket", "claim_close"), ("parent", "claim_close_parent")),
)
def test_claim_recheck_refuses_a_closed_ticket_or_parent(
    loop_fixture: LoopFixture,
    changed_issue: str,
    claim_option: str,
) -> None:
    loop_fixture.state[claim_option] = True
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop()

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert state["opencode_calls"] == []
    assert state["tickets"]["2"]["assignees"] == [{"login": "tester"}]
    assert [event[0] for event in state["events"]] == ["assign"]
    assert state["tickets"]["2"]["state"] == ("closed" if changed_issue == "ticket" else "open")
    assert state["spec"]["state"] == ("closed" if changed_issue == "parent" else "open")


def test_ownership_change_after_harness_is_rejected(loop_fixture: LoopFixture) -> None:
    loop_fixture.state["agent_action"] = "change_owner"
    loop_fixture.save_state()
    existing_head = loop_fixture.git("rev-parse", "HEAD")

    result, _ = loop_fixture.run_loop("--max-iterations", "1")

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"no longer open, unblocked, and exclusively assigned" in result.stderr
    assert state["tickets"]["2"]["state"] == "open"
    assert state["tickets"]["2"]["assignees"] == [
        {"login": "tester"},
        {"login": "other"},
    ]
    assert state["report_observation"]["ticket_state"] == "open"
    assert state["report_observation"]["events"] == [["assign", 2, "tester"]]
    assert loop_fixture.git("rev-parse", "HEAD") != existing_head
    assert loop_fixture.git("status", "--porcelain") == ""
    assert [event[0] for event in state["events"]] == ["assign"]


@pytest.mark.parametrize(
    ("action", "diagnostic"),
    (
        ("add_blocker", b"no longer open, unblocked"),
        ("remove_child", b"no longer an open direct child"),
    ),
)
def test_ticket_blocker_and_membership_are_rechecked_after_harness(
    loop_fixture: LoopFixture,
    action: str,
    diagnostic: bytes,
) -> None:
    loop_fixture.state["agent_action"] = action
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop("--max-iterations", "1")

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert diagnostic in result.stderr
    assert state["tickets"]["2"]["state"] == "open"
    assert state["tickets"]["2"]["assignees"] == [{"login": "tester"}]
    assert [event[0] for event in state["events"]] == ["assign"]


def test_parent_must_remain_open_after_harness(loop_fixture: LoopFixture) -> None:
    loop_fixture.state["agent_action"] = "close_parent"
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop("--max-iterations", "1")

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert state["spec"]["state"] == "closed"
    assert state["tickets"]["2"]["state"] == "open"
    assert [event[0] for event in state["events"]] == ["assign"]


def test_ticket_closed_during_execution_is_not_closed_again_or_followed(
    loop_fixture: LoopFixture,
) -> None:
    loop_fixture.add_ticket(3)
    loop_fixture.state["agent_action"] = "close_ticket"
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop("--max-iterations", "2")

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"no longer open" in result.stderr
    assert [call["ticket"] for call in state["opencode_calls"]] == [2]
    assert state["tickets"]["2"]["state"] == "closed"
    assert state["tickets"]["3"]["state"] == "open"
    assert state["tickets"]["3"]["assignees"] == []
    assert [event[0] for event in state["events"]] == ["assign"]
    assert "implementation-2.txt" in loop_fixture.git("show", "--pretty=", "--name-only")


def test_github_close_failure_preserves_assignment_and_local_commit(
    loop_fixture: LoopFixture,
) -> None:
    loop_fixture.add_ticket(3)
    loop_fixture.state["close_failure"] = True
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop("--max-iterations", "2")

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"GitHub CLI failed during closing ticket" in result.stderr
    assert b"simulated GitHub closure failure" in result.stderr
    assert (
        b"The closure comment or closure of ticket #2 may have taken effect. "
        b"Inspect ticket #2's live GitHub state before rerunning." in result.stderr
    )
    assert [call["ticket"] for call in state["opencode_calls"]] == [2]
    assert state["tickets"]["2"]["state"] == "open"
    assert state["tickets"]["2"]["assignees"] == [{"login": "tester"}]
    assert state["tickets"]["3"]["state"] == "open"
    assert state["tickets"]["3"]["assignees"] == []
    assert "implementation-2.txt" in loop_fixture.git("show", "--pretty=", "--name-only")
    assert [event[0] for event in state["events"]] == ["assign"]


@pytest.mark.parametrize(
    ("failure_setting", "expected_final_event"),
    (("close_comment_failure_after_write", "comment"), ("close_failure_after_write", "close")),
)
def test_partial_closure_outcomes_preserve_work_and_stop_before_the_next_ticket(
    loop_fixture: LoopFixture,
    failure_setting: str,
    expected_final_event: str,
) -> None:
    loop_fixture.add_ticket(3)
    loop_fixture.state[failure_setting] = True
    loop_fixture.state["close_failure_detail"] = (
        "simulated GitHub closure failure detail\nadditional closure stderr detail"
    )
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop("--max-iterations", "2")

    state = loop_fixture.load_state()
    attempt = runs[0] / "attempt-1-ticket-2"
    outcome = json.loads((attempt / "outcome.json").read_text())
    closure_comment = outcome["closure"]["comment"]
    assert result.returncode != 0
    assert (
        b"simulated GitHub closure failure detail\nadditional closure stderr detail"
        in result.stderr
    )
    assert (
        b"The closure comment or closure of ticket #2 may have taken effect. "
        b"Inspect ticket #2's live GitHub state before rerunning." in result.stderr
    )
    assert [call["ticket"] for call in state["opencode_calls"]] == [2]
    assert state["tickets"]["2"]["assignees"] == [{"login": "tester"}]
    assert state["tickets"]["3"]["state"] == "open"
    assert state["tickets"]["3"]["assignees"] == []
    assert "implementation-2.txt" in loop_fixture.git("show", "--pretty=", "--name-only")
    assert [event[0] for event in state["events"]] == ["assign", expected_final_event]
    if expected_final_event == "comment":
        assert state["tickets"]["2"]["state"] == "open"
        assert state["comments"]["2"][-1]["body"] == closure_comment
    else:
        assert state["tickets"]["2"]["state"] == "closed"
    assert outcome["checks"]["report_evidence_valid"]
    assert outcome["closure"]["status"] == "uncertain"


def test_oversized_context_fails_before_claim_without_truncation(loop_fixture: LoopFixture) -> None:
    loop_fixture.state["tickets"]["2"]["body"] = "x" * 101_000
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop()

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"safe 100 KB command-line budget" in result.stderr
    assert state["tickets"]["2"]["assignees"] == []
    assert state["events"] == []
    assert state["opencode_calls"] == []
