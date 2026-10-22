# Usage and recovery

## Prerequisites

- Python 3.11 or later, Git, and GitHub CLI (`gh`).
- An authenticated GitHub CLI session with permission to read native
  sub-issues/dependencies, assign issues, comment, and close issues.
- The CLI, authentication, and model account for the selected harness: OpenCode
  V2 (the default) or GitHub Copilot CLI (`--harness copilot`). The other harness
  is not required for a run.
- The external skill named `implement`. Implement Loop does not bundle, install,
  or configure it. For a personal skill shared by both harnesses, put it at
  `~/.agents/skills/implement/SKILL.md`. OpenCode also discovers skills under
  `~/.config/opencode/skills/`; Copilot also discovers `~/.copilot/skills/`.
  Project skills are supported under `.opencode/skills`, `.agents/skills`, or
  `.claude/skills` for OpenCode, and `.github/skills`, `.agents/skills`, or
  `.claude/skills` for Copilot.
- A checkout with an existing commit, an attached branch, and no staged,
  unstaged, untracked, or submodule changes.

## Run a spec

Run the executable from the target checkout, including from a subdirectory. Until
an installer is added, use the source checkout's executable path unless it is
already on `PATH`:

```bash
/path/to/implement-loop/implement-loop 123
/path/to/implement-loop/implement-loop 123 --harness copilot
/path/to/implement-loop/implement-loop 123 --max-iterations 10
/path/to/implement-loop/implement-loop 123 --config-file ./experiment.toml
/path/to/implement-loop/implement-loop 123 --copilot-model gpt-6-luna \
  --copilot-reasoning-effort high
```

OpenCode is the default. `--harness` accepts only `opencode` or `copilot`; an
unknown value fails before the run starts. The default limit is five invocations
of the selected harness, and `--max-iterations` must be a positive integer.
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
`XDG_STATE_HOME` must be absolute. Record directories are made private and
locations inside or containing the target checkout, the home directory itself,
and filesystem root are rejected so the runner cannot change shared directory
permissions or scan a parent containing the checkout.

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
and a new descendant commit. It also confirms the parent is still open and that
the ticket remains open, unblocked, exclusively assigned, and a direct child.
The closure comment names the local branch and commit and accurately reports the
agent attestation and wrapper checks. It does not claim a push, merge, or PR.

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
restarting.

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

The local lock lives in the repository's Git common directory and coordinates
worktrees on this machine only. If a process is killed uncatchably, verify the
lock owner and surviving agent work before removing that specific stale lock.
Reliable descendant cancellation and whole-attempt timeouts are deferred. There
is no automatic session resume or spend cap.
