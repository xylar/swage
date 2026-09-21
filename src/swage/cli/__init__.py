"""swage's command line entry point."""

# PYTHON_ARGCOMPLETE_OK

from __future__ import annotations

from .main import ExitCode, build_parser, main

__all__ = ["ExitCode", "build_parser", "main"]
