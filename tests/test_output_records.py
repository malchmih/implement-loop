from __future__ import annotations

import json
import os
import pty
import stat
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from support import SCRIPT, LoopTestCase


class OutputRecordsTests(LoopTestCase):
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
