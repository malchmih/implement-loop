"""Best-effort delivery of host-local loop completion alerts."""

from __future__ import annotations

import shutil
import subprocess
import sys
from enum import Enum

_DELIVERY_TIMEOUT_SECONDS = 2.0
_GENERIC_IDENTITY = "Implement Loop"


class AlertSound(Enum):
    """Normal or failure cues selected by the loop lifecycle."""

    GLASS = "Glass"
    BASSO = "Basso"


_MACOS_SOUND_PATHS = {
    AlertSound.GLASS: "/System/Library/Sounds/Glass.aiff",
    AlertSound.BASSO: "/System/Library/Sounds/Basso.aiff",
}

_LINUX_THEME_EVENTS = {
    AlertSound.GLASS: "complete",
    AlertSound.BASSO: "dialog-error",
}


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


def _find_delivery_command(name: str) -> str | None:
    try:
        return shutil.which(name)
    except Exception:
        # Command discovery is optional just like command execution.
        return None


def _deliver_linux_alert(*, title: str, reason: str, sound: AlertSound) -> None:
    notify_send = _find_delivery_command("notify-send")
    if notify_send is not None:
        _run_delivery_command(
            [
                notify_send,
                "--hint=boolean:suppress-sound:true",
                "--",
                title,
                reason,
            ]
        )

    canberra_gtk_play = _find_delivery_command("canberra-gtk-play")
    if canberra_gtk_play is not None:
        _run_delivery_command([canberra_gtk_play, f"--id={_LINUX_THEME_EVENTS[sound]}"])


def deliver_alert(*, repository_name: str | None, reason: str, sound: AlertSound) -> None:
    """Attempt host-native alert channels without affecting loop execution."""

    title = repository_name or _GENERIC_IDENTITY
    if sys.platform == "darwin":
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
    elif sys.platform.startswith("linux"):
        _deliver_linux_alert(title=title, reason=reason, sound=sound)
