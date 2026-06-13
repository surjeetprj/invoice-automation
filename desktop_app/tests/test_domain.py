from __future__ import annotations

"""Regression tests for desktop parsing and validation helpers."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from desktop_app.domain.parsing import parse_date, parse_decimal
from desktop_app.domain.schemas import InvoiceData, LineItem, SupplyType, TaxDetail
from desktop_app.services.ai_parser import enrich_from_raw_text, normalize_extracted_data, parse_invoice_source, to_float
from desktop_app.services.document_source import DocumentKind, InvoiceSource, classify_document, mime_type_for_path, validate_upload_file
from desktop_app.services.extraction import extract_page_content, table_to_markdown
from desktop_app.ui.detail_page import build_line_item_taxes, cast_line_field, flatten_line_item_taxes
from desktop_app.ui.widgets.pdf_preview import render_document_to_images
from desktop_app.domain.validation import validate_gstin, validate_invoice, validate_supply_type


class DomainHelperTests(unittest.TestCase):
    """Regression tests for desktop parsing and validation helpers."""

    def test_parse_decimal_handles_currency_tokens_and_commas(self) -> None:
        """Currency labels and comma grouping should not break numeric parsing."""
        self.assertEqual(parse_decimal("₹1,200"), 1200.0)
        self.assertEqual(parse_decimal("INR 5,310.50"), 5310.50)
        self.assertEqual(parse_decimal("Rs. 1,200"), 1200.0)
        self.assertEqual(parse_decimal("1,234.56"), 1234.56)
        self.assertIsNone(parse_decimal("", empty_as_none=True))

    def test_ai_parser_to_float_uses_shared_decimal_rules(self) -> None:
        """AI normalization should parse formatted currency through the shared helper."""
        self.assertEqual(to_float("₹1,200"), 1200.0)
        self.assertEqual(to_float("Rs. 1,200"), 1200.0)
        self.assertEqual(to_float("INR 5,310.50"), 5310.50)
        self.assertEqual(to_float(None), 0.0)

    def test_ai_normalization_derives_missing_total_tax_amount(self) -> None:
        """Component tax totals should fill total_tax_amount when AI omits it."""
        data = normalize_extracted_data({
            "total_taxable_amount": 21613.0,
            "total_igst": 3890.34,
            "total_cgst": 0.0,
            "total_sgst": 0.0,
            "total_cess": 0.0,
            "total_tax_amount": 0.0,
            "round_off": 0.0,
            "total_amount": 25503.34,
            "line_items": [],
        })
        self.assertEqual(data["total_tax_amount"], 3890.34)
        invoice = InvoiceData(vendor_name="SKE", invoice_number="SKEC2026042908", date="29-05-2026", **data)
        result = validate_invoice(invoice)
        self.assertFalse(any("Grand total mismatch" in error for error in result.errors))

    def test_table_to_markdown_cleans_pdfplumber_table(self) -> None:
        """PDF table rows should become compact Markdown for Gemini context."""
        markdown = table_to_markdown([
            ["Item", "Qty", "Amount"],
            ["Service\nPlan", "1", "â‚¹1,200.00"],
            [None, "", ""],
        ], title="Invoice Items")
        self.assertIn("### Invoice Items", markdown)
        self.assertIn("| Item | Qty | Amount |", markdown)
        self.assertIn("| Service Plan | 1 | â‚¹1,200.00 |", markdown)

    def test_extract_page_content_returns_markdown_tables(self) -> None:
        """Page extraction should preserve text and expose detected tables."""

        class FakePage:
            def extract_text(self, layout: bool = False) -> str:
                return "Invoice text"

            def extract_tables(self, table_settings):
                return [[["Item", "Amount"], ["Service", "100"]]]

        page_text, tables = extract_page_content(FakePage(), 1)
        self.assertEqual(page_text, "Invoice text")
        self.assertEqual(len(tables), 1)
        self.assertIn("| Service | 100 |", tables[0])

    def test_document_classifier_routes_images_without_pdf_extraction(self) -> None:
        """Supported image uploads should be routed as visual invoices."""
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invoice.jpg"
            path.write_bytes(b"fake image bytes")
            source = classify_document(path)

        self.assertEqual(source.document_kind, DocumentKind.IMAGE)
        self.assertEqual(source.mime_type, "image/jpeg")

    def test_document_classifier_uses_pdf_classification(self) -> None:
        """PDF uploads should keep the digital/scanned classifier result."""
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invoice.pdf"
            path.write_bytes(b"%PDF-1.4")
            with patch("desktop_app.services.document_source.classify_pdf", return_value=DocumentKind.SCANNED_PDF):
                source = classify_document(path)

        self.assertEqual(source.document_kind, DocumentKind.SCANNED_PDF)
        self.assertEqual(source.mime_type, "application/pdf")

    def test_upload_validation_rejects_unsupported_images(self) -> None:
        """Unsupported image types should fail before parsing."""
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invoice.bmp"
            path.write_bytes(b"fake image bytes")
            with self.assertRaises(ValueError):
                validate_upload_file(path)

    def test_mime_type_detection_handles_supported_uploads(self) -> None:
        """Supported upload extensions should map to Gemini MIME types."""
        self.assertEqual(mime_type_for_path(Path("invoice.pdf")), "application/pdf")
        self.assertEqual(mime_type_for_path(Path("invoice.png")), "image/png")
        self.assertEqual(mime_type_for_path(Path("invoice.webp")), "image/webp")

    def test_parser_source_routes_digital_pdf_to_text_parser(self) -> None:
        """Digital PDFs should use local text extraction plus text Gemini parser."""
        source = InvoiceSource(path=Path("invoice.pdf"), document_kind=DocumentKind.DIGITAL_PDF, mime_type="application/pdf")
        with (
            patch("desktop_app.services.ai_parser.extract_invoice_text", return_value="raw text") as extract,
            patch("desktop_app.services.ai_parser.invoke_invoice_parser", return_value={"invoice_number": "INV-1"}) as parse,
        ):
            result = parse_invoice_source(source, vendor_hint="invoice.pdf")

        extract.assert_called_once_with(source.path)
        parse.assert_called_once()
        self.assertEqual(result.source_text, "raw text")
        self.assertEqual(result.document_kind, "DIGITAL_PDF")
        self.assertEqual(result.data["invoice_number"], "INV-1")

    def test_parser_source_routes_images_to_visual_parser(self) -> None:
        """Images should skip local text extraction and call the visual parser."""
        source = InvoiceSource(path=Path("invoice.png"), document_kind=DocumentKind.IMAGE, mime_type="image/png")
        with (
            patch("desktop_app.services.ai_parser.extract_invoice_text") as extract,
            patch("desktop_app.services.ai_parser.invoke_invoice_file_parser", return_value={"invoice_number": "IMG-1"}) as parse,
        ):
            result = parse_invoice_source(source, vendor_hint="invoice.png")

        extract.assert_not_called()
        parse.assert_called_once_with(source.path, "image/png", "invoice.png")
        self.assertIsNone(result.source_text)
        self.assertEqual(result.document_kind, "IMAGE")
        self.assertEqual(result.data["invoice_number"], "IMG-1")

    def test_parser_source_routes_scanned_pdf_to_visual_parser(self) -> None:
        """Scanned PDFs should skip local text extraction and call the visual parser."""
        source = InvoiceSource(path=Path("invoice.pdf"), document_kind=DocumentKind.SCANNED_PDF, mime_type="application/pdf")
        with (
            patch("desktop_app.services.ai_parser.extract_invoice_text") as extract,
            patch("desktop_app.services.ai_parser.invoke_invoice_file_parser", return_value={"invoice_number": "SCAN-1"}) as parse,
        ):
            result = parse_invoice_source(source, vendor_hint="invoice.pdf")

        extract.assert_not_called()
        parse.assert_called_once_with(source.path, "application/pdf", "invoice.pdf")
        self.assertIsNone(result.source_text)
        self.assertEqual(result.document_kind, "SCANNED_PDF")
        self.assertEqual(result.data["invoice_number"], "SCAN-1")

    def test_visual_ai_client_returns_structured_invoice_data(self) -> None:
        """The visual Gemini client should return a schema-shaped dictionary."""
        from desktop_app.services.ai_client import invoke_invoice_file_parser

        class FakeResponse:
            parsed = InvoiceData(invoice_number="VIS-1")

        fake_client = Mock()
        fake_client.models.generate_content.return_value = FakeResponse()
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invoice.png"
            path.write_bytes(b"fake image bytes")
            with (
                patch("desktop_app.services.ai_client.GOOGLE_API_KEY", "test-key"),
                patch("google.genai.Client", return_value=fake_client),
            ):
                result = invoke_invoice_file_parser(path, "image/png", "invoice.png")

        self.assertEqual(result["invoice_number"], "VIS-1")
        fake_client.models.generate_content.assert_called_once()

    def test_document_preview_accepts_image_path(self) -> None:
        """Image invoices should render into preview image paths."""
        from PIL import Image

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invoice.png"
            Image.new("RGB", (80, 60), "white").save(path)
            pages = render_document_to_images(path)

        self.assertEqual(len(pages), 1)
        self.assertTrue(pages[0].exists())

    def test_raw_text_enrichment_extracts_ship_to_block(self) -> None:
        """Visible right-column Ship To data should be recovered if AI misses it."""
        data = {"shipping_name": None, "shipping_address": None, "shipping_gstin": None}
        raw_text = """
Bill To                                  Ship To
SISOFT TECHNOLOGIES PRIVATE LIMITED      SRC E7, SHIPRA RIVIERA BAZAR, GYAN KHAND-3,
SRC E7, GHAZIABAD                        INDIRAPURAM, Ghaziabad,
GSTIN 09AAOCS7654P3Z5                    Uttar Pradesh, 201014
                                         India
                                         GSTIN 09AAOCS7654P3Z5
Description Qty Rate Amount
"""
        enrich_from_raw_text(data, raw_text)
        self.assertIn("SHIPRA RIVIERA", data["shipping_address"])
        self.assertEqual(data["shipping_gstin"], "09AAOCS7654P3Z5")

    def test_raw_text_enrichment_extracts_due_date(self) -> None:
        """A clearly labeled due date should be filled deterministically."""
        data = {"due_date": None}
        enrich_from_raw_text(data, "Invoice Date: 27-04-2026\nDue Date: 2027-04-27\n")
        self.assertEqual(data["due_date"], "27-04-2027")

    def test_ai_normalization_updates_discounted_taxable_value(self) -> None:
        """Line taxable value should use quantity x rate minus visible discount."""
        data = normalize_extracted_data({
            "vendor_gstin": "09ABCDE1234F1Z5",
            "customer_gstin": "27ABCDE1234F1Z5",
            "total_taxable_amount": 11200.0,
            "total_cgst": 0.0,
            "total_sgst": 0.0,
            "total_igst": 2016.0,
            "total_cess": 0.0,
            "total_tax_amount": 0.0,
            "round_off": 0.0,
            "total_amount": 13216.0,
            "line_items": [{
                "description": "Service",
                "quantity": 1,
                "rate": 14000,
                "discount": 2800,
                "taxable_value": 14000,
                "total": 14000,
                "taxes": [{"tax_type": "IGST", "tax_rate": 18, "taxable_amount": 11200, "tax_amount": 2016}],
            }],
        })
        self.assertEqual(data["line_items"][0]["taxable_value"], 11200.0)
        self.assertEqual(data["line_items"][0]["total"], 11200.0)

    def test_ai_normalization_maps_inter_state_generic_tax_to_igst(self) -> None:
        """Inter-state generic GST/UTGST rows should normalize to IGST."""
        data = normalize_extracted_data({
            "vendor_gstin": "29AABCR7796N1ZC",
            "customer_gstin": "09AAOCS7654P3Z5",
            "total_taxable_amount": 2500.0,
            "total_cgst": 0.0,
            "total_sgst": 0.0,
            "total_igst": 0.0,
            "total_cess": 0.0,
            "total_tax_amount": 450.0,
            "round_off": 0.0,
            "total_amount": 2950.0,
            "line_items": [{
                "description": "Subscription",
                "quantity": 1,
                "rate": 5000,
                "discount": 0,
                "taxable_value": 2500,
                "taxes": [{"tax_type": "UTGST", "tax_rate": 18, "taxable_amount": 2500, "tax_amount": 450}],
            }],
        })
        self.assertEqual(data["supply_type"], "INTER_STATE")
        self.assertEqual(data["line_items"][0]["taxes"][0]["tax_type"], "IGST")
        self.assertEqual(data["total_igst"], 450.0)
        self.assertEqual(data["total_cgst"], 0.0)
        self.assertEqual(data["line_items"][0]["discount"], 2500.0)

    def test_ai_normalization_clears_intra_state_stale_igst_total(self) -> None:
        """Intra-state invoices should not keep a duplicate IGST aggregate."""
        data = normalize_extracted_data({
            "vendor_gstin": "09AAACQ5481G1Z9",
            "customer_gstin": "09AAOCS7654P3Z5",
            "total_taxable_amount": 2700.0,
            "total_cgst": 0.0,
            "total_sgst": 0.0,
            "total_igst": 486.0,
            "total_cess": 0.0,
            "total_tax_amount": 486.0,
            "round_off": 0.0,
            "total_amount": 3186.0,
            "line_items": [{
                "description": "Shared Server",
                "quantity": 1,
                "rate": 2700,
                "discount": 0,
                "taxable_value": 2700,
                "total": 0,
                "taxes": [{"tax_type": "GST", "tax_rate": 18, "taxable_amount": 2700, "tax_amount": 486}],
            }],
        })
        self.assertEqual(data["supply_type"], "INTRA_STATE")
        self.assertEqual(data["total_cgst"], 243.0)
        self.assertEqual(data["total_sgst"], 243.0)
        self.assertEqual(data["total_igst"], 0.0)
        self.assertEqual(data["line_items"][0]["total"], 2700.0)

    def test_visual_normalization_reconciles_invoice_8_style_state_tax_lines(self) -> None:
        """Scanned invoices should prefer a balanced summary line when item rows are partial."""
        data = normalize_extracted_data({
            "invoice_number": "GD478260000172",
            "date": "14-04-2026",
            "vendor_name": "Abhay Footwear Pvt Ltd",
            "total_taxable_amount": 73332.0,
            "total_cgst": 1833.3,
            "total_sgst": 1833.3,
            "total_igst": 0.0,
            "total_cess": 0.0,
            "total_tax_amount": 3666.6,
            "round_off": 0.0,
            "total_amount": 76998.6,
            "line_items": [{
                "sr_no": 1,
                "description": "WAVE TH-MACHO-AW25",
                "hsn_sac": "64041920",
                "quantity": 18,
                "rate": 469,
                "discount": 203.7,
                "taxable_value": 8238.3,
                "total": 76998.6,
                "taxes": [
                    {"tax_type": "CGST", "tax_rate": 2.5, "taxable_amount": 73332.0, "tax_amount": 1833.3},
                    {"tax_type": "SGST", "tax_rate": 2.5, "taxable_amount": 73332.0, "tax_amount": 1833.3},
                ],
            }],
        }, document_kind="SCANNED_PDF")

        self.assertEqual(len(data["line_items"]), 1)
        item = data["line_items"][0]
        self.assertEqual(item["quantity"], 1.0)
        self.assertEqual(item["rate"], 73332.0)
        self.assertEqual(item["taxable_value"], 73332.0)
        self.assertEqual(item["discount"], 0.0)
        self.assertEqual(item["total"], 76998.6)
        self.assertEqual([tax["tax_type"] for tax in item["taxes"]], ["CGST", "SGST"])
        result = validate_invoice(InvoiceData(**data))
        self.assertFalse(any("Taxable amount mismatch" in warning for warning in result.warnings))

    def test_visual_normalization_reconciles_invoice_9_style_igst_lines(self) -> None:
        """Image/scanned invoices should repair line totals from reliable invoice totals."""
        data = normalize_extracted_data({
            "invoice_number": "GD478260000175",
            "date": "14-04-2026",
            "vendor_name": "Abhay Footwear Pvt Ltd",
            "total_taxable_amount": 40740.0,
            "total_cgst": 0.0,
            "total_sgst": 0.0,
            "total_igst": 2037.0,
            "total_cess": 0.0,
            "total_tax_amount": 2037.0,
            "round_off": 0.0,
            "total_amount": 42777.0,
            "line_items": [{
                "sr_no": 1,
                "description": "WAVE MU-MACHO-AW25",
                "hsn_sac": "64041920",
                "quantity": 200,
                "rate": 469,
                "discount": 203.7,
                "taxable_value": 93596.3,
                "total": 42777.0,
                "taxes": [{"tax_type": "IGST", "tax_rate": 5.0, "taxable_amount": 40740.0, "tax_amount": 2037.0}],
            }],
        }, document_kind="IMAGE")

        item = data["line_items"][0]
        self.assertEqual(item["quantity"], 1.0)
        self.assertEqual(item["rate"], 40740.0)
        self.assertEqual(item["taxable_value"], 40740.0)
        self.assertEqual(item["total"], 42777.0)
        self.assertEqual(item["taxes"][0]["tax_type"], "IGST")
        self.assertEqual(item["taxes"][0]["tax_rate"], 5.0)
        result = validate_invoice(InvoiceData(**data))
        self.assertFalse(any("Taxable amount mismatch" in warning for warning in result.warnings))

    def test_digital_pdf_normalization_does_not_collapse_bad_line_items(self) -> None:
        """Digital PDFs should keep strict line-item validation behavior."""
        data = normalize_extracted_data({
            "invoice_number": "GD478260000175",
            "date": "14-04-2026",
            "vendor_name": "Abhay Footwear Pvt Ltd",
            "total_taxable_amount": 40740.0,
            "total_igst": 2037.0,
            "total_tax_amount": 2037.0,
            "round_off": 0.0,
            "total_amount": 42777.0,
            "line_items": [{
                "description": "WAVE MU-MACHO-AW25",
                "quantity": 200,
                "rate": 469,
                "discount": 203.7,
                "taxable_value": 93596.3,
                "taxes": [{"tax_type": "IGST", "tax_rate": 5.0, "taxable_amount": 40740.0, "tax_amount": 2037.0}],
            }],
        }, document_kind="DIGITAL_PDF")

        self.assertEqual(data["line_items"][0]["quantity"], 200)
        result = validate_invoice(InvoiceData(**data))
        self.assertTrue(any("line items (93596.30) and invoice total (40740.00)" in warning for warning in result.warnings))

    def test_validation_uses_component_tax_total_when_total_tax_amount_missing(self) -> None:
        """Validation should not fail grand total when only IGST aggregate is present."""
        invoice = InvoiceData(
            vendor_name="Relyon Softech Limited",
            invoice_number="RSL2026DI000215",
            date="08-05-2026",
            total_taxable_amount=2467.0,
            total_igst=444.06,
            total_tax_amount=0.0,
            round_off=-0.06,
            total_amount=2911.0,
            line_items=[
                LineItem(
                    description="Saral IncomeTax",
                    quantity=1.0,
                    rate=2467.0,
                    taxable_value=2467.0,
                    taxes=[TaxDetail(tax_type="IGST", tax_rate=18.0, taxable_amount=2467.0, tax_amount=444.06)],
                )
            ],
        )
        result = validate_invoice(invoice)
        self.assertFalse(any("Grand total mismatch" in error for error in result.errors))

    def test_validation_allows_four_digit_service_sac_group(self) -> None:
        """Four-digit SAC groups such as 9983 should not create noisy warnings."""
        warnings: list[str] = []
        from desktop_app.domain.validation import validate_hsn_sac

        validate_hsn_sac("9983", 1, warnings)
        self.assertFalse(warnings)

    def test_reverse_charge_warning_requires_raw_text_evidence(self) -> None:
        """Reverse-charge warnings should only appear when the source mentions it."""
        invoice = InvoiceData(
            vendor_name="Vendor",
            invoice_number="INV-1",
            date="01-05-2026",
            total_taxable_amount=100.0,
            total_amount=100.0,
            line_items=[LineItem(description="Service", taxable_value=100.0)],
        )
        without_label = validate_invoice(invoice)
        with_label = validate_invoice(invoice, "Reverse Charge: ")
        self.assertFalse(any("Reverse Charge" in warning for warning in without_label.warnings))
        self.assertTrue(any("Reverse Charge" in warning for warning in with_label.warnings))

    def test_line_item_casting_rejects_invalid_numeric_text(self) -> None:
        """Invalid numeric table values should raise instead of becoming zero."""
        with self.assertRaises(ValueError):
            cast_line_field("quantity", "abc")
        with self.assertRaises(ValueError):
            cast_line_field("sr_no", "1.2")
        self.assertEqual(cast_line_field("quantity", ""), 0.0)
        self.assertIsNone(cast_line_field("sr_no", ""))
        self.assertEqual(cast_line_field("rate", "123.45"), 123.45)

    def test_line_tax_helpers_flatten_and_rebuild_gst_components(self) -> None:
        """Line item tax rows should survive UI flatten/rebuild helpers."""
        item = {
            "description": "Service",
            "quantity": 1.0,
            "rate": 1000.0,
            "discount": 0.0,
            "taxable_value": 1000.0,
            "cess_amount": 0.0,
            "taxes": [
                {"tax_type": "CGST", "tax_rate": 9.0, "taxable_amount": 1000.0, "tax_amount": 90.0},
                {"tax_type": "SGST", "tax_rate": 9.0, "taxable_amount": 1000.0, "tax_amount": 90.0},
            ],
        }
        flattened = flatten_line_item_taxes(item)
        self.assertEqual(flattened["cgst_rate"], 9.0)
        self.assertEqual(flattened["sgst_amount"], 90.0)
        self.assertEqual(flattened["total"], 1180.0)

        rebuilt = build_line_item_taxes(flattened)
        self.assertEqual(rebuilt["total"], 1180.0)
        self.assertEqual(len(rebuilt["taxes"]), 2)
        self.assertEqual(rebuilt["taxes"][0]["tax_type"], "CGST")
        self.assertEqual(rebuilt["taxes"][0]["tax_rate"], 9.0)

    def test_parse_date_accepts_common_invoice_formats(self) -> None:
        """Common invoice date formats should be normalized by one helper."""
        self.assertIsNotNone(parse_date("01-05-2026"))
        self.assertIsNotNone(parse_date("2026-05-01"))
        self.assertIsNone(parse_date("not a date"))

    def test_validate_gstin_detects_bad_length(self) -> None:
        """GSTIN validation should surface invalid lengths as errors."""
        errors: list[str] = []
        warnings: list[str] = []
        validate_gstin("09ABC", "Vendor", errors, warnings)
        self.assertTrue(errors)
        self.assertFalse(warnings)

    def test_validate_supply_type_checks_state_codes(self) -> None:
        """Supply type mismatches should be reported without changing data."""
        errors: list[str] = []
        warnings: list[str] = []
        data = InvoiceData(
            vendor_gstin="09AAOCS7654P3Z5",
            customer_gstin="27ABCDE1234F1Z5",
            supply_type=SupplyType.INTRA_STATE,
        )
        validate_supply_type(data, errors, warnings)
        self.assertTrue(any("differ" in warning for warning in warnings))

    def test_validate_invoice_reports_required_fields(self) -> None:
        """Empty invoices should not silently pass validation."""
        result = validate_invoice(InvoiceData(line_items=[LineItem(description="Service", taxable_value=100)]))
        self.assertFalse(result.is_valid)
        self.assertTrue(any("invoice_number" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
