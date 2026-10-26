# Implement Loop

Implement prepared direct child GitHub issues sequentially from the target
checkout. The standalone executable is supported on Apple silicon macOS. Linux
remains supported through source execution with Python 3.11+. A run also needs
Git, an authenticated `gh` session, a supported harness CLI, and the external
`implement` skill. See [workflow prerequisites](docs/usage.md#before-a-run) for
setup and permissions.

## Install and run

### Download the rolling macOS build

Download the [latest Apple silicon macOS prerelease](https://github.com/malchmih/implement-loop/releases/download/rolling/implement-loop-macos-arm64.tar.gz):

```bash
curl -fL https://github.com/malchmih/implement-loop/releases/download/rolling/implement-loop-macos-arm64.tar.gz \
  -o implement-loop-macos-arm64.tar.gz
mkdir implement-loop-macos-arm64
tar -xzf implement-loop-macos-arm64.tar.gz -C implement-loop-macos-arm64
./implement-loop-macos-arm64/implement-loop --help
```

The archive preserves the executable permission. From a target repository, run
`/path/to/implement-loop-macos-arm64/implement-loop ISSUE_NUMBER`. This Apple
silicon macOS executable includes its Python runtime, so Python and build tools
are not needed at runtime. It still requires Git, an authenticated `gh` session,
a supported harness CLI, and the external `implement` skill. The binary is
not Developer ID-signed or notarized; macOS Gatekeeper may warn or block it. Do
not disable Gatekeeper globally.

### Build and install from source

On macOS arm64, build and install the standalone executable from the source checkout:

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
not edit shell profiles. Installation safely replaces an existing regular
executable; a failed build or replacement leaves it in place.

If the old source-symlink installer left a link at the destination, inspect the
default with `ls -l ~/.local/bin/implement-loop` and
`readlink ~/.local/bin/implement-loop`. Remove it manually only after
confirming it points to the old checkout (`rm ~/.local/bin/implement-loop` for
the default prefix). Use the matching `PREFIX/bin` path for a custom prefix.
`make install` refuses symlinks and directories and does not migrate them.
Reinstall after changing the checkout to build and install its current files;
editing the checkout alone does not update the executable.

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

## Try a CI preview

After the packaged job succeeds, the `Quality checks` workflow provides an
artifact named `implement-loop-macos-arm64`. For a pull request, open the
`Quality checks` run from its **Checks** tab; for a manual run, open that run
under **Actions**. Download the artifact from the run summary. The downloaded zip
contains a tar archive:

```bash
unzip implement-loop-macos-arm64.zip
tar -xzf implement-loop-macos-arm64.tar.gz
./implement-loop --help
```

The tar archive preserves executable permissions. To run a ticket, invoke
`/path/to/implement-loop SPEC_NUMBER` from the target repository. These are
preview builds, not Developer ID-signed or notarized releases; macOS Gatekeeper
may warn or block them. Only run an artifact from a workflow and revision you
trust, and do not disable Gatekeeper globally.

Before running, review [permissions, commits, and closure](docs/usage.md#permissions-commits-and-closure)
for trust, permission, side-effect, and completion limits.

- [Configuration and private records](docs/configuration.md)
- [Workflow and recovery](docs/usage.md)
- [Development and testing](docs/development.md)
- [Changelog](CHANGELOG.md)

## License

[Zero-Clause BSD (0BSD)](LICENSE). You may use, modify, and redistribute this
software for any purpose, including commercially, without attribution or retaining
the license notice. The software is provided "as is", without warranty.
