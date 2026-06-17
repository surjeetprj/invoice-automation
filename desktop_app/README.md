# Invoice AI Desktop App

PySide6 desktop application for invoice upload, AI extraction, human review,
audit logs, document preview, and purchase voucher export.

Supported invoice uploads:
- Digital PDF invoices
- Scanned or image-only PDF invoices
- PNG, JPG, JPEG, and WEBP image invoices

## Setup

Use these steps when installing the app on a machine for normal use.

```powershell
git clone https://github.com/surjeetprj/invoice-automation.git
cd invoice-automation\desktop_app

py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Copy-Item .env.example .env
notepad .env
```

Add your Gemini API key in `.env`:

```env
GOOGLE_API_KEY=your_gemini_key
# Optional: keep or change the Gemini extraction model.
GEMINI_MODEL=gemini-2.5-flash-lite
# Optional: turn PDF table extraction on or off.
PDF_TABLE_EXTRACTION_ENABLED=true
```

Run the app:

```powershell
.\.venv\Scripts\python.exe main.py
```

## Run Later

After setup is complete, start the app with:

```powershell
cd C:\Users\surje\Documents\invoice_automation\desktop_app
.\.venv\Scripts\python.exe main.py
```

If the virtual environment is already activated:

```powershell
python main.py
```

Do not run `python desktop_app` from inside the `desktop_app` folder. That asks
Python to open a file named `desktop_app`, which does not exist.

## Upgrade Behavior

On first startup after an app update, Invoice AI upgrades an existing local
`InvoiceAI\invoices.db` in place when needed. Legacy JSON extraction fields are
backfilled into the normalized invoice tables while invoice summaries, review
state, and audit logs are preserved. No manual runtime data cleanup is required.

## Review And Export

The invoice detail screen uses the Metadata tab as the main reviewer workspace.
Voucher details, party details, line items, shipping and transport, bank fields,
and tax totals are grouped together so missing export-critical data can be
checked against the document preview quickly. Required markers are informational
in this version; they highlight likely export problems but do not block approval
by themselves.

`Submit Corrections` saves edited extraction data and refreshes validation, but
it does not approve the invoice. Reviewers can save corrections multiple times.
Final approval always requires a separate click on `Approve`.

The Export Data menu supports file-based exports and direct TallyPrime posting:

- `CSV`, `JSON`, `Tally XML`, and `ERPNext` create downloadable export files.
- `Post Purchase Voucher to TallyPrime` posts a ledger-only accounting Purchase
  voucher to the local TallyPrime HTTP server.
- `Post Item-wise Purchase Voucher to TallyPrime` posts an inventory Purchase
  voucher using reviewed line items.
- `Sync Vendor Ledger to TallyPrime` syncs the party ledger master.
- `Sync Purchase and GST Ledgers to TallyPrime` syncs configured purchase and
  input tax ledgers.

Direct TallyPrime posting requires TallyPrime to be running locally with the
target company open and HTTP enabled, usually at `http://localhost:9000`.
Missing masters are created only after reviewer confirmation. Item-wise posting
uses the reviewed `Item Name` field as the clean TallyPrime stock item/master
name while preserving the full invoice description separately. It can create
required units, stock groups, and stock items, and it blocks before posting when
reviewed item data is incomplete.

## Developer Context

For architecture and safe-codebase context, see:

- [`../AGENTS.md`](../AGENTS.md)
- [`ARCHITECTURE.md`](ARCHITECTURE.md)
- [`DECISIONS.md`](DECISIONS.md)

## Developer Install

Use editable install when changing code locally.

```powershell
cd C:\Users\surje\Documents\invoice_automation\desktop_app
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Tests

Run checks before committing changes:

```powershell
cd C:\Users\surje\Documents\invoice_automation\desktop_app
.\.venv\Scripts\python.exe -m compileall -q -x "(\.venv|runtime|__pycache__)" .
.\.venv\Scripts\python.exe -m pytest tests -q
```
