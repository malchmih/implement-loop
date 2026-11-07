from __future__ import annotations

import json
import os
import pty
import stat
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from support import SCRIPT, LoopFixture


def _old_run(records_dir: Path, age: timedelta, suffix: str = "ABC123") -> Path:
    stamp = (datetime.now(UTC) - age).strftime("%Y%m%dT%H%M%SZ")
    run_dir = records_dir / f"run-{stamp}-{suffix}"
    run_dir.mkdir(mode=0o700)
    (run_dir / "agent.log").write_text("old run\n")
    return run_dir


def test_run_records_are_private_and_redirected_output_disables_color(
    loop_fixture: LoopFixture,
) -> None:
    result, runs = loop_fixture.run_loop(
        "--max-iterations",
        "1",
        extra_env={"FORCE_COLOR": "3", "NO_COLOR": ""},
    )

    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    call = loop_fixture.load_state()["opencode_calls"][0]
    assert call["no_color"] == "1"
    assert call["force_color"] == "0"
    run_dir = runs[0]
    attempt = run_dir / "attempt-1-ticket-2"
    assert stat.S_IMODE(run_dir.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(attempt.stat().st_mode) == 0o700
    assert stat.S_IMODE((attempt / "prompt.txt").stat().st_mode) == 0o600
    assert stat.S_IMODE((attempt / "agent.log").stat().st_mode) == 0o600
    assert not run_dir.is_relative_to(loop_fixture.repo)


def test_xdg_state_home_selects_the_standard_private_records_directory(
    loop_fixture: LoopFixture,
) -> None:
    state_home = loop_fixture.home / "custom-state"
    result, runs = loop_fixture.run_loop(
        "--max-iterations",
        "1",
        extra_env={"XDG_STATE_HOME": str(state_home)},
    )

    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    assert len(runs) == 1
    assert runs[0].parent == state_home / "implement-loop"
    assert stat.S_IMODE(runs[0].parent.stat().st_mode) == 0o700
    assert not (loop_fixture.home / ".local/state/implement-loop").exists()


def test_state_location_inside_checkout_is_rejected_without_writes(
    loop_fixture: LoopFixture,
) -> None:
    state_home = loop_fixture.repo / ".private-state"

    result, runs = loop_fixture.run_loop(extra_env={"XDG_STATE_HOME": str(state_home)})

    assert result.returncode == 1
    assert b"dedicated directory outside the target checkout" in result.stderr
    assert loop_fixture.load_state()["events"] == []
    assert loop_fixture.load_state()["opencode_calls"] == []
    assert runs == []
    assert not state_home.exists()
    assert not loop_fixture.repository_lock_path().exists()


def test_existing_shared_record_directory_is_not_made_private_in_place(
    loop_fixture: LoopFixture,
) -> None:
    state_home = loop_fixture.home / "shared-state"
    shared_dir = state_home / "implement-loop"
    shared_dir.mkdir(parents=True)
    shared_dir.chmod(0o1777)

    result, runs = loop_fixture.run_loop(extra_env={"XDG_STATE_HOME": str(state_home)})

    assert result.returncode == 1
    assert b"must already have mode 0700" in result.stderr
    assert stat.S_IMODE(shared_dir.stat().st_mode) == 0o1777
    assert loop_fixture.load_state()["events"] == []
    assert runs == []
    assert not any(shared_dir.glob("run-*"))


def test_seven_day_cleanup_preserves_active_symlinked_and_unrecognized_entries(
    loop_fixture: LoopFixture,
) -> None:
    records_dir = loop_fixture.home / ".local/state/implement-loop"
    records_dir.mkdir(parents=True, mode=0o700)

    expired = _old_run(records_dir, timedelta(days=8))
    (expired / "run.json").write_text(json.dumps({"status": "failed"}))
    boundary = _old_run(records_dir, timedelta(days=7) - timedelta(minutes=1), "BCD234")
    recent = _old_run(records_dir, timedelta(days=6), "CDE345")
    marked = _old_run(records_dir, timedelta(days=9), "DEF456")
    (marked / "active.pid").write_text("stale marker\n")
    unrecognized = records_dir / "run-not-a-timestamp-ABC123"
    unrecognized.mkdir()
    (unrecognized / "agent.log").write_text("unrelated\n")
    impossible = records_dir / "run-20260230T000000Z-ABC123"
    impossible.mkdir()
    (impossible / "agent.log").write_text("invalid timestamp\n")
    target = loop_fixture.home / "unrelated-target"
    target.mkdir()
    (target / "agent.log").write_text("symlink target\n")
    link = records_dir / "run-20000101T000000Z-ABC123"
    link.symlink_to(target, target_is_directory=True)
    legacy = loop_fixture.home / ".local/state/other-tool/run-20000101T000000Z-ABC123"
    legacy.mkdir(parents=True)
    (legacy / "agent.log").write_text("legacy\n")
    loop_fixture.state["tickets"]["2"]["assignees"] = [{"login": "tester"}]
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop()

    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    assert not expired.exists()
    for preserved in (boundary, recent, marked, unrecognized, impossible, legacy):
        assert (preserved / "agent.log").is_file()
    assert link.is_symlink()
    assert (target / "agent.log").read_text() == "symlink target\n"


def test_seven_day_expiry_is_strict_at_the_age_boundary(loop_fixture: LoopFixture) -> None:
    retention_seconds = 7 * 24 * 60 * 60
    boundary_start = int(time.time()) - retention_seconds
    records_dir = loop_fixture.home / ".local/state/implement-loop"
    records_dir.mkdir(parents=True, mode=0o700)
    stamp = datetime.fromtimestamp(boundary_start, UTC).strftime("%Y%m%dT%H%M%SZ")
    boundary_run = records_dir / f"run-{stamp}-ABC123"
    boundary_run.mkdir()
    (boundary_run / "agent.log").write_text("recent activity does not extend retention\n")
    time_support = loop_fixture.home / "time-support"
    time_support.mkdir()
    (time_support / "sitecustomize.py").write_text(
        "import os\n"
        "import time\n"
        "time.time = lambda: float(os.environ['IMPLEMENT_LOOP_TEST_FIXED_TIME'])\n"
    )
    loop_fixture.state["tickets"]["2"]["assignees"] = [{"login": "tester"}]
    loop_fixture.save_state()

    at_boundary, _ = loop_fixture.run_loop(
        extra_env={
            "PYTHONPATH": str(time_support),
            "IMPLEMENT_LOOP_TEST_FIXED_TIME": str(boundary_start + retention_seconds),
        }
    )

    assert at_boundary.returncode == 0, at_boundary.stderr.decode()
    assert boundary_run.is_dir()

    just_expired, _ = loop_fixture.run_loop(
        extra_env={
            "PYTHONPATH": str(time_support),
            "IMPLEMENT_LOOP_TEST_FIXED_TIME": str(boundary_start + retention_seconds + 0.5),
        }
    )

    assert just_expired.returncode == 0, just_expired.stderr.decode()
    assert not boundary_run.exists()


def test_cleanup_removal_failure_warns_without_blocking_run(loop_fixture: LoopFixture) -> None:
    records_dir = loop_fixture.home / ".local/state/implement-loop"
    records_dir.mkdir(parents=True, mode=0o700)
    expired = _old_run(records_dir, timedelta(days=8))
    expired.chmod(0o500)
    loop_fixture.state["tickets"]["2"]["assignees"] = [{"login": "tester"}]
    loop_fixture.save_state()

    try:
        result, _ = loop_fixture.run_loop()
    finally:
        expired.chmod(0o700)

    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    assert (expired / "agent.log").is_file()
    assert b"Cannot remove expired run" in result.stderr


def test_terminal_output_enables_color_when_no_color_is_unset(loop_fixture: LoopFixture) -> None:
    master, slave = pty.openpty()
    try:
        result = subprocess.run(
            [str(SCRIPT), "1", "--max-iterations", "1"],
            cwd=loop_fixture.repo,
            env=loop_fixture.env,
            stdout=slave,
            stderr=slave,
            timeout=30,
        )
    finally:
        os.close(slave)
        os.close(master)

    assert result.returncode == 0
    call = loop_fixture.load_state()["opencode_calls"][0]
    assert call["no_color"] is None
    assert call["force_color"] == "1"
