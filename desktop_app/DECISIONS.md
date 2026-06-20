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
- Line items have both `item_name` and `description`. `item_name` is the clean
  TallyPrime stock item/master name; `description` is the full invoice row text.
- Exports represent purchase vouchers, not sales invoices.
- The Metadata tab is the primary reviewer workspace. Line items belong inside
  Metadata, alongside voucher, party, shipping, bank, and tax-total groups, so
  missing export-critical values are visible before posting to an ERP.
- Required field markers in the review UI are informational in this version;
  they surface export risk but do not block approval by themselves.
- Direct TallyPrime posting belongs in `services/tally`; downloadable CSV,
  JSON, Tally XML, and ERPNext exports remain in `services/exports`.
- Direct TallyPrime posting uses controlled master creation after reviewer
  confirmation, not blind auto-creation.
- TallyPrime direct posting has two explicit modes: ledger-only accounting
  Purchase vouchers and item-wise inventory Purchase vouchers.
- Ledger-only posting remains available as the stable fallback when reviewed
  item data is incomplete or inventory posting is not desired.
- Item-wise TallyPrime posting may create stock groups, units, and stock item
  masters after reviewer confirmation. It uses reviewed line-item `item_name`
  values as stock item names and reviewed unit text after simple cleanup.
- Item-wise posting must block before sending a voucher to TallyPrime when
  required item fields are incomplete; it must not silently downgrade to
  ledger-only posting.
- A successful direct TallyPrime post changes invoice status to `Posted`; a
  failed post leaves the existing invoice status unchanged.
- Direct TallyPrime sync/post actions require a signed local InvoiceAI license
  whose allowed serial list matches the connected TallyPrime serial or
  support-only fallback serial. This gate must not block upload, review,
  approval, downloadable exports, or ERPNext export.
- TallyPrime serial verification should probe the connected TallyPrime over
  HTTP/XML first with the LicenseInfo TDL report, then the company identity
  collection. The `.env` `TALLY_SERIAL_NUMBER` value is a support-only fallback
  and is still verified against the signed license allow-list.
- Vendor, purchase, and GST ledger masters may be synced to TallyPrime, but
  the app relies on the configured ledger names as the stable mapping contract.
- Customer-editable Tally defaults live in runtime `settings.json`; saved
  runtime settings override `.env` defaults and affect future Tally actions
  without requiring an app restart.
- `DEFAULT_STOCK_GROUP` is customer-editable because item-wise posting may
  need customer-specific inventory grouping; other Tally master-type constants
  remain internal code constants.
- For scanned/image invoices, ERP-safe totals are preferred over unreliable
  item-level detail.
- Failed AI parsing should still leave an invoice available for pending review.
- Gemini quota or rate-limit failures should not crash processing; the invoice
  should remain reviewable with a clear validation issue.
- Submit Corrections is a save-only review action. It must preserve the current
  invoice status and review metadata, allow repeated saves, and never mark the
  invoice approved.
- Runtime uploads, exports, logs, `.env`, `.venv`, and local SQLite databases
  must stay out of git.
