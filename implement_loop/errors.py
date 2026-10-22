"""Errors that can be reported directly to the command-line user."""


class LoopError(RuntimeError):
    """A run cannot safely continue."""
