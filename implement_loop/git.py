"""Git checkout validation and a worktree-shared local lock."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .errors import LoopError


@dataclass(frozen=True)
class Checkout:
    """The target checkout and the state that must remain stable for a run."""

    root: Path
    branch: str
    initial_head: str
    common_dir: Path


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as error:
        raise LoopError(f"Unable to run Git: {error}") from error


def _checked_git(
    cwd: Path,
    *args: str,
    empty_failure_message: str | None = None,
) -> str:
    result = _git(cwd, *args)
    if result.returncode != 0:
        if (
            empty_failure_message is not None
            and result.returncode == 1
            and not result.stdout.strip()
            and not result.stderr.strip()
        ):
            raise LoopError(empty_failure_message)
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise LoopError(f"Git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def discover_checkout(cwd: Path) -> Checkout:
    """Resolve the caller's checkout, branch, commit, and common Git directory."""

    root_text = _checked_git(cwd, "rev-parse", "--show-toplevel")
    root = Path(root_text).resolve()
    branch = _checked_git(
        root,
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
        empty_failure_message=(
            "The checkout must be on an attached branch; detached HEAD is not supported."
        ),
    )
    initial_head = _checked_git(
        root,
        "rev-parse",
        "--verify",
        "--quiet",
        "HEAD^{commit}",
        empty_failure_message=(
            "The checkout must contain an existing commit; an unborn branch is not supported."
        ),
    )
    common_text = _checked_git(root, "rev-parse", "--git-common-dir")
    common_dir = Path(common_text)
    if not common_dir.is_absolute():
        common_dir = root / common_dir
    common_dir = common_dir.resolve()

    checkout = Checkout(
        root=root,
        branch=branch,
        initial_head=initial_head,
        common_dir=common_dir,
    )
    assert_checkout(checkout)
    return checkout


def current_branch(checkout: Checkout) -> str:
    return _checked_git(checkout.root, "symbolic-ref", "--quiet", "--short", "HEAD")


def current_head(checkout: Checkout) -> str:
    return _checked_git(checkout.root, "rev-parse", "--verify", "HEAD^{commit}")


def assert_checkout(checkout: Checkout) -> None:
    """Require the run's branch and a clean checkout, including submodules."""

    branch = current_branch(checkout)
    if branch != checkout.branch:
        raise LoopError(
            f"The branch changed from {checkout.branch} to {branch}; the checkout was preserved."
        )
    result = _git(
        checkout.root,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise LoopError(f"Unable to read the checkout status: {detail}")
    if result.stdout:
        raise LoopError(
            "The checkout is not clean, including untracked and submodule changes. "
            "Changes are preserved; commit or resolve them before running."
        )


def require_new_descendant(checkout: Checkout, before: str, after: str) -> None:
    """Require a new commit without accepting rewritten or unrelated history."""

    if before == after:
        raise LoopError("No new commit was made; the ticket stays open.")
    result = _git(
        checkout.root,
        "merge-base",
        "--is-ancestor",
        before,
        after,
    )
    if result.returncode == 1:
        raise LoopError("The agent rewrote or replaced existing history; the ticket stays open.")
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise LoopError(f"Unable to verify commit ancestry: {detail}")


class RepositoryLock:
    """An atomic directory lock shared by all worktrees of a repository."""

    def __init__(self, common_dir: Path) -> None:
        self.path = common_dir / "implement-loop.lock"
        self.acquired = False

    def acquire(self) -> None:
        try:
            os.mkdir(self.path, 0o700)
        except FileExistsError as error:
            raise LoopError(
                f"A loop is running or a stale lock exists at {self.path}. "
                "Verify its owner and any surviving agent work before removing it."
            ) from error
        except OSError as error:
            raise LoopError(f"Cannot create the local lock at {self.path}: {error}") from error

        try:
            with (self.path / "pid").open("x", encoding="ascii") as pid_file:
                pid_file.write(f"{os.getpid()}\n")
            (self.path / "pid").chmod(0o600)
            self.acquired = True
        except OSError as error:
            try:
                (self.path / "pid").unlink(missing_ok=True)
                self.path.rmdir()
            except OSError:
                pass
            raise LoopError(f"Cannot record the local lock owner: {error}") from error

    def release(self) -> None:
        if not self.acquired:
            return
        pid_path = self.path / "pid"
        try:
            owner = pid_path.read_text(encoding="ascii").strip()
            if owner != str(os.getpid()):
                print(
                    f"Warning: leaving lock at {self.path}; its owner marker changed.",
                    file=sys.stderr,
                )
                return
            pid_path.unlink()
            self.path.rmdir()
        except OSError as error:
            print(f"Warning: cannot release local lock {self.path}: {error}", file=sys.stderr)
        finally:
            self.acquired = False
