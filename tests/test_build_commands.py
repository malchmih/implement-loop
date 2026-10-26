from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAKE = shutil.which("make")


class BuildCommandTests(unittest.TestCase):
    def test_build_reports_unsupported_platform_before_using_uv(self) -> None:
        self.assertIsNotNone(MAKE, "test environment needs make")
        assert MAKE is not None

        with tempfile.TemporaryDirectory(prefix="implement-loop-build-platform-") as directory:
            fake_bin = Path(directory)
            fake_uname = fake_bin / "uname"
            fake_uname.write_text(
                "#!/bin/sh\n"
                'case "$1" in\n'
                "  -s) printf 'Linux\\n' ;;\n"
                "  -m) printf 'x86_64\\n' ;;\n"
                "  *) exit 1 ;;\n"
                "esac\n"
            )
            fake_uname.chmod(0o700)
            environment = dict(os.environ, PATH=str(fake_bin))

            result = subprocess.run(
                [MAKE, "build"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("only supported on macOS arm64", result.stderr)
