from __future__ import annotations

import os
import shutil
import subprocess
import sys

from support import SCRIPT, LoopFixture


def test_installed_source_command_runs_from_a_target_path_with_spaces(
    loop_fixture: LoopFixture,
) -> None:
    source = loop_fixture.copy_source_tree()
    target = loop_fixture.home / "target repository with spaces"
    loop_fixture.repo.rename(target)
    loop_fixture.repo = target

    tool_bin = loop_fixture.home / "installed tools with spaces"
    tool_dir = loop_fixture.home / "installed tool environments"
    cache_dir = loop_fixture.home / "uv cache"
    install_env = dict(
        loop_fixture.env,
        UV_TOOL_BIN_DIR=str(tool_bin),
        UV_TOOL_DIR=str(tool_dir),
        UV_CACHE_DIR=str(cache_dir),
    )
    uv = shutil.which("uv")
    assert uv is not None, "test environment needs uv"
    install = subprocess.run(
        [
            uv,
            "tool",
            "install",
            "--python",
            sys.executable,
            "--from",
            str(source),
            "implement-loop",
        ],
        cwd=source,
        env=install_env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert install.returncode == 0, install.stdout + install.stderr

    run_env = dict(install_env)
    run_env["PATH"] = str(tool_bin) + os.pathsep + run_env["PATH"]
    result = subprocess.run(
        ["implement-loop", "1", "--max-iterations", "1"],
        cwd=target,
        env=run_env,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr.decode()
    assert loop_fixture.load_state()["tickets"]["2"]["state"] == "closed"
    assert (target / "implementation-2.txt").is_file()
    assert not (source / "implementation-2.txt").exists()
    assert loop_fixture.git("status", "--porcelain", cwd=target) == ""


def test_source_command_runs_from_a_target_checkout_subdirectory(
    loop_fixture: LoopFixture,
) -> None:
    source = SCRIPT.parent
    nested = loop_fixture.repo / "nested"
    nested.mkdir()
    (nested / "tracked.txt").write_text("target checkout fixture\n")
    loop_fixture.git("add", "nested")
    loop_fixture.git("commit", "--quiet", "-m", "Add target subdirectory")

    result, _ = loop_fixture.run_loop(
        "--max-iterations",
        "1",
        cwd=nested,
        executable=SCRIPT,
    )

    assert result.returncode == 0, result.stderr.decode()
    assert loop_fixture.load_state()["tickets"]["2"]["state"] == "closed"
    assert (loop_fixture.repo / "implementation-2.txt").is_file()
    assert not (source / "implementation-2.txt").exists()
    assert loop_fixture.git("status", "--porcelain") == ""


def test_source_command_honors_config_and_progresses_returned_queue(
    loop_fixture: LoopFixture,
) -> None:
    config_home = loop_fixture.home / "configured user settings"
    config_file = config_home / "implement-loop/config.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text('model = "provider/custom-opencode"\nvariant = "high"\n')

    loop_fixture.add_ticket(3)
    loop_fixture.add_ticket(4)
    loop_fixture.state["children"] = [{"number": 3}, {"number": 2}, {"number": 4}]
    state_home = loop_fixture.home / "configured XDG state"
    loop_fixture.save_state()

    nested = loop_fixture.repo / "nested"
    nested.mkdir()
    (nested / "target.txt").write_text("target repository, not source checkout\n")
    loop_fixture.git("add", "nested")
    loop_fixture.git("commit", "--quiet", "-m", "Add target subdirectory")

    result, _ = loop_fixture.run_loop(
        "--max-iterations",
        "2",
        cwd=nested,
        executable=SCRIPT,
        extra_env={
            "XDG_CONFIG_HOME": str(config_home),
            "XDG_STATE_HOME": str(state_home),
        },
    )

    state = loop_fixture.load_state()
    assert result.returncode == 0, result.stderr.decode()
    assert [call["ticket"] for call in state["opencode_calls"]] == [3, 2]
    assert state["tickets"]["3"]["state"] == "closed"
    assert state["tickets"]["2"]["state"] == "closed"
    assert state["tickets"]["4"]["state"] == "open"
    assert state["tickets"]["4"]["assignees"] == []
    assert b"Attempt limit reached after 2 invocation(s)" in result.stdout
    assert result.stdout.count(b"Agent output") == 2
    for call in state["opencode_calls"]:
        arguments = call["args"]
        assert arguments[arguments.index("--model") + 1] == "provider/custom-opencode#high"
    assert loop_fixture.git("status", "--porcelain") == ""


def test_source_command_requires_a_new_commit_for_already_committed_work(
    loop_fixture: LoopFixture,
) -> None:
    existing_implementation = loop_fixture.repo / "implementation-2.txt"
    existing_implementation.write_text("implementation committed before approval\n")
    loop_fixture.git("add", existing_implementation.name)
    loop_fixture.git("commit", "--quiet", "-m", "Existing implementation for ticket 2")
    existing_head = loop_fixture.git("rev-parse", "HEAD")

    config_home = loop_fixture.home / "finalization user settings"
    config_file = config_home / "implement-loop/config.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text('model = "provider/custom-opencode"\nvariant = "high"\n')
    state_home = loop_fixture.home / "finalization XDG state"
    loop_fixture.add_ticket(3)
    loop_fixture.state["comments"]["2"].append(
        {"user": {"login": "reviewer"}, "body": "Human approval received."}
    )
    loop_fixture.state["make_commit"] = False
    loop_fixture.save_state()

    result, _ = loop_fixture.run_loop(
        "--max-iterations",
        "2",
        executable=SCRIPT,
        extra_env={
            "XDG_CONFIG_HOME": str(config_home),
            "XDG_STATE_HOME": str(state_home),
        },
    )

    state = loop_fixture.load_state()
    assert result.returncode != 0
    assert b"No new commit was made" in result.stderr
    assert [call["ticket"] for call in state["opencode_calls"]] == [2]
    assert state["tickets"]["2"]["state"] == "open"
    assert state["tickets"]["2"]["assignees"] == [{"login": "tester"}]
    assert state["tickets"]["3"]["state"] == "open"
    assert state["tickets"]["3"]["assignees"] == []
    assert [event[:2] for event in state["events"]] == [["assign", 2]]
    assert result.stdout.count(b"Agent output") == 1
    assert loop_fixture.git("rev-parse", "HEAD") == existing_head
    assert loop_fixture.git("status", "--porcelain") == ""
    assert existing_implementation.read_text() == "implementation committed before approval\n"
