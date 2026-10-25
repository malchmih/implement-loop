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
must be installed separately. These public Make targets are also the commands
used by CI. The checks make no live model calls or GitHub writes.

## GitHub Actions

The `Quality checks` workflow runs for pull requests targeting `main` (including
new commits pushed to a PR) and supports manual dispatch. It has no push,
scheduled, privileged pull-request, or merge-queue trigger. Manual dispatch is
available after the workflow is present on the default branch. In the Actions
interface, select a branch to validate; the run checks its current revision.

CI runs the same commands as the local targets above:

| Check name | Runner | Python | Command |
| --- | --- | --- | --- |
| `Static checks` | `ubuntu-24.04` | 3.11 | `make check-static` |
| `Behavioral tests (Linux, Python 3.11)` | `ubuntu-24.04` | 3.11 | `make test` |
| `Behavioral tests (Linux, Python 3.15)` | `ubuntu-24.04` | 3.15 | `make test` |
| `Behavioral tests (macOS, Python 3.11)` | `macos-26` (ARM64) | 3.11 | `make test` |
| `Behavioral tests (macOS, Python 3.15)` | `macos-26` (ARM64) | 3.15 | `make test` |

Install the Python version for the CI job you want to reproduce (3.11 or 3.15),
uv, `make`, Git, and ShellCheck for static checks. Run `make check-static` or
`make test` to isolate a failed job; run `make check` for the complete local
validation. The behavioral suite uses fake GitHub and harness commands, so CI
and local tests need no real `gh`, OpenCode, Copilot, model credentials, or
authenticated harness session.

The workflow grants only repository-content read permission and uses the
ordinary `pull_request` security context. Checkout does not persist Git
authentication, and no repository secrets or personal tokens are supplied to
the jobs. Each reusable action is pinned to a full commit SHA verified against
its official upstream release, with the release version annotated in the
workflow. When updating an action, verify the release in its upstream
repository and update the SHA and annotation together. The setup-uv cache is
explicitly disabled, and uv itself is pinned to 0.13.5. The workflow uploads no
artifacts. Dependencies are run with the committed lockfile and locked uv
operations.

Superseded runs of the same PR are cancelled. Different PRs and manual runs use
separate concurrency groups. Matrix fail-fast is disabled so the other
platform/Python results can finish after a failure. Every job has a 15-minute
timeout, and ordinary Actions logs are retained using the repository's existing
settings.

The workflow uses standard GitHub-hosted `ubuntu-24.04` and `macos-26` runners
in this public repository. Standard hosted runners are free for public
repositories; larger runners and storage have separate billing rules and are
not configured here. Reassess that assumption if the repository visibility or
runner choices change.

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

The OpenCode fake emits representative JSON-lines records from the inspected
2.1.4 contract, including split-stream and malformed-output cases. Acceptance
checks observe the rendered CLI output, raw private `agent.log`, and output
delivered before the fake process exits. CLI fixtures cover unchanged assistant
text, the shared tool-block layout, bounded shell and generic previews, full
failure details, wrapper styling policy, and pass-through of tool-supplied ANSI
sequences. They also verify that successful read/search results and identifiable
parent-level delegation retain their current summaries without losing their full
raw events, while unfamiliar tools use the generic fallback. Copilot fixtures
continue to verify its native byte relay and record format.
CLI references and locally verified versions for native skill loading and
invocation flags are recorded in [workflow and recovery](usage.md); fake
executables do not prove that a live model loads a user's skill.
