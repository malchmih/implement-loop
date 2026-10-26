# Development and testing

## Prerequisites and checks

Development uses `make`, Git, [uv](https://docs.astral.sh/uv/), and ShellCheck.
Install ShellCheck separately. uv provisions the Python version in
`.python-version` and uses the committed lockfile.

```bash
make check-static
make test
make check
```

`make check-static` runs ShellCheck, Ruff, and mypy; `make test` runs the
behavioral unittest suite. `make check` syncs locked dependencies, then runs both.
Locked uv commands do not rewrite `uv.lock` and fail if it needs reconciliation.
CI runs the first two targets. Tests use fake integrations and make no live model
calls or GitHub writes.

## Apple silicon macOS executable

```bash
make build
make install
make install PREFIX="$HOME/tools"
make test-packaged
```

Build and install are supported on Apple silicon macOS only. `make build` creates
`dist/implement-loop` from the files currently on disk, including uncommitted
changes. `make install` rebuilds and installs to `$(PREFIX)/bin/implement-loop`
(`~/.local/bin/implement-loop` by default); rerun it after source changes. uv
provisions Python 3.11 and the locked build dependencies. The executable includes
Python but does not bundle Git, authenticated `gh`, a harness CLI, or the external
`implement` skill. It is not Developer ID-signed or notarized, so Gatekeeper may
warn or block it. Linux remains supported through direct source execution. See the
[README](../README.md#install-and-run) for download, installation, and runtime
guidance.

`make test-packaged` installs into a smoke-test prefix and runs the installed
executable outside the checkout with fake `gh` and harness commands; it makes no
live model calls or GitHub writes.

## CI

Pull requests and manual runs perform static checks, behavioral tests, and the
packaged macOS arm64 smoke test, retaining a preview artifact. Pushes to `main`
build and smoke-test the archive before updating the rolling prerelease; they do
not repeat the PR quality suite.
