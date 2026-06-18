from __future__ import annotations

"""Reusable QRunnable worker with structured errors and traceback logging."""

import traceback
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


@dataclass(slots=True)
class WorkerResult:
    """Structured result emitted by background workers."""

    success: bool
    result: Any = None
    error_message: str | None = None
    traceback: str | None = None
    exception_type: str | None = None


class WorkerSignals(QObject):
    """Qt signals emitted by a background worker."""

    progress = Signal(object)
    completed = Signal(object)
    finished = Signal()


class Worker(QRunnable):
    """Run a blocking callable on Qt's thread pool and emit a WorkerResult."""

    def __init__(
        self,
        fn: Callable[..., Any],
        *args: Any,
        progress_callback_name: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Store the callable and arguments for later background execution."""
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        if progress_callback_name:
            self.kwargs[progress_callback_name] = self.signals.progress.emit

    @Slot()
    def run(self) -> None:
        """Execute the task and emit a structured completion payload."""
        try:
            self.signals.completed.emit(WorkerResult(success=True, result=self.fn(*self.args, **self.kwargs)))
        except Exception as exc:
            self.signals.completed.emit(
                WorkerResult(
                    success=False,
                    error_message=str(exc),
                    traceback=traceback.format_exc(),
                    exception_type=type(exc).__name__,
                )
            )
        finally:
            self.signals.finished.emit()
