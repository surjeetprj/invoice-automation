from __future__ import annotations

"""Compatibility launcher for users who run ``python main.py``."""

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from desktop_app.app import main
else:
    from .app import main


if __name__ == "__main__":
    raise SystemExit(main())
