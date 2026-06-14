# Decisions

- The application is a monolithic PySide6 desktop app under `desktop_app`.
- The current app does not depend on historical `backend` or `ui` folders.
- UI code should call `DesktopWorkflow` instead of touching DB/session code
  directly.
- Invoice data should pass through Pydantic schemas before persistence.
- Pydantic field descriptions and Gemini system prompts are the preferred way
  to improve AI extraction behavior before adding deterministic enrichment code.
- Normalized SQLite tables are the source of truth for extracted data.
- Legacy JSON invoice databases are upgraded in place at startup; invoice
  summaries, review state, and audit logs are preserved.
- Digital PDFs use local text/table extraction before Gemini parsing.
- Scanned PDFs and image invoices go directly to Gemini multimodal parsing.
- Gemini model selection is environment-configurable with `GEMINI_MODEL`; the
  code default remains `gemini-2.5-flash-lite`.
- `.env` may use a stronger Gemini model after invoice-set measurement, but
  `.env` itself must not be committed.
- Raw text enrichment was removed; missing fields should be addressed through
  schema descriptions, prompts, model choice, normalization, or validation.
- GST rates must remain visible, editable, preserved through corrections, and
  available for export.
- Exports represent purchase vouchers, not sales invoices.
- For scanned/image invoices, ERP-safe totals are preferred over unreliable
  item-level detail.
- Failed AI parsing should still leave an invoice available for pending review.
- Runtime uploads, exports, logs, `.env`, `.venv`, and local SQLite databases
  must stay out of git.
