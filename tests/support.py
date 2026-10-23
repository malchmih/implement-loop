from __future__ import annotations

import json
import os
import pty
import select
import shlex
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "implement-loop"
INSTALLER = SCRIPT.parent / "install.sh"
REPOSITORY_URL = "https://api.github.com/repos/test/repo"
LIVE_OUTPUT_FRAGMENT = b"IMPLEMENT_LOOP_LIVE_OUTPUT_7F3A9C"
LIVE_OUTPUT_TIMEOUT = 15.0
LIVE_READY_TIMEOUT = 20.0

FAKE_TOOL = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import socket
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
        attempt_dir = next(
            (home / ".local/state/implement-loop").glob(
                f"run-*/attempt-*-ticket-{ticket_number}"
            )
        )
        attempt_dir.chmod(0o500)

    if state.get("fail_preclosure_read_after_harness"):
        state["fail_next_issue_read_for"] = ticket_number
        save_state()

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
    live_fragment = os.environ.get("IMPLEMENT_LOOP_TEST_LIVE_OUTPUT_FRAGMENT")
    if live_fragment is not None:
        sys.stdout.write(live_fragment)
        sys.stdout.flush()
        with socket.create_connection(
            ("127.0.0.1", int(os.environ["IMPLEMENT_LOOP_TEST_LIVE_OUTPUT_READY_PORT"])),
            timeout=20,
        ) as ready_connection:
            ready_connection.sendall(b"R")
        sys.stdin.buffer.read(1)
        sys.stdout.write("\n")
        sys.stdout.flush()
        sys.exit(int(state.get("agent_exit", 0)))
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
    if "--remove-assignee" in args:
        login = args[args.index("--remove-assignee") + 1]
        assignee = state["login"] if login == "@me" else login
        ticket["assignees"] = [
            current for current in ticket["assignees"] if current["login"] != assignee
        ]
        state.setdefault("events", []).append(["unassign", number, login])
        save_state()
        sys.exit(0)
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
    if state.get("remove_copilot_before_invocation"):
        (Path(os.environ["PATH"].split(os.pathsep)[0]) / "copilot").unlink()
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


class TemporaryHomeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="implement-loop-test-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.env = _isolated_git_environment(self.home)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env["PATH"]
        self.invocation_markers: dict[str, Path] = {}

    def add_invocation_marker(self, command: str) -> Path:
        executable = self.bin / command
        marker = self.home / f"{command}.invoked"
        executable.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(marker))}\n")
        executable.chmod(0o700)
        self.invocation_markers[command] = marker
        return marker

    def assert_commands_not_invoked(self, *commands: str) -> None:
        for command in commands:
            marker = self.invocation_markers.get(command)
            self.assertIsNotNone(marker, f"no invocation marker configured for {command}")
            assert marker is not None
            self.assertFalse(marker.exists(), f"{command} was invoked; marker: {marker}")

    def copy_source_tree(self) -> Path:
        source = self.home / "source checkout"
        shutil.copytree(
            SCRIPT.parent,
            source,
            ignore=shutil.ignore_patterns(
                ".git", "__pycache__", ".venv", ".mypy_cache", ".ruff_cache"
            ),
        )
        return source

    def run_installer(
        self,
        source: Path,
        destination: Path | str | None = None,
        *,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        command = [str(source / INSTALLER.name)]
        if destination is not None:
            command.append(os.fspath(destination))
        return subprocess.run(
            command,
            cwd=self.home,
            env=env if env is not None else self.env,
            capture_output=True,
            timeout=30,
        )


class LoopTestCase(TemporaryHomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.home / "repo"
        self.repo.mkdir()
        for tool in ("gh", "copilot", "opencode"):
            path = self.bin / tool
            path.write_text(FAKE_TOOL)
            path.chmod(0o700)

        self.state_path = self.home / "github-state.json"
        self.state = self.default_state()
        self.save_state()
        self.env.pop("GH_REPO", None)
        self.env.pop("XDG_CONFIG_HOME", None)
        self.env.pop("XDG_STATE_HOME", None)
        self.env.pop("NO_COLOR", None)
        self.env.pop("FORCE_COLOR", None)
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
        executable: Path = SCRIPT,
    ) -> tuple[subprocess.CompletedProcess[bytes], list[Path]]:
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        state_home = Path(env.get("XDG_STATE_HOME", self.home / ".local/state"))
        state_dir = state_home / "implement-loop"
        existing_runs = set(state_dir.glob("run-*")) if state_dir.exists() else set()
        command = [str(executable), "1"]
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
                self.fail(f"Timed out waiting for {fragment!r}; received {bytes(output)!r}")
            readable, _, _ = select.select([descriptor], [], [], remaining)
            if not readable:
                self.fail(f"Timed out waiting for {fragment!r}; received {bytes(output)!r}")
            try:
                chunk = os.read(descriptor, 4096)
            except OSError as error:
                self.fail(f"Caller output closed before {fragment!r} arrived: {error}")
            if not chunk:
                self.fail(f"Caller output closed before {fragment!r} arrived")
            output.extend(chunk)

    def _assert_live_harness_output(self, harness: str, *, terminal: bool) -> None:
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
            env["IMPLEMENT_LOOP_TEST_LIVE_OUTPUT_FRAGMENT"] = LIVE_OUTPUT_FRAGMENT.decode("ascii")
            env["IMPLEMENT_LOOP_TEST_LIVE_OUTPUT_READY_PORT"] = str(ready_listener.getsockname()[1])
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
                    "--harness",
                    harness,
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
                self.assertEqual(ready_connection.recv(1), b"R")

            if terminal:
                assert master is not None
                output_descriptor = master
            else:
                assert process.stdout is not None
                output_descriptor = process.stdout.fileno()
            self._read_until_output(
                output_descriptor,
                output,
                LIVE_OUTPUT_FRAGMENT,
                timeout=LIVE_OUTPUT_TIMEOUT,
            )

            self.assertIsNone(process.poll(), "harness exited before the test released it")
            attempt_dirs = list(
                (self.home / ".local/state/implement-loop").glob("run-*/attempt-1-ticket-2")
            )
            self.assertEqual(len(attempt_dirs), 1)
            attempt_dir = attempt_dirs[0]
            self.assertTrue((attempt_dir.parent / "active.pid").is_file())
            self.assertIn(LIVE_OUTPUT_FRAGMENT, output)
            self.assertEqual((attempt_dir / "agent.log").read_bytes(), LIVE_OUTPUT_FRAGMENT)

            assert process.stdin is not None
            process.stdin.write(b"x")
            process.stdin.flush()
            process.stdin.close()
            self.assertEqual(
                process.wait(timeout=LIVE_READY_TIMEOUT),
                0,
                bytes(output).decode("utf-8", errors="replace"),
            )

            call = self.load_state()[f"{harness}_calls"][0]
            self.assertEqual(call["no_color"], None if terminal else "1")
            self.assertEqual(call["force_color"], "1" if terminal else "0")
        finally:
            if process is not None:
                if process.stdin is not None and not process.stdin.closed:
                    try:
                        process.stdin.write(b"x")
                        process.stdin.flush()
                    except (BrokenPipeError, OSError, ValueError):
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

    def install_test_command(self) -> tuple[Path, Path]:
        source = self.copy_source_tree()
        result = self.run_installer(source)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return source, self.home / ".local/bin/implement-loop"

    def load_state(self) -> dict[str, object]:
        return json.loads(self.state_path.read_text())
