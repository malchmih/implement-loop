# Workflow and recovery

## Prerequisites

- Python 3.11 or later, Git, and GitHub CLI (`gh`).
- macOS and Linux are supported; native Windows is not a target.
- An authenticated GitHub CLI session with permission to read native
  sub-issues/dependencies, assign issues, comment, and close issues.
- OpenCode V2 is the default and requires its CLI, authentication, and an
  available model. GitHub Copilot CLI (`--harness copilot`) is an optional
  alternative; only the selected harness is required for a run.
- The external skill named `implement`. Implement Loop does not bundle, install,
  or configure it. For a personal skill shared by both harnesses, put it at
  `~/.agents/skills/implement/SKILL.md`. OpenCode also discovers skills under
  `~/.config/opencode/skills/`; Copilot also discovers `~/.copilot/skills/`.
  Project skills are supported under `.opencode/skills`, `.agents/skills`, or
  `.claude/skills` for OpenCode, and `.github/skills`, `.agents/skills`, or
  `.claude/skills` for Copilot.
- A checkout with an existing commit, an attached branch, and no staged,
  unstaged, untracked, or submodule changes.

Before checkout discovery, lock acquisition, record creation, assignment, or
harness invocation, startup checks require `git`, `gh`, and only the selected
harness executable to be found on `PATH`. If one is missing, install it manually
or add it to `PATH`, then retry; the runner does not install software or change
your environment. These checks establish executable presence only. Finding an
executable on `PATH` does not prove it can still launch later or supports the
required flags/capabilities, and does not establish authentication, model
availability or account access, or successful skill loading. GitHub identity
and read checks likewise do not establish write authorization; assignment,
comments, closure, or a later harness launch can still fail.

## Install

Run the installer from the source checkout:

```bash
cd /path/to/implement-loop
./install.sh
# Or choose a different executable directory:
./install.sh "$HOME/bin"
```

With no argument, the installer creates `~/.local/bin` if needed and links
`implement-loop` there. A destination argument selects another directory, which
is also created if needed. Repeating installation of the same link is safe;
unrelated files, directories, or links at that command path are refused with a
diagnostic. The installer reports whether the destination is on `PATH` and which
runtime commands are available. It does not edit shell profiles, install
dependencies, authenticate GitHub, configure a harness, or install the external
skill. Add the printed destination to `PATH` yourself if necessary, for example:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

The executable remains backed by this source checkout. Updating the checkout
updates the linked command; moving or deleting the checkout breaks it. There is
no copy installation.

## Run a spec

Run the installed command from the target checkout, including from a subdirectory:

```bash
implement-loop 123
implement-loop 123 --harness copilot
implement-loop 123 --max-iterations 10
implement-loop 123 --config-file ./experiment.toml
implement-loop 123 --copilot-model gpt-6-luna \
  --copilot-reasoning-effort high
```

OpenCode is the default. `--harness` accepts only `opencode` or `copilot`; an
unknown value fails before the run starts. The default limit is five invocations
of the selected harness, and `--max-iterations` must be a positive integer.
`./implement-loop --help` is side-effect free. The command uses the current
checkout and its configured GitHub host. A conflicting `GH_REPO` override is
rejected. A matching override is allowed.

For TOML options, CLI precedence, state-directory selection, and retention
semantics, see [configuration and private records](configuration.md).

## Queue, ownership, and local locking

The runner uses native same-repository direct child issues, in GitHub's returned
order. It skips closed, blocked, or assigned tickets; assignments to the
authenticated user are skipped too. Labels do not affect eligibility. Missing
or malformed issue/dependency metadata, cross-repository children, and failed
GitHub reads stop the run rather than guessing. If no eligible child remains,
the command exits successfully without claiming that all children are complete.
The queue is refreshed after each confirmed closure; it does not poll.

The wrapper assigns the selected issue to the authenticated user, then rechecks
exclusive ownership, open state, blockers, and direct-child membership. It
supplies the complete parent/ticket bodies and comments as requirements context.
The parent is context, not permission to implement sibling tickets. Context
larger than the safe 100 KB invocation budget is rejected before assignment; it
is never silently truncated. Delegated workers must follow the same ticket scope
and execution restrictions, and the implementing agent must wait for them before
reporting completion.

Selection, assignment, and these rechecks are separate GitHub operations, not an
atomic distributed claim. The local lock coordinates Implement Loop runs that
share this repository's Git common directory on this machine; it does not
coordinate other machines or protect against unrelated editors. Rechecks stop
the run when a change is observed, but cannot prevent an external change that
races after a read.

## End-of-run alerts

On macOS, every normally stopped Loop run attempts one desktop notification and
one Glass sound, including runs that did not start an Attempt. This behavior is
always on and needs no configuration. The notification identifies the
`OWNER/REPO` and says either “No eligible children” or “Attempt limit reached”;
neither message claims that all tickets are complete. Ticket details, task
descriptions, checkout paths, and agent output are not included. The queue-empty
reason takes precedence when no eligible child remains at the attempt limit.

Handled execution failures likewise attempt one notification and one Basso sound
after cleanup, even when no Attempt began. The notification says “Run failed” and
does not include the exception or recovery details. Ctrl+C, termination signals,
help, and invalid arguments or configuration remain silent. Alerts are currently
supported on macOS only.

An alert means the Loop wrapper stopped; it does not prove that a harness,
delegated agent, or other background work has stopped. Follow the recovery guidance
below before rerunning interrupted or failed work.

Alerts are delivered on the machine running Implement Loop and are not forwarded
over SSH. The operating system may suppress notifications because of permissions,
focus settings, or notification policy; sound may be unavailable or muted. Missing
tools, unavailable desktop services, denied delivery, and timeouts are silent
best-effort skips and do not change the Loop run's result.

## Harness capabilities and external skill setup

The invocation contracts were checked against OpenCode CLI v2.1.3 and GitHub
Copilot CLI 1.0.103-0. The executable versions are not pinned at runtime; if a
later release changes its CLI contract, check its help and the linked V2/CLI
documentation before using it. OpenCode V1 is not supported.

- OpenCode uses `opencode run --standalone --auto --model
  github-copilot/gpt-6-luna#max`. V2 documents headless `run`, private-server
  execution, model selectors in `provider/model#variant` form, and `--auto` in
  the installed v2.1.3 command help. Each attempt starts a new process without
  `--continue`, `--session`, or `--fork`.
- Copilot uses `copilot --trust-folder -p ... --model gpt-6-luna
  --reasoning-effort max --stream on --output-format text --allow-all-tools`.
  `--trust-folder` applies repository configuration trust to this prompt-mode
  session only; it does not save persistent trust or tool/path/URL approvals.
  Each attempt starts a new process without `--continue` or `--resume`.
- The OpenCode prompt explicitly references `@implement`, which V2 resolves
  through its native skill tool. The Copilot prompt references `/implement`,
  the CLI's documented skill syntax. For Copilot, `copilot skill list` can
  confirm that the skill is discoverable. Both harnesses also discover the
  personal `.agents/skills` location above.
- Both default models are selected explicitly. If the account cannot resolve
  the chosen model or variant, the invocation fails and the wrapper stops; it
  does not fall back to another model. OpenCode availability depends on the
  connected GitHub Copilot account and its current model catalog. The selected
  model applies to the root harness run only; configured agents or subagents
  may select their own models.
- OpenCode's `--auto` requests automatic approval for permissions that are not
  explicitly denied. Copilot's `--allow-all-tools` requests unattended tool
  permission in prompt mode; it does not request Copilot's broader
  `--allow-all-paths` or `--allow-all-urls` options. Implement Loop does not
  rewrite persistent harness settings or add a wrapper denial list. Existing
  project/user instructions, agents, plugins, policies, and explicit denials
  remain in effect. These flags do not guarantee that every path, URL,
  permission, or clarification can be handled unattended.
- Both harnesses stream native text output to the terminal and the same private
  per-attempt `agent.log`. Redirected output or `NO_COLOR` (including an empty
  value) disables color; a terminal enables it when `NO_COLOR` is unset.

Official references for these contracts:

- [OpenCode V2 CLI commands](https://opencode.ai/v2/docs/cli/commands/)
- [OpenCode V2 skills](https://opencode.ai/v2/docs/skills/)
- [OpenCode V2 models](https://opencode.ai/v2/docs/models/)
- [OpenCode V2 Copilot provider](https://opencode.ai/v2/docs/providers/)
- [OpenCode V2 permissions](https://opencode.ai/v2/docs/permissions/)
- [Copilot CLI command reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)
- [Copilot CLI programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference)
- [Copilot CLI skill setup](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills)
- [Supported Copilot models](https://docs.github.com/en/copilot/reference/ai-models/supported-models)

The normal tests use fake CLIs and make no paid model calls or live issue
mutations. During development, local command help, Copilot permission help, and
`copilot skill list` were also checked against the installed versions above.
Documentation and command construction establish the native skill-loading
contract; the test suite does not make a model request to prove that a particular
user's skill file is loaded under their configuration and permission rules.

## Completion and issue lifecycle

Every ticket attempt gets a fresh harness conversation and the same result-file
contract, regardless of harness. The prompt requests the external `implement`
skill using each harness's native reference, asks the skill to validate, review,
and commit, and supplies a unique attempt ID plus an initially absent result-file
path outside the checkout. The result must be a JSON object like:

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

Before closing, the wrapper requires successful selected-harness exit and
persisted records, valid result evidence, the original branch, a clean checkout,
and a new descendant commit produced during that attempt. It also confirms the
parent is still open and that the ticket remains open, unblocked, exclusively
assigned, and a direct child.

These checks are fail-closed detection, not isolation or rollback. Git-state
checks at completion happen after the harness has run: a dirty tree, changed
branch, or rewritten history prevents wrapper closure but does not undo the
agent's files or commits. The runner does not sandbox trusted automation or
guarantee that delegated/background processes have stopped.

Existing commits alone cannot satisfy an attempt, even if the work was already
implemented or approval arrived later. The original branch and a clean checkout
remain mandatory, including untracked and submodule status. A successful closure
comment identifies the actual local branch and new descendant commit; it does
not claim a push, merge, or PR. Already-committed work can instead be
[finalized manually](#finalize-already-committed-work-after-approval).

## Output and record boundaries

Harness output streams natively to the terminal and is persisted in private
per-attempt records. See [configuration and private records](configuration.md)
for record contents, file permissions, and cleanup behavior. Loop retention does
not govern harness-native sessions, logs, telemetry, or other storage; manage
those separately using each harness's settings.

OpenCode's `--standalone` runs against a private server instead of the shared
background service. It is **not** a sandbox, filesystem/network isolation, or a
way to suppress session persistence or prove that an interrupted agent has
stopped. The wrapper does not manage or delete OpenCode or Copilot native session
data.

## Failure and manual recovery

Failures stop the loop with a nonzero status. Within its handled error boundary,
the command prints the original error and, when available, the existing run-record
directory and current ticket's record directory. A ticket pointer is labelled
`(no harness invocation started)` when preparation, assignment, or harness startup
failed before an invocation began. Ticket records can therefore exist before an
attempt; a directory name or its existence alone does not establish that an
attempt occurred.
Failures before record creation do not print an invented path, and run-level
failures after ticket processing do not point to the previously completed ticket.

The runner does not retry, roll back, stash, reset, unassign, or skip ahead. Any
existing checkout changes, commits, and assignments were left in place; this does
not imply that any of those effects occurred. A failed
`gh issue edit --add-assignee` warns that assignment of the affected ticket may
have taken effect; inspect its live GitHub state before rerunning. A failed
`gh issue close --comment` warns that its closure comment or the closure may
have taken effect. If the subsequent closure-confirmation read fails, the
command warns that the ticket may already be closed. These uncertainty warnings
are scoped to the assignment, closure, and immediate closure-confirmation
operations; an unrelated GitHub read failure does not imply uncertain closure.
Inspect the live issue and its `closure-comment.txt`, `checks.json`, and
`closed-ticket.json` records when present before deciding how to recover. A
missing confirmation record does not prove that closure failed. The runner does
not retry or repair partial GitHub writes automatically.

After a failure:

1. Start with the original error and the printed record pointers. Inspect the
   run's `agent.log`, `process.json`, context snapshots, prompt, result evidence,
   and `checks.json` when present. Use the current ticket's record directory
   when printed; its presence does not prove that the harness ran.
2. Inspect the current branch, commit history, and working tree; resolve any
   partial work deliberately.
3. Before rerunning, verify that any potentially running harness, delegated
   agents, and background work have stopped. On interruption, the printed output,
   record presence, and release of the local lock do **not** confirm shutdown.
4. Inspect the GitHub issue and decide whether to keep it assigned, close it
   manually, or unassign it before rerunning. A ticket still assigned to you is
   intentionally skipped on the next run.

### Finalize already-committed work after approval

When work was committed before approval, or an attempt committed successfully
but stopped before closure, use manual finalization rather than rerunning the
ticket just to close it:

1. Inspect the live ticket and comments, existing implementation commit, checkout,
   validation records, and any surviving agent work. Verify potentially running
   harnesses, delegated agents, and background work have stopped.
2. Perform any required review and validation and obtain human approval as
   appropriate. Do not infer correctness from the presence of a commit.
3. If the ticket is still open and the work is complete, deliberately comment on
   and close it yourself, accurately identifying the existing local commit and
   checks actually performed. Inspect any existing closure comment before adding
   another one.
4. Rerun the loop for remaining eligible tickets.

For example, after completing the inspection, validation, and approval above:

```bash
gh issue view 456 --comments
# Write an accurate account of the existing commit and checks actually performed.
gh issue close 456 --comment "$(cat /path/to/reviewed-finalization-comment.txt)"
implement-loop 123
```

Manual finalization is a human action, not a successful attempt or
wrapper-verified completion. Do not claim the wrapper verified checks it did not
perform. Do not manufacture empty commits, amend history, or weaken completion
checks to satisfy the runner. Uncommitted or incomplete work needs deliberate
resolution, not premature closure. Implement Loop does not watch for approvals,
resume sessions, or automatically finalize already-committed work.

### Stale locks and active-run markers

The local lock lives in the repository's Git common directory and coordinates
worktrees on this machine only. If a process is killed uncatchably, inspect the
PID in that repository's `implement-loop.lock/pid` and verify that the wrapper,
harness, delegated agents, and background work have stopped before removing that
specific stale lock. Never remove a lock based on age alone.

Each run also has an `active.pid` marker in its private record directory. Normal
exit removes it; termination before cleanup can leave it behind. Retention deliberately
preserves a marked run even when the marker looks stale. Do not remove a marker
based only on its age or PID: first verify that the corresponding wrapper and any
agent work have stopped. If they have, remove only that run's stale
`active.pid` marker if you want a later retention pass to consider the run. Marker
removal does not stop processes or prove that they stopped.

Reliable descendant cancellation and whole-attempt timeouts are deferred. There
is no automatic session resume or spend cap. GitHub Actions is separately tracked
in [issue #2](https://github.com/malchmih/implement-loop/issues/2).
