# Implement Loop

A personal, sequential Copilot CLI loop driven by an existing GitHub spec issue.
Run the executable from the target repository's working directory.

```bash
cd /path/to/target-repository
/path/to/implement-loop/implement-loop 123
/path/to/implement-loop/implement-loop 123 --max-iterations 10
```

The executable lives in this source checkout; no launcher or global installation
is provided. Use `./implement-loop --help` from this checkout to see usage without
starting a loop.

The default limit is **5 ticket attempts**. Each Copilot invocation counts as one
attempt, not each tool call or model turn. Limits must be positive integers.
The loop stops immediately when no eligible ticket exists; it does not poll.

## Prerequisites

- Bash 3.2 or later, Git, GitHub CLI (`gh`), `jq`, and Copilot CLI (`copilot`).
- Authenticated GitHub and Copilot sessions, with permission to read native issue
  relationships, assign issues to yourself, comment, and close issues.
- Copilot CLI supporting `-p`, `--model`, `--reasoning-effort`, `--stream`,
  `--output-format`, `--allow-all-tools`, and `--deny-tool`.
- Your personal `/implement` skill, available to Copilot. The installed skill
  implements, validates, reviews, and commits work to the current branch.
- Access to **GPT-6 Luna**, requested as `gpt-6-luna` with reasoning effort `max`.
  Model rejection stops the loop; there is no fallback.
- A clean checkout on an existing branch with at least one commit. Configure the
  repository's normal validation tools before running.

Do not set `GH_REPO`; the script infers the repository through `gh repo view` in
the checkout. Native API calls use that repository's GitHub host explicitly.

## Queue and ownership

The spec must be an open issue, not a PR. Every direct child must be an issue in
the same repository and is treated as an implementation ticket. Children are
already prepared by you; the loop never decomposes the spec or creates tickets.
Nested grandchildren are not candidates. Cross-repository children cause a
clear error rather than being implemented in the wrong checkout.

Native GitHub sub-issues and dependencies are required. There is no task-list,
label, or `Blocked by:` prose fallback. Missing dependency metadata or an API
failure stops the loop rather than assuming work is unblocked. Blockers may live
outside the spec; GitHub's count of open native blockers is the eligibility gate.

Selection takes the first open child in GitHub's returned sub-issue order with
zero open blockers and no assignee. Assigned tickets are skipped even when
assigned to you. The wrapper assigns the selected ticket to the authenticated
GitHub user, rechecks its ownership, dependencies, and parent membership, and
invokes `/implement` for **only that ticket**. It supplies the parent spec and
both issues' comments as context. The queue is refreshed after every completion.

Subagents are allowed within the selected ticket, including the parallel
Standards and Spec reviews required by `/code-review`. They inherit the same
scope and execution restrictions. The implementing agent must wait for all
subagents to finish and incorporate their results before declaring completion.
Review completion remains part of the agent's attestation, not a separate
wrapper check.

GitHub assignment is not an atomic claim: do not use concurrent loops from other
machines against the same queue. A local lock in the repository's common Git
directory prevents concurrent invocations, including through other worktrees.
This does not stop unrelated editors or agents from modifying your checkout.

This is a fresh rename of `copilot-ralph`, with independent lock and state names
and no compatibility layer. **Do not run the old and new versions concurrently
against the same checkout or queue:** their locks do not protect against each
other. Importing this source does not modify or retire the old installation.

## Completion and failure

Completion means **committed locally**, not reviewed or merged through a PR.
The wrapper closes the ticket with a comment containing the branch and commit,
which makes its dependants eligible. It never intentionally pushes, opens PRs,
merges, or changes branches.

The wrapper requires all of these before closure:

- Copilot exits with status zero and its log is successfully written.
- Exactly one valid `IMPLEMENT_LOOP_COMPLETE` record identifies the selected
  ticket and reports that acceptance criteria and required validation passed.
- The branch is unchanged, a new descendant commit exists without rewriting
  prior history, and the checkout is clean, including untracked files.
- The spec is still open and the ticket is still an open, unblocked direct child
  exclusively assigned to the authenticated user.

The completion record is an **agent attestation**, not independent verification
of every criterion or test result. The wrapper does not run a second test suite
or inspect the semantic contents of the commit. An agent with broad permissions
can make mistakes or falsify output; these checks are not a security boundary.

On a failed invocation, unavailable model, missing completion evidence, dirty
checkout, changed ownership, or GitHub failure, the loop stops with nonzero
status. It preserves commits, uncommitted changes, and issue assignments.
There are no automatic retries, resets, unassignments, or stashes. Failed run
records are subject to the same retention policy as successful runs.
If comment/closure operations partially succeed, inspect GitHub before restarting.

After a failure, inspect the log and checkout, resolve any partial work, and
deliberately close or unassign the ticket as appropriate. A restart skips a
ticket still assigned to you. No resume database is required: GitHub issue state
is the queue's source of truth.

## Progress and private records

Native text streaming shows Copilot output and iteration/ticket headers without
a custom UI. Each invocation starts a fresh Copilot session; there is no
conversation carry-over between tickets.

Copilot's native colors are enabled when the loop's stdout is a terminal, even
though its output passes through `tee`. Setting `NO_COLOR` (including an empty
value) or redirecting stdout disables colors, overriding an inherited
`FORCE_COLOR`. Loop headers remain unstyled. `agent.log` preserves the original
output, including any ANSI colors; only completion parsing strips ANSI SGR
styling, so a colored `IMPLEMENT_LOOP_COMPLETE` record is recognized without
relaxing the completion checks. No pseudo-terminal is used.

Per-run records are retained under:

```text
~/.local/state/implement-loop/run-<UTC timestamp>-<suffix>/
```

Records include repository metadata, issue snapshots, prompts, comments, agent
output, process exit codes, completion records, and successful commit hashes.
Files are created with private permissions; nothing is uploaded by the wrapper.
They can contain confidential code and issue content, so do not publish them.
On startup, the wrapper deletes entire run directories whose UTC start timestamp
in the directory name is more than seven days (168 hours) old, including failed
runs. This removes all records for those runs, not just `agent.log`. Cleanup
does not run while the tool is unused, and recent file activity does not extend
retention. Cleanup failures produce warnings without blocking the new run; a
later startup retries.

Active runs are protected by an `active.pid` marker, removed on normal exits,
failures, and catchable interrupts. Marked runs are never automatically deleted,
even if the recorded process has exited: an orphaned agent might still be
writing. After an uncatchable termination, verify that both the loop and its
agents have stopped before removing that run's specific `active.pid` file.
The next startup can then expire the run. Existing directories without markers
in the new state directory are included in cleanup. The old
`~/.local/state/copilot-ralph/` directory is not read, migrated, or cleaned up.
Unrecognized names and symlinked run directories are left untouched.

The full prompt is limited to 100 KB to avoid operating-system argument limits.
Oversized issue/comment context fails before claiming or invoking the agent.

Normal exits and catchable interrupts release the local lock. After an
uncatchable termination, inspect `<common-git-directory>/implement-loop.lock/pid`
and verify no loop or agent is still running before removing that specific
`pid` file and empty lock directory. Never remove another active run's lock.

## Trust and permissions

**Use only with trusted repositories, skills, specs, and comments.** Copilot gets
broad tool approval so implementation and commits can run unattended.
The prompt forbids work outside the selected ticket and GitHub lifecycle writes;
CLI tool denials cover common push, branch/history mutation, PR, and issue-write
commands. GitHub writes are owned by the wrapper.

These are guardrails, **not a sandbox or exhaustive command filter**. Equivalent
commands, shell scripts, APIs, other tools, and network access are not completely
restricted. The script does not automatically approve external paths or URLs;
if extra permission or input is required, the unattended run cannot complete
until addressed. There is no timeout or spend cap: five ticket attempts can
still involve substantial work and model usage. Interrupt explicitly if needed.

## Files and runtime data

- Executable: `implement-loop` at this repository's root.
- Documentation and regression tests: `README.md` and `tests/test_loop.py`.
- Run records: `~/.local/state/implement-loop/`.
- Local lock: `<common-git-directory>/implement-loop.lock/` in the target
  repository.

## Regression tests

With Python 3.9 or later and the normal Bash, Git, and `jq` prerequisites:

```bash
python3 -m unittest discover -s tests -v
```

The tests run the outer loop in temporary local repositories with mocked Copilot
and GitHub commands. They cover terminal/redirected color settings, raw log
preservation, colored completion records, and rejection of invalid completion
evidence. Retention tests cover the exact seven-day cutoff, active/stale markers,
legacy run deletion, unrelated entries and symlinks, and nonfatal cleanup
failures. They do not contact GitHub or invoke a model.
