# Implement Loop

Implement prepared direct child GitHub issues sequentially from the target
checkout. Standalone local builds are supported on Apple silicon macOS and Ubuntu
x86-64. Source execution on other Linux systems remains available with Python 3.11+.
A run also needs
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

On macOS arm64 or Ubuntu x86-64, build and install the standalone executable from
the source checkout:

```bash
make install
# or choose a prefix; the executable is installed in PREFIX/bin:
make install PREFIX="$HOME/tools"
```

The default destination is `~/.local/bin/implement-loop`. `make` and
[uv](https://docs.astral.sh/uv/) are needed to build and install; Ubuntu also
needs `binutils` (in particular `objdump`) for PyInstaller. uv provisions
the project's Python 3.11 and locked build dependencies. The installed
executable includes Python and does not need the source checkout or build tools
at runtime. Add `~/.local/bin` to `PATH` yourself if needed; the command does
not edit shell profiles. Installation safely replaces an existing regular
executable; a failed build or replacement leaves it in place. `make install`
refuses to replace a symlink or directory.
Reinstall after changing the checkout to build and install its current files;
editing the checkout alone does not update the executable.

Ubuntu preview builds use 24.04 x86-64 as their baseline, with the same archive
configured for validation on Ubuntu 24.04 and 26.04. Hosted validation still needs
maintainer verification. Builds made on newer systems may require newer system
libraries. No compatibility is promised for Ubuntu 22.04, other distributions,
Linux arm64, untested interim Ubuntu releases, or future releases. The rolling
download above is currently macOS only.

The single-file executable extracts its bundled runtime to a temporary directory
at startup. On Linux, that directory must permit execution; if the default is
mounted `noexec`, set `TMPDIR` to a writable directory on an executable filesystem.

After installation, run the command from the target repository:

```bash
cd /path/to/target-repository
implement-loop 123
implement-loop 123 --harness copilot
```

When running from source on Linux or macOS, use Python 3.11 or later and
invoke the checkout's command from the target repository:

```bash
cd /path/to/target-repository
python3 /path/to/implement-loop/implement-loop 123
```

## Try a CI preview

The `Quality checks` workflow provides preview artifacts named
`implement-loop-macos-arm64` and `implement-loop-ubuntu-x86_64`. The Ubuntu
archive is built on Ubuntu 24.04 and the same executable is configured for
packaged validation on Ubuntu 24.04 and 26.04. Check that the corresponding
packaged validation jobs succeeded before using an artifact. For a pull request,
open the `Quality checks` run from its **Checks** tab; for a manual run, open that
run under **Actions**. Download the artifact from the run summary. The downloaded
zip contains a tar archive:

```bash
unzip implement-loop-macos-arm64.zip
tar -xzf implement-loop-macos-arm64.tar.gz
./implement-loop --help
```

For Ubuntu, use `implement-loop-ubuntu-x86_64.zip` and its matching `.tar.gz`
in the same commands. The tar archive preserves executable permissions. To run a
ticket, invoke `/path/to/implement-loop SPEC_NUMBER` from the target repository.
The executable bundles Python, but still requires Git, authenticated `gh`, a
supported harness CLI, and the external `implement` skill. These are preview
builds; macOS builds are not Developer ID-signed or notarized, so Gatekeeper may
warn or block them. Only run an artifact from a workflow and revision you trust,
and do not disable Gatekeeper globally.

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
