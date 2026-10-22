from __future__ import annotations

import json
import os
import pty
import resource
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "implement-loop"
REPOSITORY_URL = "https://api.github.com/repos/test/repo"

FAKE_TOOL = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import subprocess
import sys

home = Path(os.environ["HOME"])
state_path = Path(os.environ["IMPLEMENT_LOOP_TEST_STATE"])
state = json.loads(state_path.read_text())
args = sys.argv[1:]

def save_state():
    state_path.write_text(json.dumps(state))

def ticket_for(number):
    return state["tickets"][str(number)]

tool_name = Path(sys.argv[0]).name
if tool_name in ("copilot", "opencode"):
    if tool_name == "opencode":
        prompt = args[-1] if args else ""
    else:
        prompt = args[args.index("-p") + 1] if "-p" in args else ""
    ticket_number = int(
        re.search(r"(?:/implement|@implement) Implement only ticket #(\d+)", prompt).group(1)
    )
    attempt_id = next(
        line.removeprefix("Attempt ID: ").strip()
        for line in prompt.splitlines() if line.startswith("Attempt ID: ")
    )
    result_path = next(
        line.strip() for line in prompt.splitlines()
        if line.strip().endswith("/completion-result.json")
    )
    call = {
        "args": args,
        "prompt": prompt,
        "attempt_id": attempt_id,
        "ticket": ticket_number,
        "result_initially_absent": not Path(result_path).exists(),
        "no_color": os.environ.get("NO_COLOR"),
        "force_color": os.environ.get("FORCE_COLOR"),
        "copilot_config": os.environ.get("COPILOT_TEST_CONFIG"),
        "opencode_config": os.environ.get("OPENCODE_TEST_CONFIG"),
    }
    state.setdefault(f"{tool_name}_calls", []).append(call)
    save_state()

    if state.get("model_rejected_for") == tool_name:
        sys.stdout.write("Model unavailable: explicitly selected GPT-6 Luna\n")
        sys.exit(1)

    make_commit = state.get("make_commit_by_ticket", {}).get(
        str(ticket_number), state.get("make_commit", True)
    )
    if make_commit:
        output_path = Path(f"implementation-{ticket_number}.txt")
        output_path.write_text(f"implemented ticket {ticket_number} in attempt {attempt_id}\n")
        subprocess.run(["git", "add", output_path.name], check=True)
        subprocess.run([
            "git", "commit", "--quiet", "-m",
            f"Implement ticket {ticket_number}\n\n"
            "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
        ], check=True)

    action = state.get("agent_action_by_ticket", {}).get(
        str(ticket_number), state.get("agent_action")
    )
    if action == "dirty":
        Path("unfinished.txt").write_text("uncommitted change\n")
    elif action == "branch":
        subprocess.run(["git", "switch", "--quiet", "-c", "agent-branch"], check=True)
    elif action == "rewrite_history":
        branch = subprocess.check_output(["git", "branch", "--show-current"], text=True).strip()
        root = subprocess.check_output(
            ["git", "rev-list", "--max-parents=0", "HEAD"], text=True
        ).strip()
        subprocess.run(["git", "reset", "--hard", root], check=True, stdout=subprocess.DEVNULL)
        Path("rewritten-history.txt").write_text("unrelated commit\n")
        subprocess.run(["git", "add", "rewritten-history.txt"], check=True)
        subprocess.run(["git", "commit", "--quiet", "-m", "Rewrite fixture history"], check=True)
        current_branch = subprocess.check_output(
            ["git", "branch", "--show-current"], text=True
        ).strip()
        if current_branch != branch:
            sys.exit("Unexpected test branch")
    elif action == "change_owner":
        ticket_for(ticket_number)["assignees"].append({"login": "other"})
        save_state()
    elif action == "add_blocker":
        ticket_for(ticket_number)["issue_dependencies_summary"]["blocked_by"] = 1
        save_state()
    elif action == "remove_child":
        state["children"] = [
            child for child in state["children"] if child.get("number") != ticket_number
        ]
        save_state()
    elif action == "close_parent":
        state["spec"]["state"] = "closed"
        save_state()
    elif action == "break_records":
        attempt_dir = next(
            (home / ".local/state/implement-loop").glob(
                f"run-*/attempt-*-ticket-{ticket_number}"
            )
        )
        attempt_dir.chmod(0o500)

    mode = os.environ.get("IMPLEMENT_LOOP_TEST_RESULT_MODE", "valid")
    if mode != "missing":
        result = {
            "issue": ticket_number,
            "attempt_id": attempt_id,
            "status": "completed",
            "acceptance_criteria": "passed",
            "validation": "passed",
        }
        if mode == "malformed":
            Path(result_path).write_text("{not-json")
        elif mode == "wrong-root":
            Path(result_path).write_text(json.dumps([result]))
        else:
            if mode == "wrong-ticket":
                result["issue"] = "not-a-number"
            elif mode == "wrong-ticket-number":
                result["issue"] = ticket_number + 1
            elif mode == "wrong-attempt":
                result["attempt_id"] = "stale-attempt"
            elif mode == "wrong-status":
                result["status"] = "failed"
            elif mode == "failed-acceptance":
                result["acceptance_criteria"] = "failed"
            elif mode == "failed-validation":
                result["validation"] = "failed"
            Path(result_path).write_text(json.dumps(result))

    output = os.environ.get(
        "IMPLEMENT_LOOP_TEST_OUTPUT",
        state.get(
            "agent_output",
            'IMPLEMENT_LOOP_COMPLETE {"issue":2,"status":"completed",'
            '"acceptance_criteria":"passed","validation":"passed"}',
        ),
    )
    sys.stdout.write("Agent output\n" + output + "\n")
    sys.exit(int(state.get("agent_exit", 0)))

if args[:2] == ["repo", "view"]:
    common = Path(subprocess.check_output(
        ["git", "rev-parse", "--git-common-dir"], text=True
    ).strip())
    if not common.is_absolute():
        common = Path.cwd() / common
    lock = common.resolve() / "implement-loop.lock/pid"
    if not lock.is_file() or lock.read_text().strip() != str(os.getppid()):
        sys.exit("Missing implement-loop lock")
    state_home = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
    records_dir = Path(state.get("records_dir", state_home / "implement-loop"))
    markers = records_dir.glob("run-*/active.pid")
    if not any(path.read_text().strip() == str(os.getppid()) for path in markers):
        sys.exit("Missing live run marker")
    if state.get("repository_view_failure"):
        sys.exit("simulated repository discovery failure")
    if "GH_REPO" in os.environ:
        state["repo_view_saw_override"] = os.environ["GH_REPO"]
    result = {"nameWithOwner": "test/repo", "url": "https://github.com/test/repo"}
elif args[:2] == ["issue", "edit"]:
    number = int(args[2])
    ticket = ticket_for(number)
    login = args[args.index("--add-assignee") + 1]
    ticket["assignees"].append({"login": login})
    if state.get("claim_conflict"):
        ticket["assignees"].append({"login": "other"})
    if state.get("claim_blocker"):
        ticket["issue_dependencies_summary"]["blocked_by"] = 1
    if state.get("claim_close"):
        ticket["state"] = "closed"
    if state.get("claim_remove_child"):
        state["children"] = [child for child in state["children"] if child["number"] != number]
    state.setdefault("events", []).append(["assign", number, login])
    save_state()
    sys.exit(0)
elif args[:2] == ["issue", "close"]:
    number = int(args[2])
    if state.get("close_failure"):
        sys.exit("simulated GitHub closure failure")
    ticket_for(number)["state"] = "closed"
    comment = args[args.index("--comment") + 1]
    state.setdefault("events", []).append(["close", number, comment])
    save_state()
    sys.exit(0)
elif args[:1] == ["api"]:
    if "--hostname" not in args:
        sys.exit("GitHub API call omitted the resolved hostname")
    state.setdefault("api_hosts", []).append(args[args.index("--hostname") + 1])
    endpoint = args[-1]
    if endpoint == "user":
        result = {"login": state["login"]}
    elif endpoint.endswith("/comments?per_page=100"):
        number = endpoint.split("/issues/")[1].split("/")[0]
        comments = state.get("comments", {}).get(number, [])
        page_size = state.get("comment_page_size", 100)
        result = [comments[index:index + page_size] for index in range(0, len(comments), page_size)]
        if not result:
            result = [[]]
    elif endpoint.endswith("/sub_issues?per_page=100"):
        children = []
        for child in state["children"]:
            number = child.get("number")
            ticket = ticket_for(number) if str(number) in state["tickets"] else {}
            children.append({
                "number": number,
                "state": ticket.get("state", child.get("state")),
                "repository_url": child.get("repository_url", state["repository_url"]),
                "pull_request": child.get("pull_request"),
            })
        page_size = state.get("sub_issue_page_size", 100)
        result = [children[index:index + page_size] for index in range(0, len(children), page_size)]
        if not result:
            result = [[]]
    else:
        match = re.search(r"/issues/(\d+)$", endpoint)
        if not match:
            sys.exit("Unexpected API endpoint: " + endpoint)
        number = int(match.group(1))
        result = state["spec"] if number == state["spec"]["number"] else ticket_for(number)
else:
    sys.exit("Unexpected gh arguments: " + repr(args))

save_state()
print(json.dumps(result))
"""


class LoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="implement-loop-test-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo = self.home / "repo"
        self.repo.mkdir()
        self.bin = self.home / "bin"
        self.bin.mkdir()
        for tool in ("gh", "copilot", "opencode"):
            path = self.bin / tool
            path.write_text(FAKE_TOOL)
            path.chmod(0o700)

        self.state_path = self.home / "github-state.json"
        self.state = self.default_state()
        self.save_state()
        self.env = dict(os.environ, HOME=str(self.home))
        self.env.pop("GH_REPO", None)
        self.env.pop("XDG_CONFIG_HOME", None)
        self.env.pop("XDG_STATE_HOME", None)
        self.env.pop("NO_COLOR", None)
        self.env.pop("FORCE_COLOR", None)
        self.env["PATH"] = str(self.bin) + os.pathsep + os.environ["PATH"]
        self.env["IMPLEMENT_LOOP_TEST_STATE"] = str(self.state_path)

        self.git("init", "--quiet")
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        self.git("config", "user.name", "Implement Loop Test")
        self.git("config", "user.email", "implement-loop-test@example.invalid")
        self.git("commit", "--quiet", "--allow-empty", "-m", "Initial fixture")

    def default_state(self) -> dict[str, object]:
        ticket = {
            "number": 2,
            "state": "open",
            "repository_url": REPOSITORY_URL,
            "assignees": [],
            "issue_dependencies_summary": {"blocked_by": 0},
            "body": "Ticket fixture: implement the requested behavior.",
            "labels": [{"name": "ignored-label"}],
        }
        spec = {
            "number": 1,
            "state": "open",
            "repository_url": REPOSITORY_URL,
            "body": "Spec fixture with shared requirements.",
        }
        return {
            "login": "tester",
            "repository_url": REPOSITORY_URL,
            "spec": spec,
            "children": [{"number": 2}],
            "tickets": {"2": ticket},
            "comments": {
                "1": [{"user": {"login": "owner"}, "body": "Parent comment fixture."}],
                "2": [
                    {"user": {"login": "owner"}, "body": "Ticket comment fixture."},
                    {"user": {"login": "reviewer"}, "body": "Second paginated ticket comment."},
                ],
            },
            "comment_page_size": 1,
            "events": [],
            "copilot_calls": [],
            "opencode_calls": [],
            "agent_output": (
                'IMPLEMENT_LOOP_COMPLETE {"issue":2,"status":"completed",'
                '"acceptance_criteria":"passed","validation":"passed"}'
            ),
        }

    def save_state(self) -> None:
        self.state_path.write_text(json.dumps(self.state))

    def add_ticket(
        self,
        number: int,
        *,
        blocked: int = 0,
        assignees: list[str] | None = None,
        labels: list[str] | None = None,
    ) -> None:
        self.state["tickets"][str(number)] = {
            "number": number,
            "state": "open",
            "repository_url": REPOSITORY_URL,
            "assignees": [{"login": login} for login in (assignees or [])],
            "issue_dependencies_summary": {"blocked_by": blocked},
            "body": f"Ticket #{number} fixture.",
            "labels": [{"name": label} for label in (labels or [])],
        }
        self.state["children"].append({"number": number})

    def git(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or self.repo,
            env=self.env if hasattr(self, "env") else os.environ,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def run_loop(
        self,
        *args: str,
        harness: str | None = "copilot",
        extra_env: dict[str, str] | None = None,
        cwd: Path | None = None,
        preexec_fn: Callable[[], None] | None = None,
    ) -> tuple[subprocess.CompletedProcess[bytes], list[Path]]:
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        state_home = Path(env.get("XDG_STATE_HOME", self.home / ".local/state"))
        state_dir = state_home / "implement-loop"
        existing_runs = set(state_dir.glob("run-*")) if state_dir.exists() else set()
        command = [str(SCRIPT), "1"]
        if harness is not None:
            command.extend(("--harness", harness))
        command.extend(args)
        result = subprocess.run(
            command,
            cwd=cwd or self.repo,
            env=env,
            capture_output=True,
            timeout=30,
            preexec_fn=preexec_fn,
        )
        runs = (
            sorted(path for path in state_dir.glob("run-*") if path not in existing_runs)
            if state_dir.exists()
            else []
        )
        return result, runs

    def load_state(self) -> dict[str, object]:
        return json.loads(self.state_path.read_text())

    def test_help_works_without_locking_or_contacting_services(self) -> None:
        result = subprocess.run(
            [str(SCRIPT), "--help"],
            cwd=self.home,
            env=self.env,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b"implement-loop", result.stdout)
        self.assertIn(b"--max-iterations", result.stdout)
        self.assertIn(b"--harness", result.stdout)
        self.assertIn(b"--allow-no-new-commit TICKET_NUMBER", result.stdout)
        self.assertIn(b"--config-file", result.stdout)
        self.assertIn(b"--opencode-model", result.stdout)
        self.assertIn(b"--copilot-reasoning-effort", result.stdout)
        self.assertIn(b"--records-dir", result.stdout)
        self.assertIn(b"--retention-days", result.stdout)
        self.assertIn(b"--keep-records-forever", result.stdout)
        self.assertIn(b"opencode", result.stdout)
        self.assertIn(b"copilot", result.stdout)
        self.assertEqual(self.load_state()["events"], [])
        self.assertFalse((self.repo / ".git/implement-loop.lock").exists())
        self.assertFalse((self.home / ".local/state/implement-loop").exists())

    def test_default_selection_uses_opencode_and_native_skill_reference(self) -> None:
        result, runs = self.run_loop(
            "--max-iterations",
            "1",
            harness=None,
            extra_env={"OPENCODE_TEST_CONFIG": "inherited-setting"},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["copilot_calls"], [])
        self.assertEqual(len(state["opencode_calls"]), 1)
        call = state["opencode_calls"][0]
        args = call["args"]
        self.assertEqual(args[0], "run")
        self.assertIn("--standalone", args)
        self.assertIn("--auto", args)
        self.assertEqual(args[args.index("--model") + 1], "github-copilot/gpt-6-luna#max")
        self.assertNotIn("--continue", args)
        self.assertNotIn("--session", args)
        self.assertTrue(call["result_initially_absent"])
        self.assertTrue(call["prompt"].startswith("@implement Implement only ticket #2"))
        self.assertEqual(call["opencode_config"], "inherited-setting")
        self.assertEqual(call["no_color"], "1")
        self.assertEqual(call["force_color"], "0")
        self.assertEqual(len(runs), 1)
        attempt = runs[0] / "attempt-1-ticket-2"
        attempt_metadata = json.loads((attempt / "attempt.json").read_text())
        self.assertEqual(attempt_metadata["harness"], "opencode")
        self.assertEqual(attempt_metadata["attempt_id"], call["attempt_id"])
        self.assertIn(attempt_metadata["result_path"], call["prompt"])
        self.assertFalse(Path(attempt_metadata["result_path"]).exists())
        completion = json.loads((attempt / "completion.json").read_text())
        self.assertEqual(completion["attempt_id"], call["attempt_id"])
        run_metadata = json.loads((runs[0] / "run.json").read_text())
        self.assertEqual(run_metadata["model"], "github-copilot/gpt-6-luna")
        self.assertEqual(run_metadata["model_variant"], "max")
        self.assertIsNone(run_metadata["reasoning_effort"])
        self.assertIsNone(run_metadata["configuration_file"])
        self.assertEqual(
            run_metadata["records_directory"],
            str((self.home / ".local/state/implement-loop").resolve()),
        )
        self.assertEqual(run_metadata["retention_days"], 7)
        self.assertFalse(run_metadata["keep_records_forever"])
        self.assertTrue((attempt / "agent.log").is_file())
        self.assertIn("successful OpenCode exit", state["events"][1][2])

    def test_project_configuration_is_not_loaded_automatically(self) -> None:
        (self.repo / ".implement-loop.toml").write_text("[execution\nharness = 'copilot'\n")
        self.git("add", ".implement-loop.toml")
        self.git("commit", "--quiet", "-m", "Add unselected project config fixture")

        result, _ = self.run_loop("--max-iterations", "1", harness=None)

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(state["opencode_calls"]), 1)
        self.assertEqual(state["copilot_calls"], [])

    def test_user_config_selects_harness_models_and_attempt_limit(self) -> None:
        self.add_ticket(3)
        self.save_state()
        config_home = self.home / "custom-config"
        config_file = config_home / "implement-loop/config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            '[execution]\nharness = "copilot"\nmax_attempts = 1\n\n'
            "[execution.harnesses.copilot]\n"
            'model = "custom-copilot-model"\nreasoning_effort = "high"\n'
        )

        result, runs = self.run_loop(
            harness=None,
            extra_env={"XDG_CONFIG_HOME": str(config_home)},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(state["copilot_calls"]), 1)
        self.assertEqual(state["opencode_calls"], [])
        self.assertEqual(
            state["copilot_calls"][0]["args"][
                state["copilot_calls"][0]["args"].index("--model") + 1
            ],
            "custom-copilot-model",
        )
        self.assertEqual(
            state["copilot_calls"][0]["args"][
                state["copilot_calls"][0]["args"].index("--reasoning-effort") + 1
            ],
            "high",
        )
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        self.assertEqual(len(runs), 1)

    def test_conventional_user_config_path_is_discovered(self) -> None:
        config_file = self.home / ".config/implement-loop/config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text('[execution]\nharness = "copilot"\n')

        result, runs = self.run_loop(harness=None)

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(state["copilot_calls"]), 1)
        self.assertEqual(state["opencode_calls"], [])
        metadata = json.loads((runs[0] / "run.json").read_text())
        self.assertEqual(metadata["configuration_file"], str(config_file))

    def test_user_config_overrides_opencode_model_and_variant(self) -> None:
        config_home = self.home / "opencode-config"
        config_file = config_home / "implement-loop/config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            "[execution.harnesses.opencode]\n"
            'model = "custom-provider/custom-model"\nvariant = "high"\n'
        )

        result, runs = self.run_loop(
            "--max-iterations",
            "1",
            harness=None,
            extra_env={"XDG_CONFIG_HOME": str(config_home)},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["copilot_calls"], [])
        self.assertEqual(len(state["opencode_calls"]), 1)
        args = state["opencode_calls"][0]["args"]
        self.assertEqual(args[args.index("--model") + 1], "custom-provider/custom-model#high")
        metadata = json.loads((runs[0] / "run.json").read_text())
        self.assertEqual(metadata["model"], "custom-provider/custom-model")
        self.assertEqual(metadata["model_variant"], "high")

    def test_cli_model_and_reasoning_values_override_user_config(self) -> None:
        config_home = self.home / "cli-model-config"
        config_file = config_home / "implement-loop/config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            '[execution]\nharness = "copilot"\n\n'
            "[execution.harnesses.copilot]\n"
            'model = "configured-model"\nreasoning_effort = "low"\n'
        )

        result, _ = self.run_loop(
            "--max-iterations",
            "1",
            "--copilot-model",
            "cli-selected-model",
            "--copilot-reasoning-effort",
            "high",
            harness=None,
            extra_env={"XDG_CONFIG_HOME": str(config_home)},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        args = state["copilot_calls"][0]["args"]
        self.assertEqual(args[args.index("--model") + 1], "cli-selected-model")
        self.assertEqual(args[args.index("--reasoning-effort") + 1], "high")

    def test_cli_opencode_model_and_variant_override_user_config(self) -> None:
        config_home = self.home / "cli-opencode-config"
        config_file = config_home / "implement-loop/config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            "[execution.harnesses.opencode]\n"
            'model = "configured-provider/configured-model"\nvariant = "low"\n'
        )

        result, _ = self.run_loop(
            "--max-iterations",
            "1",
            "--opencode-model",
            "cli-provider/cli-model",
            "--opencode-variant",
            "medium",
            harness=None,
            extra_env={"XDG_CONFIG_HOME": str(config_home)},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        args = state["opencode_calls"][0]["args"]
        self.assertEqual(args[args.index("--model") + 1], "cli-provider/cli-model#medium")

    def test_explicit_config_replaces_discovery_and_cli_values_take_precedence(self) -> None:
        self.add_ticket(3)
        self.save_state()
        config_home = self.home / "custom-config"
        discovered_file = config_home / "implement-loop/config.toml"
        discovered_file.parent.mkdir(parents=True)
        discovered_file.write_text(
            '[execution]\nharness = "copilot"\nmax_attempts = 5\n\n'
            '[execution.harnesses.copilot]\nmodel = "discovered-model"\n'
            'reasoning_effort = "low"\n'
        )
        explicit_file = self.home / "explicit.toml"
        explicit_file.write_text(
            '[execution]\nharness = "opencode"\nmax_attempts = 2\n\n'
            '[execution.harnesses.copilot]\nmodel = "explicit-model"\n'
            'reasoning_effort = "high"\n'
        )

        result, runs = self.run_loop(
            "--config-file",
            str(explicit_file),
            "--harness",
            "copilot",
            "--max-iterations",
            "1",
            harness=None,
            extra_env={"XDG_CONFIG_HOME": str(config_home)},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(state["copilot_calls"]), 1)
        self.assertEqual(state["opencode_calls"], [])
        call_args = state["copilot_calls"][0]["args"]
        self.assertEqual(call_args[call_args.index("--model") + 1], "explicit-model")
        self.assertEqual(call_args[call_args.index("--reasoning-effort") + 1], "high")
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        run_metadata = json.loads((runs[0] / "run.json").read_text())
        self.assertEqual(run_metadata["configuration_file"], str(explicit_file.resolve()))

    def test_invalid_configuration_fails_before_claiming_or_invoking_a_harness(self) -> None:
        cases = (
            ("malformed", "[execution\nharness = 'copilot'\n", None, b"Invalid TOML"),
            ("invalid-encoding", b"\xff", None, b"not valid UTF-8"),
            (
                "unknown-key",
                '[execution.harnesses.opencode]\nreasoning_effort = "max"\n',
                None,
                b"Unknown configuration key",
            ),
            (
                "non-persistent-commit-exception",
                "[execution]\nallow_no_new_commit_ticket = 2\n",
                None,
                b"Unknown configuration key execution.allow_no_new_commit_ticket",
            ),
            (
                "invalid-type",
                "[execution]\nmax_attempts = true\n",
                None,
                b"execution.max_attempts must be a positive integer",
            ),
            (
                "invalid-reasoning-effort",
                '[execution]\nharness = "copilot"\n\n'
                '[execution.harnesses.copilot]\nreasoning_effort = "bogus"\n',
                None,
                b"reasoning_effort must be one of",
            ),
            (
                "nul-model",
                '[execution.harnesses.opencode]\nmodel = "\\u0000"\n',
                None,
                b"without whitespace or NUL",
            ),
            ("missing-explicit", None, "missing.toml", b"Cannot read explicitly selected"),
        )
        for name, contents, explicit_path, diagnostic in cases:
            with self.subTest(name=name):
                state_dir = self.home / ".local/state/implement-loop"
                if state_dir.exists():
                    shutil.rmtree(state_dir)
                self.state = self.default_state()
                self.save_state()
                config_home = self.home / f"config-{name}"
                args: tuple[str, ...] = ()
                if explicit_path is None:
                    config_file = config_home / "implement-loop/config.toml"
                    config_file.parent.mkdir(parents=True)
                    assert contents is not None
                    if isinstance(contents, bytes):
                        config_file.write_bytes(contents)
                    else:
                        config_file.write_text(contents)
                else:
                    args = ("--config-file", str(config_home / explicit_path))

                result, runs = self.run_loop(
                    *args,
                    harness=None,
                    extra_env={"XDG_CONFIG_HOME": str(config_home)},
                )

                state = self.load_state()
                self.assertEqual(result.returncode, 2, result.stderr.decode())
                self.assertIn(diagnostic, result.stderr)
                self.assertEqual(state["events"], [])
                self.assertEqual(state["opencode_calls"], [])
                self.assertEqual(state["copilot_calls"], [])
                self.assertEqual(runs, [])
                self.assertFalse(state_dir.exists())
                self.assertFalse((self.repo / ".git/implement-loop.lock").exists())

    def test_run_records_selected_configuration_before_github_discovery(self) -> None:
        config_home = self.home / "early-record-config"
        config_file = config_home / "implement-loop/config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(
            '[execution]\nharness = "copilot"\nmax_attempts = 2\n\n'
            "[execution.harnesses.copilot]\n"
            'model = "configured-model"\nreasoning_effort = "high"\n'
        )
        self.state["repository_view_failure"] = True
        self.save_state()

        result, runs = self.run_loop(
            harness=None,
            extra_env={"XDG_CONFIG_HOME": str(config_home)},
        )

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"simulated repository discovery failure", result.stderr)
        self.assertEqual(len(runs), 1)
        self.assertEqual(state["events"], [])
        self.assertEqual(state["copilot_calls"], [])
        self.assertEqual(state["opencode_calls"], [])
        run_metadata = json.loads((runs[0] / "run.json").read_text())
        self.assertEqual(run_metadata["configuration_file"], str(config_file.resolve()))
        self.assertEqual(run_metadata["harness"], "copilot")
        self.assertEqual(run_metadata["model"], "configured-model")
        self.assertEqual(run_metadata["reasoning_effort"], "high")

    def test_invalid_cli_reasoning_effort_is_rejected_before_side_effects(self) -> None:
        result, runs = self.run_loop(
            "--harness",
            "copilot",
            "--copilot-reasoning-effort",
            "bogus",
            harness=None,
        )

        self.assertEqual(result.returncode, 2, result.stderr.decode())
        self.assertIn(b"reasoning effort must be one of", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertEqual(self.load_state()["copilot_calls"], [])
        self.assertEqual(runs, [])
        self.assertFalse((self.home / ".local/state/implement-loop").exists())
        self.assertFalse((self.repo / ".git/implement-loop.lock").exists())

    def test_empty_configuration_and_record_paths_are_rejected_early(self) -> None:
        for option in ("--config-file", "--records-dir"):
            with self.subTest(option=option):
                self.state = self.default_state()
                self.save_state()

                result, runs = self.run_loop(option, "", harness=None)

                self.assertEqual(result.returncode, 2, result.stderr.decode())
                self.assertIn(b"non-empty path", result.stderr)
                self.assertEqual(self.load_state()["events"], [])
                self.assertEqual(self.load_state()["opencode_calls"], [])
                self.assertEqual(self.load_state()["copilot_calls"], [])
                self.assertEqual(runs, [])
                self.assertFalse((self.home / ".local/state/implement-loop").exists())
                self.assertFalse((self.repo / ".git/implement-loop.lock").exists())

    def test_explicit_copilot_selection_preserves_native_skill_and_model(self) -> None:
        result, runs = self.run_loop("--max-iterations", "1", harness="copilot")

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["opencode_calls"], [])
        self.assertEqual(len(state["copilot_calls"]), 1)
        call = state["copilot_calls"][0]
        args = call["args"]
        self.assertIn("--trust-folder", args)
        self.assertEqual(args[args.index("--model") + 1], "gpt-6-luna")
        self.assertEqual(args[args.index("--reasoning-effort") + 1], "max")
        self.assertNotIn("--continue", args)
        self.assertNotIn("--resume", args)
        self.assertTrue(call["prompt"].startswith("/implement Implement only ticket #2"))
        self.assertEqual(len(runs), 1)
        attempt = runs[0] / "attempt-1-ticket-2"
        metadata = json.loads((attempt / "attempt.json").read_text())
        self.assertEqual(metadata["harness"], "copilot")
        self.assertEqual(metadata["model"], "gpt-6-luna")
        self.assertEqual(metadata["reasoning_effort"], "max")

    def test_unknown_harness_is_rejected_before_side_effects(self) -> None:
        result, _ = self.run_loop("--harness", "unknown", harness=None)

        self.assertEqual(result.returncode, 2)
        self.assertIn(b"invalid choice: 'unknown'", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertEqual(self.load_state()["opencode_calls"], [])
        self.assertEqual(self.load_state()["copilot_calls"], [])
        self.assertFalse((self.home / ".local/state/implement-loop").exists())

    def test_opencode_attempts_are_private_and_use_fresh_sessions(self) -> None:
        self.add_ticket(3)
        self.save_state()

        result, runs = self.run_loop("--max-iterations", "2", harness=None)

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([call["ticket"] for call in state["opencode_calls"]], [2, 3])
        self.assertEqual(state["copilot_calls"], [])
        self.assertEqual(
            len({call["attempt_id"] for call in state["opencode_calls"]}),
            2,
        )
        for call in state["opencode_calls"]:
            args = call["args"]
            self.assertEqual(args[0], "run")
            self.assertIn("--standalone", args)
            self.assertNotIn("--continue", args)
            self.assertNotIn("--session", args)
            self.assertNotIn("--fork", args)
            self.assertTrue(call["result_initially_absent"])
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["3"]["state"], "closed")
        self.assertTrue((runs[0] / "attempt-1-ticket-2/agent.log").is_file())
        self.assertTrue((runs[0] / "attempt-2-ticket-3/agent.log").is_file())

    def test_transcript_marker_without_result_file_does_not_close_ticket(self) -> None:
        for harness, selected_calls, unselected_calls in (
            (None, "opencode_calls", "copilot_calls"),
            ("copilot", "copilot_calls", "opencode_calls"),
        ):
            with self.subTest(harness=harness or "default"):
                self.state = self.default_state()
                self.save_state()
                result, runs = self.run_loop(
                    "--max-iterations",
                    "1",
                    harness=harness,
                    extra_env={"IMPLEMENT_LOOP_TEST_RESULT_MODE": "missing"},
                )
                self.assertNotEqual(result.returncode, 0)
                state = self.load_state()
                self.assertEqual(state["tickets"]["2"]["state"], "open")
                self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
                self.assertEqual([event[0] for event in state["events"]], ["assign"])
                self.assertEqual(len(state[selected_calls]), 1)
                self.assertEqual(state[unselected_calls], [])
                self.assertEqual(len(runs), 1)
                attempt = runs[0] / "attempt-1-ticket-2"
                self.assertEqual(
                    json.loads((attempt / "completion-evidence.json").read_text())["status"],
                    "missing",
                )
                self.assertIn(b"did not write the required per-attempt result file", result.stderr)
                self.assertIn(b"IMPLEMENT_LOOP_COMPLETE", (attempt / "agent.log").read_bytes())

    def test_result_file_closes_ticket_after_clean_descendant_commit(self) -> None:
        result, runs = self.run_loop(
            "--max-iterations",
            "1",
            extra_env={"COPILOT_TEST_CONFIG": "inherited-setting"},
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        state = self.load_state()
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
        self.assertEqual([event[0] for event in state["events"]], ["assign", "close"])
        self.assertEqual(set(state["api_hosts"]), {"github.com"})
        self.assertEqual(len(runs), 1)
        attempt = runs[0] / "attempt-1-ticket-2"
        metadata = json.loads((attempt / "attempt.json").read_text())
        completion = json.loads((attempt / "completion.json").read_text())
        self.assertEqual(completion["issue"], 2)
        self.assertEqual(completion["attempt_id"], metadata["attempt_id"])
        self.assertTrue(state["copilot_calls"][0]["result_initially_absent"])
        self.assertFalse(Path(metadata["result_path"]).exists())
        self.assertFalse(Path(metadata["result_path"]).is_relative_to(self.repo))
        self.assertEqual(state["copilot_calls"][0]["copilot_config"], "inherited-setting")
        call_args = state["copilot_calls"][0]["args"]
        self.assertEqual(call_args[call_args.index("--model") + 1], "gpt-6-luna")
        self.assertEqual(call_args[call_args.index("--reasoning-effort") + 1], "max")
        self.assertNotIn("--deny-tool", call_args)
        prompt = state["copilot_calls"][0]["prompt"]
        self.assertIn("/implement Implement only ticket #2", prompt)
        self.assertIn("Spec fixture with shared requirements", prompt)
        self.assertIn("Parent comment fixture.", prompt)
        self.assertIn("Ticket comment fixture.", prompt)
        self.assertIn("Second paginated ticket comment.", prompt)
        self.assertIn("Any delegated workers must follow these same scope", prompt)
        self.assertIn("incorporate its results before reporting completion", prompt)
        self.assertIn("branch main", state["events"][1][2])
        self.assertIn("The wrapper did not push or open a PR", state["events"][1][2])
        self.assertEqual((attempt / "commit").read_text().strip(), self.git("rev-parse", "HEAD"))
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertTrue((attempt / "agent.log").is_file())
        self.assertTrue((attempt / "process.json").is_file())
        self.assertTrue((attempt / "commit").is_file())
        self.assertFalse((runs[0] / "active.pid").exists())

    def test_invocation_from_checkout_subdirectory_uses_checkout_root(self) -> None:
        nested = self.repo / "nested"
        nested.mkdir()
        (nested / "tracked.txt").write_text("tracked fixture\n")
        self.git("add", "nested")
        self.git("commit", "--quiet", "-m", "Add nested fixture")

        result, _ = self.run_loop("--max-iterations", "1", cwd=nested)

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(self.git("branch", "--show-current", cwd=nested), "main")
        self.assertTrue((self.repo / "implementation-2.txt").exists())

    def test_result_file_rejections_never_close_or_unassign_ticket(self) -> None:
        modes = (
            "malformed",
            "wrong-root",
            "wrong-ticket",
            "wrong-ticket-number",
            "wrong-attempt",
            "wrong-status",
            "failed-acceptance",
            "failed-validation",
        )
        for mode in modes:
            with self.subTest(mode=mode):
                state = self.load_state()
                state["tickets"]["2"]["state"] = "open"
                state["tickets"]["2"]["assignees"] = []
                state["tickets"]["2"]["issue_dependencies_summary"]["blocked_by"] = 0
                state["events"] = []
                state["make_commit"] = False
                self.state = state
                self.save_state()
                result, runs = self.run_loop(
                    "--max-iterations",
                    "1",
                    "--allow-no-new-commit",
                    "2",
                    extra_env={"IMPLEMENT_LOOP_TEST_RESULT_MODE": mode},
                )
                after = self.load_state()
                self.assertNotEqual(result.returncode, 0, result.stdout.decode())
                self.assertEqual(after["tickets"]["2"]["state"], "open")
                self.assertEqual(after["tickets"]["2"]["assignees"], [{"login": "tester"}])
                self.assertEqual([event[0] for event in after["events"]], ["assign"])
                attempt = runs[-1] / "attempt-1-ticket-2"
                evidence = json.loads((attempt / "completion-evidence.json").read_text())
                self.assertNotEqual(evidence["status"], "valid")
                self.assertTrue((attempt / "completion-result.json").is_file())

    def test_nonzero_harness_exit_rejects_even_valid_result_file(self) -> None:
        for harness, selected_calls, unselected_calls, display_name in (
            (None, "opencode_calls", "copilot_calls", "OpenCode"),
            ("copilot", "copilot_calls", "opencode_calls", "Copilot"),
        ):
            with self.subTest(harness=harness or "default"):
                self.state = self.default_state()
                self.state["agent_exit"] = 7
                self.state["make_commit"] = False
                self.save_state()
                result, runs = self.run_loop(
                    "--max-iterations",
                    "1",
                    "--allow-no-new-commit",
                    "2",
                    harness=harness,
                )
                state = self.load_state()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(state[selected_calls]), 1)
                self.assertEqual(state[unselected_calls], [])
                self.assertEqual(state["tickets"]["2"]["state"], "open")
                self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
                self.assertIn(f"{display_name} exited with status 7".encode(), result.stderr)
                attempt = runs[-1] / "attempt-1-ticket-2"
                self.assertEqual(json.loads((attempt / "process.json").read_text())["exit_code"], 7)
                self.assertEqual(
                    json.loads((attempt / "completion-evidence.json").read_text())["status"],
                    "valid",
                )

    def test_required_record_persistence_failure_prevents_closure(self) -> None:
        self.state["agent_action"] = "break_records"
        self.state["make_commit"] = False
        self.save_state()

        result, runs = self.run_loop(
            "--max-iterations",
            "1",
            "--allow-no-new-commit",
            "2",
        )

        state = self.load_state()
        attempt = runs[-1] / "attempt-1-ticket-2"
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Cannot persist required run record", result.stderr)
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual([event[0] for event in state["events"]], ["assign"])
        attempt.chmod(0o700)

    @unittest.skipUnless(hasattr(resource, "RLIMIT_FSIZE"), "requires POSIX file-size limits")
    def test_agent_log_write_failure_prevents_closure(self) -> None:
        def limit_file_size() -> None:
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (8192, 8192))

        result, runs = self.run_loop(
            "--max-iterations",
            "1",
            extra_env={"IMPLEMENT_LOOP_TEST_OUTPUT": "x" * 20_000},
            preexec_fn=limit_file_size,
        )

        state = self.load_state()
        attempt = runs[-1] / "attempt-1-ticket-2"
        process = json.loads((attempt / "process.json").read_text())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Unable to persist the complete Copilot output log", result.stderr)
        self.assertFalse(process["log_persisted"])
        self.assertLessEqual((attempt / "agent.log").stat().st_size, 8192)
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual([event[0] for event in state["events"]], ["assign"])

    def test_queue_skips_blocked_and_assigned_children_and_ignores_labels(self) -> None:
        self.state["tickets"]["2"]["issue_dependencies_summary"]["blocked_by"] = 1
        self.add_ticket(3, assignees=["tester"])
        self.add_ticket(4, labels=["wontfix"])
        self.state["children"] = [{"number": 2}, {"number": 3}, {"number": 4}]
        self.save_state()

        result, _ = self.run_loop("--max-iterations", "1")

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [4])
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        self.assertEqual(state["tickets"]["4"]["state"], "closed")

    def test_named_exception_does_not_bypass_blockers_or_assigned_ticket_skips(self) -> None:
        for exception_ticket in (2, 3):
            with self.subTest(exception_ticket=exception_ticket):
                self.state = self.default_state()
                self.state["tickets"]["2"]["issue_dependencies_summary"]["blocked_by"] = 1
                self.add_ticket(3, assignees=["tester"])
                self.add_ticket(4)
                self.save_state()

                result, _ = self.run_loop(
                    "--max-iterations",
                    "1",
                    "--allow-no-new-commit",
                    str(exception_ticket),
                )

                state = self.load_state()
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [4])
                self.assertEqual(state["tickets"]["2"]["state"], "open")
                self.assertEqual(state["tickets"]["3"]["state"], "open")
                self.assertEqual(state["tickets"]["3"]["assignees"], [{"login": "tester"}])
                self.assertEqual(state["tickets"]["4"]["state"], "closed")

    def test_queue_refreshes_after_closure_and_preserves_returned_order(self) -> None:
        self.add_ticket(3)
        self.state["children"] = [{"number": 3}, {"number": 2}]
        self.state["sub_issue_page_size"] = 1
        self.save_state()

        result, runs = self.run_loop("--max-iterations", "2")

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [3, 2])
        self.assertEqual(
            len({call["attempt_id"] for call in state["copilot_calls"]}),
            2,
        )
        for call in state["copilot_calls"]:
            self.assertNotIn("--continue", call["args"])
            self.assertNotIn("--resume", call["args"])
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["3"]["state"], "closed")
        self.assertTrue((runs[0] / "attempt-1-ticket-3/completion-result.json").is_file())
        self.assertTrue((runs[0] / "attempt-2-ticket-2/completion-result.json").is_file())

    def test_no_eligible_children_exits_successfully_without_harness(self) -> None:
        self.state["tickets"]["2"]["assignees"] = [{"login": "tester"}]
        self.add_ticket(3, blocked=2)
        self.save_state()

        result, _ = self.run_loop()

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["copilot_calls"], [])
        self.assertEqual(state["events"], [])
        self.assertIn(b"No eligible children", result.stdout)

    def test_attempt_limit_refreshes_queue_but_does_not_claim_next_ticket(self) -> None:
        self.add_ticket(3)
        self.save_state()

        result, _ = self.run_loop("--max-iterations", "1")

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [2])
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        self.assertEqual(state["tickets"]["3"]["assignees"], [])
        self.assertIn(b"Attempt limit reached", result.stdout)

    def test_default_limit_is_five_harness_invocations(self) -> None:
        for number in range(3, 8):
            self.add_ticket(number)
        self.save_state()

        result, _ = self.run_loop(harness=None)

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(state["opencode_calls"]), 5)
        self.assertEqual(state["copilot_calls"], [])
        self.assertEqual(state["tickets"]["7"]["state"], "open")
        self.assertIn(b"Attempt limit reached after 5 invocation(s)", result.stdout)

    def test_initial_dirty_checkout_is_rejected_before_assignment(self) -> None:
        (self.repo / "untracked.txt").write_text("keep me\n")

        result, _ = self.run_loop()

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"checkout is not clean", result.stderr)
        self.assertEqual(state["events"], [])
        self.assertEqual(state["copilot_calls"], [])
        self.assertTrue((self.repo / "untracked.txt").is_file())

    def test_detached_head_is_rejected_before_github_access(self) -> None:
        self.git("checkout", "--quiet", "--detach")

        result, _ = self.run_loop()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.load_state()["events"], [])
        self.assertFalse((self.repo / ".git/implement-loop.lock").exists())

    def test_repository_common_lock_blocks_a_second_worktree(self) -> None:
        worktree = self.home / "other-worktree"
        self.git("worktree", "add", "--quiet", "-b", "other", str(worktree), "HEAD")
        common_dir = Path(self.git("rev-parse", "--git-common-dir"))
        if not common_dir.is_absolute():
            common_dir = self.repo / common_dir
        lock = common_dir.resolve() / "implement-loop.lock"
        lock.mkdir()
        (lock / "pid").write_text("another loop\n")

        result, _ = self.run_loop(cwd=worktree)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"stale lock exists", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertEqual((lock / "pid").read_text(), "another loop\n")

    def test_conflicting_github_repository_override_is_rejected(self) -> None:
        result, _ = self.run_loop(extra_env={"GH_REPO": "someone/else"})

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"GH_REPO conflicts", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertEqual(self.load_state()["copilot_calls"], [])

    def test_matching_github_repository_override_is_allowed(self) -> None:
        result, _ = self.run_loop(
            "--max-iterations",
            "1",
            extra_env={"GH_REPO": "test/repo"},
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        state = self.load_state()
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertNotIn("repo_view_saw_override", state)

    def test_invalid_attempt_limit_is_rejected_before_side_effects(self) -> None:
        result, _ = self.run_loop("--max-iterations", "0")

        self.assertEqual(result.returncode, 2)
        self.assertIn(b"positive integer", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertFalse((self.home / ".local/state/implement-loop").exists())

    def test_missing_copilot_cli_fails_before_claim(self) -> None:
        limited_bin = self.home / "limited-bin"
        limited_bin.mkdir()
        (limited_bin / "python3").symlink_to(sys.executable)
        (limited_bin / "git").symlink_to(shutil.which("git"))
        (limited_bin / "gh").symlink_to(self.bin / "gh")

        result, _ = self.run_loop(extra_env={"PATH": str(limited_bin)})

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Required executable not found on PATH: copilot", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertFalse((self.home / ".local/state/implement-loop").exists())

    def test_missing_default_opencode_cli_does_not_fall_back_to_copilot(self) -> None:
        limited_bin = self.home / "limited-bin"
        limited_bin.mkdir()
        for tool, target in (
            ("python3", sys.executable),
            ("git", shutil.which("git")),
            ("gh", self.bin / "gh"),
            ("copilot", self.bin / "copilot"),
        ):
            (limited_bin / tool).symlink_to(target)

        result, _ = self.run_loop(harness=None, extra_env={"PATH": str(limited_bin)})

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Required executable not found on PATH: opencode", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertEqual(self.load_state()["copilot_calls"], [])
        self.assertFalse((self.home / ".local/state/implement-loop").exists())

    def test_default_opencode_runs_without_a_copilot_executable(self) -> None:
        limited_bin = self.home / "limited-bin"
        limited_bin.mkdir()
        for tool, target in (
            ("python3", sys.executable),
            ("git", shutil.which("git")),
            ("gh", self.bin / "gh"),
            ("opencode", self.bin / "opencode"),
        ):
            (limited_bin / tool).symlink_to(target)

        result, _ = self.run_loop(
            "--max-iterations",
            "1",
            harness=None,
            extra_env={"PATH": str(limited_bin)},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(len(state["opencode_calls"]), 1)
        self.assertEqual(state["copilot_calls"], [])

    def test_explicit_copilot_runs_without_an_opencode_executable(self) -> None:
        limited_bin = self.home / "limited-bin"
        limited_bin.mkdir()
        for tool, target in (
            ("python3", sys.executable),
            ("git", shutil.which("git")),
            ("gh", self.bin / "gh"),
            ("copilot", self.bin / "copilot"),
        ):
            (limited_bin / tool).symlink_to(target)

        result, _ = self.run_loop(
            "--max-iterations",
            "1",
            harness="copilot",
            extra_env={"PATH": str(limited_bin)},
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(len(state["copilot_calls"]), 1)
        self.assertEqual(state["opencode_calls"], [])

    def test_rejected_model_stops_selected_harness_without_fallback(self) -> None:
        for selected_harness, call_key, other_key in (
            (None, "opencode_calls", "copilot_calls"),
            ("copilot", "copilot_calls", "opencode_calls"),
        ):
            with self.subTest(harness=selected_harness or "default"):
                self.state = self.default_state()
                self.state["model_rejected_for"] = call_key.removesuffix("_calls")
                self.save_state()

                result, runs = self.run_loop(
                    "--max-iterations",
                    "1",
                    harness=selected_harness,
                )

                state = self.load_state()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(state[call_key]), 1)
                self.assertEqual(state[other_key], [])
                self.assertEqual(state["tickets"]["2"]["state"], "open")
                self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
                self.assertEqual([event[0] for event in state["events"]], ["assign"])
                attempt = runs[0] / "attempt-1-ticket-2"
                self.assertEqual(json.loads((attempt / "process.json").read_text())["exit_code"], 1)
                self.assertIn(b"Model unavailable", (attempt / "agent.log").read_bytes())

    def test_missing_native_blocker_metadata_fails_closed(self) -> None:
        self.state["tickets"]["2"].pop("issue_dependencies_summary")
        self.save_state()

        result, _ = self.run_loop()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"missing readable native dependency data", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertEqual(self.load_state()["copilot_calls"], [])

    def test_cross_repository_child_fails_closed(self) -> None:
        self.state["children"][0]["repository_url"] = "https://api.github.com/repos/other/repo"
        self.save_state()

        result, _ = self.run_loop()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"cross-repository children are not supported", result.stderr)
        self.assertEqual(self.load_state()["events"], [])
        self.assertEqual(self.load_state()["copilot_calls"], [])

    def test_claim_is_rechecked_for_exclusive_ownership_and_blockers(self) -> None:
        self.state["claim_conflict"] = True
        self.save_state()

        result, _ = self.run_loop("--allow-no-new-commit", "2")

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"no longer open, unblocked, and exclusively assigned", result.stderr)
        self.assertEqual(
            state["tickets"]["2"]["assignees"],
            [{"login": "tester"}, {"login": "other"}],
        )
        self.assertEqual(state["copilot_calls"], [])
        self.assertEqual([event[0] for event in state["events"]], ["assign"])

    def test_blocker_appearing_after_claim_prevents_harness_invocation(self) -> None:
        self.state["claim_blocker"] = True
        self.save_state()

        result, _ = self.run_loop("--allow-no-new-commit", "2")

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"no longer open, unblocked", result.stderr)
        self.assertEqual(state["tickets"]["2"]["issue_dependencies_summary"]["blocked_by"], 1)
        self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
        self.assertEqual(state["copilot_calls"], [])

    def test_claim_is_rechecked_for_direct_child_membership(self) -> None:
        self.state["claim_remove_child"] = True
        self.save_state()

        result, _ = self.run_loop("--allow-no-new-commit", "2")

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"no longer an open direct child", result.stderr)
        self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
        self.assertEqual(state["copilot_calls"], [])

    def test_ownership_change_after_harness_is_rejected_even_with_named_exception(self) -> None:
        self.state["agent_action"] = "change_owner"
        self.state["make_commit"] = False
        self.save_state()
        existing_head = self.git("rev-parse", "HEAD")

        result, _ = self.run_loop(
            "--max-iterations",
            "1",
            "--allow-no-new-commit",
            "2",
        )

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual(
            state["tickets"]["2"]["assignees"],
            [{"login": "tester"}, {"login": "other"}],
        )
        self.assertEqual(self.git("rev-parse", "HEAD"), existing_head)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual([event[0] for event in state["events"]], ["assign"])

    def test_ticket_blocker_and_membership_are_rechecked_with_named_exception(self) -> None:
        cases = (
            ("add_blocker", b"no longer open, unblocked"),
            ("remove_child", b"no longer an open direct child"),
        )
        for action, diagnostic in cases:
            with self.subTest(action=action):
                self.state = self.default_state()
                self.state["agent_action"] = action
                self.state["make_commit"] = False
                self.save_state()

                result, _ = self.run_loop(
                    "--max-iterations",
                    "1",
                    "--allow-no-new-commit",
                    "2",
                )

                state = self.load_state()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(diagnostic, result.stderr)
                self.assertEqual(state["tickets"]["2"]["state"], "open")
                self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
                self.assertEqual([event[0] for event in state["events"]], ["assign"])

    def test_parent_must_remain_open_even_with_named_exception(self) -> None:
        self.state["agent_action"] = "close_parent"
        self.state["make_commit"] = False
        self.save_state()

        result, _ = self.run_loop(
            "--max-iterations",
            "1",
            "--allow-no-new-commit",
            "2",
        )

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["spec"]["state"], "closed")
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual([event[0] for event in state["events"]], ["assign"])

    def test_github_close_failure_preserves_assignment_and_local_commit(self) -> None:
        self.state["close_failure"] = True
        self.save_state()

        result, _ = self.run_loop("--max-iterations", "1")

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"GitHub CLI failed during closing ticket", result.stderr)
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
        self.assertIn("implementation-2.txt", self.git("show", "--pretty=", "--name-only"))
        self.assertEqual([event[0] for event in state["events"]], ["assign"])

    def test_new_commit_is_required_by_default(self) -> None:
        self.state["make_commit"] = False
        self.save_state()

        result, _ = self.run_loop("--max-iterations", "1")

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"No new commit was made", result.stderr)
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual([event[0] for event in state["events"]], ["assign"])

    def test_named_ticket_can_be_finalized_without_a_new_commit(self) -> None:
        self.state["make_commit"] = False
        self.save_state()
        existing_head = self.git("rev-parse", "HEAD")

        result, runs = self.run_loop(
            "--max-iterations",
            "1",
            "--allow-no-new-commit",
            "2",
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual([event[0] for event in state["events"]], ["assign", "close"])
        self.assertEqual(self.git("rev-parse", "HEAD"), existing_head)
        self.assertEqual(self.git("status", "--porcelain"), "")

        comment = state["events"][1][2]
        self.assertIn(f"branch main at commit {existing_head}", comment)
        self.assertIn("new-commit requirement was waived for ticket #2", comment)
        self.assertIn(f"HEAD remained at the pre-attempt commit {existing_head}", comment)
        self.assertNotIn("a new descendant commit was produced", comment)

        attempt = runs[0] / "attempt-1-ticket-2"
        checks = json.loads((attempt / "checks.json").read_text())
        self.assertTrue(checks["new_commit_requirement_waived"])
        self.assertFalse(checks["new_descendant_commit"])
        prompt = state["copilot_calls"][0]["prompt"]
        self.assertIn("assess the existing committed implementation", prompt)
        self.assertIn("do not manufacture a commit solely to satisfy the wrapper", prompt)

    def test_named_ticket_exception_does_not_force_selection(self) -> None:
        self.add_ticket(3)
        self.state["make_commit"] = False
        self.save_state()

        result, runs = self.run_loop(
            "--max-iterations",
            "2",
            "--allow-no-new-commit",
            "3",
        )

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"No new commit was made", result.stderr)
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [2])
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual(state["tickets"]["2"]["assignees"], [{"login": "tester"}])
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        self.assertEqual(state["tickets"]["3"]["assignees"], [])
        self.assertEqual([event[0] for event in state["events"]], ["assign"])
        self.assertIn(
            "requires a new local descendant commit",
            state["copilot_calls"][0]["prompt"],
        )
        metadata = json.loads((runs[0] / "run.json").read_text())
        self.assertEqual(metadata["allow_no_new_commit_ticket"], 3)

    def test_only_named_ticket_can_complete_without_a_new_commit_in_the_run(self) -> None:
        self.add_ticket(3)
        self.state["make_commit_by_ticket"] = {"3": False}
        self.save_state()

        result, runs = self.run_loop(
            "--max-iterations",
            "2",
            "--allow-no-new-commit",
            "3",
        )

        state = self.load_state()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [2, 3])
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["3"]["state"], "closed")
        self.assertEqual(
            [event[0] for event in state["events"]],
            [
                "assign",
                "close",
                "assign",
                "close",
            ],
        )

        first_comment = state["events"][1][2]
        second_comment = state["events"][3][2]
        self.assertNotIn("new-commit requirement was waived", first_comment)
        self.assertIn("new descendant commit was produced and verified", first_comment)
        self.assertIn("new-commit requirement was waived for ticket #3", second_comment)
        self.assertIn("HEAD remained at the pre-attempt commit", second_comment)
        self.assertNotIn("a new descendant commit was produced", second_comment)

        first_checks = json.loads((runs[0] / "attempt-1-ticket-2/checks.json").read_text())
        second_checks = json.loads((runs[0] / "attempt-2-ticket-3/checks.json").read_text())
        self.assertFalse(first_checks["new_commit_requirement_waived"])
        self.assertTrue(first_checks["new_descendant_commit"])
        self.assertTrue(second_checks["new_commit_requirement_waived"])
        self.assertFalse(second_checks["new_descendant_commit"])

    def test_named_exception_does_not_apply_to_later_tickets(self) -> None:
        self.add_ticket(3)
        self.add_ticket(4)
        self.state["make_commit_by_ticket"] = {"3": False, "4": False}
        self.save_state()

        result, runs = self.run_loop(
            "--max-iterations",
            "3",
            "--allow-no-new-commit",
            "3",
        )

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"No new commit was made", result.stderr)
        self.assertEqual([call["ticket"] for call in state["copilot_calls"]], [2, 3, 4])
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["3"]["state"], "closed")
        self.assertEqual(state["tickets"]["4"]["state"], "open")
        self.assertEqual(state["tickets"]["4"]["assignees"], [{"login": "tester"}])
        self.assertEqual(
            [event[:2] for event in state["events"]],
            [["assign", 2], ["close", 2], ["assign", 3], ["close", 3], ["assign", 4]],
        )
        prompt = state["copilot_calls"][2]["prompt"]
        self.assertIn("requires a new local descendant commit", prompt)
        self.assertNotIn("new-commit requirement is not required", prompt)
        later_attempt = json.loads((runs[0] / "attempt-3-ticket-4/attempt.json").read_text())
        self.assertFalse(later_attempt["new_commit_requirement_waived"])

    def test_dirty_checkout_is_rejected_even_with_named_exception(self) -> None:
        self.state["agent_action"] = "dirty"
        self.state["make_commit"] = False
        self.save_state()

        result, _ = self.run_loop(
            "--max-iterations",
            "1",
            "--allow-no-new-commit",
            "2",
        )

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"checkout is not clean", result.stderr)
        self.assertTrue((self.repo / "unfinished.txt").is_file())
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual([event[0] for event in state["events"]], ["assign"])

    def test_both_harnesses_share_git_and_ownership_completion_checks(self) -> None:
        for harness, selected_calls, unselected_calls in (
            (None, "opencode_calls", "copilot_calls"),
            ("copilot", "copilot_calls", "opencode_calls"),
        ):
            for action in ("dirty", "change_owner"):
                with self.subTest(harness=harness or "default", action=action):
                    self.state = self.default_state()
                    self.state["agent_action"] = action
                    self.save_state()

                    result, runs = self.run_loop(
                        "--max-iterations",
                        "1",
                        harness=harness,
                    )

                    state = self.load_state()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(len(state[selected_calls]), 1)
                    self.assertEqual(state[unselected_calls], [])
                    self.assertEqual(state["tickets"]["2"]["state"], "open")
                    self.assertEqual([event[0] for event in state["events"]], ["assign"])
                    self.assertIn(
                        "implementation-2.txt",
                        self.git("show", "--pretty=", "--name-only"),
                    )
                    self.assertTrue((runs[0] / "attempt-1-ticket-2/process.json").is_file())
                    if action == "dirty":
                        self.assertIn(b"checkout is not clean", result.stderr)
                        self.assertTrue((self.repo / "unfinished.txt").is_file())
                        (self.repo / "unfinished.txt").unlink()
                    else:
                        self.assertIn(b"no longer open, unblocked", result.stderr)
                        self.assertEqual(
                            state["tickets"]["2"]["assignees"],
                            [{"login": "tester"}, {"login": "other"}],
                        )

    def test_changed_branch_is_rejected_even_with_named_exception(self) -> None:
        self.state["agent_action"] = "branch"
        self.state["make_commit"] = False
        self.save_state()

        result, _ = self.run_loop(
            "--max-iterations",
            "1",
            "--allow-no-new-commit",
            "2",
        )

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"branch changed from main to agent-branch", result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "agent-branch")
        self.assertEqual(state["tickets"]["2"]["state"], "open")
        self.assertEqual([event[0] for event in state["events"]], ["assign"])

    def test_non_descendant_history_is_rejected_even_with_named_exception(self) -> None:
        self.add_ticket(3)
        self.state["agent_action_by_ticket"] = {"3": "rewrite_history"}
        self.save_state()

        result, _ = self.run_loop(
            "--max-iterations",
            "2",
            "--allow-no-new-commit",
            "3",
        )

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"rewrote or replaced existing history", result.stderr)
        self.assertEqual(state["tickets"]["2"]["state"], "closed")
        self.assertEqual(state["tickets"]["3"]["state"], "open")
        self.assertEqual(state["tickets"]["3"]["assignees"], [{"login": "tester"}])
        self.assertEqual([event[0] for event in state["events"]], ["assign", "close", "assign"])

    def test_oversized_context_fails_before_claim_without_truncation(self) -> None:
        self.state["tickets"]["2"]["body"] = "x" * 101_000
        self.save_state()

        result, _ = self.run_loop()

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"safe 100 KB command-line budget", result.stderr)
        self.assertEqual(state["tickets"]["2"]["assignees"], [])
        self.assertEqual(state["events"], [])
        self.assertEqual(state["copilot_calls"], [])

    def test_run_records_are_private_and_redirected_output_disables_color(self) -> None:
        result, runs = self.run_loop(
            "--max-iterations",
            "1",
            extra_env={"FORCE_COLOR": "3", "NO_COLOR": ""},
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        state = self.load_state()
        self.assertEqual(state["copilot_calls"][0]["no_color"], "1")
        self.assertEqual(state["copilot_calls"][0]["force_color"], "0")
        run_dir = runs[-1]
        attempt = run_dir / "attempt-1-ticket-2"
        self.assertEqual(stat.S_IMODE(run_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(attempt.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((attempt / "prompt.txt").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((attempt / "agent.log").stat().st_mode), 0o600)

    def test_xdg_state_home_selects_private_external_records(self) -> None:
        state_home = self.home / "custom-state"
        result, runs = self.run_loop(
            "--max-iterations",
            "1",
            extra_env={"XDG_STATE_HOME": str(state_home)},
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].parent, state_home / "implement-loop")
        self.assertEqual(stat.S_IMODE(runs[0].parent.stat().st_mode), 0o700)
        self.assertFalse((self.home / ".local/state/implement-loop").exists())

    def test_records_directory_cannot_contain_the_target_checkout(self) -> None:
        result, runs = self.run_loop("--records-dir", str(self.home), harness=None)

        state = self.load_state()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"must be a dedicated directory outside the target checkout", result.stderr)
        self.assertEqual(state["events"], [])
        self.assertEqual(state["opencode_calls"], [])
        self.assertEqual(state["copilot_calls"], [])
        self.assertEqual(runs, [])
        self.assertFalse((self.repo / ".git/implement-loop.lock").exists())

    def test_existing_shared_record_directory_is_not_made_private_in_place(self) -> None:
        shared_dir = self.home / "shared-records"
        shared_dir.mkdir()
        shared_dir.chmod(0o1777)
        self.state["records_dir"] = str(shared_dir)
        self.state["tickets"]["2"]["assignees"] = [{"login": "tester"}]
        self.save_state()

        result, _ = self.run_loop(
            "--records-dir",
            str(shared_dir),
            harness=None,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"must already have mode 0700", result.stderr)
        self.assertEqual(stat.S_IMODE(shared_dir.stat().st_mode), 0o1777)
        self.assertEqual(self.load_state()["events"], [])
        self.assertFalse(any(shared_dir.glob("run-*")))

    def test_configured_record_directory_and_retention_are_effective(self) -> None:
        records_dir = self.home / "private-records"
        records_dir.mkdir(mode=0o700)

        def old_run(age: timedelta, suffix: str) -> Path:
            stamp = (datetime.now(UTC) - age).strftime("%Y%m%dT%H%M%SZ")
            path = records_dir / f"run-{stamp}-{suffix}"
            path.mkdir()
            (path / "agent.log").write_text("recent file activity does not extend retention\n")
            return path

        retained = old_run(timedelta(days=8), "ABC123")
        expired = old_run(timedelta(days=16), "DEF456")
        config_home = self.home / "records-config"
        config_file = config_home / "implement-loop/config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(f'[records]\ndirectory = "{records_dir}"\nretention_days = 14\n')
        self.state["records_dir"] = str(records_dir)
        self.state["tickets"]["2"]["assignees"] = [{"login": "tester"}]
        self.save_state()

        result, _ = self.run_loop(
            harness=None,
            extra_env={"XDG_CONFIG_HOME": str(config_home)},
        )
        runs = sorted(path for path in records_dir.glob("run-*") if (path / "run.json").is_file())

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].parent, records_dir)
        self.assertTrue(retained.is_dir())
        self.assertFalse(expired.exists())
        self.assertEqual(stat.S_IMODE(records_dir.stat().st_mode), 0o700)
        metadata = json.loads((runs[0] / "run.json").read_text())
        self.assertEqual(metadata["records_directory"], str(records_dir.resolve()))
        self.assertEqual(metadata["retention_days"], 14)

    def test_cli_record_directory_and_retention_override_user_configuration(self) -> None:
        configured_dir = self.home / "configured-records"
        cli_records_dir = self.home / "cli-records"
        configured_dir.mkdir(mode=0o700)
        cli_records_dir.mkdir(mode=0o700)

        def old_run(directory: Path, age: timedelta, suffix: str) -> Path:
            stamp = (datetime.now(UTC) - age).strftime("%Y%m%dT%H%M%SZ")
            path = directory / f"run-{stamp}-{suffix}"
            path.mkdir()
            (path / "agent.log").write_text("old run\n")
            return path

        old_in_configured_dir = old_run(configured_dir, timedelta(days=20), "ABC123")
        within_cli_retention = old_run(cli_records_dir, timedelta(days=8), "DEF456")
        outside_cli_retention = old_run(cli_records_dir, timedelta(days=16), "GHI789")
        config_home = self.home / "record-override-config"
        config_file = config_home / "implement-loop/config.toml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text(f'[records]\ndirectory = "{configured_dir}"\nretention_days = 0\n')
        self.state["records_dir"] = str(cli_records_dir)
        self.state["tickets"]["2"]["assignees"] = [{"login": "tester"}]
        self.save_state()

        result, _ = self.run_loop(
            "--records-dir",
            str(cli_records_dir),
            "--retention-days",
            "14",
            harness=None,
            extra_env={"XDG_CONFIG_HOME": str(config_home)},
        )
        runs = sorted(
            path for path in cli_records_dir.glob("run-*") if (path / "run.json").is_file()
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].parent, cli_records_dir)
        self.assertTrue(old_in_configured_dir.is_dir())
        self.assertTrue(within_cli_retention.is_dir())
        self.assertFalse(outside_cli_retention.exists())
        metadata = json.loads((runs[0] / "run.json").read_text())
        self.assertEqual(metadata["records_directory"], str(cli_records_dir.resolve()))
        self.assertEqual(metadata["retention_days"], 14)

    def test_keep_forever_disables_expiry_in_config_and_on_the_cli(self) -> None:
        for source in ("config", "cli"):
            with self.subTest(source=source):
                self.state = self.default_state()
                records_dir = self.home / f"keep-forever-{source}"
                records_dir.mkdir(mode=0o700)
                stamp = (datetime.now(UTC) - timedelta(days=100)).strftime("%Y%m%dT%H%M%SZ")
                expired = records_dir / f"run-{stamp}-ABC123"
                expired.mkdir()
                (expired / "agent.log").write_text("keep forever\n")
                self.state["records_dir"] = str(records_dir)
                self.state["tickets"]["2"]["assignees"] = [{"login": "tester"}]
                self.save_state()

                config_home = self.home / f"keep-config-{source}"
                config_file = config_home / "implement-loop/config.toml"
                config_file.parent.mkdir(parents=True)
                configured_dir = records_dir if source == "config" else self.home / "unused-records"
                config_file.write_text(
                    "[records]\n"
                    f'directory = "{configured_dir}"\n'
                    "retention_days = 0\n" + ("keep_forever = true\n" if source == "config" else "")
                )
                args = (
                    ("--records-dir", str(records_dir), "--keep-records-forever")
                    if source == "cli"
                    else ()
                )

                result, _ = self.run_loop(
                    *args,
                    harness=None,
                    extra_env={"XDG_CONFIG_HOME": str(config_home)},
                )
                runs = sorted(
                    path for path in records_dir.glob("run-*") if (path / "run.json").is_file()
                )

                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertTrue(expired.is_dir())
                self.assertEqual(len(runs), 1)
                metadata = json.loads((runs[0] / "run.json").read_text())
                self.assertIsNone(metadata["retention_days"])
                self.assertTrue(metadata["keep_records_forever"])

    def test_terminal_output_enables_color_when_no_color_is_unset(self) -> None:
        master, slave = pty.openpty()
        try:
            result = subprocess.run(
                [str(SCRIPT), "1", "--max-iterations", "1"],
                cwd=self.repo,
                env=self.env,
                stdout=slave,
                stderr=slave,
                timeout=30,
            )
        finally:
            os.close(slave)
            os.close(master)

        self.assertEqual(result.returncode, 0)
        call = self.load_state()["opencode_calls"][0]
        self.assertIsNone(call["no_color"])
        self.assertEqual(call["force_color"], "1")

    def test_seven_day_retention_protects_marked_unrelated_symlink_and_legacy_data(self) -> None:
        state_dir = self.home / ".local/state/implement-loop"
        state_dir.mkdir(parents=True, mode=0o700)

        def old_run(age: timedelta, suffix: str = "ABC123") -> Path:
            stamp = (datetime.now(UTC) - age).strftime("%Y%m%dT%H%M%SZ")
            path = state_dir / f"run-{stamp}-{suffix}"
            path.mkdir()
            (path / "agent.log").write_text("keep or prune by age\n")
            return path

        expired = old_run(timedelta(days=8))
        (expired / "run.json").write_text(json.dumps({"status": "failed"}))
        (expired / "process.json").write_text(json.dumps({"exit_code": 1}))
        boundary = old_run(timedelta(days=7) - timedelta(minutes=1))
        recent = old_run(timedelta(days=6))
        marked = old_run(timedelta(days=9), "DEF456")
        (marked / "active.pid").write_text("stale marker\n")
        unrecognized = state_dir / "run-not-a-timestamp-ABC123"
        unrecognized.mkdir()
        (unrecognized / "agent.log").write_text("unrelated\n")
        impossible = state_dir / "run-20260230T000000Z-ABC123"
        impossible.mkdir()
        (impossible / "agent.log").write_text("invalid timestamp\n")
        target = self.home / "unrelated-target"
        target.mkdir()
        (target / "agent.log").write_text("symlink target\n")
        link = state_dir / "run-20000101T000000Z-ABC123"
        link.symlink_to(target, target_is_directory=True)
        legacy = self.home / ".local/state/copilot-ralph/run-20000101T000000Z-ABC123"
        legacy.mkdir(parents=True)
        (legacy / "agent.log").write_text("legacy\n")
        self.state["tickets"]["2"]["assignees"] = [{"login": "tester"}]
        self.save_state()

        result, _ = self.run_loop()

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertFalse(expired.exists())
        for preserved in (boundary, recent, marked, unrecognized, impossible, legacy):
            self.assertTrue((preserved / "agent.log").is_file())
        self.assertTrue(link.is_symlink())
        self.assertEqual((target / "agent.log").read_text(), "symlink target\n")

    def test_retention_expires_strictly_after_the_configured_boundary(self) -> None:
        retention_seconds = 7 * 24 * 60 * 60
        boundary_start = int(time.time()) - retention_seconds
        state_dir = self.home / ".local/state/implement-loop"
        state_dir.mkdir(parents=True, mode=0o700)
        stamp = datetime.fromtimestamp(boundary_start, UTC).strftime("%Y%m%dT%H%M%SZ")
        boundary_run = state_dir / f"run-{stamp}-ABC123"
        boundary_run.mkdir()
        (boundary_run / "agent.log").write_text("recent activity must not affect age\n")
        support = self.home / "time-support"
        support.mkdir()
        (support / "sitecustomize.py").write_text(
            "import os\n"
            "import time\n"
            "time.time = lambda: float(os.environ['IMPLEMENT_LOOP_TEST_FIXED_TIME'])\n"
        )
        self.state["tickets"]["2"]["assignees"] = [{"login": "tester"}]
        self.save_state()

        at_boundary, _ = self.run_loop(
            extra_env={
                "PYTHONPATH": str(support),
                "IMPLEMENT_LOOP_TEST_FIXED_TIME": str(boundary_start + retention_seconds),
            }
        )

        self.assertEqual(at_boundary.returncode, 0, at_boundary.stderr.decode())
        self.assertTrue(boundary_run.is_dir())

        just_expired, _ = self.run_loop(
            extra_env={
                "PYTHONPATH": str(support),
                "IMPLEMENT_LOOP_TEST_FIXED_TIME": str(boundary_start + retention_seconds + 0.5),
            }
        )

        self.assertEqual(just_expired.returncode, 0, just_expired.stderr.decode())
        self.assertFalse(boundary_run.exists())

    def test_retention_removal_failure_warns_and_does_not_block_run(self) -> None:
        state_dir = self.home / ".local/state/implement-loop"
        state_dir.mkdir(parents=True, mode=0o700)
        stamp = (datetime.now(UTC) - timedelta(days=8)).strftime("%Y%m%dT%H%M%SZ")
        expired = state_dir / f"run-{stamp}-ABC123"
        expired.mkdir()
        (expired / "agent.log").write_text("keep after failed cleanup\n")
        expired.chmod(0o500)
        self.state["tickets"]["2"]["assignees"] = [{"login": "tester"}]
        self.save_state()

        try:
            result, _ = self.run_loop()
        finally:
            expired.chmod(0o700)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertTrue((expired / "agent.log").is_file())
        self.assertIn(b"Cannot remove expired run", result.stderr)


if __name__ == "__main__":
    unittest.main()
