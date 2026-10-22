# Development

## Setup

Python 3.11+ and [uv](https://docs.astral.sh/uv/) are used for development. The
runtime executable itself uses only the Python standard library; Git, `gh`, and
Copilot CLI are subprocess integrations.

```bash
uv sync --locked
make check
```

`uv.lock` pins the development environment. `make check` runs Ruff linting and
format validation, mypy, and the behavioral unittest suite. It is the documented
local check command; no hosted CI or live model/GitHub tests are configured.

## Behavioral tests

Tests invoke the public executable in temporary real Git repositories and use
fake `gh` and Copilot commands on `PATH`. They verify observable issue state,
Git history and status, command invocation, private records, output, and
retention behavior. They do not make live issue writes or model calls and do not
depend on the runner's private module layout.
