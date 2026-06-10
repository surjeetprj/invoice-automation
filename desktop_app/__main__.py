from __future__ import annotations

"""Allow running the desktop app with ``python -m desktop_app``."""

from .app import main


if __name__ == "__main__":
    raise SystemExit(main())
