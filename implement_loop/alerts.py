"""Best-effort delivery of host-local loop completion alerts."""

from __future__ import annotations

import subprocess
import sys
from enum import Enum

_DELIVERY_TIMEOUT_SECONDS = 2.0
_GENERIC_IDENTITY = "Implement Loop"


class AlertSound(Enum):
    """Native macOS cues available to loop outcome alerts."""

    GLASS = "Glass"


_MACOS_SOUND_PATHS = {AlertSound.GLASS: "/System/Library/Sounds/Glass.aiff"}


def _run_delivery_command(arguments: list[str]) -> None:
    try:
        subprocess.run(
            arguments,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_DELIVERY_TIMEOUT_SECONDS,
        )
    except Exception:
        # Desktop integration is optional; it must not affect the loop result.
        pass


def deliver_alert(*, repository_name: str | None, reason: str, sound: AlertSound) -> None:
    """Attempt one macOS notification and one sound without affecting execution."""

    if sys.platform != "darwin":
        return

    title = repository_name or _GENERIC_IDENTITY
    _run_delivery_command(
        [
            "osascript",
            "-e",
            "on run argv",
            "-e",
            "display notification (item 2 of argv) with title (item 1 of argv)",
            "-e",
            "end run",
            title,
            reason,
        ]
    )
    _run_delivery_command(["afplay", _MACOS_SOUND_PATHS[sound]])
