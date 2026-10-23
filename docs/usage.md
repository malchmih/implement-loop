# Usage and recovery

## Prerequisites

- Python 3.11 or later, Git, and GitHub CLI (`gh`).
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
implement-loop 123 --allow-no-new-commit 456
implement-loop 123 --copilot-model gpt-6-luna \
  --copilot-reasoning-effort high
```

OpenCode is the default. `--harness` accepts only `opencode` or `copilot`; an
unknown value fails before the run starts. The default limit is five invocations
of the selected harness, and `--max-iterations` must be a positive integer.
`--allow-no-new-commit TICKET_NUMBER` is a per-invocation exception for one
ticket; it is not a user preference and cannot be set in TOML. It does not affect
ticket selection or waive eligibility, ownership, or any completion check other
than the requirement to create a new descendant commit for that named ticket.
`./implement-loop --help` is side-effect free. The command uses the current
checkout and its configured GitHub host. A conflicting `GH_REPO` override is
rejected. A matching override is allowed.

## User configuration

Configuration is optional. By default the command looks for
`${XDG_CONFIG_HOME:-$HOME/.config}/implement-loop/config.toml`; a non-empty
`XDG_CONFIG_HOME` must be an absolute path. `--config-file PATH` selects a
different file instead of discovering the normal one. If explicitly selected,
the file must exist and be readable. The runner never loads configuration from
the target checkout.

Settings are merged in this order: built-in defaults, the selected user TOML
file, then options explicitly supplied on the command line. Omitted CLI options
do not replace values from the file. For example:

```toml
[execution]
harness = "opencode"
max_attempts = 5

[execution.harnesses.opencode]
model = "github-copilot/gpt-6-luna"
variant = "max"

[execution.harnesses.copilot]
model = "gpt-6-luna"
reasoning_effort = "max"

[records]
directory = "~/.local/state/implement-loop"
retention_days = 7
# Set to true to disable expiry; it takes precedence over retention_days.
# keep_forever = true
```

Supported configuration keys are `execution.harness`, positive integer
`execution.max_attempts`, `execution.harnesses.opencode.model` and `.variant`,
`execution.harnesses.copilot.model` and `.reasoning_effort`, and the `records`
keys shown above. Copilot reasoning effort accepts `none`, `minimal`, `low`,
`medium`, `high`, `xhigh`, or `max`. Unknown keys, wrong types, invalid values,
and malformed TOML are errors before the runner claims a ticket or invokes a
harness. Attempts must be from 1 through 999,999,999. Retention days may be from
0 through 999,999,999; zero expires every unmarked prior run at the next startup.

The corresponding one-run CLI overrides are `--harness`, `--max-iterations`,
`--opencode-model`, `--opencode-variant`, `--copilot-model`,
`--copilot-reasoning-effort`, `--records-dir`, and either `--retention-days N`
or `--keep-records-forever`. The two retention flags are mutually exclusive.
An explicit `--retention-days` overrides a file's `keep_forever = true` choice.
The harness model/reasoning values describe the root harness invocation only;
configured agents or subagents may select their own models.

`records.directory` and `--records-dir` name the exact directory that contains
`run-*` folders. `~` is expanded and relative paths are resolved from the
invocation's current directory. When neither is set, records use
`${XDG_STATE_HOME:-$HOME/.local/state}/implement-loop`; a non-empty
`XDG_STATE_HOME` must be absolute. A missing dedicated records directory is
created with mode `0700`; an existing one must already have that mode. The runner
refuses to change permissions on an existing directory, which could be shared.
Locations inside or containing the target checkout, the home directory itself,
and filesystem root are also rejected so the runner cannot scan a parent
containing the checkout.

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

## Harness capabilities and skill setup

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
and—by default—a new descendant commit. It also confirms the parent is still
open and that the ticket remains open, unblocked, exclusively assigned, and a
direct child.

These checks are fail-closed detection, not isolation or rollback. Git-state
checks at completion happen after the harness has run: a dirty tree, changed
branch, or rewritten history prevents wrapper closure but does not undo the
agent's files or commits. The runner does not sandbox trusted automation or
guarantee that delegated/background processes have stopped.

For one named ticket, `--allow-no-new-commit TICKET_NUMBER` waives only the
requirement to create a new descendant commit during that attempt. The skill must
still assess the existing committed implementation against the current ticket
and parent requirements, perform required review and validation, and write valid
attempt-bound result evidence. The wrapper does not infer correctness from the
presence or content of existing commits. The original branch and a clean checkout
remain mandatory in either case, including untracked and submodule status. If
HEAD changes, it must still descend from the pre-attempt commit. All harness,
record, result, parent, ticket, ownership, and eligibility checks remain
mandatory. Other tickets in the same run still require a new descendant commit.

The exception does not select or prioritize its named ticket. The runner still
chooses the first eligible direct child in GitHub's returned order; a different
selected ticket receives the normal new-commit requirement. A successful closure
comment identifies the actual local branch and commit. When the exception is
used, it says the requirement was waived and, if HEAD stayed unchanged, that it
remained at the pre-attempt commit rather than claiming a new descendant commit.
It does not claim a push, merge, or PR.

## Output and private records

Harness output is streamed live and stored in private per-run records. Records
are stored in the configured records directory or, by default, under
`${XDG_STATE_HOME:-$HOME/.local/state}/implement-loop/`. They
include repository and user metadata, issue/comment snapshots, the full prompt,
process outcome, raw and validated completion evidence, agent output, selected
harness/model/reasoning settings, selected configuration-file path, retention
policy, and successful commit references. This metadata describes the root
harness selection, not the effective settings of every configured subagent.
Files and directories are private. At startup, cleanup removes only identifiable
loop-owned run directories whose encoded start time is older than the configured
retention duration (seven days by default); recent file activity does not extend
retention. Failed runs follow the same policy on a later startup. Marked runs
(even with stale markers), symlinked directories, unrecognized entries, and
legacy `copilot-ralph` data are preserved. Cleanup errors warn and do not block a
run. `keep_forever = true` or `--keep-records-forever` disables expiry.

This policy applies only to Implement Loop-owned records. It does not control
harness-native sessions, logs, telemetry, or other harness-native storage; manage
those separately using the harness's own settings.

OpenCode's `--standalone` runs against a private server instead of the shared
background service. It is **not** a sandbox, filesystem/network isolation, or a
way to suppress session persistence. The wrapper does not manage or delete
OpenCode or Copilot native session data.

## Failure and manual recovery

Failures stop the loop with a nonzero status. The runner does not retry, roll
back, stash, reset, unassign, or skip ahead. It preserves assignments, commits,
and working files. If GitHub closure partially succeeds, inspect the issue before
restarting. A failed `gh issue close --comment` command does not prove that
neither its comment nor the closure took effect, and a closure-confirmation read
can fail after GitHub has closed the ticket. Check the issue itself and the
attempt's `closure-comment.txt`, `checks.json`, and `closed-ticket.json` when
present before deciding how to recover; the runner will not retry or repair a
partial outcome automatically.

After a failure:

1. Read the run's `agent.log`, `process.json`, context snapshots, prompt, result
   evidence, and `checks.json` when present.
2. Inspect the current branch, commit history, and working tree; resolve any
   partial work deliberately.
3. Check whether the selected harness, delegated agents, or background work is
   still running. An interrupt or release of the local lock does **not** confirm
   descendant shutdown.
4. Inspect the GitHub issue and decide whether to keep it assigned, close it
   manually, or unassign it before rerunning. A ticket still assigned to you is
   intentionally skipped on the next run.

### Finalize already-committed work after approval

When a ticket's implementation is already committed and any needed human review
or approval is complete, inspect the ticket and checkout, then unassign the
ticket yourself before rerunning. For example:

```bash
gh issue view 456 --comments
# Inspect the existing branch/commit and confirm the checkout is clean.
# Obtain any required human approval before continuing.
gh issue edit 456 --remove-assignee @me
implement-loop 123 --allow-no-new-commit 456
```

Replace `implement-loop` with the executable path from [Run a spec](#run-a-spec)
if it is not on `PATH`. Confirm ticket `#456` is the first eligible child in the
normal queue order; the option does not force its selection or skip earlier
eligible tickets. The skill receives approval comments as requirements context,
but Implement Loop does not watch for approval comments or use them as resume
triggers. It also does not unassign the ticket automatically or restore an old
harness conversation: each rerun starts a fresh attempt. The named ticket must
still have a clean, committed checkout; uncommitted changes are never accepted.

The local lock lives in the repository's Git common directory and coordinates
worktrees on this machine only. If a process is killed uncatchably, verify the
lock owner and surviving agent work before removing that specific stale lock.
Reliable descendant cancellation and whole-attempt timeouts are deferred. There
is no automatic session resume or spend cap.
