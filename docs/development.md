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

`make install` runs this build and installs to `$(PREFIX)/bin/implement-loop`,
defaulting to `~/.local/bin/implement-loop`. Set `PREFIX` to choose another
prefix. See the [README](../README.md#install-and-run) for operator install,
upgrade, and old-symlink instructions. Rebuild and reinstall to update the
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

## GitHub Actions and distribution

Pull requests targeting `main` and manual runs perform the static checks,
behavioral matrix, and packaged macOS arm64 smoke test. They retain the
`implement-loop-macos-arm64` preview artifact. A push to `main` runs the packaged
build and smoke test, then updates the single `rolling` prerelease; it does not
repeat the static and behavioral suites already required for PRs. A failed
packaged job cannot reach the release job, leaving the previous download intact.

Only the main-push publication job has repository-content write permission. It
serializes release updates and skips a build when its commit is no longer the
current `main` head. PR and manual runs cannot publish. The
[README](../README.md#download-the-rolling-macos-build) has the stable download,
extraction, runtime requirements, and Gatekeeper guidance. The packaged smoke
test installs through the public Make targets and exercises the executable
outside the checkout with fake GitHub and harness commands; it makes no live
GitHub writes or model calls.

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
See [workflow prerequisites and skill setup](usage.md#before-a-run); fake
executables do not prove that a live model loads a user's skill.
