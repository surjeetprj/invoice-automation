from __future__ import annotations

"""Regression tests for purchase voucher exports."""

import json
import unittest

from desktop_app.domain.schemas import InvoiceData, LineItem, TaxDetail
from desktop_app.services.exports.exporters import export_invoice_json, export_invoice_tally


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
                    item_name="Clean Service",
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
        self.assertIn("<PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>", xml)
        self.assertIn("<ISINVOICE>No</ISINVOICE>", xml)
        self.assertIn("<PARTYINVNO>PI-1</PARTYINVNO>", xml)
        self.assertIn('<PARTYINVDATE TYPE="Date">20260501</PARTYINVDATE>', xml)
        self.assertIn("<LEDGERNAME>Vendor Pvt Ltd</LEDGERNAME>", xml)
        self.assertIn("<LEDGERNAME>Purchase Account</LEDGERNAME>", xml)
        self.assertIn("<LEDGERNAME>Input CGST</LEDGERNAME>", xml)
        self.assertIn("<LEDGERNAME>Input SGST</LEDGERNAME>", xml)
        self.assertNotIn("<ALLINVENTORYENTRIES.LIST>", xml)
        self.assertNotIn("<STOCKITEMNAME>Service</STOCKITEMNAME>", xml)

    def test_json_export_keeps_previous_line_item_shape(self) -> None:
        """JSON file export should not expose the TallyPrime-only item_name field."""
        content, _filename = export_invoice_json(1, self.sample_purchase_invoice())
        payload = json.loads(content.decode("utf-8"))
        self.assertNotIn("item_name", payload["data"]["line_items"][0])


if __name__ == "__main__":
    unittest.main()
