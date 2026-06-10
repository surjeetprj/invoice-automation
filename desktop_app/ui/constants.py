"""Shared UI constants for invoice pages and widgets."""

STATUS_COLORS = {
    "Approved": "#059669",
    "Rejected": "#dc2626",
    "Pending_Review": "#d97706",
    "Extracted": "#7c3aed",
    "New": "#2563eb",
    "In_Process": "#0891b2",
    "Posted": "#6d28d9",
}

NUMERIC_FIELDS = {
    "total_taxable_amount", "total_cgst", "total_sgst", "total_igst",
    "total_cess", "total_tax_amount", "round_off", "total_amount",
}
LINE_FLOAT_FIELDS = {"quantity", "rate", "discount", "taxable_value", "cess_amount", "total"}

FIELD_GROUPS = {
    "General": ["invoice_number", "date", "due_date", "place_of_supply", "amount_in_words"],
    "Vendor": ["vendor_name", "vendor_address", "vendor_gstin", "vendor_state_code", "vendor_pan", "vendor_msme_no", "vendor_contact"],
    "Customer": ["customer_name", "customer_address", "customer_gstin", "customer_state_code", "customer_pan", "customer_phone"],
    "Shipping & Transport": ["shipping_name", "shipping_address", "shipping_gstin", "transport_name", "transport_id", "vehicle_number", "challan_no", "challan_date", "e_way_bill_no", "irn", "ack_number", "ack_date"],
    "Tax & Totals": ["total_taxable_amount", "total_cgst", "total_sgst", "total_igst", "total_cess", "total_tax_amount", "round_off", "total_amount"],
    "Bank": ["bank_name", "account_no", "ifsc", "branch"],
}

LINE_COLUMNS = [
    ("sr_no", "Sr No"), ("description", "Description"), ("hsn_sac", "HSN/SAC"),
    ("unit", "Unit"), ("quantity", "Quantity"), ("rate", "Rate"),
    ("taxable_value", "Taxable"), ("cess_amount", "Cess"), ("discount", "Discount"),
    ("total", "Total"),
]
