# Architecture

Invoice AI Desktop is a monolithic PySide6 app. The UI calls
`DesktopWorkflow`, which is the main facade for upload, processing, review,
audit logs, and exports.

## Processing Flow

Uploads are validated and classified by `services/documents/document_source.py`:

- `DIGITAL_PDF`: uses `pdfplumber` layout text plus Markdown table extraction,
  then the text Gemini parser.
- `SCANNED_PDF`: skips local text extraction and goes directly to Gemini
  multimodal parsing.
- `IMAGE`: accepts PNG, JPG, JPEG, and WEBP files and uses Gemini multimodal
  parsing.

All parser routes return the same `InvoiceData` shape. The data then passes
through deterministic normalization, domain validation, normalized SQLite
persistence, human review, and purchase voucher export.

## Key Modules

- `services/workflow.py`: UI-facing invoice lifecycle orchestration.
- `services/parsing/ai_parser.py`: parser facade for text and visual invoice sources.
- `services/parsing/ai_client.py`: Gemini structured-output clients.
- `services/documents/extraction.py`: digital PDF text and table extraction.
- `services/parsing/invoice_normalizer.py`: numeric, GST, total, and visual line-item
  reconciliation.
- `domain/schemas.py`: Pydantic data contracts shared across layers.
- `domain/validation.py`: GST and arithmetic validation rules.
- `db/models.py`: normalized SQLAlchemy tables.
- `db/repository.py`: conversion between ORM rows and Pydantic models.
- `db/migrations.py`: safe startup migration for legacy JSON-based SQLite DBs.
- `services/exports/exporters.py`: CSV, JSON, Tally XML, and ERPNext purchase exports.
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

For scanned/image invoices, reliable invoice totals are preferred over
unreliable visual line-item detail. If visual line rows do not reconcile with
invoice totals, normalization creates one ERP-safe summary purchase line.
