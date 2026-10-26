.PHONY: check check-static test build test-packaged

check:
	uv sync --locked
	$(MAKE) check-static
	$(MAKE) test

check-static:
	shellcheck install.sh
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

test-packaged: build
	uv run --locked python -m unittest discover -s tests -p packaged_smoke.py -v
