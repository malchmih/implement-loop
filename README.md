# Implement Loop

Implement prepared direct child GitHub issues sequentially from the target
checkout. OpenCode V2 is the default harness; GitHub Copilot CLI remains
selectable. Python 3.11+ is required, and the runner has no third-party runtime
dependencies. A run also needs Git, `gh`, an authenticated GitHub CLI session, the
selected harness CLI (OpenCode by default; Copilot is optional), and the external
`implement` skill.

## Install and run

From the source checkout, install a link into `~/.local/bin` (or choose a
destination):

```bash
./install.sh
# or: ./install.sh "$HOME/bin"
```

Then run the command from the target repository:

```bash
cd /path/to/target-repository
implement-loop 123
implement-loop 123 --harness copilot
```

The default limit is five harness invocations. Override it with
`--max-iterations N`. An optional user TOML file and explicit flags can also
select harness/model preferences and private record retention. Only the selected
harness CLI is required. Already-committed work can be finalized only with the
one-run `--allow-no-new-commit TICKET_NUMBER` exception after manual unassignment.
Installation symlinks to the source checkout: updating it updates the command,
while moving or deleting it breaks the link. The installer does not edit shell
profiles; add the destination to `PATH` yourself if needed. See [usage and
recovery](docs/usage.md) for prerequisites, setup, supported harness capabilities,
queue rules, completion checks, records, and failure handling. See [development
checks](docs/development.md) for contributor setup.

The wrapper claims and closes issues; it does not push commits or open PRs.
Completion is based partly on the agent's result-file attestation, not independent
proof of correctness. Run only against trusted repositories, skills, and issue
content.
