# Implement Loop

Implement prepared direct child GitHub issues sequentially from the target
checkout. The installer and runner support macOS and Linux. Python 3.11+ is
required; the runner has no third-party runtime dependencies. A run also needs
Git, `gh` with an authenticated GitHub CLI session, the selected harness CLI
(OpenCode V2 by default; Copilot is optional), and the external `implement`
skill.

## Install and run

From the source checkout, install a link into `~/.local/bin` (or choose a
destination):

```bash
./install.sh
# or: ./install.sh "$HOME/bin"
```

Then run the compact command from the target repository:

```bash
cd /path/to/target-repository
implement-loop 123
implement-loop 123 --harness copilot
```

The default limit is five harness invocations. Only the selected harness CLI is
required. Installation symlinks to the source checkout: updating it updates the
command, while moving or deleting it breaks the link. The installer does not edit
shell profiles; add the destination to `PATH` yourself if needed.

Run only against trusted repositories, skills, and issue content: this is local
automation, not a sandbox, and agent guidance is not an enforcement boundary. The
wrapper closes issues after a clean-checkout check and, by default, a new local
commit; it does not push commits, open PRs, or merge work. Completion includes the
external skill's validation/review and result-file attestation, not independent
proof of semantic correctness.

- [Configuration and private records](docs/configuration.md)
- [Workflow and recovery](docs/usage.md)
- [Development and testing](docs/development.md)
- [Changelog](CHANGELOG.md)

## License

[Zero-Clause BSD (0BSD)](LICENSE). You may use, modify, and redistribute this
software for any purpose, including commercially, without attribution or retaining
the license notice. The software is provided "as is", without warranty.
