# Development and testing

## Prerequisites and checks

Development uses `make`, Git, and [uv](https://docs.astral.sh/uv/). uv provisions
the Python version in `.python-version` and uses the committed lockfile.

```bash
make check-static
make test
make check
```

`make check-static` runs Ruff and mypy; `make test` runs the behavioral unittest
suite. `make check` syncs locked dependencies, then runs both. Locked uv commands
do not rewrite `uv.lock` and fail if it needs reconciliation. CI runs the source
checks on Linux and macOS. Tests use fake integrations and make no live model
calls or GitHub writes.

## Source installation smoke check

```bash
uv tool install --editable .
implement-loop --help
```

The behavioral suite installs the source command and runs a complete fake
workflow from a separate target checkout whose path contains spaces. Python and
installed source code are required at runtime; this is not a standalone
executable. See the [README](../README.md#install-and-run) for installation and
runtime prerequisites.

## CI

Pull requests and manual runs perform source static checks and behavioral tests
on Linux and macOS. There are no executable build, preview-artifact, or rolling
release jobs. Actual hosted workflow verification is reserved for the
maintainer.
