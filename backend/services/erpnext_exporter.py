"""
ERPNext Exporter — Push validated invoice data to ERPNext via REST API.

Provides integration to automatically create "Sales Invoice" doctypes
in Frappe / ERPNext from the extracted InvoiceData schema.
"""
import logging
import requests
import re
import hashlib
import urllib.parse
from datetime import datetime
from typing import Dict, Any

from config import ERPNEXT_URL, ERPNEXT_API_KEY, ERPNEXT_API_SECRET
from schemas import InvoiceData

logger = logging.getLogger(__name__)

def _format_date(date_str: str | None) -> str:
    """Format extracted date string to ERPNext standard YYYY-MM-DD."""
    if not date_str:
        return datetime.now().strftime("%Y-%m-%d")
        
    formats_to_try = [
        "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
        "%d %b %Y", "%d %B %Y", "%d-%b-%Y"
    ]
    
    for fmt in formats_to_try:
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
            
    # Fallback to current date if parsing fails
    logger.warning(f"Could not parse date '{date_str}', falling back to current date.")
    return datetime.now().strftime("%Y-%m-%d")


def _ensure_supplier(supplier_name: str, headers: dict):
    """Ensure supplier exists in ERPNext."""
    safe_name = urllib.parse.quote(supplier_name)
    url = f"{ERPNEXT_URL.rstrip('/')}/api/resource/Supplier/{safe_name}"
    if requests.get(url, headers=headers).status_code == 200:
        return
    
    logger.info(f"Creating missing Supplier in ERPNext: {supplier_name}")
    payload = {
        "doctype": "Supplier",
        "supplier_name": supplier_name,
        "supplier_type": "Company",
        "supplier_group": "All Supplier Groups" # typical default
    }
    requests.post(f"{ERPNEXT_URL.rstrip('/')}/api/resource/Supplier", json=payload, headers=headers)


def _ensure_hsn_code(hsn_code: str, headers: dict):
    """Ensure HSN/SAC code exists in ERPNext (India Compliance)."""
    if not hsn_code:
        return
    safe_hsn = urllib.parse.quote(hsn_code)
    url = f"{ERPNEXT_URL.rstrip('/')}/api/resource/GST HSN Code/{safe_hsn}"
    if requests.get(url, headers=headers).status_code == 200:
        return
    
    logger.info(f"Creating missing GST HSN Code in ERPNext: {hsn_code}")
    payload = {
        "doctype": "GST HSN Code",
        "name": hsn_code,
        "hsn_code": hsn_code,
        "description": f"Auto-created HSN {hsn_code}"
    }
    requests.post(f"{ERPNEXT_URL.rstrip('/')}/api/resource/GST HSN Code", json=payload, headers=headers)


def _ensure_item(item_code: str, item_name: str, hsn_code: str, headers: dict) -> str:
    """Ensure item exists in ERPNext."""
    safe_item_code = urllib.parse.quote(item_code)
    url = f"{ERPNEXT_URL.rstrip('/')}/api/resource/Item/{safe_item_code}"
    
    if requests.get(url, headers=headers).status_code == 200:
        return item_code
        
    # Ensure HSN code exists first (India Compliance requirement)
    _ensure_hsn_code(hsn_code, headers)

    logger.info(f"Creating missing Item in ERPNext: {item_code}")
    payload = {
        "doctype": "Item",
        "item_code": item_code,
        "item_name": str(item_name)[:140],
        "item_group": "All Item Groups", # safest default
        "stock_uom": "Nos", 
        "is_stock_item": 0,
        "gst_hsn_code": hsn_code # Required for India Compliance
    }
    res = requests.post(f"{ERPNEXT_URL.rstrip('/')}/api/resource/Item", json=payload, headers=headers)
    
    if res.status_code == 417 and "Item Group" in res.text:
        # Fallback to another common default if "All Item Groups" is missing
        payload["item_group"] = "Products"
        res = requests.post(f"{ERPNEXT_URL.rstrip('/')}/api/resource/Item", json=payload, headers=headers)

    if res.status_code not in (200, 201):
        logger.error(f"Failed to create Item {item_code}: {res.status_code} - {res.text}")
    return item_code


def export_to_erpnext(invoice_data: InvoiceData) -> Dict[str, Any]:
    """
    Exports the validated InvoiceData to ERPNext as a Purchase Invoice.
    
    Args:
        invoice_data: Extracted and validated invoice data.
        
    Returns:
        Dict containing success status and message or ERPNext reference.
    """
    if not ERPNEXT_URL or ERPNEXT_URL == "http://localhost:8000":
        logger.warning("ERPNEXT_URL may not be configured properly.")
        
    if not ERPNEXT_API_KEY or not ERPNEXT_API_SECRET:
        return {"success": False, "error": "ERPNext API credentials are not configured in environment/config."}

    # Prepare Headers
    headers = {
        "Authorization": f"token {ERPNEXT_API_KEY}:{ERPNEXT_API_SECRET}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    supplier_name = invoice_data.vendor_name or "Unknown Supplier"
    _ensure_supplier(supplier_name, headers)

    # Map Line Items
    items = []
    for idx, item in enumerate(invoice_data.line_items):
        raw_description = item.description or f"Unknown Item {idx}"
        
        # Generate a safe, unique ERPNext item_code to prevent URL/Database errors
        safe_prefix = re.sub(r'[^A-Za-z0-9]', '', raw_description.upper())[:10]
        hash_str = hashlib.md5(raw_description.encode('utf-8')).hexdigest()[:6]
        item_code = f"ITM-{safe_prefix}-{hash_str}"
        
        _ensure_item(item_code, raw_description, item.hsn_sac, headers)

        items.append({
            "item_code": item_code,
            "item_name": raw_description[:140],
            "description": f"HSN: {item.hsn_sac}\n{raw_description}" if item.hsn_sac else raw_description,
            "qty": item.quantity if item.quantity > 0 else 1.0,
            "rate": item.rate,
            "uom": "Nos"
        })
        
    # Prepare Payload
    payload = {
        "doctype": "Purchase Invoice",
        "supplier": supplier_name,
        "posting_date": _format_date(invoice_data.date),
        "due_date": _format_date(invoice_data.due_date) if invoice_data.due_date else _format_date(invoice_data.date),
        "items": items,
        "set_posting_time": 1,
        "bill_no": invoice_data.invoice_number,
        "bill_date": _format_date(invoice_data.date),
        "remarks": f"Auto-generated Purchase Invoice (Payable).\nVendor: {supplier_name}"
    }

    try:
        endpoint = f"{ERPNEXT_URL.rstrip('/')}/api/resource/Purchase Invoice"
        logger.info(f"Pushing invoice {invoice_data.invoice_number} to ERPNext at {endpoint}")
        
        response = requests.post(endpoint, json=payload, headers=headers, timeout=15)
        
        if response.status_code == 200:
            resp_data = response.json()
            erp_doc_name = resp_data.get("data", {}).get("name")
            logger.info(f"Successfully created ERPNext Sales Invoice: {erp_doc_name}")
            return {
                "success": True, 
                "message": f"Successfully exported to ERPNext. Reference: {erp_doc_name}",
                "erp_reference": erp_doc_name
            }
        else:
            error_msg = f"ERPNext export failed with status {response.status_code}: {response.text}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
            
    except requests.exceptions.RequestException as e:
        logger.exception("Network error while connecting to ERPNext.")
        return {"success": False, "error": f"Connection error: {str(e)}"}
