from __future__ import annotations

import json
import subprocess

import pytest
from support import SCRIPT, LoopTestCase


def test_help_lists_only_supported_runtime_options(cli_case: LoopTestCase) -> None:
    result = subprocess.run(
        [str(SCRIPT), "--help"],
        cwd=cli_case.home,
        env=cli_case.env,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr.decode()
    assert b"--max-iterations" in result.stdout
    assert b"default: 10" in result.stdout
    for removed_option in (
        b"--config-file",
        b"--opencode-model",
        b"--opencode-variant",
        b"--records-dir",
        b"--retention-days",
        b"--keep-records-forever",
    ):
        assert removed_option not in result.stdout
    assert not (cli_case.home / ".local/state/implement-loop").exists()
    assert not cli_case.repository_lock_path().exists()
    assert cli_case.load_state()["events"] == []


def test_built_in_opencode_defaults_and_private_state_location(cli_case: LoopTestCase) -> None:
    result, runs = cli_case.run_loop(
        "--max-iterations",
        "1",
        extra_env={"OPENCODE_TEST_CONFIG": "inherited-setting"},
    )

    state = cli_case.load_state()
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    call = state["opencode_calls"][0]
    assert call["args"][call["args"].index("--model") + 1] == "github-copilot/gpt-6-luna#max"
    assert call["opencode_config"] == "inherited-setting"
    metadata = json.loads((runs[0] / "run.json").read_text())
    assert metadata["model"] == "github-copilot/gpt-6-luna"
    assert metadata["model_variant"] == "max"
    assert metadata["configuration_file"] is None
    assert metadata["max_iterations"] == 1
    assert metadata["records_directory"] == str(
        (cli_case.home / ".local/state/implement-loop").resolve()
    )
    assert metadata["retention_days"] == 7
    assert "keep_records_forever" not in metadata


def test_project_configuration_is_not_loaded_automatically(cli_case: LoopTestCase) -> None:
    (cli_case.repo / ".implement-loop.toml").write_text("[execution\n")
    cli_case.git("add", ".implement-loop.toml")
    cli_case.git("commit", "--quiet", "-m", "Add unselected project config fixture")

    result, _ = cli_case.run_loop("--max-iterations", "1")

    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    assert len(cli_case.load_state()["opencode_calls"]) == 1


@pytest.mark.parametrize(
    ("name", "contents", "diagnostic"),
    [
        ("malformed", "[model\n", b"Invalid TOML"),
        ("invalid-encoding", b"\xff", b"not valid UTF-8"),
        ("attempt-limit", "max_attempts = 1\n", b"Unknown configuration key root.max_attempts"),
        (
            "legacy-attempt-limit",
            "[execution]\nmax_attempts = 1\n",
            b"Unknown configuration key root.execution",
        ),
        (
            "per-harness",
            '[execution.harnesses.opencode]\nmodel = "old-model"\n',
            b"Unknown configuration key root.execution",
        ),
        (
            "records-path",
            '[records]\ndirectory = "/tmp/old-records"\n',
            b"Unknown configuration key root.records",
        ),
        ("invalid-model-type", "model = true\n", b"model must be a non-empty string"),
        ("invalid-variant", 'variant = "has spaces"\n', b"variant must be a non-empty string"),
        ("nul-model", 'model = "\\u0000"\n', b"without whitespace or NUL"),
    ],
)
def test_invalid_user_configuration_stops_before_claiming(
    cli_case: LoopTestCase,
    name: str,
    contents: str | bytes,
    diagnostic: bytes,
) -> None:
    config_home = cli_case.home / f"config-{name}"
    config_file = config_home / "implement-loop/config.toml"
    config_file.parent.mkdir(parents=True)
    if isinstance(contents, bytes):
        config_file.write_bytes(contents)
    else:
        config_file.write_text(contents)

    result, runs = cli_case.run_loop(extra_env={"XDG_CONFIG_HOME": str(config_home)})

    assert result.returncode == 2
    assert diagnostic in result.stderr
    assert cli_case.load_state()["events"] == []
    assert cli_case.load_state()["opencode_calls"] == []
    assert runs == []
    assert not (cli_case.home / ".local/state/implement-loop").exists()
    assert not cli_case.repository_lock_path().exists()


def test_uninspectable_user_configuration_is_not_treated_as_missing(
    cli_case: LoopTestCase,
) -> None:
    config_home = cli_case.home / "config home is a file"
    config_home.write_text("not a directory\n")

    result, runs = cli_case.run_loop(extra_env={"XDG_CONFIG_HOME": str(config_home)})

    assert result.returncode == 2
    assert b"Cannot inspect discovered configuration file" in result.stderr
    assert cli_case.load_state()["events"] == []
    assert cli_case.load_state()["opencode_calls"] == []
    assert runs == []
    assert not (cli_case.home / ".local/state/implement-loop").exists()


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("XDG_CONFIG_HOME", "relative/config"),
        ("XDG_CONFIG_HOME", "~/.config"),
        ("XDG_STATE_HOME", "relative/state"),
        ("XDG_STATE_HOME", "~/.local/state"),
    ],
)
def test_xdg_home_values_must_be_absolute(
    cli_case: LoopTestCase,
    variable: str,
    value: str,
) -> None:
    result, runs = cli_case.run_loop(extra_env={variable: value})

    assert result.returncode == 2
    assert f"{variable} must be an absolute path".encode() in result.stderr
    assert cli_case.load_state()["events"] == []
    assert cli_case.load_state()["opencode_calls"] == []
    assert runs == []
    assert not (cli_case.home / ".local/state/implement-loop").exists()


@pytest.mark.parametrize(
    "arguments",
    [
        ("--config-file", "settings.toml"),
        ("--opencode-model", "custom-model"),
        ("--opencode-variant", "high"),
        ("--records-dir", "records"),
        ("--retention-days", "14"),
        ("--keep-records-forever",),
    ],
)
def test_removed_runtime_overrides_are_rejected_before_side_effects(
    cli_case: LoopTestCase,
    arguments: tuple[str, ...],
) -> None:
    result, runs = cli_case.run_loop(*arguments)

    assert result.returncode == 2
    assert b"unrecognized arguments" in result.stderr
    assert cli_case.load_state()["events"] == []
    assert cli_case.load_state()["opencode_calls"] == []
    assert runs == []
    assert not (cli_case.home / ".local/state/implement-loop").exists()
    assert not cli_case.repository_lock_path().exists()


def test_configuration_is_recorded_before_github_discovery(cli_case: LoopTestCase) -> None:
    config_home = cli_case.home / "early-record-config"
    config_file = config_home / "implement-loop/config.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text('model = "configured-model"\nvariant = "high"\n')
    cli_case.state["repository_view_failure"] = True
    cli_case.save_state()

    result, runs = cli_case.run_loop(extra_env={"XDG_CONFIG_HOME": str(config_home)})

    assert result.returncode != 0
    assert b"simulated repository discovery failure" in result.stderr
    assert cli_case.load_state()["events"] == []
    assert cli_case.load_state()["opencode_calls"] == []
    assert len(runs) == 1
    metadata = json.loads((runs[0] / "run.json").read_text())
    assert metadata["configuration_file"] == str(config_file)
    assert metadata["model"] == "configured-model"
    assert metadata["model_variant"] == "high"


def test_missing_git_fails_before_checkout_discovery_or_side_effects(
    cli_case: LoopTestCase,
) -> None:
    minimal_bin = cli_case.controlled_path_directory("python3", "gh", "opencode")

    result, runs = cli_case.run_loop(extra_env={"PATH": str(minimal_bin)})

    assert result.returncode == 1
    assert b"'git' was not found on PATH" in result.stderr
    assert cli_case.load_state()["events"] == []
    assert cli_case.load_state()["opencode_calls"] == []
    assert runs == []
    assert not (cli_case.home / ".local/state/implement-loop").exists()
    assert not cli_case.repository_lock_path().exists()


def test_missing_gh_fails_before_record_creation_or_side_effects(cli_case: LoopTestCase) -> None:
    minimal_bin = cli_case.controlled_path_directory("python3", "git", "opencode")

    result, runs = cli_case.run_loop(extra_env={"PATH": str(minimal_bin)})

    assert result.returncode == 1
    assert b"'gh' was not found on PATH" in result.stderr
    assert cli_case.load_state()["events"] == []
    assert cli_case.load_state()["opencode_calls"] == []
    assert runs == []
    assert not (cli_case.home / ".local/state/implement-loop").exists()
    assert not cli_case.repository_lock_path().exists()


def test_removed_harness_option_is_rejected_before_side_effects(cli_case: LoopTestCase) -> None:
    result, runs = cli_case.run_loop("--harness", "copilot")

    assert result.returncode == 2
    assert b"unrecognized arguments: --harness copilot" in result.stderr
    assert cli_case.load_state()["events"] == []
    assert cli_case.load_state()["opencode_calls"] == []
    assert runs == []
    assert not (cli_case.home / ".local/state/implement-loop").exists()


def test_invalid_attempt_limit_is_rejected_before_side_effects(cli_case: LoopTestCase) -> None:
    result, runs = cli_case.run_loop("--max-iterations", "0")

    assert result.returncode == 2
    assert b"positive integer" in result.stderr
    assert cli_case.load_state()["events"] == []
    assert runs == []
    assert not (cli_case.home / ".local/state/implement-loop").exists()
