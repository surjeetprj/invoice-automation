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
  TallyPrime stock item/master name; `description` is optional multiline detail text without redundant item-name prefixes.
- Exports represent purchase vouchers, not sales invoices.
- The Metadata tab is the primary reviewer workspace. Line items belong inside
  Metadata, alongside voucher, party, shipping, bank, and tax-total groups, so
  missing export-critical values are visible before posting to an ERP.
- Required field markers in the review UI are informational in this version;
  they surface export risk but do not block approval by themselves.
- Direct TallyPrime posting belongs in `services/tally`; downloadable JSON
  and Tally XML exports remain in `services/exports`.
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
- Direct TallyPrime sync/post actions are not gated by a BahiAI license. The Settings `Test Connection` action may read the TallyPrime serial from the Product AboutPage HTTP/XML report for display and support visibility only.
- Direct TallyPrime sync/post actions must block when the selected company is
  blank or not returned by TallyPrime, so a wrong setting cannot accidentally
  create masters or vouchers in another company.
- Vendor, purchase, and GST ledger masters may be synced to TallyPrime, but
  the app relies on the configured ledger names as the stable mapping contract.
- Customer-editable Tally connection values live in runtime `settings.json`; Tally URL, timeout, and selected company are global. Confirmed ledger/group/item/unit mappings live in SQLite `tally_master_mapping` rows keyed by company, mapping type, and source value.
- Settings should prefer Tally-provided dropdown choices for ledger mappings and Stock groups while remaining editable for intentional master creation. Similarity-ranked suggestions are allowed for invoice-review dynamic mappings, but SQL confirmed mappings remain the posting source of truth.
- Settings-page dropdown choices must be populated dynamically by querying Tally master parents and recursively tracing descendants to enforce correct accounting categories (e.g., Vendor A/C Group must only show descendants of "Sundry Creditors").
- Invoice-review dynamic mapping rows must carry their generated company context. Correction saves must use that submitted company, not whichever company is selected globally at save time.
- Refreshing Tally ledger/group dropdowns must not clear mapping fields; preserve
  saved/current values and fall back to `.env`/config defaults for new companies.
- `DEFAULT_STOCK_GROUP` backs the customer-editable `Stock Group` field because item-wise posting may
  need customer-specific inventory grouping; other Tally master-type constants
  remain internal code constants.
- For scanned/image invoices, export-safe totals are preferred over unreliable
  item-level detail.
- Failed AI parsing should still leave an invoice available for pending review.
- Gemini quota or rate-limit failures should not crash processing; the invoice
  should remain reviewable with a clear validation issue.
- Submit Corrections is a save-only review action. It must preserve the current
  invoice status and review metadata, allow repeated saves, and never mark the
  invoice approved.
- Runtime uploads, exports, logs, `.env`, `.venv`, and local SQLite databases
  must stay out of git.
- Line-item descriptions are normalized to strip redundant stock item name prefixes.
- LineItem descriptions are nullable (`str | None`) in schemas, databases, and normalizers to handle blank descriptions gracefully without validation errors.
- Gemini AI client is instructed via Pydantic schema and prompts to extract compact descriptions without redundant item names and to preserve multi-line formatting.
- TallyPrime item-wise voucher exports embed line item descriptions within `<BASICUSERDESCRIPTION>` and `<ADDLDESCRIPTION>` tags for TallyPrime compatibility, omitting them dynamically when not present.
- Disabled `QToolButton` elements (such as "Export Data") are styled with visual opacity/blur to match standard button disable states.
- Invoice Detail page displays a prominent processing error banner at the top of the reviewer workspace when Gemini parsing fails due to quota limits or parsing exceptions, ensuring users are immediately notified.
