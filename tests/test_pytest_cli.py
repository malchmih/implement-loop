from __future__ import annotations

from support import LoopTestCase


def test_cli_completes_a_child_ticket_with_fake_integrations(cli_case: LoopTestCase) -> None:
    result, _ = cli_case.run_loop("--max-iterations", "1")

    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    assert cli_case.load_state()["tickets"]["2"]["state"] == "closed"
    assert cli_case.git("status", "--porcelain") == ""
