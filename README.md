# Implement Loop

Implement prepared direct child GitHub issues sequentially from the target
checkout. Source-installed use is supported on Linux and macOS with the latest
stable CPython and [uv](https://docs.astral.sh/uv/). A run also needs Git, an
authenticated `gh` session, the OpenCode CLI, and the external `implement`
skill. See [workflow prerequisites](docs/usage.md#before-a-run) for setup and
permissions.

## Install and run

Install the source command with uv:

```bash
uv tool install --from git+https://github.com/malchmih/implement-loop implement-loop
```

uv installs the command in an isolated environment using a system or uv-managed
supported CPython. This installs source code, not a bundled runtime executable;
Python and the installed package remain required at runtime. Ensure
uv's tool bin directory (usually `~/.local/bin`) is on your `PATH`. Upgrade with
`uv tool upgrade implement-loop`.

To install a local checkout for development, use an editable source install:

```bash
git clone https://github.com/malchmih/implement-loop ~/src/implement-loop
uv tool install --editable ~/src/implement-loop
```

The editable installation uses that checkout's source, so changes there take
effect without reinstalling. Quote the source path if it contains spaces.

From a target repository, run:

```bash
cd /path/to/target-repository
implement-loop 123
```

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
