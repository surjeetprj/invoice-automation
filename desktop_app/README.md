# Invoice AI Desktop App

Self-contained PySide6 desktop application for invoice upload, AI extraction,
human review, audit logs, PDF preview, and data export.

The repository is now simplified to a desktop-only app. The application code,
configuration, tests, and local virtual environment all live under
`desktop_app`.

## Setup

```powershell
cd C:\Users\surje\Documents\invoice_automation\desktop_app
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest tests -q
```

## Run

From inside `desktop_app`:

```powershell
cd C:\Users\surje\Documents\invoice_automation\desktop_app
.\.venv\Scripts\python.exe main.py
```

If `desktop_app\.venv` is already activated:

```powershell
cd C:\Users\surje\Documents\invoice_automation\desktop_app
python main.py
```

From the parent `invoice_automation` folder:

```powershell
cd C:\Users\surje\Documents\invoice_automation
.\desktop_app\.venv\Scripts\python.exe -m desktop_app
```

Do not run `python desktop_app` from inside the `desktop_app` folder. That asks
Python to open a file named `desktop_app`, which does not exist.

Runtime files are written to the OS app-data directory by default:

- Windows: `%LOCALAPPDATA%\InvoiceAI`
- macOS: `~/Library/Application Support/InvoiceAI`
- Linux: `$XDG_DATA_HOME/InvoiceAI` or `~/.local/share/InvoiceAI`

Set `DESKTOP_RUNTIME_DIR` in `.env` to override this for local testing.

## Environment

Optional `.env` values:

```env
GOOGLE_API_KEY=your_gemini_key
ERPNEXT_URL=https://your-erpnext.example
ERPNEXT_API_KEY=...
ERPNEXT_API_SECRET=...
```

Copy `.env.example` to `.env` when configuring a machine.

## Checks

```powershell
cd C:\Users\surje\Documents\invoice_automation\desktop_app
.\.venv\Scripts\python.exe -m compileall -q -x "(\.venv|runtime|__pycache__)" .
.\.venv\Scripts\python.exe -m pytest tests -q
```
