# Workflow and recovery

## Before a run

Install the source command for Linux or macOS using the [README instructions](../README.md#install-and-run).
The installation needs the latest stable supported CPython and uv; uv may use a
system or uv-managed Python. Add uv's tool bin directory to `PATH` if needed.
The target checkout must have an existing commit and attached branch, with no
staged, unstaged, untracked, or submodule changes. Check it with
`git status --short --branch`.

A run also requires:

- Git and GitHub CLI (`gh`), authenticated with permission to read native child
  issues and dependencies, assign issues, comment, and close issues. Check the
  session with `gh auth status`; a valid session does not guarantee repository
  write permission.
- OpenCode V2, with authentication and an available model.
- The external skill named `implement`. It is not bundled, installed, or
  configured by Implement Loop. OpenCode discovers personal skills in
  `~/.agents/skills/implement/SKILL.md`, `~/.claude/skills/implement/SKILL.md`,
  and `~/.config/opencode/skills/implement/SKILL.md`, and project skills in
  `.agents/skills/implement/SKILL.md`, `.claude/skills/implement/SKILL.md`, and
  `.opencode/skills/implement/SKILL.md`.

The runner does not install or configure these tools. A command being on `PATH`
does not confirm authentication, model access, or successful skill loading.

## Run a spec

Run from the target checkout:

```bash
implement-loop 123
implement-loop 123 --max-iterations 10
```

OpenCode is the only supported agent CLI. The default limit is ten ticket
Attempts. The optional user TOML file controls only the root OpenCode model and
variant; the limit is set with `--max-iterations`. See
[configuration](configuration.md) for supported settings and intentional
compatibility breaks.

The loop considers direct child issues in GitHub's returned order. It skips
closed, blocked, or assigned tickets, including tickets assigned to you. After
each confirmed closure it refreshes the queue; it does not poll. A Loop run can
stop at its invocation limit or when no eligible child remains, and a successful
stop does not mean every child is complete. An Attempt is one OpenCode
invocation for a selected ticket. Preparing or assigning a ticket alone is not
an Attempt, and Ticket records can exist before one starts.

## Permissions, commits, and closure

Use only trusted repositories, issue content, and skills. OpenCode runs
unattended with the permissions available through its own configuration;
Implement Loop is not a sandbox and does not enforce a filesystem or network
boundary. Parent and ticket text is requirements context, not permission to
implement sibling tickets.

The runner assigns the selected ticket to you and supplies a private,
Attempt-bound reporting command outside the checkout. After implementation,
required review and validation, joined delegated work, and committing are
complete, report explicit passing claims with the command and syntax shown in
the Attempt prompt:

```bash
/path/to/implement-loop-report completed --acceptance-criteria passed --validation passed
/path/to/implement-loop-report --help
```

If you cannot complete the ticket, submit a blocked outcome with a meaningful
reason of at least five non-whitespace characters instead of claiming
completion:

```bash
/path/to/implement-loop-report blocked --reason "External dependency is unavailable"
```

The blocked reason is retained in private Ticket records and the run stops with
the ticket open and assigned. Missing or invalid reports are diagnosed
separately. The command rejects blank or shorter placeholder text; the agent is
responsible for making the reason a useful explanation. Identical repeated
reports are harmless; conflicting sequential reports (including switching
between blocked and completed) stop the Attempt without automatic closure. Do
not submit a second outcome to correct or replace the first one.

The command supplies ticket and Attempt identity; do not author a report file or
copy identifiers. Wait for reporting to finish and treat a nonzero result as a
failed handoff. Reporting itself does not close issues or change the checkout.
Only after the harness exits successfully does the runner check the persisted
Agent attestation, original branch, clean checkout, new local descendant commit,
and the ticket's current state before closing it. The commit stays local: the
runner does not push, open a PR, or merge. Agent attestation and wrapper checks
are not independent proof of semantic correctness or human approval.

## Troubleshoot missing output

Terminal previews can abbreviate tool output. When log persistence succeeds, the
full raw output is in the ticket's private `agent.log`; a log-write failure is
reported and can leave it incomplete. See
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

Records use the standard user state location
`${XDG_STATE_HOME:-$HOME/.local/state}/implement-loop`; there is no separate
records-path option or retention override. Records older than seven days are
eligible for conservative cleanup on a later startup. Treat any printed path as
authoritative if the environment's XDG state directory has changed.

Review the ticket's `agent.log`, `process.json`, result evidence, and `checks.json`
when present. Before rerunning an interrupted run, verify that the wrapper,
OpenCode, delegated agents, and other background work have stopped. Output, record
presence, and release of the local lock do not confirm that they have stopped.

### Blocked or conflicting report

An intentional blocked report is distinct from a missing or invalid handoff. Its
reason is recorded in `completion-result.json` and `completion-evidence.json`;
inspect these with the process status and live issue state. A blocked report
stops the run without retrying, unassigning, rolling back, or closing the ticket.
If OpenCode also exits unsuccessfully, both the process failure and blocked
reason are retained. Conflicting sequential reports preserve the competing
claims in the result evidence and prevent automatic completion.

Agents are cooperative but fallible: finish and join delegated work, wait for
the reporting command to finish, and do not submit detached or late reports.
Completion reports are Agent attestations, not independent correctness checks
or human approval. For already-committed work, inspect the records and use the
separate [Manual finalization](#manually-finalize-approved-committed-work) flow
if appropriate.

### Uncertain assignment or closure

If an assignment command failed, it may still have taken effect. If a close
command or the follow-up confirmation read failed, the issue may already be
closed. Check GitHub before retrying:

```bash
TICKET=456
gh issue view "$TICKET" --json state,assignees --jq '{state, assignees: [.assignees[].login]}'
gh issue view "$TICKET" --comments
```

An issue still assigned to you is skipped on the next run. If it is open and
still assigned to you, remove your assignment deliberately before retrying:

```bash
gh issue edit "$TICKET" --remove-assignee @me
```

For an uncertain closure, inspect `closure-comment.txt`, `checks.json`, and
`closed-ticket.json` in the ticket records when present. A missing
`closed-ticket.json` does not prove that closure failed. If the issue is closed,
do not close it again; if it is still open, review the commit and records before
retrying or [manually finalizing approved work](#manually-finalize-approved-committed-work).

### Stale lock or active-run marker

The local lock is in the repository's Git common directory and coordinates
worktrees on this machine. From the target checkout, inspect its owner:

```bash
lock="$(git rev-parse --git-common-dir)/implement-loop.lock"
pid="$(cat "$lock/pid")"
ps -p "$pid" -o pid=,command=
```

Check that the PID is the expected wrapper and that OpenCode, agents, and
other background work have also stopped. Only then remove that specific stale
lock:

```bash
rm "$lock/pid"
rmdir "$lock"
```

An active Loop run has an `active.pid` marker in its run record; interruption
before cleanup can leave it behind. Retention keeps a marked run, even if the
marker looks stale. Inspect its owner and related work before removing only
that run's marker:

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
