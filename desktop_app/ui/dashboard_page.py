from __future__ import annotations

"""Dashboard page for desktop KPIs and status distribution."""

from typing import Any

from PySide6.QtCore import QDate, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QDateEdit, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .constants import STATUS_COLORS
from .formatters import clear_layout

USAGE_COLORS = {
    "AI Calls": "#2563eb",
    "Reprocesses": "#d97706",
}


class DonutCanvas(QWidget):
    """Painted circular distribution chart canvas."""

    def __init__(self) -> None:
        super().__init__()
        self.segments: list[tuple[str, int, str]] = []
        self.center_text = "0"
        self.empty_text = "No data"
        self.setMinimumSize(280, 280)
        self.setToolTip(self.empty_text)

    def sizeHint(self) -> QSize:
        """Return stable chart dimensions for dashboard layout."""
        return QSize(300, 300)

    def set_data(self, segments: list[tuple[str, int, str]], center_text: str, empty_text: str) -> None:
        """Update chart data and repaint."""
        self.segments = [(label, int(count), color) for label, count, color in segments if int(count) > 0]
        self.center_text = center_text
        self.empty_text = empty_text
        if self.segments:
            self.setToolTip("\n".join(f"{label}: {count}" for label, count, _color in self.segments))
        else:
            self.setToolTip(empty_text)
        self.update()

    def paintEvent(self, _event) -> None:
        """Paint the donut chart."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - 48
        rect = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        pen = QPen(QColor("#e5e7eb"), 32)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.drawEllipse(rect)

        total = sum(count for _label, count, _color in self.segments)
        if total:
            start_angle = 90 * 16
            used_span = 0
            full_span = -(360 * 16)
            for index, (_label, count, color) in enumerate(self.segments):
                if index == len(self.segments) - 1:
                    span_angle = full_span - used_span
                else:
                    span_angle = -round((count / total) * 360 * 16)
                    used_span += span_angle
                pen.setColor(QColor(color))
                painter.setPen(pen)
                painter.drawArc(rect, start_angle, span_angle)
                start_angle += span_angle

        painter.setPen(QColor("#0f172a"))
        font = painter.font()
        font.setPointSize(26)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.center_text)
        if not total:
            painter.setPen(QColor("#64748b"))
            font.setPointSize(9)
            font.setBold(False)
            painter.setFont(font)
            label_rect = QRectF(rect.left(), rect.center().y() + 22, rect.width(), 24)
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, self.empty_text)


class DonutChart(QFrame):
    """Dashboard card containing a donut chart and legend."""

    def __init__(self, title: str, empty_text: str) -> None:
        super().__init__()
        self.setObjectName("card")
        self.empty_text = empty_text
        self.legend_labels: list[QLabel] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("sectionTitle")
        self.controls = QHBoxLayout()
        self.controls.setSpacing(8)
        self.canvas = DonutCanvas()
        self.legend = QHBoxLayout()
        self.legend.setSpacing(16)
        layout.addWidget(self.title_label)
        layout.addLayout(self.controls)
        layout.addWidget(self.canvas, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(self.legend)

    @property
    def segments(self) -> list[tuple[str, int, str]]:
        """Expose current non-empty chart segments for tests."""
        return self.canvas.segments

    def set_data(self, segments: list[tuple[str, int, str]], center_total: int) -> None:
        """Render chart data and legend rows."""
        self.canvas.set_data(segments, str(center_total), self.empty_text)
        clear_layout(self.legend)
        self.legend_labels = []
        visible_segments = self.canvas.segments or [(self.empty_text, 0, "#94a3b8")]
        for label, count, color in visible_segments:
            row = QHBoxLayout()
            row.setSpacing(5)
            swatch = QFrame()
            swatch.setFixedSize(10, 10)
            swatch.setStyleSheet(f"background: {color}; border-radius: 5px;")
            text = QLabel(f"{label}: {count}") if count else QLabel(label)
            text.setStyleSheet(f"color: {color}; font-weight: 600;")
            self.legend_labels.append(text)
            row.addWidget(swatch)
            row.addWidget(text)
            self.legend.addLayout(row)
        self.legend.addStretch()


class DashboardPage(QWidget):
    """Dashboard page showing KPI cards and distribution charts."""

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

        self.usage_from = QDateEdit()
        self.usage_from.setDisplayFormat("yyyy-MM-dd")
        self.usage_from.setCalendarPopup(True)
        self.usage_from.setDate(QDate.currentDate())
        self.usage_from.setToolTip("Usage counts include AI calls and reprocesses on or after this date.")
        self.usage_from.dateChanged.connect(lambda _date: self.refresh_requested.emit())

        grid = QGridLayout()
        grid.setSpacing(14)
        self.cards: dict[str, QLabel] = {}
        card_labels = [
            "Avg Processing Time",
            "Accuracy Rate",
        ]
        for index, label in enumerate(card_labels):
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

        charts = QGridLayout()
        charts.setSpacing(14)
        self.usage_chart = DonutChart("AI Usage Distribution", "No usage")
        self.usage_chart.controls.addWidget(QLabel("Usage From"))
        self.usage_chart.controls.addWidget(self.usage_from)
        self.usage_chart.controls.addStretch()
        self.status_chart = DonutChart("Invoice Status Distribution", "No invoices")
        charts.addWidget(self.usage_chart, 0, 0)
        charts.addWidget(self.status_chart, 0, 1)
        charts.setColumnStretch(0, 1)
        charts.setColumnStretch(1, 1)
        layout.addLayout(charts, stretch=1)
        layout.addStretch()

    def usage_from_date(self) -> str:
        """Return the selected usage start date for workflow stats."""
        return self.usage_from.date().toString("yyyy-MM-dd")

    def set_loading(self) -> None:
        """Show a lightweight loading state."""
        self.cards["Avg Processing Time"].setText("...")

    def set_stats(self, stats: dict[str, Any]) -> None:
        """Render dashboard statistics returned by the workflow service."""
        total = stats.get("total_invoices") or 0
        avg_ms = stats.get("avg_processing_time_ms")
        approved = stats.get("total_approved") or 0
        accuracy = round((approved / total) * 100) if total else 0
        total_usage = int(stats.get("total_usage_count") or 0)
        ai_calls = int(stats.get("ai_calls_since_date") or 0)
        reprocesses = int(stats.get("reprocesses_since_date") or 0)
        self.cards["Avg Processing Time"].setText("--" if avg_ms is None else f"{float(avg_ms) / 1000:.2f}s")
        self.cards["Accuracy Rate"].setText(f"{accuracy}%")
        self.usage_chart.set_data(
            [
                ("AI Calls", ai_calls, USAGE_COLORS["AI Calls"]),
                ("Reprocesses", reprocesses, USAGE_COLORS["Reprocesses"]),
            ],
            total_usage,
        )
        distribution = stats.get("status_distribution") or {}
        status_segments = [
            (status.replace("_", " "), int(count), STATUS_COLORS.get(status, "#64748b"))
            for status, count in distribution.items()
        ]
        self.status_chart.set_data(status_segments, int(total))
