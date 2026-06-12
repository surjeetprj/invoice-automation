from __future__ import annotations

"""Regression tests for purchase voucher exports."""

import unittest

from desktop_app.domain.schemas import InvoiceData, LineItem, TaxDetail
from desktop_app.services.exporters import build_erpnext_purchase_invoice_payload, export_invoice_tally


class ExporterTests(unittest.TestCase):
    """Exercise purchase voucher export mappings."""

    def sample_purchase_invoice(self) -> InvoiceData:
        """Return an invoice with visible line-level GST rates."""
        return InvoiceData(
            invoice_number="PI-1",
            date="01-05-2026",
            due_date="10-05-2026",
            vendor_name="Vendor Pvt Ltd",
            vendor_gstin="09ABCDE1234F1Z5",
            customer_name="Sisoft Technologies Pvt Ltd",
            customer_gstin="09AAOCS7654P3Z5",
            place_of_supply="Uttar Pradesh",
            line_items=[
                LineItem(
                    description="Service",
                    hsn_sac="9983",
                    quantity=1,
                    rate=1000,
                    taxable_value=1000,
                    taxes=[
                        TaxDetail(tax_type="CGST", tax_rate=9, taxable_amount=1000, tax_amount=90),
                        TaxDetail(tax_type="SGST", tax_rate=9, taxable_amount=1000, tax_amount=90),
                    ],
                    total=1180,
                )
            ],
            total_taxable_amount=1000,
            total_cgst=90,
            total_sgst=90,
            total_tax_amount=180,
            total_amount=1180,
        )

    def test_tally_export_uses_purchase_voucher_and_input_tax_ledgers(self) -> None:
        """Tally XML should be purchase-oriented and include input GST ledgers."""
        content, filename = export_invoice_tally(1, self.sample_purchase_invoice())
        xml = content.decode("utf-8")
        self.assertTrue(filename.endswith("_tally.xml"))
        self.assertIn('VCHTYPE="Purchase"', xml)
        self.assertIn("<VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>", xml)
        self.assertIn("<LEDGERNAME>Vendor Pvt Ltd</LEDGERNAME>", xml)
        self.assertIn("<LEDGERNAME>Purchase Account</LEDGERNAME>", xml)
        self.assertIn("<LEDGERNAME>Input CGST</LEDGERNAME>", xml)
        self.assertIn("<LEDGERNAME>Input SGST</LEDGERNAME>", xml)

    def test_erpnext_payload_includes_purchase_taxes(self) -> None:
        """ERPNext purchase payload should include item and GST tax rows."""
        payload = build_erpnext_purchase_invoice_payload(self.sample_purchase_invoice())
        self.assertEqual(payload["doctype"], "Purchase Invoice")
        self.assertEqual(payload["supplier"], "Vendor Pvt Ltd")
        self.assertEqual(payload["items"][0]["expense_account"], "Purchase Account")
        self.assertEqual(payload["items"][0]["gst_hsn_code"], "9983")
        self.assertEqual(len(payload["taxes"]), 2)
        self.assertEqual(payload["taxes"][0]["account_head"], "Input CGST")
        self.assertEqual(payload["taxes"][0]["tax_amount"], 90)


if __name__ == "__main__":
    unittest.main()
