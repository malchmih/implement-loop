from __future__ import annotations

import json
import os
import resource
import signal
import socket
import subprocess
from pathlib import Path

import pytest
from support import (
    LIVE_OUTPUT_FRAGMENT,
    LIVE_OUTPUT_TIMEOUT,
    LIVE_READY_TIMEOUT,
    SCRIPT,
    LoopFixture,
)


@pytest.mark.parametrize(
    "stop_signal",
    (signal.SIGINT, signal.SIGTERM),
    ids=("ctrl-c", "terminate"),
)
def test_interruption_reports_run_and_ticket_records(
    loop_fixture: LoopFixture,
    stop_signal: int,
) -> None:
    ready_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ready_listener.bind(("127.0.0.1", 0))
    ready_listener.listen()
    ready_listener.settimeout(LIVE_READY_TIMEOUT)
    env = dict(loop_fixture.env)
    env["IMPLEMENT_LOOP_TEST_LIVE_OUTPUT_FRAGMENT"] = LIVE_OUTPUT_FRAGMENT.decode("ascii")
    env["IMPLEMENT_LOOP_TEST_LIVE_OUTPUT_READY_PORT"] = str(ready_listener.getsockname()[1])
    process: subprocess.Popen[bytes] | None = None
    output = bytearray()

    try:
        process = subprocess.Popen(
            [str(SCRIPT), "1", "--max-iterations", "1"],
            cwd=loop_fixture.repo,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        with ready_listener.accept()[0] as ready_connection:
            ready_connection.settimeout(LIVE_READY_TIMEOUT)
            assert ready_connection.recv(1) == b"R"
        assert process.stdout is not None
        loop_fixture._read_until_output(
            process.stdout.fileno(),
            output,
            LIVE_OUTPUT_FRAGMENT,
            timeout=LIVE_OUTPUT_TIMEOUT,
        )
        attempt_directories = list(
            (loop_fixture.home / ".local/state/implement-loop").glob("run-*/attempt-1-ticket-2")
        )
        assert len(attempt_directories) == 1
        run_directory = attempt_directories[0].parent
        attempt_directory = attempt_directories[0]

        process.send_signal(stop_signal)
        assert process.stdin is not None
        process.stdin.write(b"x")
        process.stdin.flush()
        remaining, _ = process.communicate(timeout=LIVE_READY_TIMEOUT)
        output.extend(remaining)

        assert process.returncode == 130, output.decode("utf-8", errors="replace")
        assert b"Interrupted." in output
        assert f"Run records: {run_directory.resolve()}".encode() in output
        assert f"Ticket #2 records: {attempt_directory.resolve()}".encode() in output
        assert (
            b"This output, the records, and release of the local lock do not confirm shutdown"
            in output
        )
        assert run_directory.is_dir()
        assert attempt_directory.is_dir()
        assert not (run_directory / "active.pid").exists()
        assert not loop_fixture.repository_lock_path().exists()
        state = loop_fixture.load_state()
        assert len(state["opencode_calls"]) == 1
        agent_log = (attempt_directory / "agent.log").read_bytes()
        assert [json.loads(line)["type"] for line in agent_log.splitlines()] == ["text", "tool_use"]
        assert LIVE_OUTPUT_FRAGMENT in agent_log
    finally:
        if process is not None:
            if process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.write(b"x")
                    process.stdin.flush()
                except BrokenPipeError, OSError, ValueError:
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


def test_missing_report_does_not_close_ticket(loop_fixture: LoopFixture) -> None:
    result, runs = loop_fixture.run_loop(
        "--max-iterations",
        "1",
        extra_env={"IMPLEMENT_LOOP_TEST_RESULT_MODE": "missing"},
    )

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert state["tickets"]["2"]["state"] == "open"
    assert state["tickets"]["2"]["assignees"] == [{"login": "tester"}]
    assert [event[0] for event in state["events"]] == ["assign"]
    assert len(state["opencode_calls"]) == 1
    assert len(runs) == 1
    attempt = runs[0] / "attempt-1-ticket-2"
    evidence = json.loads((attempt / "completion-evidence.json").read_text())
    assert evidence["status"] == "missing"
    assert b"did not write the required per-attempt result file" in result.stderr
    assert b"IMPLEMENT_LOOP_COMPLETE" in (attempt / "agent.log").read_bytes()


def test_valid_report_closes_ticket_after_clean_descendant_commit(
    loop_fixture: LoopFixture,
) -> None:
    result, runs = loop_fixture.run_loop(
        "--max-iterations",
        "1",
        extra_env={"OPENCODE_TEST_CONFIG": "inherited-setting"},
    )

    state = loop_fixture.load_state()
    assert result.returncode == 0, result.stderr.decode()
    assert state["tickets"]["2"]["state"] == "closed"
    assert state["tickets"]["2"]["assignees"] == [{"login": "tester"}]
    assert [event[0] for event in state["events"]] == ["assign", "close"]
    assert set(state["api_hosts"]) == {"github.com"}
    assert len(runs) == 1
    attempt = runs[0] / "attempt-1-ticket-2"
    metadata = json.loads((attempt / "attempt.json").read_text())
    completion = json.loads((attempt / "completion.json").read_text())
    assert completion["issue"] == 2
    assert completion["attempt_id"] == metadata["attempt_id"]
    call = state["opencode_calls"][0]
    assert call["result_initially_absent"]
    assert not Path(metadata["result_path"]).exists()
    assert not Path(metadata["result_path"]).is_relative_to(loop_fixture.repo)
    assert call["opencode_config"] == "inherited-setting"
    assert "branch main" in state["events"][1][2]
    assert "The wrapper did not push or open a PR" in state["events"][1][2]
    assert (attempt / "commit").read_text().strip() == loop_fixture.git("rev-parse", "HEAD")
    assert loop_fixture.git("status", "--porcelain") == ""
    assert (attempt / "agent.log").is_file()
    assert (attempt / "process.json").is_file()
    assert (attempt / "commit").is_file()
    assert not (runs[0] / "active.pid").exists()


@pytest.mark.parametrize(
    "mode",
    (
        "malformed",
        "wrong-root",
        "wrong-ticket",
        "wrong-ticket-number",
        "wrong-attempt",
        "wrong-status",
        "failed-acceptance",
        "failed-validation",
    ),
)
def test_invalid_report_never_closes_or_unassigns_ticket(
    loop_fixture: LoopFixture,
    mode: str,
) -> None:
    loop_fixture.state["make_commit"] = False
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop(
        "--max-iterations",
        "1",
        extra_env={"IMPLEMENT_LOOP_TEST_RESULT_MODE": mode},
    )

    state = loop_fixture.load_state()
    assert result.returncode != 0, result.stdout.decode()
    assert state["tickets"]["2"]["state"] == "open"
    assert state["tickets"]["2"]["assignees"] == [{"login": "tester"}]
    assert [event[0] for event in state["events"]] == ["assign"]
    attempt = runs[-1] / "attempt-1-ticket-2"
    evidence = json.loads((attempt / "completion-evidence.json").read_text())
    assert evidence["status"] != "valid"
    assert (attempt / "completion-result.json").is_file()


def test_nonzero_opencode_exit_rejects_valid_report(loop_fixture: LoopFixture) -> None:
    loop_fixture.state["agent_exit"] = 7
    loop_fixture.state["make_commit"] = False
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop("--max-iterations", "1")

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert len(state["opencode_calls"]) == 1
    assert state["tickets"]["2"]["state"] == "open"
    assert state["tickets"]["2"]["assignees"] == [{"login": "tester"}]
    assert b"OpenCode exited with status 7" in result.stderr
    attempt = runs[-1] / "attempt-1-ticket-2"
    assert f"Run records: {runs[-1].resolve()}".encode() in result.stderr
    assert f"Ticket #2 records: {attempt.resolve()}".encode() in result.stderr
    assert json.loads((attempt / "process.json").read_text())["exit_code"] == 7
    assert json.loads((attempt / "completion-evidence.json").read_text())["status"] == "valid"


def test_required_record_persistence_failure_prevents_closure(loop_fixture: LoopFixture) -> None:
    loop_fixture.state["agent_action"] = "break_records"
    loop_fixture.state["make_commit"] = False
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop("--max-iterations", "1")

    state = loop_fixture.load_state()
    attempt = runs[-1] / "attempt-1-ticket-2"
    assert result.returncode != 0
    assert b"Cannot persist required run record" in result.stderr
    assert state["tickets"]["2"]["state"] == "open"
    assert [event[0] for event in state["events"]] == ["assign"]
    attempt.chmod(0o700)


def test_agent_log_write_failure_prevents_closure(loop_fixture: LoopFixture) -> None:
    def limit_file_size() -> None:
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE, (8192, 8192))

    result, runs = loop_fixture.run_loop(
        "--max-iterations",
        "1",
        extra_env={"IMPLEMENT_LOOP_TEST_OUTPUT": "x" * 20_000},
        preexec_fn=limit_file_size,
    )

    state = loop_fixture.load_state()
    attempt = runs[-1] / "attempt-1-ticket-2"
    process = json.loads((attempt / "process.json").read_text())
    assert result.returncode != 0
    assert b"Unable to persist the complete OpenCode output log" in result.stderr
    assert not process["log_persisted"]
    assert (attempt / "agent.log").stat().st_size <= 8192
    assert state["tickets"]["2"]["state"] == "open"
    assert [event[0] for event in state["events"]] == ["assign"]


def test_missing_opencode_cli_fails_before_claim(loop_fixture: LoopFixture) -> None:
    limited_bin = loop_fixture.controlled_path_directory("python3", "git", "gh")

    result, runs = loop_fixture.run_loop(extra_env={"PATH": str(limited_bin)})

    loop_fixture.assert_missing_executable_guidance(result, "opencode")
    loop_fixture.assert_missing_executable_stopped_before_run(runs)


def test_rejected_opencode_model_stops_without_retry_or_fallback(loop_fixture: LoopFixture) -> None:
    loop_fixture.state["model_rejected_for"] = "opencode"
    loop_fixture.save_state()

    result, runs = loop_fixture.run_loop("--max-iterations", "1")

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert len(state["opencode_calls"]) == 1
    assert state["tickets"]["2"]["state"] == "open"
    assert state["tickets"]["2"]["assignees"] == [{"login": "tester"}]
    assert [event[0] for event in state["events"]] == ["assign"]
    attempt = runs[0] / "attempt-1-ticket-2"
    assert json.loads((attempt / "process.json").read_text())["exit_code"] == 1
    assert b"Model unavailable" in (attempt / "agent.log").read_bytes()
