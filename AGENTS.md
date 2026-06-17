# Project Context

This project is a self-contained PySide6 desktop app for invoice automation.
The active source lives in `desktop_app`. Historical `backend` or `ui` folders
are not part of the current app and should not be imported from.

Core flow:

```text
upload invoice -> classify document -> parse/extract -> normalize -> validate -> persist -> review -> export
```

Important commands, run from `desktop_app`:

```powershell
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe -m compileall -q -x "(\.venv|runtime|__pycache__)" .
.\.venv\Scripts\python.exe -m pytest tests -q
```

Working rules:

- Keep UI code in `desktop_app/ui`.
- Keep workflow orchestration in `desktop_app/services/workflow.py`.
- Keep parsing services in `desktop_app/services/parsing`, document services in
  `desktop_app/services/documents`, and export services in
  `desktop_app/services/exports`.
- Keep direct TallyPrime HTTP/XML posting services in
  `desktop_app/services/tally`; file-based downloadable exports remain in
  `desktop_app/services/exports`.
- Keep TallyPrime ledger-only and item-wise purchase posting behavior explicit
  in workflow and UI labels. Ledger-only posting creates accounting purchase
  vouchers; item-wise posting creates inventory purchase vouchers with stock
  item and unit masters only after reviewer confirmation.
- Keep database models, repository helpers, and migrations in `desktop_app/db`.
- Keep Pydantic schemas, parsing helpers, and validation in `desktop_app/domain`.
- Keep review UI changes focused on surfacing missing export-essential fields
  early, especially voucher details, party details, line items, tax totals, and
  Tally/ERP master mapping inputs.
- Improve AI extraction through Pydantic field descriptions, prompts, configurable
  model choice, normalization, and validation before adding ad hoc post-LLM
  enrichment modules.
- Do not commit `.env`, `.venv`, runtime uploads, exports, logs, or local SQLite databases.
- Do not delete local runtime data unless the user explicitly asks for cleanup.
- Prefer preserving user invoice history, audit logs, and review state during upgrades.
