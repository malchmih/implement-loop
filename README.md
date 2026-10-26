# Implement Loop

Implement prepared direct child GitHub issues sequentially from the target
checkout. The standalone executable is supported on Apple silicon macOS. Linux
remains supported through source execution with Python 3.11+. A run also needs
Git, `gh` with an authenticated GitHub CLI session, the selected harness CLI
(OpenCode V2 by default; Copilot is optional), and the external `implement`
skill.

## Install and run

On macOS arm64, install the standalone executable from the source checkout:

```bash
make install
# or choose a prefix; the executable is installed in PREFIX/bin:
make install PREFIX="$HOME/tools"
```

The default destination is `~/.local/bin/implement-loop`. `make` and
[uv](https://docs.astral.sh/uv/) are needed to build and install; uv provisions
the project's Python 3.11 and locked build dependencies. The installed
executable includes Python and does not need the source checkout or build tools
at runtime. Add `~/.local/bin` to `PATH` yourself if needed; the command does
not edit shell profiles.

If you previously used the source installer, it left a symlink in the bin
directory. Inspect it with `ls -l` and `readlink`, then remove it manually only
after confirming it is the old source-checkout link (for the default prefix,
`rm ~/.local/bin/implement-loop`). `make install` refuses all existing
symlinks and directories and does not migrate them. Reinstall after changing
the checkout to build and install its current files; editing the checkout alone
does not update the executable.

After installation on macOS, run the command from the target repository:

```bash
cd /path/to/target-repository
implement-loop 123
implement-loop 123 --harness copilot
```

On Linux (or when running from source on macOS), use Python 3.11 or later and
invoke the checkout's command from the target repository:

```bash
cd /path/to/target-repository
python3 /path/to/implement-loop/implement-loop 123
```

The default limit is five harness invocations. Only the selected harness CLI is
required.

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
