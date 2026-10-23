# Development and testing

## Prerequisites and local checks

Python 3.11+, [uv](https://docs.astral.sh/uv/), `make`, Git, and ShellCheck are
used for development. ShellCheck is a separate developer prerequisite, not a
Python or runtime dependency. The runtime executable itself uses only the Python
standard library; Git, `gh`, and the selected OpenCode or Copilot CLI are
subprocess integrations.

Run either validation group independently, or use `make check` for the complete
local validation:

```bash
make check-static
make test
make check
```

`make check-static` runs ShellCheck on `install.sh`, Ruff linting and format
validation, and mypy. `make test` runs the existing behavioral unittest suite.
Both targets use `uv run --locked`, which prepares the development environment
from committed `uv.lock` when needed. `make check` first runs `uv sync --locked`,
then invokes both targets. Locked uv operations do not rewrite the lockfile; they
fail if the project dependencies and lockfile need to be reconciled. ShellCheck
must be installed separately. These are the public local validation commands;
this change does not configure GitHub Actions or repository settings. The checks
make no live model calls or GitHub writes.

## Behavioral tests

Runner integration tests invoke the public executable in temporary real Git
repositories and use fake `gh`, OpenCode, and Copilot commands on `PATH`. They
verify observable issue state, Git history and status, harness selection and
invocation, the shared result contract, private records, output, and retention
behavior. They do not make live issue writes or model calls and do not depend on
the runner's private module layout. The help smoke test and installation-only
tests use lightweight isolated homes; installed-command integration tests retain
real target repositories, including a scenario that invokes the installed link
from a target-checkout subdirectory.
CLI references and locally verified versions for native skill loading and
invocation flags are recorded in [workflow and recovery](usage.md); fake
executables do not prove that a live model loads a user's skill.
