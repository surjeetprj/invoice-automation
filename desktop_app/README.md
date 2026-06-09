# Invoice AI Desktop App

Self-contained PySide6 monolithic desktop application. This folder is the root
for the desktop runtime and does not import from the project `backend` or `ui`
folders.

## Setup

```powershell
cd C:\Users\surje\Documents\invoice_automation\desktop_app
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Runtime files are written under `desktop_app\runtime`.

## Environment

Optional `.env` values:

```env
GOOGLE_API_KEY=your_gemini_key
ERPNEXT_URL=https://your-erpnext.example
ERPNEXT_API_KEY=...
ERPNEXT_API_SECRET=...
```
