# Configuration and private records

With no configuration file or limit flag, `implement-loop SPEC_NUMBER` uses
OpenCode, a ten-Attempt limit, GPT-6 Luna with the `max` variant, private records
under the standard user state directory, and fixed seven-day expiry.

## User OpenCode preferences

The optional user TOML file is discovered at
`${XDG_CONFIG_HOME:-$HOME/.config}/implement-loop/config.toml`. A non-empty
`XDG_CONFIG_HOME` must be an absolute path. Implement Loop does not load
configuration from the target checkout.

The file accepts only the root OpenCode model and variant preferences:

```toml
model = "github-copilot/gpt-6-luna"
variant = "max"
```

Omit either key to keep its built-in default. These values select the root
OpenCode invocation; delegated agents may choose different models. Malformed
TOML, unknown keys, and invalid values are diagnosed before a ticket is claimed
or OpenCode is invoked.

The parent issue number remains the positional argument. Set the maximum number
of ticket Attempts on the command line with `--max-iterations N`; the default
is ten. The limit is not a user-file setting and does not retry failed work.

### Intentional compatibility breaks

Configuration has been narrowed deliberately. Older `[execution]`,
`[execution.harnesses.*]`, and `[records]` tables are no longer supported.
In particular, `max_attempts` in TOML is rejected rather than used as a limit.
The `--config-file`, `--opencode-model`, `--opencode-variant`, and
`--records-dir` options have been removed, as have `--retention-days` and
`--keep-records-forever`. Move only `model` and `variant` to the discovered TOML
file, pass the limit with `--max-iterations`, and use standard XDG state
discovery for records.

## Record location and privacy

Records are stored at
`${XDG_STATE_HOME:-$HOME/.local/state}/implement-loop`. A non-empty
`XDG_STATE_HOME` must be absolute. There is no separate records-path setting.
The record directory must be dedicated and outside the target checkout; the
runner rejects locations that contain or are contained by the checkout, the
home directory itself, or filesystem root.

A missing record directory is created with mode `0700`. An existing directory
must already have that mode; the runner will not change permissions on a
possibly shared directory. Run and attempt directories are private (`0700`),
and files are private (`0600`). Records can contain repository paths, issue and
comment snapshots, prompts, full OpenCode output, process results, and
completion evidence. Treat them as sensitive.

When log persistence succeeds, the full output is in the ticket's `agent.log`;
a log-write failure is reported and can leave it incomplete. Raw OpenCode
JSON-lines output is retained separately from the conservative live summary.

In the live transcript, each nonempty OpenCode text event is preceded by
`── Agent ──`; its message remains unchanged, including literal Markdown. The
heading is bold cyan on color-enabled terminals and plain when output is
redirected or `NO_COLOR` is set. This presentation does not alter the raw
`agent.log` output.

For successfully returned OpenCode subagent calls whose nested metadata identifies
an untruncated running session, the live transcript shows the task, agent, and
`Running in background · session: <session ID>` instead of the tool output
preview. The call has returned, but the delegated work is still running. Other
results and errors retain their existing rendering. The complete original output
remains available to the parent agent and in `agent.log` when logging succeeds.

## Retention

At startup, the runner removes only identifiable Implement Loop run directories
whose encoded start time is more than seven days old. Recent file activity does
not extend expiry, and failed runs follow the same policy on a later startup.
The seven-day period is fixed: it cannot be configured or disabled.

Cleanup is conservative: runs with an active marker are kept, as are symlinked
run directories and unrecognized entries. Cleanup errors warn without blocking
a new run. Retention applies only to Implement Loop records. OpenCode-native
sessions, logs, telemetry, and other storage are separate; manage them through
OpenCode's settings. OpenCode private-server mode does not disable native
session persistence.
