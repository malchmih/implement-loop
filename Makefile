.PHONY: check check-static test build install test-packaged

PREFIX ?= $(if $(HOME),$(HOME)/.local)

check:
	uv sync --locked
	$(MAKE) check-static
	$(MAKE) test

check-static:
	shellcheck scripts/install-binary.sh
	uv run --locked ruff check .
	uv run --locked ruff format --check .
	uv run --locked mypy

test:
	uv run --locked python -m unittest discover -s tests -v

build:
	@case "$$(uname -s)/$$(uname -m)" in \
		Darwin/arm64) ;; \
		*) printf '%s\n' 'Error: make build is only supported on macOS arm64.' >&2; exit 2 ;; \
	esac
	uv run --locked --group build pyinstaller \
		--noconfirm --clean --onefile --name implement-loop \
		--distpath dist --workpath build/pyinstaller --specpath build \
		--target-architecture arm64 --paths . \
		implement_loop/__main__.py

install: build
	sh scripts/install-binary.sh "$(PREFIX)" dist/implement-loop

PACKAGED_TEST_PREFIX := $(CURDIR)/build/packaged-smoke

test-packaged:
	$(MAKE) install PREFIX="$(PACKAGED_TEST_PREFIX)"
	IMPLEMENT_LOOP_PACKAGED_EXECUTABLE="$(PACKAGED_TEST_PREFIX)/bin/implement-loop" \
		uv run --locked python -m unittest discover -s tests -p packaged_smoke.py -v
