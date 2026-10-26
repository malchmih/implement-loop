# Development and testing

## Prerequisites and local checks

[uv](https://docs.astral.sh/uv/), `make`, Git, and ShellCheck are used for
development. uv provisions the project's Python 3.11 for checks and builds;
ShellCheck is a separate developer prerequisite. The runtime executable uses
only the Python standard library; Git, `gh`, and the selected OpenCode or Copilot
CLI are subprocess integrations.

Run either validation group independently, or use `make check` for the complete
local validation:

```bash
make check-static
make test
make check
```

`make check-static` runs ShellCheck on `scripts/install-binary.sh`, Ruff linting
and format validation, and mypy. `make test` runs the existing behavioral
unittest suite.
Both targets use `uv run --locked`, which prepares the development environment
from committed `uv.lock` when needed. `make check` first runs `uv sync --locked`,
then invokes both targets. Locked uv operations do not rewrite the lockfile; they
fail if the project dependencies and lockfile need to be reconciled. ShellCheck
must be installed separately. These public Make targets are also the commands
used by CI. The checks make no live model calls or GitHub writes.

## macOS arm64 executable

On Apple Silicon macOS, `make build` creates the one-file PyInstaller executable
at `dist/implement-loop`. It packages the application files currently on disk,
including uncommitted edits; no clean checkout or commit is required. The
project's `.python-version` selects Python 3.11, and uv provisions that Python
and resolves the separately locked `build` dependency group. The executable
includes its Python runtime and runs independently of the source checkout.
PyInstaller's local ad-hoc signing needs no Developer ID credentials or
notarization; a downloaded copy may still encounter Gatekeeper restrictions.

`make install` runs this build and installs a regular executable at
`$(PREFIX)/bin/implement-loop`, defaulting to `~/.local/bin/implement-loop`. Set
`PREFIX` to choose another prefix. It safely replaces a regular executable but
refuses symlinks and directories; users of the old source-symlink installer
must inspect and remove that link manually. Rebuild and reinstall to update the
installed command after editing the checkout.

Contributors may install the optional pinned tools with `mise install` (uv and
ShellCheck only); uv owns Python provisioning. mise is not required when uv,
ShellCheck, and the other Make prerequisites are already available. Run
`make test-packaged` to build and install into an ignored smoke prefix, then
exercise that installed executable outside the checkout with fake GitHub and
harness commands. The checks make no live GitHub writes or model calls. This
focused target can be used by CI without running the behavioral suite against
the frozen binary.

At runtime, Git, an authenticated GitHub CLI session, the selected harness CLI,
and the external `implement` skill remain required. The executable does not
bundle those integrations or credentials. Binary builds are intentionally
limited to macOS arm64; Linux source execution and the existing source-based
test matrix remain supported.

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
| `macOS arm64 executable` | `macos-26` (ARM64) | 3.11 | `make test-packaged` |

The packaged job runs `make test-packaged`, which installs through Make's public
`install` and `build` targets, then exercises the installed executable outside
the checkout with fake GitHub and harness commands. It makes no live GitHub
writes or model calls. After the smoke tests pass, CI uploads
`implement-loop-macos-arm64`, containing a tar archive so the executable
permission survives Actions artifact download. See the
[README](../README.md#try-a-ci-preview) for download and extraction steps and the
Gatekeeper warning.

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
explicitly disabled, and uv itself is pinned to 0.13.5. The workflow uploads the
validated macOS arm64 preview described above, and no other artifacts.
Dependencies are run with the committed lockfile and locked uv operations.

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
the runner's private module layout. Make install tests use an isolated home and
prefix with a fake build command; source-command integration tests retain real
target repositories, including a scenario invoked from a target-checkout
subdirectory. The packaged smoke test exercises the Make-installed executable
outside the checkout.

The OpenCode fake emits representative JSON-lines records from the inspected
2.1.4 contract, including split-stream and malformed-output cases. Acceptance
checks observe the rendered CLI output, raw private `agent.log`, and output
delivered before the fake process exits. CLI fixtures cover unchanged assistant
text, the shared tool-block layout, bounded shell and generic previews, full
failure details, wrapper styling policy, and pass-through of tool-supplied ANSI
sequences. Read fixtures cover file, directory, and media-shaped events,
metadata-supplied zero/reliable counts, truncated or missing counts, and content
suppression without changing raw events. Search fixtures cover verified `grep`,
`glob`, and `websearch` inputs, natural query/scope/filter details, count
semantics, omitted result dumps, and generic fallback for unsupported shapes.
Delegation fixtures cover `task`, `subagent`, explicit delegation metadata,
missing agent identity, ID/wrapper omission, bounded result previews, and
unrecognized tools that must keep their generic results. Failure fixtures verify
complete read/search/delegation error and output details. Copilot fixtures
continue to verify its native byte relay and record format.
File-modification and web-fetch fixtures follow the verified OpenCode 2.1.4
schemas for `patch`, `edit`, `write`, and `webfetch`. Their integration tests
cover result previews, patch-only success-header removal, first-line
preservation, unsupported-shape fallback, full failures, tool-supplied colors,
and raw-log retention of filtered or omitted content.
Skill and question fixtures follow the verified OpenCode 2.1.4 built-in tool and
question prompt/answer schemas. Their CLI tests cover instruction-payload
suppression with raw-log retention, single and multiple questions, selected and
free-form answers, empty answers, partial/unsupported fallback, incomplete
interactions, and complete failure details.
CLI references and locally verified versions for native skill loading and
invocation flags are recorded in [workflow and recovery](usage.md); fake
executables do not prove that a live model loads a user's skill.
