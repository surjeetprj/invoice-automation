from __future__ import annotations

"""Application factory and entrypoint for the desktop package."""

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .config import LOG_DIR
from .ui.main_window import MainWindow

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    """Configure console and file logging for the desktop application."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "desktop_app.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
        force=True,
    )
    logger.info("Logging initialized: %s", log_file)


def load_stylesheet(app: QApplication) -> None:
    """Load the package QSS stylesheet if it is available."""
    qss_path = Path(__file__).resolve().parent / "resources" / "styles.qss"
    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))


def create_app(argv: list[str] | None = None) -> tuple[QApplication, MainWindow]:
    """Create the Qt application and top-level window for tests or launchers."""
    configure_logging()
    app = QApplication(argv or sys.argv)
    load_stylesheet(app)
    window = MainWindow()
    return app, window


def main() -> int:
    """Run the Invoice AI desktop application."""
    app, window = create_app()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
