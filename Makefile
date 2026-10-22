.PHONY: check

check:
	uv sync --locked
	shellcheck install.sh
	uv run --locked ruff check .
	uv run --locked ruff format --check .
	uv run --locked mypy
	uv run --locked python -m unittest discover -s tests -v
