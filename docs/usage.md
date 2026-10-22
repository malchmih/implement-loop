# Usage and recovery

## Prerequisites

- Python 3.11 or later, Git, GitHub CLI (`gh`), and GitHub Copilot CLI.
- Authenticated GitHub and Copilot sessions with permission to read native
  sub-issues/dependencies, assign issues, comment, and close issues.
- The external `implement` skill must be available to Copilot. The CLI discovers
  personal skills under `~/.agents/skills/` and `~/.copilot/skills/`; check that
  `copilot skill list` includes `implement` before running. The skill remains the
  owner of implementation, required validation, review, and commits.
- A checkout with an existing commit, an attached branch, and no staged,
  unstaged, untracked, or submodule changes.

The runner uses Copilot prompt mode (`-p`), `--model`, `--reasoning-effort`,
`--stream`, `--output-format`, and `--allow-all-tools`. These flags are present in
the locally verified Copilot CLI 1.0.103-0. The selected model is
`gpt-6-luna` with `max` reasoning; rejected or unavailable model selections stop
the run without fallback. No live model call is part of the project's tests.

## Run a spec

Run the executable from the target checkout, including from a subdirectory. This
intermediate slice has no installer, so use the source checkout's executable
path unless it is already on `PATH`:

```bash
/path/to/implement-loop/implement-loop 123
/path/to/implement-loop/implement-loop 123 --max-iterations 10
```

`./implement-loop --help` is side-effect free. The default limit is five
Copilot invocations; `--max-iterations` must be a positive integer. The command
uses the current checkout and its configured GitHub host. A conflicting
`GH_REPO` override is rejected. A matching override is allowed.

The runner uses native same-repository direct child issues, in GitHub's returned
order. It skips closed, blocked, or assigned tickets; assignments to the
authenticated user are skipped too. Labels do not affect eligibility. Missing
or malformed issue/dependency metadata, cross-repository children, and failed
GitHub reads stop the run rather than guessing. If no eligible child remains,
the command exits successfully without claiming that all children are complete.
The queue is refreshed after each confirmed closure; it does not poll.

The runner assigns the selected issue to the authenticated user, then rechecks
exclusive ownership, open state, blockers, and direct-child membership. It
supplies the complete parent/ticket bodies and comments as requirements context.
The parent is context, not permission to implement sibling tickets. Context
larger than the safe 100 KB Copilot prompt budget is rejected before assignment;
it is never silently truncated. Any delegated workers must follow the same
ticket scope and execution restrictions; the implementing agent must wait for
them and incorporate their results before reporting completion.

## Completion and issue lifecycle

Every attempt starts a fresh Copilot process. The prompt requests `/implement`
for only the selected issue, asks the skill to validate, review, and commit, and
supplies a unique attempt ID plus an initially absent result-file path outside
the checkout. The result must be a JSON object like:

```json
{
  "issue": 123,
  "attempt_id": "the-id-provided-for-this-attempt",
  "status": "completed",
  "acceptance_criteria": "passed",
  "validation": "passed"
}
```

Transcript markers do not complete a ticket. Missing, malformed, stale,
wrong-ticket, wrong-attempt, wrong-type, or unsuccessful result evidence is
rejected. A valid result is an agent attestation, not independent verification
of semantic correctness or test results.

Before closing, the wrapper requires successful Copilot exit and persisted
records, valid result evidence, the original branch, a clean checkout, and a
new descendant commit. It also confirms the parent is still open and that the
ticket remains open, unblocked, exclusively assigned, and a direct child. The
closure comment names the local branch and commit and accurately reports the
agent attestation and wrapper checks. It does not claim a push, merge, or PR.

## Output and private records

Copilot text output streams to the terminal and is stored in private per-run
records. Native terminal color is enabled when stdout is a terminal; `NO_COLOR`
(including an empty value) or redirected output disables it. The inherited
Copilot environment and configuration are retained; the wrapper adds no
command-denial list. `--allow-all-tools` is used for non-interactive execution,
but Copilot's own explicit denial rules still take precedence. This is trusted
local automation, not a sandbox.

Records are stored under `${XDG_STATE_HOME:-$HOME/.local/state}/implement-loop/`.
They include repository and user metadata, issue/comment snapshots, the full
prompt, process outcome, raw and validated completion evidence, agent output,
and successful commit references. Files and directories are private. Startup
cleanup removes only recognized, unmarked run directories older than seven
days. Marked, symlinked, unrecognized, malformed, and legacy
`copilot-ralph` data is preserved. Cleanup errors warn and do not block a run.
This retention policy does not control Copilot's own sessions, logs, telemetry,
or other harness-native storage.

## Failure and manual recovery

Failures stop the loop with a nonzero status. The runner does not retry, roll
back, stash, reset, unassign, or skip ahead. It preserves assignments, commits,
and working files. If GitHub closure partially succeeds, inspect the issue before
restarting.

After a failure:

1. Read the run's `agent.log`, `process.json`, context snapshots, result evidence,
   and `checks.json` when present.
2. Inspect the current branch, commit history, and working tree; resolve any
   partial work deliberately.
3. Check whether Copilot, delegated agents, or background work is still running.
   An interrupt or release of the local lock does **not** confirm descendant
   shutdown.
4. Inspect the GitHub issue and decide whether to keep it assigned, close it
   manually, or unassign it before rerunning. A ticket still assigned to you is
   intentionally skipped on the next run.

The local lock lives in the repository's Git common directory and coordinates
worktrees on this machine only. If a process is killed uncatchably, verify the
lock owner and surviving agent work before removing that specific stale lock.
There is no automatic session resume, cancellation guarantee, timeout, or spend
cap.
