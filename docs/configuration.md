# Configuration and private records

Configuration is optional. With no file or flags, `implement-loop SPEC_NUMBER`
uses OpenCode, a five-attempt limit, GPT-6 Luna with the `max` variant for
OpenCode, GPT-6 Luna with `max` reasoning for Copilot, private records under the
user state directory, and seven-day startup retention.

## Discovering and overriding settings

By default, the command reads
`${XDG_CONFIG_HOME:-$HOME/.config}/implement-loop/config.toml`. A non-empty
`XDG_CONFIG_HOME` must be an absolute path. `--config-file PATH` selects that
file instead of the discovered file; an explicitly selected file must exist and
be readable. The command does not load configuration from the target checkout.

Settings are merged in this order:

1. Built-in defaults.
2. The selected user TOML file, if present.
3. Options explicitly supplied on this invocation.

Omitted command-line options do not replace values from the file. For example:

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
# Set true to disable expiry; it takes precedence over retention_days.
# keep_forever = true
```

Supported keys are `execution.harness`, positive integer
`execution.max_attempts`, `execution.harnesses.opencode.model` and `.variant`,
`execution.harnesses.copilot.model` and `.reasoning_effort`, and the `records`
keys shown above. Copilot reasoning effort accepts `none`, `minimal`, `low`,
`medium`, `high`, `xhigh`, or `max`. Attempts must be from 1 through 999,999,999;
retention days may be from 0 through 999,999,999. Zero expires every unmarked
prior run at the next startup. Unknown keys, invalid types or values, and
malformed TOML are rejected before a ticket is claimed or a harness is invoked.

The equivalent per-run options are:

```text
--config-file PATH
--harness opencode|copilot
--max-iterations N
--opencode-model MODEL --opencode-variant VARIANT
--copilot-model MODEL --copilot-reasoning-effort EFFORT
--records-dir PATH
--retention-days N | --keep-records-forever
```

The retention flags are mutually exclusive. An explicit `--retention-days N`
overrides a file's `keep_forever = true`. The named-ticket
`--allow-no-new-commit TICKET_NUMBER` exception is invocation-only and cannot be
stored in TOML; see [workflow and recovery](usage.md#finalize-already-committed-work-after-approval).

Model and reasoning settings describe the root harness invocation. Configured
agents or subagents may choose different models; the setting does not force one
model globally.

## Record location and privacy

`records.directory` and `--records-dir` name the exact directory that contains
`run-*` folders. `~` is expanded and relative paths are resolved from the
invocation's current directory. If neither is set, records go under
`${XDG_STATE_HOME:-$HOME/.local/state}/implement-loop`; a non-empty
`XDG_STATE_HOME` must be an absolute path.

A missing record directory is created with mode `0700`. An existing directory
must already have that mode; the runner will not change permissions on a
possibly shared directory. The selected directory must be dedicated and outside
the target checkout: the runner rejects locations that contain or are contained
by the checkout, the home directory itself, and filesystem root.

Each private run stores repository and user metadata, issue/comment snapshots,
the prompt, harness output, process outcome, raw and validated result evidence,
selected harness/model/reasoning settings, the selected configuration-file path,
retention policy, and successful commit references. This metadata records the
root harness selection; it does not assert the effective settings of every
configured subagent. Run and attempt directories are private (`0700`), and files
are private (`0600`).

## Retention

At startup, the runner removes only identifiable Implement Loop run directories
whose encoded start time is older than the configured retention duration. Recent
file activity does not extend retention, and failed runs follow the same policy
on a later startup. Seven days is the default; `keep_forever = true` or
`--keep-records-forever` disables expiry.

Cleanup is conservative: marked runs are kept even when a marker appears stale;
symlinked run directories are neither followed nor removed, and unrecognized
entries and legacy `copilot-ralph` data are preserved. Cleanup errors warn
without blocking a new run. This policy controls only Implement Loop records.
Harness-native sessions, logs,
telemetry, and other storage are separate and must be managed through each
harness's own settings. OpenCode private-server mode does not suppress its native
session persistence; see [harness capabilities and skill setup](usage.md#harness-capabilities-and-external-skill-setup).
