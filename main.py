"""Entrypoint shim for local CLI and deploy platforms that look for main.py."""

from lingo.server import app, main

__all__ = ["app", "main"]

if __name__ == "__main__":
    main()
