from __future__ import annotations

import os
import shutil
import subprocess
import sys

from support import LoopFixture


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
        ["implement-loop", "--help"],
        cwd=target,
        env=run_env,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    assert b"usage:" in result.stdout.lower()
    assert b"--max-iterations" in result.stdout
    assert loop_fixture.load_state()["events"] == []
    assert loop_fixture.load_state()["opencode_calls"] == []
    assert loop_fixture.git("status", "--porcelain", cwd=target) == ""
