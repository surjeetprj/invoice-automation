from __future__ import annotations

"""Dashboard page for desktop KPIs and status distribution."""

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .constants import STATUS_COLORS
from .formatters import clear_layout


class DashboardPage(QWidget):
    """Dashboard page showing KPI cards and status distribution."""

    refresh_requested = Signal()

    def __init__(self) -> None:
        """Build the dashboard page layout."""
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(20)
        header = QHBoxLayout()
        title = QLabel("Dashboard")
        title.setObjectName("pageTitle")
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_requested.emit)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(refresh)
        layout.addLayout(header)

        grid = QGridLayout()
        grid.setSpacing(14)
        self.cards: dict[str, QLabel] = {}
        for index, label in enumerate(["Total Invoices", "Avg Processing Time", "Accuracy Rate", "Pending Review"]):
            card = QFrame()
            card.setObjectName("card")
            body = QVBoxLayout(card)
            caption = QLabel(label)
            caption.setObjectName("muted")
            value = QLabel("--")
            value.setObjectName("kpi")
            self.cards[label] = value
            body.addWidget(caption)
            body.addWidget(value)
            grid.addWidget(card, index // 2, index % 2)
        layout.addLayout(grid)
        section = QLabel("Status Distribution")
        section.setObjectName("sectionTitle")
        self.status_body = QVBoxLayout()
        layout.addWidget(section)
        layout.addLayout(self.status_body)
        layout.addStretch()

    def set_loading(self) -> None:
        """Show a lightweight loading state."""
        self.cards["Total Invoices"].setText("...")

    def set_stats(self, stats: dict[str, Any]) -> None:
        """Render dashboard statistics returned by the workflow service."""
        total = stats.get("total_invoices") or 0
        avg_ms = stats.get("avg_processing_time_ms")
        approved = stats.get("total_approved") or 0
        accuracy = round((approved / total) * 100) if total else 0
        self.cards["Total Invoices"].setText(str(total))
        self.cards["Avg Processing Time"].setText("--" if avg_ms is None else f"{float(avg_ms) / 1000:.2f}s")
        self.cards["Accuracy Rate"].setText(f"{accuracy}%")
        self.cards["Pending Review"].setText(str(stats.get("total_pending_review") or 0))
        clear_layout(self.status_body)
        distribution = stats.get("status_distribution") or {}
        if not distribution:
            self.status_body.addWidget(QLabel("No invoices processed yet."))
            return
        bar = QFrame()
        bar.setObjectName("statusBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        for status, count in distribution.items():
            segment = QFrame()
            segment.setToolTip(f"{status}: {count}")
            segment.setStyleSheet(f"background: {STATUS_COLORS.get(status, '#64748b')};")
            row.addWidget(segment, int(count))
        self.status_body.addWidget(bar)
        legend = QHBoxLayout()
        for status, count in distribution.items():
            label = QLabel(f"{status.replace('_', ' ')}: {count}")
            label.setStyleSheet(f"color: {STATUS_COLORS.get(status, '#475569')}; font-weight: 600;")
            legend.addWidget(label)
        legend.addStretch()
        self.status_body.addLayout(legend)
