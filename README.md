# Implement Loop

Implement prepared direct child GitHub issues sequentially from the target
checkout. This first Python slice uses Copilot CLI with the external `/implement`
skill; Python 3.11+ is required, and the runner has no third-party runtime
dependencies.

```bash
cd /path/to/target-repository
/path/to/implement-loop/implement-loop 123
```

The default limit is five Copilot invocations. Override it with
`--max-iterations N`. See [usage and recovery](docs/usage.md) for prerequisites,
queue rules, completion checks, records, and failure handling. See
[development checks](docs/development.md) for contributor setup.

The wrapper claims and closes issues; it does not push commits or open PRs.
Completion is based partly on the agent's result-file attestation, not independent
proof of correctness. Run only against trusted repositories, skills, and issue
content.
