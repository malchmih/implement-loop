from __future__ import annotations

import json

from support import LoopFixture


def test_user_toml_configures_root_opencode_preferences(loop_fixture: LoopFixture) -> None:
    config_home = loop_fixture.home / "user settings"
    config_file = config_home / "implement-loop/config.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text('model = "provider/custom-model"\nvariant = "high"\n')

    result, runs = loop_fixture.run_loop(
        "--max-iterations",
        "1",
        extra_env={"XDG_CONFIG_HOME": str(config_home)},
    )

    state = loop_fixture.load_state()
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    call = state["opencode_calls"][0]
    assert call["args"][call["args"].index("--model") + 1] == "provider/custom-model#high"
    metadata = json.loads((runs[0] / "run.json").read_text())
    assert metadata["configuration_file"] == str(config_file)


def test_root_model_preference_does_not_replace_a_delegated_model(
    loop_fixture: LoopFixture,
) -> None:
    config_home = loop_fixture.home / "root model settings"
    config_file = config_home / "implement-loop/config.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text('model = "root-provider/root-model"\nvariant = "high"\n')
    delegated_model = "delegate-provider/delegate-model"
    events = [
        {
            "type": "tool_use",
            "timestamp": 1,
            "sessionID": "ses_fixture",
            "part": {
                "type": "tool",
                "tool": "task",
                "state": {
                    "status": "completed",
                    "title": "Delegate implementation",
                    "input": {"model": delegated_model},
                    "output": "Delegated work completed.",
                },
            },
        }
    ]
    raw_output = "\n".join(json.dumps(event) for event in events) + "\n"
    loop_fixture.state["opencode_raw_output"] = raw_output
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop(
        "--max-iterations",
        "1",
        extra_env={"XDG_CONFIG_HOME": str(config_home)},
    )

    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    call = loop_fixture.load_state()["opencode_calls"][0]
    assert call["args"][call["args"].index("--model") + 1] == "root-provider/root-model#high"
    recorded_events = [
        json.loads(line)
        for line in (runs[0] / "attempt-1-ticket-2/agent.log").read_text().splitlines()
    ]
    assert recorded_events[0]["part"]["state"]["input"]["model"] == delegated_model


def test_default_attempt_limit_is_ten(loop_fixture: LoopFixture) -> None:
    for ticket_number in range(3, 13):
        loop_fixture.add_ticket(ticket_number)
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop()

    state = loop_fixture.load_state()
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    assert [call["ticket"] for call in state["opencode_calls"]] == list(range(2, 12))
    assert state["tickets"]["12"]["state"] == "open"
    assert b"Attempt limit reached after 10 invocation(s)" in result.stdout


def test_explicit_attempt_limit_stops_before_the_next_ticket(loop_fixture: LoopFixture) -> None:
    loop_fixture.add_ticket(3)
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop("--max-iterations", "1")

    state = loop_fixture.load_state()
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    assert [call["ticket"] for call in state["opencode_calls"]] == [2]
    assert state["tickets"]["3"]["state"] == "open"
    assert state["tickets"]["3"]["assignees"] == []
    assert b"Attempt limit reached after 1 invocation(s)" in result.stdout
