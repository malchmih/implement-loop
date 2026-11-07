from __future__ import annotations

import json

from support import LoopTestCase


def test_user_toml_configures_root_opencode_preferences(cli_case: LoopTestCase) -> None:
    config_home = cli_case.home / "user settings"
    config_file = config_home / "implement-loop/config.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text('model = "provider/custom-model"\nvariant = "high"\n')

    result, runs = cli_case.run_loop(
        "--max-iterations",
        "1",
        extra_env={"XDG_CONFIG_HOME": str(config_home)},
    )

    state = cli_case.load_state()
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    call = state["opencode_calls"][0]
    assert call["args"][call["args"].index("--model") + 1] == "provider/custom-model#high"
    metadata = json.loads((runs[0] / "run.json").read_text())
    assert metadata["configuration_file"] == str(config_file)


def test_default_attempt_limit_is_ten(cli_case: LoopTestCase) -> None:
    for ticket_number in range(3, 13):
        cli_case.add_ticket(ticket_number)
    cli_case.save_state()

    result, _ = cli_case.run_loop()

    state = cli_case.load_state()
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    assert [call["ticket"] for call in state["opencode_calls"]] == list(range(2, 12))
    assert state["tickets"]["12"]["state"] == "open"
    assert b"Attempt limit reached after 10 invocation(s)" in result.stdout


def test_explicit_attempt_limit_stops_before_the_next_ticket(cli_case: LoopTestCase) -> None:
    cli_case.add_ticket(3)
    cli_case.save_state()

    result, _ = cli_case.run_loop("--max-iterations", "1")

    state = cli_case.load_state()
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    assert [call["ticket"] for call in state["opencode_calls"]] == [2]
    assert state["tickets"]["3"]["state"] == "open"
    assert state["tickets"]["3"]["assignees"] == []
    assert b"Attempt limit reached after 1 invocation(s)" in result.stdout
