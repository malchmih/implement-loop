.PHONY: check check-static test

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
