from datetime import datetime, timezone
import json
import os
from pathlib import Path
import pty
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "implement-loop"
RECORD = (
    'IMPLEMENT_LOOP_COMPLETE {"issue":2,"status":"completed",'
    '"acceptance_criteria":"passed","validation":"passed"}'
)
FAKE_TOOL = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys

home = Path(os.environ["HOME"])
args = sys.argv[1:]
if Path(sys.argv[0]).name == "copilot":
    (home / "color-env.json").write_text(json.dumps({
        key: os.environ.get(key) for key in ("NO_COLOR", "FORCE_COLOR")
    }))
    Path("implementation.txt").write_text("implemented\n")
    subprocess.run(["git", "add", "implementation.txt"], check=True)
    subprocess.run([
        "git", "commit", "--quiet", "-m",
        "Implement fixture\n\nCo-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"
    ], check=True)
    sys.stdout.write("Agent output\n" + os.environ["IMPLEMENT_LOOP_TEST_OUTPUT"] + "\n")
    sys.exit(int(os.environ.get("IMPLEMENT_LOOP_TEST_AGENT_EXIT", "0")))

repository_url = "https://api.github.com/repos/test/repo"
spec = {"number": 1, "state": "open", "repository_url": repository_url}
ticket = {
    "number": 2, "state": "open", "repository_url": repository_url,
    "assignees": [{"login": "tester"}] if (home / "assigned").exists() else [],
    "issue_dependencies_summary": {"blocked_by": 0},
}
if (home / "closed").exists():
    ticket["state"] = "closed"

if args[:2] == ["repo", "view"]:
    lock = Path(".git/implement-loop.lock/pid")
    if not lock.is_file() or lock.read_text().strip() != str(os.getppid()):
        sys.exit("Missing implement-loop lock")
    markers = (home / ".local/state/implement-loop").glob("run-*/active.pid")
    if not any(path.read_text().strip() == str(os.getppid()) for path in markers):
        sys.exit("Missing live run marker")
    result = {"nameWithOwner": "test/repo", "url": "https://github.com/test/repo"}
elif args[:2] == ["issue", "edit"]:
    (home / "assigned").touch()
    sys.exit(0)
elif args[:2] == ["issue", "close"]:
    (home / "closed").touch()
    sys.exit(0)
elif args[:1] == ["api"]:
    endpoint = args[-1]
    if endpoint == "user":
        result = {"login": "tester"}
    elif endpoint.endswith("/comments?per_page=100"):
        result = [[]]
    elif endpoint.endswith("/sub_issues?per_page=100"):
        result = [[ticket]]
    elif endpoint == "repos/test/repo/issues/1":
        result = spec
    elif endpoint == "repos/test/repo/issues/2":
        result = ticket
    else:
        sys.exit("Unexpected API endpoint: " + endpoint)
else:
    sys.exit("Unexpected gh arguments: " + repr(args))
print(json.dumps(result))
'''


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="implement-loop-test-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo = self.home / "repo"
        self.repo.mkdir()
        self.bin = self.home / "bin"
        self.bin.mkdir()
        for tool in ("gh", "copilot"):
            path = self.bin / tool
            path.write_text(FAKE_TOOL)
            path.chmod(0o700)
        self.env = dict(os.environ, HOME=str(self.home))
        for key in ("GH_REPO", "NO_COLOR", "FORCE_COLOR"):
            self.env.pop(key, None)
        self.env["PATH"] = str(self.bin) + os.pathsep + os.environ["PATH"]
        self.git("init", "--quiet")
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        self.git("config", "user.name", "Implement Loop Test")
        self.git("config", "user.email", "implement-loop-test@example.invalid")
        self.git(
            "commit", "--quiet", "--allow-empty", "-m",
            "Initial fixture\n\nCo-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>",
        )

    def git(self, *args):
        subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env,
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def run_loop(self, output=RECORD, terminal=False, agent_exit=0):
        env = dict(self.env, IMPLEMENT_LOOP_TEST_OUTPUT=output, IMPLEMENT_LOOP_TEST_AGENT_EXIT=str(agent_exit))
        command = ["bash", str(SCRIPT), "1", "--max-iterations", "1"]
        state = self.home / ".local/state/implement-loop"
        existing_runs = set(state.glob("run-*"))
        if terminal:
            master, slave = pty.openpty()
            try:
                result = subprocess.run(
                    command, cwd=self.repo, env=env, stdout=slave,
                    stderr=subprocess.STDOUT, timeout=30,
                )
            finally:
                os.close(slave)
                os.close(master)
        else:
            result = subprocess.run(
                command, cwd=self.repo, env=env, capture_output=True, timeout=30,
            )
        runs = list(set(state.glob("run-*")) - existing_runs)
        self.assertEqual(len(runs), 1)
        self.assertFalse((runs[0] / "active.pid").exists())
        iteration = runs[0] / "iteration-1-ticket-2"
        self.assertEqual(
            (iteration / "agent.log").read_bytes(),
            ("Agent output\n" + output + "\n").encode(),
        )
        self.assertEqual(
            json.loads((iteration / "process.json").read_text()),
            {"agent_exit": agent_exit, "log_exit": 0},
        )
        self.assertFalse((self.repo / ".git/implement-loop.lock").exists())
        return result, iteration

    def assert_completed(self, output):
        result, iteration = self.run_loop(output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / "closed").exists())
        self.assertEqual(
            json.loads((iteration / "completion.json").read_text()),
            json.loads(RECORD.removeprefix("IMPLEMENT_LOOP_COMPLETE ")),
        )

    def assert_rejected(self, output):
        result, _ = self.run_loop(output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"No unique valid completion record", result.stderr)
        self.assertFalse((self.home / "closed").exists())
        self.assertTrue((self.home / "assigned").exists())

    def test_plain_completion(self):
        self.assert_completed(RECORD)

    def test_executable_help_uses_new_name_without_starting_a_run(self):
        result = subprocess.run(
            [str(SCRIPT), "--help"], cwd=self.repo, env=self.env,
            capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"Usage: implement-loop SPEC_NUMBER", result.stdout)
        self.assertNotIn(b"copilot-ralph", result.stdout)
        self.assertFalse((self.home / ".local/state/implement-loop").exists())
        self.assertFalse((self.repo / ".git/implement-loop.lock").exists())

    def test_old_installation_state_and_lock_are_untouched(self):
        old_state = self.home / ".local/state/copilot-ralph"
        old_run = old_state / "run-20000101T000000Z-ABC123"
        old_run.mkdir(parents=True)
        old_log = old_run / "agent.log"
        old_log.write_text("preserve old logs\n")
        old_lock = self.repo / ".git/copilot-ralph.lock"
        old_lock.mkdir()
        old_pid = old_lock / "pid"
        old_pid.write_text("legacy process\n")
        result, _ = self.run_loop()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(old_log.read_text(), "preserve old logs\n")
        self.assertEqual(old_pid.read_text(), "legacy process\n")

    def test_colored_completion_and_json(self):
        colored = RECORD.replace("IMPLEMENT_LOOP_", "\x1b[1;32mIMPLEMENT_LOOP_\x1b[0m")
        colored = colored.replace('"completed"', '"\x1b[38;2;20;200;30mcompleted\x1b[39m"')
        self.assert_completed("\x1b[32m" + colored + "\x1b[0m")

    def test_colon_color_parameters(self):
        self.assert_completed("\x1b[38:2::20:200:30m" + RECORD + "\x1b[m")

    def test_duplicate_plain_and_colored_records(self):
        self.assert_rejected(RECORD + "\n\x1b[32m" + RECORD + "\x1b[0m")

    def test_malformed_colored_json(self):
        self.assert_rejected("\x1b[32mIMPLEMENT_LOOP_COMPLETE {bad json}\x1b[0m")

    def test_missing_record(self):
        self.assert_rejected("Not complete")

    def test_embedded_marker_is_not_a_record(self):
        self.assert_rejected("Example: \x1b[32m" + RECORD + "\x1b[0m")

    def test_wrong_ticket(self):
        self.assert_rejected(RECORD.replace('"issue":2', '"issue":3'))

    def test_failed_validation(self):
        self.assert_rejected(RECORD.replace('"validation":"passed"', '"validation":"failed"'))

    def test_agent_failure_preserves_exit_status(self):
        result, _ = self.run_loop("\x1b[32m" + RECORD + "\x1b[0m", agent_exit=7)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Copilot exited with status 7", result.stderr)
        self.assertFalse((self.home / "closed").exists())

    def prepare_retention(self):
        self.retention_now = 1792497600
        self.state = self.home / ".local/state/implement-loop"
        self.state.mkdir(parents=True)
        self.env["IMPLEMENT_LOOP_TEST_NOW"] = str(self.retention_now)
        self.env["IMPLEMENT_LOOP_TEST_DATE"] = shutil.which("date")
        path = self.bin / "date"
        path.write_text(
            "#!/usr/bin/env python3\n"
            "import os, subprocess, sys\n"
            'if sys.argv[1:] == ["+%s"]:\n'
            '    print(os.environ["IMPLEMENT_LOOP_TEST_NOW"])\n'
            "else:\n"
            '    sys.exit(subprocess.call([os.environ["IMPLEMENT_LOOP_TEST_DATE"], *sys.argv[1:]]))\n'
        )
        path.chmod(0o700)

    def old_run(self, age, suffix="ABC123"):
        stamp = datetime.fromtimestamp(
            self.retention_now - age, timezone.utc,
        ).strftime("%Y%m%dT%H%M%SZ")
        path = self.state / f"run-{stamp}-{suffix}"
        iteration = path / "iteration-1-ticket-2"
        iteration.mkdir(parents=True)
        (iteration / "agent.log").write_text("diagnostic output\n")
        (path / "run.json").write_text("{}\n")
        return path

    def test_retention_uses_start_time_and_strict_seven_day_cutoff(self):
        self.prepare_retention()
        week = 7 * 24 * 60 * 60
        expired = self.old_run(week + 1)
        boundary = self.old_run(week)
        recent = self.old_run(week - 1)
        future = self.old_run(-60)
        # Newly written diagnostics must not extend retention.
        os.utime(expired / "run.json", (self.retention_now, self.retention_now))
        self.assertGreater((expired / "run.json").stat().st_mtime, self.retention_now - week)
        result, _ = self.run_loop()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(expired.exists())
        for kept in (boundary, recent, future):
            self.assertTrue((kept / "iteration-1-ticket-2/agent.log").exists())

    def test_retention_preserves_active_and_stale_marked_runs(self):
        self.prepare_retention()
        active = self.old_run(8 * 24 * 60 * 60)
        stale = self.old_run(9 * 24 * 60 * 60)
        (active / "active.pid").write_text(str(os.getpid()) + "\n")
        (stale / "active.pid").write_text("invalid or stale PID\n")
        result, _ = self.run_loop()
        self.assertEqual(result.returncode, 0, result.stderr)
        for kept in (active, stale):
            self.assertTrue((kept / "iteration-1-ticket-2/agent.log").exists())
            self.assertIn(str(kept).encode(), result.stderr)
        self.assertEqual(result.stderr.count(b"Keeping expired run with an active marker"), 2)

    def test_retention_preserves_unrecognized_names_and_symlinks(self):
        self.prepare_retention()
        target = self.home / "unrelated"
        target.mkdir()
        (target / "agent.log").write_text("keep\n")
        names = (
            "other-data",
            "run-not-a-timestamp-ABC123",
            "run-20000101T000000Z-unexpected-suffix",
            "run-20000230T000000Z-ABC123",
        )
        for name in names:
            (self.state / name).mkdir()
        link = self.state / "run-20000101T000000Z-ABC123"
        link.symlink_to(target, target_is_directory=True)
        regular = self.state / "run-20000102T000000Z-ABC123"
        regular.write_text("not a directory\n")
        result, _ = self.run_loop()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all((self.state / name).is_dir() for name in names))
        self.assertTrue(link.is_symlink())
        self.assertEqual((target / "agent.log").read_text(), "keep\n")
        self.assertTrue(regular.is_file())
        self.assertIn(b"Cannot read the start timestamp", result.stderr)

    def test_retention_failure_warns_and_continues(self):
        self.prepare_retention()
        expired = self.old_run(8 * 24 * 60 * 60)
        self.env["IMPLEMENT_LOOP_TEST_REMOVE_FAILURE"] = str(expired)
        self.env["IMPLEMENT_LOOP_TEST_RM"] = shutil.which("rm")
        path = self.bin / "rm"
        path.write_text(
            "#!/usr/bin/env python3\n"
            "import os, subprocess, sys\n"
            'if os.environ["IMPLEMENT_LOOP_TEST_REMOVE_FAILURE"] in sys.argv[1:]:\n'
            "    sys.exit(1)\n"
            'sys.exit(subprocess.call([os.environ["IMPLEMENT_LOOP_TEST_RM"], *sys.argv[1:]]))\n'
        )
        path.chmod(0o700)
        result, _ = self.run_loop()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(expired.exists())
        self.assertTrue((self.home / "closed").exists())
        self.assertIn(b"Cannot remove expired run", result.stderr)
        self.assertIn(b"a later startup will retry", result.stderr)

    def assert_color_env(self, terminal, expected):
        result, _ = self.run_loop(terminal=terminal)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads((self.home / "color-env.json").read_text()), expected)

    def test_terminal_enables_color_through_tee(self):
        self.assert_color_env(True, {"NO_COLOR": None, "FORCE_COLOR": "1"})

    def test_redirect_disables_inherited_force_color(self):
        self.env["FORCE_COLOR"] = "3"
        self.assert_color_env(False, {"NO_COLOR": "1", "FORCE_COLOR": "0"})

    def test_terminal_respects_no_color(self):
        self.env.update(NO_COLOR="1", FORCE_COLOR="3")
        self.assert_color_env(True, {"NO_COLOR": "1", "FORCE_COLOR": "0"})

    def test_terminal_respects_empty_no_color(self):
        self.env["NO_COLOR"] = ""
        self.assert_color_env(True, {"NO_COLOR": "1", "FORCE_COLOR": "0"})


if __name__ == "__main__":
    unittest.main()
