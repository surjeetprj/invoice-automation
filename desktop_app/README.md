# Invoice AI Desktop App

Self-contained PySide6 monolithic desktop application. This folder is the root
for the desktop runtime and does not import from the project `backend` or `ui`
folders.

## Setup

```powershell
cd C:\Users\surje\Documents\invoice_automation\desktop_app
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest tests -q
cd ..
.\desktop_app\.venv\Scripts\python.exe -m desktop_app
```

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
