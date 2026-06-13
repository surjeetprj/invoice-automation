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
