# Development

## Setup

Python 3.11+, [uv](https://docs.astral.sh/uv/), and ShellCheck are used for
development. ShellCheck is a separate developer prerequisite, not a Python or
runtime dependency. The runtime executable itself uses only the Python standard
library; Git, `gh`, and the selected OpenCode or Copilot CLI are subprocess
integrations.

```bash
uv sync --locked
make check
```

`uv.lock` pins the Python development environment. `make check` runs ShellCheck
on `install.sh`, Ruff linting and format validation, mypy, and the behavioral
unittest suite. It is the documented local check command; no hosted CI or live
model/GitHub tests are configured.

## Behavioral tests

Tests invoke the public executable in temporary real Git repositories and use
fake `gh`, OpenCode, and Copilot commands on `PATH`. They verify observable issue
state, Git history and status, harness selection and invocation, the shared
result contract, private records, output, and retention behavior. They do not
make live issue writes or model calls and do not depend on the runner's private
module layout. Separate installer tests use isolated homes, source trees, and
destinations, then invoke the installed link from a different target repository.
CLI references and locally verified versions for native skill loading and
invocation flags are recorded in [usage and recovery](usage.md); fake executables
do not prove that a live model loads a user's skill.
