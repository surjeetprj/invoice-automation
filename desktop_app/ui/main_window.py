from __future__ import annotations

"""Main desktop window and UI-to-workflow orchestration."""

import logging
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..services.workflow import DesktopWorkflow
from .dashboard_page import DashboardPage
from .detail_page import DetailPage
from .invoices_page import InvoicesPage
from .upload_page import UploadPage
from .widgets.pdf_preview import render_document_to_images
from .widgets.worker import Worker, WorkerResult

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Top-level desktop shell with sidebar navigation and page routing."""

    def __init__(self) -> None:
        """Initialize workflow services, pages, worker pool, and health checks."""
        super().__init__()
        self.workflow = DesktopWorkflow()
        self.workflow.initialize()
        self.thread_pool = QThreadPool.globalInstance()
        self.active_workers: set[Worker] = set()
        self.current_document_invoice_id: int | None = None
        self.document_request_token = 0
        self.nav_buttons: dict[str, QPushButton] = {}

        self.setWindowTitle("Invoice AI Desktop")
        self.resize(1280, 820)

        root = QSplitter(Qt.Orientation.Horizontal)
        root.setHandleWidth(1)
        root.addWidget(self.build_sidebar())
        self.stack = QStackedWidget()
        self.dashboard = DashboardPage()
        self.invoices = InvoicesPage()
        self.upload = UploadPage()
        self.detail = DetailPage()
        for page in (self.dashboard, self.invoices, self.upload, self.detail):
            self.stack.addWidget(page)
        root.addWidget(self.stack)
        root.setSizes([230, 1050])
        self.setCentralWidget(root)

        self.connect_signals()
        self.health_timer = QTimer(self)
        self.health_timer.timeout.connect(self.check_health)
        self.health_timer.start(10_000)
        self.show_page("dashboard", self.dashboard)
        QTimer.singleShot(0, self.check_health)
        QTimer.singleShot(0, self.load_dashboard)

    def build_sidebar(self) -> QWidget:
        """Create the left sidebar with app status, navigation, and reviewer name."""
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(220)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(18, 24, 18, 18)

        title = QLabel("Invoice AI")
        title.setObjectName("appTitle")
        status_row = QHBoxLayout()
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(10, 10)
        self.status_text = QLabel("Checking")
        self.status_text.setObjectName("muted")
        status_row.addWidget(self.status_dot)
        status_row.addWidget(self.status_text)
        status_row.addStretch()

        buttons = {
            "dashboard": ("Dashboard", self.show_dashboard),
            "invoices": ("Invoices", self.show_invoices),
            "upload": ("Upload Invoice", self.show_upload),
        }
        layout.addWidget(title)
        layout.addLayout(status_row)
        layout.addSpacing(18)
        for key, (label, handler) in buttons.items():
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.setMinimumHeight(38)
            button.clicked.connect(handler)
            self.nav_buttons[key] = button
            layout.addWidget(button)
        layout.addStretch()

        reviewer_label = QLabel("Reviewer")
        reviewer_label.setObjectName("sectionTitle")
        self.reviewer = QLineEdit("reviewer")
        layout.addWidget(reviewer_label)
        layout.addWidget(self.reviewer)
        return sidebar

    def connect_signals(self) -> None:
        """Connect page-level signals to workflow actions."""
        self.dashboard.refresh_requested.connect(self.load_dashboard)
        self.invoices.refresh_requested.connect(self.load_invoices)
        self.invoices.invoice_selected.connect(self.open_invoice)
        self.upload.upload_requested.connect(self.upload_invoice)
        self.detail.back_requested.connect(self.show_invoices)
        self.detail.audit_requested.connect(self.load_audit)
        self.detail.approve_requested.connect(self.approve_invoice)
        self.detail.corrections_requested.connect(self.submit_corrections)
        self.detail.reject_requested.connect(self.reject_invoice)
        self.detail.reprocess_requested.connect(self.reprocess_invoice)
        self.detail.export_requested.connect(self.export_invoice)
        self.detail.pdf_requested.connect(self.load_pdf)
        self.detail.pdf_preview.zoom_requested.connect(lambda _zoom: self.reload_current_document())

    def show_page(self, key: str, page: QWidget) -> None:
        """Switch to a page and update sidebar active state."""
        self.stack.setCurrentWidget(page)
        for name, button in self.nav_buttons.items():
            button.setProperty("active", name == key)
            button.style().unpolish(button)
            button.style().polish(button)

    def show_dashboard(self) -> None:
        """Open dashboard and refresh statistics."""
        self.show_page("dashboard", self.dashboard)
        self.load_dashboard()

    def show_invoices(self) -> None:
        """Open invoice list and refresh rows."""
        self.show_page("invoices", self.invoices)
        self.load_invoices()

    def show_upload(self) -> None:
        """Open upload page."""
        self.show_page("upload", self.upload)

    def run_task(
        self,
        fn: Callable[..., Any],
        on_success: Callable[[Any], None],
        *args: Any,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        """Run a blocking callable on the Qt worker pool with structured errors."""
        worker = Worker(fn, *args)
        self.active_workers.add(worker)

        def completed(result: WorkerResult) -> None:
            if result.success:
                try:
                    on_success(result.result)
                except Exception as exc:
                    logger.exception("Task success handler failed")
                    self.show_error(str(exc))
                return
            if result.traceback:
                logger.error("Background task failed: %s\n%s", result.error_message, result.traceback)
            handler = on_error or self.show_error
            handler(result.error_message or "Unknown desktop task error.")

        worker.signals.completed.connect(completed)
        worker.signals.finished.connect(lambda w=worker: self.active_workers.discard(w))
        self.thread_pool.start(worker)

    def check_health(self) -> None:
        """Refresh the sidebar readiness indicator."""
        def ok(_result: Any) -> None:
            self.status_dot.setStyleSheet("border-radius: 5px; background: #10b981;")
            self.status_text.setText("Ready")

        self.run_task(self.workflow.health, ok, on_error=lambda _err: self.set_offline())

    def set_offline(self) -> None:
        """Show a local database/service error in the sidebar."""
        self.status_dot.setStyleSheet("border-radius: 5px; background: #ef4444;")
        self.status_text.setText("DB error")

    def load_dashboard(self) -> None:
        """Load dashboard statistics in the background."""
        self.dashboard.set_loading()
        self.run_task(self.workflow.stats, self.dashboard.set_stats)

    def load_invoices(self) -> None:
        """Load invoice table records in the background."""
        self.invoices.set_loading()
        self.run_task(self.workflow.list_invoices, self.invoices.set_invoices)

    def upload_invoice(self, path: str) -> None:
        """Start invoice validation, extraction, parsing, and persistence."""
        self.upload.set_busy(True, "Validating document and processing invoice...")

        def done(invoice: dict[str, Any]) -> None:
            self.upload.set_busy(False, "Upload complete.")
            self.open_invoice(int(invoice["id"]))

        self.run_task(
            self.workflow.upload_invoice,
            done,
            path,
            on_error=lambda err: (self.upload.set_busy(False, ""), self.show_error(err)),
        )

    def open_invoice(self, invoice_id: int) -> None:
        """Open an invoice detail page and trigger async PDF rendering."""
        self.stack.setCurrentWidget(self.detail)
        self.current_document_invoice_id = invoice_id

        def loaded(invoice: dict[str, Any]) -> None:
            self.detail.load_invoice(invoice)

        self.run_task(self.workflow.get_invoice, loaded, invoice_id)

    def load_pdf(self, invoice_id: int) -> None:
        """Load and render the invoice document preview without blocking the UI."""
        self.current_document_invoice_id = invoice_id
        self.document_request_token += 1
        token = self.document_request_token

        def path_loaded(document_path: Path) -> None:
            if not self.is_current_pdf_request(invoice_id, token):
                return
            self.detail.set_pdf_loading(document_path)
            scale = self.detail.pdf_preview.zoom

            def rendered(image_paths: list[Path]) -> None:
                if self.is_current_pdf_request(invoice_id, token):
                    self.detail.set_pdf_pages(image_paths)

            self.run_task(
                lambda: render_document_to_images(document_path, scale=scale),
                rendered,
                on_error=lambda err: self.detail.set_pdf_error(err) if self.is_current_pdf_request(invoice_id, token) else None,
            )

        self.run_task(
            self.workflow.get_document_path,
            path_loaded,
            invoice_id,
            on_error=lambda err: self.detail.set_pdf_error(err) if self.is_current_pdf_request(invoice_id, token) else None,
        )

    def reload_current_document(self) -> None:
        """Re-render the currently selected invoice document after zoom changes."""
        if self.current_document_invoice_id is not None:
            self.load_pdf(self.current_document_invoice_id)

    def is_current_pdf_request(self, invoice_id: int, token: int) -> bool:
        """Return True when an async preview result still belongs to the visible invoice."""
        return self.current_document_invoice_id == invoice_id and self.document_request_token == token

    def load_audit(self, invoice_id: int) -> None:
        """Load audit logs for the current invoice."""
        self.detail.audit.set_loading()
        self.run_task(
            self.workflow.audit_log,
            self.detail.audit.set_logs,
            invoice_id,
            on_error=self.detail.audit.set_error,
        )

    def approve_invoice(self, invoice_id: int) -> None:
        """Approve the current invoice without corrections."""
        payload = {"decision": "approve", "reviewer": self.reviewer_name()}
        self.run_task(lambda: self.workflow.submit_review(invoice_id, payload), lambda _result: self.open_invoice(invoice_id))

    def submit_corrections(self, invoice_id: int, corrections: dict[str, Any]) -> None:
        """Submit manual corrections and approve the invoice."""
        payload = {"decision": "approve_with_corrections", "reviewer": self.reviewer_name(), "corrections": corrections}
        self.run_task(lambda: self.workflow.submit_review(invoice_id, payload), lambda _result: self.open_invoice(invoice_id))

    def reject_invoice(self, invoice_id: int, reason: str) -> None:
        """Reject an invoice with a reviewer-provided reason."""
        payload = {"decision": "reject", "reviewer": self.reviewer_name(), "rejection_reason": reason}
        self.run_task(lambda: self.workflow.submit_review(invoice_id, payload), lambda _result: self.open_invoice(invoice_id))

    def reprocess_invoice(self, invoice_id: int) -> None:
        """Re-run extraction and validation for an invoice."""
        self.run_task(self.workflow.reprocess_invoice, lambda _result: self.open_invoice(invoice_id), invoice_id)

    def export_invoice(self, invoice_id: int, fmt: str) -> None:
        """Export approved invoice data locally or push it to ERPNext."""
        if fmt == "tally_post":
            self.post_invoice_to_tally(invoice_id)
            return
        if fmt == "tally_vendor":
            self.sync_vendor_master_to_tally(invoice_id)
            return
        if fmt == "tally_ledgers":
            self.sync_tally_system_ledgers(invoice_id)
            return
        if fmt == "erpnext":
            self.run_task(
                lambda: self.workflow.export_invoice(invoice_id, fmt),
                lambda _result: QMessageBox.information(self, "ERPNext", "Invoice pushed to ERPNext."),
            )
            return
        ext = "xml" if fmt == "tally" else fmt
        path, _ = QFileDialog.getSaveFileName(self, "Save Export", f"invoice_{invoice_id}.{ext}")
        if not path:
            return

        def build_and_save() -> Path:
            content, _filename = self.workflow.export_invoice(invoice_id, fmt)
            if not isinstance(content, bytes):
                raise TypeError("Export did not return file content.")
            output_path = Path(path)
            output_path.write_bytes(content)
            return output_path

        self.run_task(build_and_save, lambda saved: QMessageBox.information(self, "Export Complete", f"Saved export to:\n{saved}"))

    def post_invoice_to_tally(self, invoice_id: int) -> None:
        """Preflight and post an approved invoice to local TallyPrime."""
        def preflight_done(result: dict[str, Any]) -> None:
            missing = result.get("missing_masters") or []
            create_missing = False
            if missing:
                message = "TallyPrime is missing these masters:\n\n"
                message += "\n".join(f"- {name}" for name in missing)
                message += "\n\nCreate these masters and post the invoice?"
                if QMessageBox.question(self, "Post to TallyPrime", message) != QMessageBox.StandardButton.Yes:
                    return
                create_missing = True
            elif QMessageBox.question(self, "Post to TallyPrime", "Post this approved invoice to TallyPrime?") != QMessageBox.StandardButton.Yes:
                return

            def posted(post_result: dict[str, Any]) -> None:
                QMessageBox.information(self, "TallyPrime", post_result.get("message", "Invoice posted to TallyPrime."))
                self.open_invoice(invoice_id)

            self.run_task(
                lambda: self.workflow.post_invoice_to_tally(invoice_id, create_missing_masters=create_missing),
                posted,
            )

        self.run_task(lambda: self.workflow.tally_preflight(invoice_id), preflight_done)

    def sync_vendor_master_to_tally(self, invoice_id: int) -> None:
        """Update only the vendor ledger master in TallyPrime."""
        if QMessageBox.question(self, "Sync Vendor Master", "Update this vendor ledger in TallyPrime with extracted vendor details?") != QMessageBox.StandardButton.Yes:
            return

        def synced(result: dict[str, Any]) -> None:
            QMessageBox.information(self, "TallyPrime", result.get("message", "Vendor master synced to TallyPrime."))

        self.run_task(lambda: self.workflow.sync_vendor_master_to_tally(invoice_id), synced)

    def sync_tally_system_ledgers(self, invoice_id: int) -> None:
        """Update purchase and GST ledger masters in TallyPrime."""
        if QMessageBox.question(self, "Sync GST Ledgers", "Update Purchase Account and Input GST ledgers in TallyPrime?") != QMessageBox.StandardButton.Yes:
            return

        def synced(result: dict[str, Any]) -> None:
            QMessageBox.information(self, "TallyPrime", result.get("message", "Purchase and GST ledgers synced to TallyPrime."))

        self.run_task(lambda: self.workflow.sync_tally_system_ledgers(invoice_id), synced)

    def reviewer_name(self) -> str:
        """Return reviewer identity used for audit log rows."""
        return self.reviewer.text().strip() or "reviewer"

    def show_error(self, message: str) -> None:
        """Display a workflow error dialog."""
        QMessageBox.critical(self, "Invoice AI Desktop", message)
