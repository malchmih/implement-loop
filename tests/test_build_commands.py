from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import SCRIPT

MAKE = shutil.which("make")


class BuildCommandTests(unittest.TestCase):
    def run_build(self, system: str, machine: str) -> subprocess.CompletedProcess[str]:
        self.assertIsNotNone(MAKE, "test environment needs make")
        assert MAKE is not None
        with tempfile.TemporaryDirectory(prefix="implement-loop-build-platform-") as directory:
            fake_bin = Path(directory)
            (fake_bin / "uname").write_text(
                '#!/bin/sh\ncase "$1" in\n'
                f"  -s) printf '{system}\\n' ;;\n"
                f"  -m) printf '{machine}\\n' ;;\n"
                "  *) exit 1 ;;\nesac\n"
            )
            (fake_bin / "uv").write_text("#!/bin/sh\nprintf 'UV ARGS: %s\\n' \"$*\"\n")
            (fake_bin / "uname").chmod(0o700)
            (fake_bin / "uv").chmod(0o700)
            return subprocess.run(
                [MAKE, "build"],
                cwd=SCRIPT.parent,
                env=dict(os.environ, PATH=str(fake_bin)),
                capture_output=True,
                text=True,
                timeout=30,
            )

    def test_build_uses_platform_specific_pyinstaller_arguments(self) -> None:
        for system, machine in (("Darwin", "arm64"), ("Linux", "x86_64")):
            with self.subTest(system=system, machine=machine):
                result = self.run_build(system, machine)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("UV ARGS: run --locked --group build pyinstaller", result.stdout)
                self.assertIn("--onefile --name implement-loop", result.stdout)
                self.assertIn("--distpath dist", result.stdout)
                if system == "Darwin":
                    self.assertIn("--target-architecture arm64", result.stdout)
                else:
                    self.assertNotIn("--target-architecture", result.stdout)

    def test_build_rejects_unsupported_targets_before_using_uv(self) -> None:
        for system, machine in (("Linux", "aarch64"), ("Darwin", "x86_64"), ("FreeBSD", "x86_64")):
            with self.subTest(system=system, machine=machine):
                result = self.run_build(system, machine)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("only supported on macOS arm64 and Linux x86_64", result.stderr)
                self.assertNotIn("UV ARGS:", result.stdout)
