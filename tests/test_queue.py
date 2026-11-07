from __future__ import annotations

from support import LoopFixture


def test_selection_skips_blockers_and_assignments_and_ignores_labels(
    loop_fixture: LoopFixture,
) -> None:
    loop_fixture.state["tickets"]["2"]["issue_dependencies_summary"]["blocked_by"] = 1
    loop_fixture.state["tickets"]["2"]["blocking_issues"] = [3]
    loop_fixture.add_ticket(3, assignees=["tester"])
    loop_fixture.add_ticket(4, assignees=["someone-else"])
    loop_fixture.add_ticket(5, blocked=1, labels=["wontfix"])
    loop_fixture.state["tickets"]["5"]["blocking_issues"] = [99]
    loop_fixture.add_ticket(6, labels=["wontfix"])
    loop_fixture.add_ticket(99)
    loop_fixture.state["children"] = [
        {"number": 2},
        {"number": 3},
        {"number": 4},
        {"number": 5},
        {"number": 6},
    ]
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop("--max-iterations", "1")

    state = loop_fixture.load_state()
    assert result.returncode == 0, result.stderr.decode()
    assert [call["ticket"] for call in state["opencode_calls"]] == [6]
    assert state["tickets"]["2"]["state"] == "open"
    assert state["tickets"]["3"]["state"] == "open"
    assert state["tickets"]["4"]["state"] == "open"
    assert state["tickets"]["5"]["state"] == "open"
    assert state["tickets"]["6"]["state"] == "closed"
    assert 99 not in [child["number"] for child in state["children"]]


def test_selection_refreshes_after_closure_and_preserves_returned_order(
    loop_fixture: LoopFixture,
) -> None:
    loop_fixture.add_ticket(3)
    loop_fixture.state["children"] = [{"number": 3}, {"number": 2}]
    loop_fixture.state["sub_issue_page_size"] = 1
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop("--max-iterations", "2")

    state = loop_fixture.load_state()
    assert result.returncode == 0, result.stderr.decode()
    assert [call["ticket"] for call in state["opencode_calls"]] == [3, 2]
    assert len({call["attempt_id"] for call in state["opencode_calls"]}) == 2
    for call in state["opencode_calls"]:
        assert "--continue" not in call["args"]
        assert "--resume" not in call["args"]
    assert state["tickets"]["2"]["state"] == "closed"
    assert state["tickets"]["3"]["state"] == "closed"
    assert (runs[0] / "attempt-1-ticket-3/completion-result.json").is_file()
    assert (runs[0] / "attempt-2-ticket-2/completion-result.json").is_file()


def test_no_eligible_children_exits_successfully_without_opencode_invocation(
    loop_fixture: LoopFixture,
) -> None:
    loop_fixture.state["tickets"]["2"]["assignees"] = [{"login": "tester"}]
    loop_fixture.add_ticket(3, blocked=2)
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop()

    state = loop_fixture.load_state()
    assert result.returncode == 0, result.stderr.decode()
    assert state["opencode_calls"] == []
    assert state["events"] == []
    assert b"No eligible children" in result.stdout


def test_attempt_limit_refreshes_queue_but_does_not_claim_next_ticket(
    loop_fixture: LoopFixture,
) -> None:
    loop_fixture.add_ticket(3)
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop("--max-iterations", "1")

    state = loop_fixture.load_state()
    assert result.returncode == 0, result.stderr.decode()
    assert [call["ticket"] for call in state["opencode_calls"]] == [2]
    assert state["tickets"]["2"]["state"] == "closed"
    assert state["tickets"]["3"]["state"] == "open"
    assert state["tickets"]["3"]["assignees"] == []
    assert b"Attempt limit reached" in result.stdout
