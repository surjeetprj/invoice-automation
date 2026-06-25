# Project Context

This project is a self-contained PySide6 desktop app for invoice automation.
The active source lives in `desktop_app`. Historical `backend` or `ui` folders
are not part of the current app and should not be imported from.

Core flow:

```text
upload invoice -> classify document -> parse/extract -> normalize -> validate -> persist -> review -> export
```

Important commands:

```powershell
# From the project root, after activating .venv:
python desktop_app

# From desktop_app, after activating ..\.venv:
python app.py

# Checks from the project root:
.\.venv\Scripts\python.exe -m compileall -q -x "(\.venv|runtime|__pycache__)" desktop_app
.\.venv\Scripts\python.exe -m pytest desktop_app\tests -q
```

Working rules:

- Keep UI code in `desktop_app/ui`. `main_window.py` stays the shell; settings
  dialog actions live in `desktop_app/ui/settings_actions.py`, and direct
  TallyPrime button flows live in `desktop_app/ui/tally_actions.py`.
- Keep workflow orchestration in `desktop_app/services/workflow.py`; upload
  pipeline utilities live in `desktop_app/services/workflow_pipeline.py`, review
  persistence helpers live in `desktop_app/services/workflow_review.py`, and
  Tally selected-company guard helpers live in `desktop_app/services/workflow_tally.py`.
- Keep parsing services in `desktop_app/services/parsing`, document services in
  `desktop_app/services/documents`, and export services in
  `desktop_app/services/exports`.
- Keep runtime-editable Tally settings in `desktop_app/services/settings.py`;
  saved app-data `settings.json` stores only global Tally connection settings.
  Confirmed Tally master mappings live in SQLite `tally_master_mapping`
  rows. Settings-page defaults use `source_value = "DEFAULT"`; invoice review
  shows only dynamic mappings such as vendor ledger, stock item, and unit.
  Review-page mapping rows must carry the company used when they were
  generated; saving corrections must use that submitted company context, not
  the current top-bar/Settings company selected later.
  Refreshing Tally dropdown choices must preserve current values and use
  `.env`/config defaults when no SQL mapping exists.
- Keep direct TallyPrime HTTP/XML posting services in
  `desktop_app/services/tally`; file-based downloadable exports remain in
  `desktop_app/services/exports`.
- TallyPrime direct actions are not license-gated. Keep the selected-company guard in `desktop_app/services/workflow_tally.py`. The Settings dialog may read the local TallyPrime serial using the Product AboutPage HTTP/XML report for display only; do not use the serial as an export restriction.
- Keep TallyPrime ledger-only and item-wise purchase posting behavior explicit
  in workflow and UI labels. Ledger-only posting creates accounting purchase
  vouchers; item-wise posting creates inventory purchase vouchers with stock
  item and unit masters only after reviewer confirmation.
- Keep `LineItem.item_name` as the clean TallyPrime stock item/master name.
  Preserve `LineItem.description` as the full visible invoice row text.
- Keep database models, repository helpers, and migrations in `desktop_app/db`.
- Keep Pydantic schemas, parsing helpers, and validation in `desktop_app/domain`.
- Keep review UI changes focused on surfacing missing export-essential fields
  early, especially voucher details, party details, line items, tax totals, and
  Tally/ERP master mapping inputs.
- `Submit Corrections` must only save corrected extraction data and revalidate.
  Approval must remain a separate explicit reviewer action.
- Improve AI extraction through Pydantic field descriptions, prompts, configurable
  model choice, normalization, and validation before adding ad hoc post-LLM
  enrichment modules.
- Keep Gemini quota/rate-limit failures reviewable: persist the invoice with a
  validation issue instead of crashing the desktop app.
- Keep XML parsing robust: sanitize invalid control characters/entities (e.g. `&#4;` and non-printable hex codes) using regex before parsing XML strings using `ElementTree` to avoid `ParseError`.
- Avoid using python boolean operators (`or`) directly on `ElementTree.Element` objects because empty elements (elements with no subelements) evaluate to `False` in boolean contexts.
- Prevent TallyPrime collection and TDL caching by appending a unique random suffix (e.g. `uuid.uuid4().hex[:12]`) to the collection name and header ID for all export XML queries.
- Do not commit `.env`, `.venv`, runtime uploads, exports, logs, or local SQLite databases.
- Do not delete local runtime data unless the user explicitly asks for cleanup.
- Prefer preserving user invoice history, audit logs, and review state during upgrades.
