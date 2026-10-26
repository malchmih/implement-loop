# Workflow and recovery

## Before a run

Install the command for your platform using the [README instructions](../README.md#install-and-run).
The target checkout must have an existing commit and attached branch, with no
staged, unstaged, untracked, or submodule changes. Check it with
`git status --short --branch`.

A run also requires:

- Git and GitHub CLI (`gh`), authenticated with permission to read native child
  issues and dependencies, assign issues, comment, and close issues. Check the
  session with `gh auth status`; a valid session does not guarantee repository
  write permission.
- OpenCode V2 (the default) or GitHub Copilot CLI (`--harness copilot`), with
  authentication and an available model. Only the selected harness is needed.
- The external skill named `implement`. It is not bundled, installed, or
  configured by Implement Loop. For a personal skill shared by both harnesses,
  use `~/.agents/skills/implement/SKILL.md`. OpenCode also discovers
  `~/.config/opencode/skills/`; Copilot also discovers `~/.copilot/skills/`.
  Project skill locations include `.agents/skills` and `.claude/skills` for
  both harnesses, plus `.opencode/skills` for OpenCode and `.github/skills` for
  Copilot.

The runner does not install or configure these tools. A command being on `PATH`
does not confirm authentication, model access, or successful skill loading.

## Run a spec

Run from the target checkout:

```bash
implement-loop 123
implement-loop 123 --harness copilot
implement-loop 123 --max-iterations 10
```

OpenCode is the default; `--harness` accepts `opencode` or `copilot`. The default
limit is five harness invocations. See [configuration](configuration.md) for
other settings and options.

The loop considers direct child issues in GitHub's returned order. It skips
closed, blocked, or assigned tickets, including tickets assigned to you. After
each confirmed closure it refreshes the queue; it does not poll. A Loop run can
stop at its invocation limit or when no eligible child remains, and a successful
stop does not mean every child is complete. An Attempt is one harness
invocation for a selected ticket. Preparing or assigning a ticket alone is not
an Attempt, and Ticket records can exist before one starts.

## Permissions, commits, and closure

Use only trusted repositories, issue content, and skills. The selected harness
runs unattended with the permissions available through its own configuration;
Implement Loop is not a sandbox and does not enforce a filesystem or network
boundary. Parent and ticket text is requirements context, not permission to
implement sibling tickets.

The runner assigns the selected ticket to you. After the external skill reports
completion, the runner checks the harness result, its result-file attestation,
the original branch, a clean checkout, a new local descendant commit, and the
ticket's current state before closing it. The commit stays local: the runner
does not push, open a PR, or merge. The result file and wrapper checks are not
independent proof of semantic correctness.

## Troubleshoot missing output

Terminal previews can abbreviate tool output. After a harness starts, its full
raw output remains in the ticket's private `agent.log`; see
[record location and privacy](configuration.md#record-location-and-privacy).

## Recovery

Failures do not automatically retry, roll back, stash, reset, resume, unassign,
or repair partial GitHub writes. Existing checkout changes, commits, and issue
assignments remain in place. Inspect them and resolve partial work deliberately
before rerunning.

### Interrupted or failed run

Start with the error and any printed run/ticket record paths. Check the checkout
and live issue, then inspect the records that exist:

```bash
TICKET=456
git status --short --branch
git log -5 --oneline
gh issue view "$TICKET" --comments
```

Review the ticket's `agent.log`, `process.json`, result evidence, and `checks.json`
when present. Before rerunning an interrupted run, verify that the wrapper,
harness, delegated agents, and other background work have stopped. Output, record
presence, and release of the local lock do not confirm that they have stopped.

### Uncertain assignment or closure

If an assignment command failed, it may still have taken effect. If a close
command or the follow-up confirmation read failed, the issue may already be
closed. Check GitHub before retrying:

```bash
TICKET=456
gh issue view "$TICKET" --json state,assignees --jq '{state, assignees: [.assignees[].login]}'
gh issue view "$TICKET" --comments
```

An issue still assigned to you is skipped on the next run. For an uncertain
closure, inspect `closure-comment.txt`, `checks.json`, and `closed-ticket.json`
in the ticket records when present. A missing `closed-ticket.json` does not
prove that closure failed. Decide from the live issue and records; the runner
does not retry or repair partial GitHub writes.

### Stale lock or active-run marker

The local lock is in the repository's Git common directory and coordinates
worktrees on this machine. From the target checkout, inspect its owner:

```bash
lock="$(git rev-parse --git-common-dir)/implement-loop.lock"
pid="$(cat "$lock/pid")"
ps -p "$pid" -o pid=,command=
```

Check that the PID is the expected wrapper and that its harness, agents, and
other background work have also stopped. Only then remove that specific stale
lock:

```bash
rm "$lock/pid"
rmdir "$lock"
```

Each run record also has an `active.pid` marker. Retention keeps a run while the
marker exists, even if it looks stale. Inspect its owner and related work before
removing only that run's marker:

```bash
marker="/path/to/run-record/active.pid"
pid="$(cat "$marker")"
ps -p "$pid" -o pid=,command=
# Only after verifying the wrapper and all related work have stopped:
rm "$marker"
```

Never remove a lock or marker based on age or PID alone. Removing a marker does
not stop a process; if the PID file is missing or unreadable, investigate rather
than guessing.

### Manually finalize approved, committed work

If work was committed before approval or the run stopped after committing but
before closure, inspect the live issue, checkout, commit, records, and any
surviving agent work. Complete required review and validation and obtain human
approval before closing an issue yourself:

```bash
TICKET=456
gh issue view "$TICKET" --comments
git status --short --branch
git log -1 --oneline
# After review and approval, prepare an accurate comment about the commit and checks:
gh issue close "$TICKET" --comment "$(cat /path/to/reviewed-finalization-comment.txt)"
```

Manual finalization is a human action, not a successful attempt or
wrapper-verified completion. Do not infer correctness from a commit, manufacture
an empty commit, or rewrite history to satisfy completion checks.
