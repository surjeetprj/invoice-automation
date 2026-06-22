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
The top bar includes a Company selector and Settings button for customer-editable
Tally defaults, including the Stock Group for item-wise posting.
Tally URL, timeout, and license file are saved globally in `InvoiceAI\settings.json`.
Ledger mapping values, including `Vender A/C Group` and `Stock Group`, are saved per selected company. The Settings
dialog can refresh Tally ledgers and stock groups into editable dropdowns, so
users can select existing masters or type names that should be created later.
Refreshing ledger/group choices does not clear mapping fields: existing saved values
are preserved, and companies without saved mappings fall back to `.env`/config defaults.
Before any direct Tally sync/post, InvoiceAI verifies that the selected company
is returned by the running TallyPrime instance. If the company is blank, typed
wrongly, or not open/loaded in TallyPrime, export is blocked before masters or
vouchers are created.
Missing masters are created only after reviewer confirmation. Item-wise posting
uses the reviewed `Item Name` field as the clean TallyPrime stock item/master
name while preserving the full invoice description separately. It can create
required units, stock groups, and stock items, and it blocks before posting when
reviewed item data is incomplete.

Direct TallyPrime posting and Tally master sync also require a signed InvoiceAI
license file whose allowed TallyPrime serial list matches the connected local
TallyPrime installation. Configure `INVOICEAI_LICENSE_FILE` when the license
file is not stored at the default app-data path. InvoiceAI verifies the TallyPrime serial only through the Product AboutPage HTTP/XML report. If Product AboutPage does not expose the serial, direct TallyPrime sync/post is blocked.

## License Key Generation

Do not manually choose or type a private key. Generate an Ed25519 key pair with
a cryptographic random generator:

```powershell
cd C:\Users\surje\Documents\invoice_automation

.\.venv\Scripts\python.exe -c "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; from cryptography.hazmat.primitives import serialization; key=Ed25519PrivateKey.generate(); print('PRIVATE_KEY_HEX=' + key.private_bytes(encoding=serialization.Encoding.Raw, format=serialization.PrivateFormat.Raw, encryption_algorithm=serialization.NoEncryption()).hex()); print('PUBLIC_KEY_HEX=' + key.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw).hex())"
```

Keep `PRIVATE_KEY_HEX` secret and never commit it. Replace `PUBLIC_KEY_HEX` in
`desktop_app/services/licensing.py` before building a customer release.

Use the private key only to generate signed customer license files:

```powershell
$env:INVOICEAI_LICENSE_PRIVATE_KEY_HEX="your-private-key-hex"

.\.venv\Scripts\python.exe desktop_app\tools\sign_license.py `
  --customer "Customer Name" `
  --serial "TALLY-SERIAL-NUMBER" `
  --output "invoiceai_license.json"
```

Place `invoiceai_license.json` at the default app runtime location on the customer machine:

```text
C:\Users\<WindowsUser>\AppData\Local\InvoiceAI\invoiceai_license.json
```

Alternatively, store it anywhere safe and set `INVOICEAI_LICENSE_FILE` in
`desktop_app\.env` to the full file path.

Share only `invoiceai_license.json` with the customer. If the private key leaks,
rotate the key pair and ship a new app build with the new public key.

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
