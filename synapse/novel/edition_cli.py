"""Command-line entry point for the model-free novel edition flow."""

from __future__ import annotations

from .edition_flow import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
