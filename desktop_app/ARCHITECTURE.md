# Architecture

Invoice AI Desktop is a monolithic PySide6 app. The UI calls
`DesktopWorkflow`, which is the main facade for upload, processing, review,
audit logs, exports, and direct TallyPrime posting. The invoice detail page is
designed as a compact reviewer workspace: Metadata contains grouped voucher,
party, line-item, shipping, bank, and tax-total sections so export-critical
fields can be checked against the document preview without moving between tabs.

## Processing Flow

Uploads are validated and classified by `services/documents/document_source.py`:

- `DIGITAL_PDF`: uses `pdfplumber` layout text plus Markdown table extraction,
  then the text Gemini parser.
- `SCANNED_PDF`: skips local text extraction and goes directly to Gemini
  multimodal parsing.
- `IMAGE`: accepts PNG, JPG, JPEG, and WEBP files and uses Gemini multimodal
  parsing.

All parser routes return the same `InvoiceData` shape. Gemini uses structured
output against that Pydantic schema, so field descriptions and system prompts
are part of the extraction contract. The model defaults to
`gemini-2.5-flash-lite` and can be changed with `GEMINI_MODEL` in `.env`.

The extracted data then passes through deterministic normalization, domain
validation, normalized SQLite persistence, human review, and purchase voucher
export.

Digital PDF extraction can be enabled or disabled for table parsing with
`PDF_TABLE_EXTRACTION_ENABLED`. Workflow logs report PDF extraction time and AI
parsing time separately so slow invoices can be diagnosed without guessing.
Gemini quota/rate-limit failures are converted into validation issues and the
invoice remains available for review.

## Key Modules

- `services/workflow.py`: UI-facing invoice lifecycle orchestration.
- `services/parsing/ai_parser.py`: parser facade for text and visual invoice sources.
- `services/parsing/ai_client.py`: Gemini structured-output clients using the
  configured model.
- `services/documents/extraction.py`: digital PDF text and table extraction.
- `services/parsing/invoice_normalizer.py`: numeric, GST, total, and visual line-item
  reconciliation, plus deterministic line-item identity cleanup for item names,
  HSN/SAC codes, and units.
- `domain/schemas.py`: Pydantic data contracts shared across layers.
- `domain/validation.py`: GST and arithmetic validation rules.
- `db/models.py`: normalized SQLAlchemy tables.
- `db/repository.py`: conversion between ORM rows and Pydantic models.
- `db/migrations.py`: safe startup migration for legacy JSON-based SQLite DBs.
- `services/exports/exporters.py`: CSV, JSON, downloadable Tally XML, and
  ERPNext purchase exports.
- `services/settings.py`: runtime-editable Tally defaults stored in app-data JSON.
- `services/tally/`: local TallyPrime HTTP/XML client, controlled master XML,
  ledger-only and item-wise purchase voucher XML, inventory master XML, and
  response parsing for direct posting.
- `ui/`: PySide6 pages and widgets.

## Persistence

The normalized SQLite database stores invoice summary rows, scalar extraction
fields, line items, line taxes, invoice-level tax breakups, validation issues,
and audit logs. Legacy JSON `invoices.db` files are upgraded in place on
startup without dropping old columns, summaries, review state, or audit logs.

Runtime uploads, exports, logs, and the local SQLite database live outside the
repo in the configured app-data directory.

## Export Model

Exports are purchase-voucher oriented. Tally XML uses Purchase voucher
semantics and input tax ledgers. ERPNext export creates a Purchase Invoice
payload with supplier, item rows, and GST tax rows.

Downloadable exports stay in `services/exports`. Direct posting to a locally
running TallyPrime instance is handled by `services/tally` and orchestrated by
`DesktopWorkflow`. Customer-editable Tally settings are stored in runtime
`settings.json` as global connection settings plus per-company ledger mappings such as `Vender A/C Group` and `Stock Group`.
The Settings dialog can query TallyPrime for company, ledger, and stock group
choices without requiring users to type existing master names manually. Master
refreshes populate dropdown choices only; they preserve the current mapping values
and use `.env`/config defaults when a company has no saved mapping yet.

Direct TallyPrime posting has two explicit modes:

- `Post Purchase Voucher to TallyPrime`: posts a ledger-only accounting
  Purchase voucher. This is the stable fallback when inventory item data is not
  reviewed enough for stock posting.
- `Post Item-wise Purchase Voucher to TallyPrime`: posts an inventory Purchase
  voucher with `ALLINVENTORYENTRIES.LIST` rows from reviewed line items.

Both modes follow a controlled flow:

- Verify the selected Tally company is non-empty and returned by TallyPrime.
- Check the local TallyPrime HTTP endpoint and active company.
- Preflight required masters for the approved invoice.
- Ask the reviewer before creating missing masters.
- Create or sync the vendor ledger, purchase ledger, GST ledgers, and purchase
  voucher type where allowed.
- For item-wise posting, create or sync required stock groups, units, and stock
  items where allowed. Item-wise posting uses reviewed `item_name` values as
  clean stock item/master names and preserves the full invoice `description`
  separately.
- Mark the invoice as `Posted` only after Tally accepts the voucher.

Master creation is intentionally controlled. Vendor ledgers are created under
`Sundry Creditors`, purchase ledgers under `Purchase Accounts`, input tax
ledgers under `Duties & Taxes`, stock items under the configured Stock
Group, and units as simple units. Item-wise posting is blocked before any Tally
voucher request when required line-item fields are missing or invalid, rather
than silently downgrading to ledger-only posting.

For scanned/image invoices, reliable invoice totals are preferred over
unreliable visual line-item detail. If visual line rows do not reconcile with
invoice totals, normalization creates one ERP-safe summary purchase line.

## Review Corrections

The review flow separates saving corrections from approval:

- `Submit Corrections` persists edited invoice fields and line items, refreshes
  validation, and keeps the existing invoice status, normally `Pending_Review`.
- Reviewers can submit corrections repeatedly; each save reloads the detail
  page from the saved payload.
- `Approve` is the only UI action that marks the invoice approved.

## TallyPrime License Gate

Direct TallyPrime preflight, master sync, and voucher posting are protected by a
signed local InvoiceAI license. The workflow asks `TallyClient` for the connected
TallyPrime serial number, then `services/licensing.py` verifies that the serial
appears in the signed allow-list. Upload, AI extraction, review, approval,
downloadable CSV/JSON/Tally XML exports, and ERPNext export are not blocked by
this license gate.

The license file defaults to the app runtime directory as
`invoiceai_license.json`, or `INVOICEAI_LICENSE_FILE` may point to a custom path.
InvoiceAI probes TallyPrime over HTTP/XML in this order:

1. LicenseInfo TDL report probe using `$$LicenseInfo:SerialNumber`.
2. Company collection identity probe.
3. Hidden support-only `.env` fallback `TALLY_SERIAL_NUMBER`.

The fallback remains signed-license checked and is not editable from the app
Settings dialog.

License files are signed with Ed25519. The app embeds only the public key. The
private signing key and generated customer license files must remain outside git.
