# Development and testing

## Prerequisites and checks

Install [mise](https://mise.jdx.dev/getting-started.html) and Git. The project
configuration selects the latest stable CPython and the project's uv release; uv
handles the locked development environment and dependencies.

```bash
mise install
mise run setup
mise run check
mise run test
mise run source-install
```

`setup` synchronizes the committed lockfile. `check` runs Ruff, mypy, and pytest;
`test` runs pytest, and `source-install` installs this checkout as an editable
source command. The test suite uses pytest functions, assertions, and one shared
fixture for a real temporary Git checkout with fake GitHub/OpenCode commands.
Tests make no live model calls or GitHub writes. Locked uv commands do not
rewrite `uv.lock` and fail if it needs reconciliation.

The floating Python request selects the latest stable release when mise resolves
or upgrades tools; the supported minor series is declared in `pyproject.toml`,
Ruff, and mypy. When adopting a new stable series, update those settings, run
`mise upgrade python`, and regenerate the lockfile with `mise exec -- uv lock`.
The floating request does not replace the interpreter in an existing uv
environment: remove the local `.venv`, then run `mise run setup` to create an
environment with the newly selected interpreter.

## Source installation smoke check

`mise run source-install` performs the editable source install. Then verify it
with `implement-loop --help` from a target checkout.

The installation smoke test installs the source command and invokes `--help`
from a separate target checkout whose path contains spaces. The primary CLI
suite covers the full fake workflow. Python and installed source code are
required at runtime; this is not a standalone executable. See the
[README](../README.md#install-and-run) for installation and runtime prerequisites.

## CI

Pull requests and manual runs invoke `mise run check` on Linux and macOS using
the same tool and task configuration as local development. There are no Python
version matrices or executable build, preview-artifact, or rolling release jobs.
Actual hosted workflow verification is reserved for the maintainer.
