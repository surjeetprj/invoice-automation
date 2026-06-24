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
cd invoice-automation

py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r .\desktop_app\requirements.txt

Copy-Item .\desktop_app\.env.example .\desktop_app\.env
notepad .\desktop_app\.env
```

Add your Gemini API key in `desktop_app\.env`:

```env
GOOGLE_API_KEY=your_gemini_key
# Optional: keep or change the Gemini extraction model.
GEMINI_MODEL=gemini-2.5-flash-lite
# Optional: turn PDF table extraction on or off.
PDF_TABLE_EXTRACTION_ENABLED=true
```

Run the app from the project root:

```powershell
.\.venv\Scripts\Activate.ps1
python desktop_app
```

## Run Later

After setup is complete, start the app from the project root with:

```powershell
cd C:\Users\surje\Documents\invoice_automation
.\.venv\Scripts\Activate.ps1
python desktop_app
```

If you are already inside `desktop_app`, use:

```powershell
cd C:\Users\surje\Documents\invoice_automation\desktop_app
..\.venv\Scripts\Activate.ps1
python app.py
```

Use `python desktop_app` only from the project root. From inside
`desktop_app`, use `python app.py`.

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

- `JSON` and `Tally XML` create downloadable export files.
- `Post Purchase Voucher to TallyPrime` posts a ledger-only accounting Purchase
  voucher to the local TallyPrime HTTP server.
- `Post Item-wise Purchase Voucher to TallyPrime` posts an inventory Purchase
  voucher using reviewed line items.
- `Sync Vendor Ledger to TallyPrime` syncs the party ledger master.
- `Sync Purchase and GST Ledgers to TallyPrime` syncs configured purchase and
  input tax ledgers.

Direct TallyPrime posting requires TallyPrime to be running locally with the
target company open and HTTP enabled, usually at `http://localhost:9000`.
The top bar includes a Company selector and Settings button for customer-editable
Tally defaults, including the Stock Group for item-wise posting.
Tally URL, timeout, and selected company are saved globally in
`InvoiceAI\settings.json`. Confirmed ledger/group mappings are stored in SQLite
in `tally_master_mapping`, keyed by selected company and mapping type.
Settings-page values such as `Vender A/C Group`, `Stock Group`, Purchase Ledger,
and input GST ledgers are saved as SQL `DEFAULT` mappings. The Settings dialog
can refresh Tally ledgers and stock groups into editable dropdowns, so users can
select existing masters or type names that should be created later. Refreshing
ledger/group choices does not clear mapping fields: existing SQL values are
preserved, and companies without saved mappings fall back to `.env`/config defaults.
Before any direct Tally sync/post, InvoiceAI verifies that the selected company
is returned by the running TallyPrime instance. If the company is blank, typed
wrongly, or not open/loaded in TallyPrime, export is blocked before masters or
vouchers are created.
Missing masters are created only after reviewer confirmation. Item-wise posting
uses the reviewed `Item Name` field as the clean TallyPrime stock item/master
name while preserving the full invoice description separately. It can create
required units, stock groups, and stock items, and it blocks before posting when
reviewed item data is incomplete.

The Settings dialog `Test Connection` action reads the local TallyPrime serial number from the Product AboutPage HTTP/XML report and displays it for support visibility only. Direct TallyPrime export does not verify a signed InvoiceAI license.

## Developer Context

For architecture and safe-codebase context, see:

- [`AGENTS.md`](AGENTS.md)
- [`desktop_app/ARCHITECTURE.md`](desktop_app/ARCHITECTURE.md)
- [`desktop_app/DECISIONS.md`](desktop_app/DECISIONS.md)

## Developer Install

Use editable install when changing code locally.

```powershell
cd C:\Users\surje\Documents\invoice_automation
.\.venv\Scripts\python.exe -m pip install -e ".\desktop_app[dev]"
```

## Tests

Run checks before committing changes:

```powershell
cd C:\Users\surje\Documents\invoice_automation
.\.venv\Scripts\python.exe -m compileall -q -x "(\.venv|runtime|__pycache__)" desktop_app
.\.venv\Scripts\python.exe -m pytest desktop_app\tests -q
```
