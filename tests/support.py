from __future__ import annotations

import json
import os
import pty
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "implement-loop"
REPOSITORY_URL = "https://api.github.com/repos/test/repo"
LIVE_OUTPUT_FRAGMENT = b"IMPLEMENT_LOOP_LIVE_OUTPUT_7F3A9C"
LIVE_OUTPUT_TIMEOUT = 15.0
LIVE_READY_TIMEOUT = 20.0

FAKE_TOOL = r"""#!/usr/bin/env python3
import json
import os
import resource
from pathlib import Path
import re
import shlex
import signal
import socket
import stat
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

def configured_failure_detail(key, default):
    return state.get(key, default)

def page_response(items, page_size):
    pages = [items[index:index + page_size] for index in range(0, len(items), page_size)]
    if "--paginate" in args and "--slurp" in args:
        return pages or [[]]
    return pages[0] if pages else []

def write_opencode_event(event):
    encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    sys.stdout.buffer.write(encoded.encode("utf-8"))
    sys.stdout.buffer.flush()

def write_opencode_text_event(text, session_id):
    write_opencode_event({
        "type": "text",
        "timestamp": 1,
        "sessionID": session_id,
        "part": {"type": "text", "text": text},
    })

tool_name = Path(sys.argv[0]).name
if tool_name == "opencode":
    prompt = args[-1] if args else ""
    ticket_number = int(
        re.search(r"(?:/implement|@implement) Implement only ticket #(\d+)", prompt).group(1)
    )
    report_command = shlex.split(next(
        line.removeprefix("Completion report command: ").strip()
        for line in prompt.splitlines()
        if line.startswith("Completion report command: ")
    ))[0]
    state_home = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
    attempt_dir = next(
        (state_home / "implement-loop").glob(f"run-*/attempt-*-ticket-{ticket_number}")
    )
    attempt_metadata = json.loads((attempt_dir / "attempt.json").read_text())
    attempt_id = attempt_metadata["attempt_id"]
    result_path = Path(attempt_metadata["result_path"])
    exit_status = state.get("agent_exit_by_ticket", {}).get(
        str(ticket_number), state.get("agent_exit", 0)
    )
    call = {
        "args": args,
        "prompt": prompt,
        "attempt_id": attempt_id,
        "ticket": ticket_number,
        "report_command": report_command,
        "report_directory_mode": stat.S_IMODE(Path(report_command).parent.stat().st_mode),
        "report_command_mode": stat.S_IMODE(Path(report_command).stat().st_mode),
        "result_initially_absent": not Path(result_path).exists(),
        "no_color": os.environ.get("NO_COLOR"),
        "force_color": os.environ.get("FORCE_COLOR"),
        "opencode_config": os.environ.get("OPENCODE_TEST_CONFIG"),
    }
    state.setdefault(f"{tool_name}_calls", []).append(call)
    save_state()

    if state.get("model_rejected_for") == tool_name:
        sys.stdout.write("Model unavailable: explicitly selected GPT-6 Luna\n")
        sys.exit(1)

    if state.get("make_commit", True):
        output_path = Path(f"implementation-{ticket_number}.txt")
        output_path.write_text(f"implemented ticket {ticket_number} in attempt {attempt_id}\n")
        subprocess.run(["git", "add", output_path.name], check=True)
        subprocess.run([
            "git", "commit", "--quiet", "-m",
            f"Implement ticket {ticket_number}"
        ], check=True)

    mode = os.environ.get("IMPLEMENT_LOOP_TEST_RESULT_MODE", "valid")
    help_result = subprocess.run(
        [report_command, "--help"], check=False, capture_output=True, text=True
    )
    report_results = []

    def run_completed_report(acceptance="passed", validation="passed", *, fail_write=False):
        command = [
            report_command,
            "completed",
            "--acceptance-criteria",
            acceptance,
            "--validation",
            validation,
        ]
        if not fail_write:
            return subprocess.run(command, check=False, capture_output=True, text=True)

        def limit_report_file():
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))

        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            preexec_fn=limit_report_file,
        )

    def run_blocked_report(reason="External dependency is unavailable"):
        return subprocess.run(
            [report_command, "blocked", "--reason", reason],
            check=False,
            capture_output=True,
            text=True,
        )

    if mode == "report-write-failure":
        report_results.append(run_completed_report(fail_write=True))
    elif mode == "conflicting":
        report_results.append(run_completed_report())
        report_results.append(run_completed_report(acceptance="failed"))
    elif mode == "failed-acceptance":
        report_results.append(run_completed_report(acceptance="failed"))
    elif mode == "failed-validation":
        report_results.append(run_completed_report(validation="failed"))
    elif mode == "duplicate":
        report_results.append(run_completed_report())
        report_results.append(run_completed_report())
    elif mode == "blocked":
        report_results.append(run_blocked_report())
    elif mode == "blocked-invalid-reason":
        report_results.append(run_blocked_report(" \t "))
    elif mode == "blocked-duplicate":
        report_results.append(run_blocked_report())
        report_results.append(run_blocked_report())
    elif mode == "blocked-then-completed":
        report_results.append(run_blocked_report())
        report_results.append(run_completed_report())
    elif mode == "completed-then-blocked":
        report_results.append(run_completed_report())
        report_results.append(run_blocked_report())
    elif mode != "missing":
        report_results.append(run_completed_report())

    state["opencode_calls"][-1]["report_help"] = help_result.stdout
    state["opencode_calls"][-1]["report_exit_codes"] = [
        result.returncode for result in report_results
    ]
    state["opencode_calls"][-1]["report_errors"] = [
        result.stderr for result in report_results if result.stderr
    ]
    state["opencode_calls"][-1]["report_mode"] = (
        stat.S_IMODE(result_path.stat().st_mode) if result_path.is_file() else None
    )
    state["report_observation"] = {
        "ticket_state": ticket_for(ticket_number)["state"],
        "events": list(state["events"]),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "status": subprocess.check_output(["git", "status", "--porcelain"], text=True).strip(),
    }
    save_state()

    if mode in {
        "malformed",
        "duplicate-field",
        "extra-field",
        "wrong-root",
        "wrong-ticket",
        "wrong-ticket-number",
        "wrong-attempt",
        "wrong-status",
    }:
        result = {
            "issue": ticket_number,
            "attempt_id": attempt_id,
            "status": "completed",
            "acceptance_criteria": "passed",
            "validation": "passed",
        }
        if mode == "malformed":
            Path(result_path).write_text("{not-json")
        elif mode == "duplicate-field":
            Path(result_path).write_text(
                '{"issue":2,"issue":2,"attempt_id":"%s",'
                '"status":"completed","acceptance_criteria":"passed",'
                '"validation":"passed"}' % attempt_id
            )
        elif mode == "extra-field":
            result["unexpected"] = True
            Path(result_path).write_text(json.dumps(result))
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
            Path(result_path).write_text(json.dumps(result))

    action = state.get("agent_action_by_ticket", {}).get(
        str(ticket_number), state.get("agent_action")
    )
    if action == "dirty":
        Path("unfinished.txt").write_text("uncommitted change\n")
    elif action == "dirty_submodule":
        Path("nested-module/uncommitted.txt").write_text("submodule work to preserve\n")
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
    elif action == "close_ticket":
        ticket_for(ticket_number)["state"] = "closed"
        save_state()
    elif action == "close_parent":
        state["spec"]["state"] = "closed"
        save_state()
    elif action == "break_records":
        attempt_dir.chmod(0o500)

    if state.get("fail_preclosure_read_after_harness"):
        state["fail_next_issue_read_for"] = ticket_number
        save_state()

    report_output = "\n".join(
        [help_result.stdout, *(result.stdout + result.stderr for result in report_results)]
    )
    output = os.environ.get(
        "IMPLEMENT_LOOP_TEST_OUTPUT",
        state.get("agent_output", report_output),
    )
    if "opencode_raw_output" in state:
        raw_output = state["opencode_raw_output"].encode("utf-8")
        split_bytes = os.environ.get("IMPLEMENT_LOOP_TEST_OPENCODE_SPLIT_BYTES")
        if split_bytes is None:
            sys.stdout.buffer.write(raw_output)
            sys.stdout.buffer.flush()
        else:
            split_at = int(split_bytes)
            section = raw_output[:split_at]
            offset = 0
            while offset < len(section):
                offset += os.write(sys.stdout.fileno(), section[offset:])
            with socket.create_connection(
                ("127.0.0.1", int(os.environ["IMPLEMENT_LOOP_TEST_LIVE_OUTPUT_READY_PORT"])),
                timeout=20,
            ) as ready_connection:
                ready_connection.sendall(b"R")
            sys.stdin.buffer.read(1)
            section = raw_output[split_at:]
            offset = 0
            while offset < len(section):
                offset += os.write(sys.stdout.fileno(), section[offset:])
        sys.exit(int(exit_status))
    live_fragment = os.environ.get("IMPLEMENT_LOOP_TEST_LIVE_OUTPUT_FRAGMENT")
    if live_fragment is not None:
        write_opencode_text_event("Live **agent** text", "ses_live_fixture")
        write_opencode_event({
                "type": "tool_use",
                "timestamp": 1,
                "sessionID": "ses_live_fixture",
                "part": {
                    "type": "tool",
                    "tool": "live_tool",
                    "state": {
                        "status": "completed",
                        "title": "Live completed tool",
                        "input": {"fixture": "live"},
                        "output": live_fragment,
                    },
                },
        })
        if os.environ.get("IMPLEMENT_LOOP_TEST_LIVE_FAILURE") == "1":
            write_opencode_event({
                    "type": "tool_use",
                    "timestamp": 2,
                    "sessionID": "ses_live_fixture",
                    "part": {
                        "type": "tool",
                        "tool": "live_failure",
                        "state": {
                            "status": "error",
                            "title": "Live failed tool",
                            "error": "live failure details",
                        },
                    },
            })
        with socket.create_connection(
            ("127.0.0.1", int(os.environ["IMPLEMENT_LOOP_TEST_LIVE_OUTPUT_READY_PORT"])),
            timeout=20,
        ) as ready_connection:
            ready_connection.sendall(b"R")
        sys.stdin.buffer.read(1)
        sys.exit(int(exit_status))
    write_opencode_text_event("Agent output\n" + output, "ses_fixture")
    sys.exit(int(exit_status))

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
    repository_name = state.get("repository_name", "test/repo")
    result = {
        "nameWithOwner": repository_name,
        "url": f"https://github.com/{repository_name}",
    }
elif args[:2] == ["issue", "edit"]:
    number = int(args[2])
    ticket = ticket_for(number)
    login = args[args.index("--add-assignee") + 1]
    if state.get("claim_failure") == "before_write":
        sys.exit(
            configured_failure_detail(
                "claim_failure_detail", "simulated GitHub claim failure before writing"
            )
        )
    ticket["assignees"].append({"login": login})
    if state.get("claim_conflict"):
        ticket["assignees"].append({"login": "other"})
    if state.get("claim_blocker"):
        ticket["issue_dependencies_summary"]["blocked_by"] = 1
    if state.get("claim_close"):
        ticket["state"] = "closed"
    if state.get("claim_close_parent"):
        state["spec"]["state"] = "closed"
    if state.get("claim_remove_child"):
        state["children"] = [child for child in state["children"] if child["number"] != number]
    state.setdefault("events", []).append(["assign", number, login])
    if state.get("remove_opencode_before_invocation"):
        (Path(os.environ["PATH"].split(os.pathsep)[0]) / "opencode").unlink()
    if state.get("precreate_agent_log_before_invocation"):
        state_home = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
        records_dir = Path(state.get("records_dir", state_home / "implement-loop"))
        attempt_dir = next(records_dir.glob("run-*/attempt-1-ticket-2"))
        (attempt_dir / "agent.log").write_text("preexisting log record\n")
    save_state()
    if state.get("claim_failure") == "after_write":
        sys.exit(
            configured_failure_detail(
                "claim_failure_detail", "simulated GitHub claim failure after writing"
            )
        )
    sys.exit(0)
elif args[:2] == ["issue", "close"]:
    number = int(args[2])
    comment = args[args.index("--comment") + 1]
    if state.get("close_comment_failure_after_write"):
        state.setdefault("comments", {}).setdefault(str(number), []).append(
            {"user": {"login": "tester"}, "body": comment}
        )
        state.setdefault("events", []).append(["comment", number, comment])
        save_state()
        sys.exit(
            configured_failure_detail(
                "close_failure_detail",
                "simulated GitHub failure after posting the closure comment",
            )
        )
    if state.get("close_failure"):
        sys.exit(
            configured_failure_detail(
                "close_failure_detail", "simulated GitHub closure failure"
            )
        )
    ticket_for(number)["state"] = "closed"
    state.setdefault("events", []).append(["close", number, comment])
    if state.get("fail_queue_refresh_after_close"):
        state["api_failure_endpoint"] = "repos/test/repo/issues/1/sub_issues?per_page=100"
    if state.get("fail_confirmation_after_close"):
        state["fail_next_issue_read_for"] = number
    save_state()
    if state.get("close_failure_after_write"):
        sys.exit(
            configured_failure_detail(
                "close_failure_detail",
                "simulated GitHub closure failure after closing the issue",
            )
        )
    sys.exit(0)
elif args[:1] == ["api"]:
    if "--hostname" not in args:
        sys.exit("GitHub API call omitted the resolved hostname")
    state.setdefault("api_hosts", []).append(args[args.index("--hostname") + 1])
    endpoint = args[-1]
    if state.get("api_failure_endpoint") == endpoint:
        sys.exit("simulated required GitHub API read failure")
    if endpoint == "user":
        result = {"login": state["login"]}
    elif endpoint.endswith("/comments?per_page=100"):
        number = endpoint.split("/issues/")[1].split("/")[0]
        comments = state.get("comments", {}).get(number, [])
        page_size = state.get("comment_page_size", 100)
        result = page_response(comments, page_size)
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
        result = page_response(children, page_size)
    else:
        match = re.search(r"/issues/(\d+)$", endpoint)
        if not match:
            sys.exit("Unexpected API endpoint: " + endpoint)
        number = int(match.group(1))
        if state.get("fail_next_issue_read_for") == number:
            state.pop("fail_next_issue_read_for")
            save_state()
            sys.exit(
                state.get("issue_read_failure_detail", "simulated required issue read failure")
            )
        result = state["spec"] if number == state["spec"]["number"] else ticket_for(number)
else:
    sys.exit("Unexpected gh arguments: " + repr(args))

save_state()
print(json.dumps(result))
"""


def _without_inherited_git_environment(environment: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in environment.items() if not key.startswith("GIT_")}


def _isolated_git_environment(home: Path) -> dict[str, str]:
    environment = _without_inherited_git_environment(os.environ)
    global_config = home / ".gitconfig"
    global_config.touch()
    environment.update(
        {
            "HOME": str(home),
            "GIT_CONFIG_GLOBAL": str(global_config),
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    return environment


class _TemporaryHomeFixture:
    def initialize_home(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="implement-loop-test-")
        self.home = Path(self.temp.name)
        self.env = _isolated_git_environment(self.home)
        for variable in ("GH_REPO", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "NO_COLOR", "FORCE_COLOR"):
            self.env.pop(variable, None)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env["PATH"]
        self.controlled_path_count = 0

    def cleanup(self) -> None:
        self.temp.cleanup()

    def copy_source_tree(self) -> Path:
        source = self.home / "source checkout"
        shutil.copytree(
            SCRIPT.parent,
            source,
            ignore=shutil.ignore_patterns(
                ".git",
                "__pycache__",
                ".venv",
                ".mypy_cache",
                ".ruff_cache",
                "build",
                "dist",
            ),
        )
        return source


class _LoopFixtureSupport:
    def initialize_loop(self) -> None:
        self.repo = self.home / "repo"
        self.repo.mkdir()
        for tool in (
            "gh",
            "opencode",
        ):
            path = self.bin / tool
            path.write_text(FAKE_TOOL)
            path.chmod(0o700)

        self.state_path = self.home / "github-state.json"
        self.state = self.default_state()
        self.save_state()
        self.env["IMPLEMENT_LOOP_TEST_STATE"] = str(self.state_path)
        self.env["IMPLEMENT_LOOP_TEST_REPO"] = str(self.repo)

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
            "opencode_calls": [],
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

    def add_submodule(self) -> Path:
        source = self.home / "submodule-source"
        source.mkdir()
        self.git("init", "--quiet", cwd=source)
        self.git("config", "user.name", "Implement Loop Test", cwd=source)
        self.git("config", "user.email", "implement-loop-test@example.invalid", cwd=source)
        (source / "tracked.txt").write_text("submodule fixture\n")
        self.git("add", "tracked.txt", cwd=source)
        self.git("commit", "--quiet", "-m", "Initial submodule fixture", cwd=source)
        self.git(
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(source),
            "nested-module",
        )
        self.git("add", ".gitmodules", "nested-module")
        self.git("commit", "--quiet", "-m", "Add test submodule")
        return self.repo / "nested-module"

    def repository_lock_path(self, cwd: Path | None = None) -> Path:
        common_dir = Path(self.git("rev-parse", "--git-common-dir", cwd=cwd))
        if not common_dir.is_absolute():
            common_dir = (cwd or self.repo) / common_dir
        return common_dir.resolve() / "implement-loop.lock"

    def controlled_path_directory(self, *commands: str) -> Path:
        self.controlled_path_count += 1
        directory = self.home / f"controlled-bin-{self.controlled_path_count}"
        directory.mkdir()
        for command in commands:
            if command == "python3":
                target = Path(sys.executable)
            elif command == "git":
                git = shutil.which("git", path=self.env["PATH"])
                assert git is not None, "test environment needs git"
                target = Path(git)
            else:
                target = self.bin / command
                assert target.is_file(), f"no fake executable for {command}"
            (directory / command).symlink_to(target)
        return directory

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
        extra_env: dict[str, str] | None = None,
        cwd: Path | None = None,
        preexec_fn: Callable[[], None] | None = None,
        executable: Path = SCRIPT,
    ) -> tuple[subprocess.CompletedProcess[bytes], list[Path]]:
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        state_home = Path(env.get("XDG_STATE_HOME", self.home / ".local/state"))
        state_dir = state_home / "implement-loop"
        existing_runs = set(state_dir.glob("run-*")) if state_dir.exists() else set()
        command = [str(executable), "1"]
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

    def assert_missing_executable_guidance(
        self,
        result: subprocess.CompletedProcess[bytes],
        executable: str,
    ) -> None:
        assert result.returncode != 0, result.stdout.decode() + result.stderr.decode()
        output = (result.stdout + result.stderr).lower()
        for guidance in (
            executable.encode(),
            b"not found on path",
            b"install",
            b"add it to path",
            b"retry",
        ):
            assert guidance.lower() in output

    def assert_missing_executable_stopped_before_run(self, runs: list[Path]) -> None:
        state = self.load_state()
        assert state["events"] == []
        assert state["opencode_calls"] == []
        assert runs == []
        assert not (self.home / ".local/state/implement-loop").exists()
        assert not self.repository_lock_path().exists()

    def _read_until_output(
        self,
        descriptor: int,
        output: bytearray,
        fragment: bytes,
        *,
        timeout: float,
    ) -> None:
        deadline = time.monotonic() + timeout
        while fragment not in output:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"Timed out waiting for {fragment!r}; received {bytes(output)!r}"
                )
            readable, _, _ = select.select([descriptor], [], [], remaining)
            if not readable:
                raise AssertionError(
                    f"Timed out waiting for {fragment!r}; received {bytes(output)!r}"
                )
            try:
                chunk = os.read(descriptor, 4096)
            except OSError as error:
                raise AssertionError(
                    f"Caller output closed before {fragment!r} arrived: {error}"
                ) from error
            if not chunk:
                raise AssertionError(f"Caller output closed before {fragment!r} arrived")
            output.extend(chunk)

    def _assert_live_harness_output(
        self,
        *,
        terminal: bool,
        no_color: bool = False,
        include_failure: bool = False,
        live_output_fragment: bytes = LIVE_OUTPUT_FRAGMENT,
    ) -> bytes:
        ready_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ready_listener.bind(("127.0.0.1", 0))
        ready_listener.listen()
        ready_listener.settimeout(LIVE_READY_TIMEOUT)
        master: int | None = None
        slave: int | None = None
        process: subprocess.Popen[bytes] | None = None
        output = bytearray()

        try:
            env = dict(self.env)
            env["IMPLEMENT_LOOP_TEST_LIVE_OUTPUT_FRAGMENT"] = live_output_fragment.decode("utf-8")
            env["IMPLEMENT_LOOP_TEST_LIVE_OUTPUT_READY_PORT"] = str(ready_listener.getsockname()[1])
            if include_failure:
                env["IMPLEMENT_LOOP_TEST_LIVE_FAILURE"] = "1"
            if no_color:
                env["NO_COLOR"] = ""
            if terminal:
                master, slave = pty.openpty()
                stdout = slave
                stderr = slave
            else:
                stdout = subprocess.PIPE
                stderr = subprocess.STDOUT

            process = subprocess.Popen(
                [
                    str(SCRIPT),
                    "1",
                    "--max-iterations",
                    "1",
                ],
                cwd=self.repo,
                env=env,
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            if slave is not None:
                os.close(slave)
                slave = None

            with ready_listener.accept()[0] as ready_connection:
                ready_connection.settimeout(LIVE_READY_TIMEOUT)
                assert ready_connection.recv(1) == b"R"

            if terminal:
                assert master is not None
                output_descriptor = master
            else:
                assert process.stdout is not None
                output_descriptor = process.stdout.fileno()
            self._read_until_output(
                output_descriptor,
                output,
                live_output_fragment.splitlines()[0],
                timeout=LIVE_OUTPUT_TIMEOUT,
            )
            if include_failure:
                self._read_until_output(
                    output_descriptor,
                    output,
                    b"Live failed tool",
                    timeout=LIVE_OUTPUT_TIMEOUT,
                )

            assert process.poll() is None, "harness exited before the test released it"
            attempt_dirs = list(
                (self.home / ".local/state/implement-loop").glob("run-*/attempt-1-ticket-2")
            )
            assert len(attempt_dirs) == 1
            attempt_dir = attempt_dirs[0]
            assert (attempt_dir.parent / "active.pid").is_file()
            for fragment_line in live_output_fragment.splitlines():
                assert fragment_line in output
            heading = "── Agent ──".encode()
            if terminal and not no_color:
                assert b"\x1b[1;36m" + heading + b"\x1b[0m\r\nLive **agent** text\r\n\r\n" in output
                assert (
                    b"\x1b[32m\xe2\x9c\x93\x1b[0m "
                    b"\x1b[1mLive completed tool\x1b[0m "
                    b"\x1b[2m(live_tool)\x1b[0m" in output
                )
                assert b'  \x1b[2m\xe2\x94\x82\x1b[0m {"fixture":"live"}' in output
            else:
                newline = b"\r\n" if terminal else b"\n"
                assert heading + newline + b"Live **agent** text" + newline * 2 in output
                assert b"\xe2\x9c\x93 Live completed tool (live_tool)" in output
                assert b"\x1b[" not in output
            assert output.count(heading) == 1
            if include_failure:
                if terminal and not no_color:
                    assert (
                        b"\x1b[31m\xe2\x9c\x97\x1b[0m "
                        b"\x1b[1mLive failed tool\x1b[0m "
                        b"\x1b[2m(live_failure)\x1b[0m" in output
                    )
                else:
                    assert "✗ Live failed tool (live_failure)".encode() in output
            expected_events = [
                {
                    "type": "text",
                    "timestamp": 1,
                    "sessionID": "ses_live_fixture",
                    "part": {"type": "text", "text": "Live **agent** text"},
                },
                {
                    "type": "tool_use",
                    "timestamp": 1,
                    "sessionID": "ses_live_fixture",
                    "part": {
                        "type": "tool",
                        "tool": "live_tool",
                        "state": {
                            "status": "completed",
                            "title": "Live completed tool",
                            "input": {"fixture": "live"},
                            "output": live_output_fragment.decode("utf-8"),
                        },
                    },
                },
            ]
            if include_failure:
                expected_events.append(
                    {
                        "type": "tool_use",
                        "timestamp": 2,
                        "sessionID": "ses_live_fixture",
                        "part": {
                            "type": "tool",
                            "tool": "live_failure",
                            "state": {
                                "status": "error",
                                "title": "Live failed tool",
                                "error": "live failure details",
                            },
                        },
                    }
                )
            expected_log = "".join(
                json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
                for event in expected_events
            ).encode("utf-8")
            assert (attempt_dir / "agent.log").read_bytes() == expected_log

            assert process.stdin is not None
            process.stdin.write(b"x")
            process.stdin.flush()
            process.stdin.close()
            assert process.wait(timeout=LIVE_READY_TIMEOUT) == 0, bytes(output).decode(
                "utf-8", errors="replace"
            )

            call = self.load_state()["opencode_calls"][0]
            expected_no_color = "1" if no_color or not terminal else None
            expected_force_color = "1" if terminal and not no_color else "0"
            assert call["no_color"] == expected_no_color
            assert call["force_color"] == expected_force_color
            return bytes(output)
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
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=5)
                if process.stdout is not None:
                    process.stdout.close()
            if slave is not None:
                os.close(slave)
            if master is not None:
                os.close(master)
            ready_listener.close()

    def load_state(self) -> dict[str, object]:
        return json.loads(self.state_path.read_text())


class LoopFixture(_TemporaryHomeFixture, _LoopFixtureSupport):
    def __init__(self) -> None:
        self.initialize_home()
        self.initialize_loop()
