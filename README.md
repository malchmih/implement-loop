# Implement Loop

Implement prepared direct child GitHub issues sequentially from the target
checkout. OpenCode V2 is the default harness; GitHub Copilot CLI remains
selectable. Python 3.11+ is required, and the runner has no third-party runtime
dependencies.

```bash
cd /path/to/target-repository
/path/to/implement-loop/implement-loop 123
/path/to/implement-loop/implement-loop 123 --harness copilot
```

The default limit is five harness invocations. Override it with
`--max-iterations N`. An optional user TOML file and explicit flags can also
select harness/model preferences and private record retention. Only the selected
harness CLI is required. Already-committed work can be finalized only with the
one-run `--allow-no-new-commit TICKET_NUMBER` exception after manual unassignment.
See [usage and recovery](docs/usage.md) for prerequisites, supported harness
capabilities, queue rules, completion checks, records, and failure handling. See
[development checks](docs/development.md) for contributor setup.

The wrapper claims and closes issues; it does not push commits or open PRs.
Completion is based partly on the agent's result-file attestation, not independent
proof of correctness. Run only against trusted repositories, skills, and issue
content.
