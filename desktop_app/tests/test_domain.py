from __future__ import annotations

"""Regression tests for desktop parsing and validation helpers."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

from desktop_app.config import DEFAULT_GEMINI_MODEL
from desktop_app.domain.parsing import parse_date, parse_decimal
from desktop_app.domain.schemas import InvoiceData, LineItem, SupplyType, TaxDetail
from desktop_app.services.documents.document_source import DocumentKind, InvoiceSource, classify_document, mime_type_for_path, validate_upload_file
from desktop_app.services.documents.extraction import extract_page_content, should_extract_tables, table_to_markdown
from desktop_app.services.parsing.ai_prompts import SYSTEM_PROMPT, VISUAL_SYSTEM_PROMPT
from desktop_app.services.parsing.ai_parser import extract_invoice_source_text, normalize_extracted_data, parse_invoice_source, to_float
from desktop_app.ui.widgets.line_items_table import build_line_item_taxes, cast_line_field, flatten_line_item_taxes
from desktop_app.ui.widgets.pdf_preview import render_document_to_images
from desktop_app.domain.validation import validate_gstin, validate_invoice, validate_supply_type


class DomainHelperTests(unittest.TestCase):
    """Regression tests for desktop parsing and validation helpers."""

    def test_parse_decimal_handles_currency_tokens_and_commas(self) -> None:
        """Currency labels and comma grouping should not break numeric parsing."""
        self.assertEqual(parse_decimal("â‚¹1,200"), 1200.0)
        self.assertEqual(parse_decimal("INR 5,310.50"), 5310.50)
        self.assertEqual(parse_decimal("Rs. 1,200"), 1200.0)
        self.assertEqual(parse_decimal("1,234.56"), 1234.56)
        self.assertIsNone(parse_decimal("", empty_as_none=True))

    def test_ai_parser_to_float_uses_shared_decimal_rules(self) -> None:
        """AI normalization should parse formatted currency through the shared helper."""
        self.assertEqual(to_float("â‚¹1,200"), 1200.0)
        self.assertEqual(to_float("Rs. 1,200"), 1200.0)
        self.assertEqual(to_float("INR 5,310.50"), 5310.50)
        self.assertEqual(to_float(None), 0.0)

    def test_invoice_schema_describes_high_risk_llm_fields(self) -> None:
        """Structured output schema should guide the LLM on ambiguous invoice fields."""
        invoice_properties = InvoiceData.model_json_schema()["properties"]
        line_properties = LineItem.model_json_schema()["properties"]
        tax_properties = TaxDetail.model_json_schema()["properties"]

        self.assertIn("Payment Due Date", invoice_properties["due_date"]["description"])
        self.assertIn("Ship To", invoice_properties["shipping_name"]["description"])
        self.assertIn("Keep separate", invoice_properties["shipping_address"]["description"])
        self.assertIn("Complete visible invoice rows", invoice_properties["line_items"]["description"])
        self.assertIn("totals section", invoice_properties["total_taxable_amount"]["description"])
        self.assertIn("0.0 to 1.0", invoice_properties["confidence_score"]["description"])
        self.assertIn("Short clean product or service name", line_properties["item_name"]["description"])
        self.assertIn("Do not include HSN/SAC", line_properties["item_name"]["description"])
        self.assertIn("do not guess", line_properties["quantity"]["description"])
        self.assertIn("CGST, SGST, IGST, and CESS", line_properties["taxes"]["description"])
        self.assertIn("Taxable base amount", tax_properties["taxable_amount"]["description"])

    def test_ai_prompts_include_extraction_contracts(self) -> None:
        """Prompts should reinforce schema behavior for text and visual invoices."""
        for prompt in (SYSTEM_PROMPT, VISUAL_SYSTEM_PROMPT):
            self.assertIn("Preserve nulls", prompt)
            self.assertIn("do not hallucinate", prompt)
            self.assertIn("DD-MM-YYYY", prompt)
            self.assertIn("Bill To/Billed To/Customer", prompt)
            self.assertIn("Ship To/Shipped To/Delivery To", prompt)
            self.assertIn("Due Date, Payment Due Date, Valid Upto", prompt)
            self.assertIn("CGST", prompt)
            self.assertIn("SGST", prompt)
            self.assertIn("IGST", prompt)
            self.assertIn("CESS", prompt)
            self.assertIn("item_name", prompt)
            self.assertIn("optional multiline item details", prompt)
            self.assertIn("HSN: 997315", prompt)
            self.assertIn("yr, year, month, nos, pcs, license, or user", prompt)

        self.assertIn("Use both sources together", SYSTEM_PROMPT)
        self.assertIn("Return exactly one summary line", VISUAL_SYSTEM_PROMPT)
        self.assertIn("quantity = 1", VISUAL_SYSTEM_PROMPT)
        self.assertIn("rate = total_taxable_amount", VISUAL_SYSTEM_PROMPT)

    def test_config_default_gemini_model_stays_flash_lite(self) -> None:
        """The configurable Gemini model should keep the current default."""
        self.assertEqual(DEFAULT_GEMINI_MODEL, "gemini-3.1-flash-lite")

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
            ["Service\nPlan", "1", "Ã¢â€šÂ¹1,200.00"],
            [None, "", ""],
        ], title="Invoice Items")
        self.assertIn("### Invoice Items", markdown)
        self.assertIn("| Item | Qty | Amount |", markdown)
        self.assertIn("| Service Plan | 1 | Ã¢â€šÂ¹1,200.00 |", markdown)

    def test_extract_page_content_returns_markdown_tables(self) -> None:
        """Page extraction should preserve text and expose detected tables."""

        class FakePage:
            def extract_text(self, layout: bool = False) -> str:
                return "Invoice text"

            def extract_tables(self, table_settings):
                return [[["Item", "Amount"], ["Service", "100"]]]

        with (
            patch("desktop_app.services.documents.extraction.PDF_TABLE_EXTRACTION_ENABLED", True),
        ):
            page_text, tables = extract_page_content(FakePage(), 1)
        self.assertEqual(page_text, "Invoice text")
        self.assertEqual(len(tables), 1)
        self.assertIn("| Service | 100 |", tables[0])

    def test_table_extraction_can_be_disabled(self) -> None:
        """Table extraction should be controlled by a simple on/off setting."""
        with (
            patch("desktop_app.services.documents.extraction.PDF_TABLE_EXTRACTION_ENABLED", False),
        ):
            self.assertFalse(should_extract_tables())

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
            with patch("desktop_app.services.documents.document_source.classify_pdf", return_value=DocumentKind.SCANNED_PDF):
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

    def test_upload_validation_rejects_directories_with_supported_suffixes(self) -> None:
        """Upload validation should reject folders before extension or size routing."""
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invoice.pdf"
            path.mkdir()
            with self.assertRaises(ValueError):
                validate_upload_file(path)
            with self.assertRaises(ValueError):
                classify_document(path)

    def test_mime_type_detection_handles_supported_uploads(self) -> None:
        """Supported upload extensions should map to Gemini MIME types."""
        self.assertEqual(mime_type_for_path(Path("invoice.pdf")), "application/pdf")
        self.assertEqual(mime_type_for_path(Path("invoice.png")), "image/png")
        self.assertEqual(mime_type_for_path(Path("invoice.webp")), "image/webp")

    def test_parser_source_routes_digital_pdf_to_text_parser(self) -> None:
        """Digital PDFs should use local text extraction plus text Gemini parser."""
        source = InvoiceSource(path=Path("invoice.pdf"), document_kind=DocumentKind.DIGITAL_PDF, mime_type="application/pdf")
        with (
            patch("desktop_app.services.parsing.ai_parser.extract_invoice_text", return_value="raw text") as extract,
            patch("desktop_app.services.parsing.ai_parser.invoke_invoice_parser", return_value={"invoice_number": "INV-1"}) as parse,
        ):
            result = parse_invoice_source(source, vendor_hint="invoice.pdf")

        extract.assert_called_once_with(source.path, validate=False)
        parse.assert_called_once()
        self.assertEqual(result.source_text, "raw text")
        self.assertEqual(result.document_kind, "DIGITAL_PDF")
        self.assertEqual(result.data["invoice_number"], "INV-1")

    def test_digital_pdf_text_extraction_helper_skips_reclassification(self) -> None:
        """Workflow can time text extraction separately without reclassifying the PDF."""
        source = InvoiceSource(path=Path("invoice.pdf"), document_kind=DocumentKind.DIGITAL_PDF, mime_type="application/pdf")
        with patch("desktop_app.services.parsing.ai_parser.extract_invoice_text", return_value="raw text") as extract:
            text = extract_invoice_source_text(source)

        extract.assert_called_once_with(source.path, validate=False)
        self.assertEqual(text, "raw text")

    def test_parser_source_routes_images_to_visual_parser(self) -> None:
        """Images should skip local text extraction and call the visual parser."""
        source = InvoiceSource(path=Path("invoice.png"), document_kind=DocumentKind.IMAGE, mime_type="image/png")
        with (
            patch("desktop_app.services.parsing.ai_parser.extract_invoice_text") as extract,
            patch("desktop_app.services.parsing.ai_parser.invoke_invoice_file_parser", return_value={"invoice_number": "IMG-1"}) as parse,
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
            patch("desktop_app.services.parsing.ai_parser.extract_invoice_text") as extract,
            patch("desktop_app.services.parsing.ai_parser.invoke_invoice_file_parser", return_value={"invoice_number": "SCAN-1"}) as parse,
        ):
            result = parse_invoice_source(source, vendor_hint="invoice.pdf")

        extract.assert_not_called()
        parse.assert_called_once_with(source.path, "application/pdf", "invoice.pdf")
        self.assertIsNone(result.source_text)
        self.assertEqual(result.document_kind, "SCANNED_PDF")
        self.assertEqual(result.data["invoice_number"], "SCAN-1")

    def test_visual_ai_client_returns_structured_invoice_data(self) -> None:
        """The visual Gemini client should return a schema-shaped dictionary."""
        from desktop_app.services.parsing.ai_client import invoke_invoice_file_parser

        class FakeResponse:
            parsed = InvoiceData(invoice_number="VIS-1")

        fake_client = Mock()
        fake_client.models.generate_content.return_value = FakeResponse()
        fake_context = MagicMock()
        fake_context.__enter__.return_value = fake_client
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invoice.png"
            path.write_bytes(b"fake image bytes")
            with (
                patch("desktop_app.services.parsing.ai_client.get_gemini_config", return_value=("test-key", "test-visual-model")),
                patch("google.genai.Client", return_value=fake_context),
            ):
                result = invoke_invoice_file_parser(path, "image/png", "invoice.png")

        self.assertEqual(result["invoice_number"], "VIS-1")
        fake_client.models.generate_content.assert_called_once()
        fake_context.__exit__.assert_called_once()
        self.assertEqual(fake_client.models.generate_content.call_args.kwargs["model"], "test-visual-model")

    def test_text_ai_client_uses_configured_gemini_model(self) -> None:
        """The text Gemini client should pass the configured model into google-genai."""
        from desktop_app.services.parsing.ai_client import invoke_invoice_parser

        fake_client = Mock()
        fake_client.models.generate_content.return_value = Mock(parsed=InvoiceData(invoice_number="TXT-1"))
        fake_context = MagicMock()
        fake_context.__enter__.return_value = fake_client

        with (
            patch("desktop_app.services.parsing.ai_client.get_gemini_config", return_value=("test-key", "test-text-model")),
            patch("google.genai.Client", return_value=fake_context),
        ):
            result = invoke_invoice_parser("raw invoice text", "invoice.pdf")

        self.assertEqual(result["invoice_number"], "TXT-1")
        fake_client.models.generate_content.assert_called_once()
        fake_context.__exit__.assert_called_once()
        self.assertEqual(fake_client.models.generate_content.call_args.kwargs["model"], "test-text-model")

    def test_text_ai_client_raises_clean_rate_limit_error(self) -> None:
        """Gemini quota errors should become concise application exceptions."""
        from desktop_app.services.parsing.ai_client import AIRateLimitError, invoke_invoice_parser

        fake_client = Mock()
        fake_client.models.generate_content.side_effect = RuntimeError("429 quota exceeded. Please retry in 36.5s.")
        fake_context = MagicMock()
        fake_context.__enter__.return_value = fake_client

        with (
            patch("desktop_app.services.parsing.ai_client.get_gemini_config", return_value=("test-key", "test-text-model")),
            patch("google.genai.Client", return_value=fake_context),
        ):
            with self.assertRaises(AIRateLimitError) as context:
                invoke_invoice_parser("raw invoice text", "invoice.pdf")

        message = str(context.exception)
        self.assertIn("Gemini quota or rate limit reached", message)
        self.assertIn("Retry after about 36.5 seconds", message)
        self.assertNotIn("Traceback", message)

    def test_ai_response_helpers_validate_all_result_shapes(self) -> None:
        """Gemini structured results should always pass through InvoiceData validation."""
        from desktop_app.services.parsing.ai_client import invoice_response_to_dict, invoice_result_to_dict

        class DumpableInvoice:
            def __init__(self, payload):
                self.payload = payload

            def model_dump(self):
                return self.payload

        self.assertEqual(invoice_result_to_dict(InvoiceData(invoice_number="P-1"))["invoice_number"], "P-1")
        self.assertEqual(invoice_result_to_dict({"invoice_number": "D-1"})["invoice_number"], "D-1")
        self.assertEqual(invoice_result_to_dict(DumpableInvoice({"invoice_number": "M-1"}))["invoice_number"], "M-1")
        self.assertEqual(invoice_response_to_dict(Mock(parsed=None, text='{"invoice_number":"J-1"}'))["invoice_number"], "J-1")

        with self.assertRaises(Exception):
            invoice_result_to_dict(DumpableInvoice({"line_items": "not-a-list"}))

    def test_visual_ai_client_rejects_invalid_files_before_client_creation(self) -> None:
        """Visual parsing should fail fast for invalid files without opening Gemini."""
        from desktop_app.services.parsing.ai_client import invoke_invoice_file_parser

        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            missing_path = temp_path / "missing.png"
            valid_path = temp_path / "invoice.png"
            valid_path.write_bytes(b"fake image bytes")
            large_path = temp_path / "large.pdf"
            large_path.write_bytes(b"0" * (15 * 1024 * 1024 + 1))

            with (
                patch("desktop_app.services.parsing.ai_client.get_gemini_config", return_value=("test-key", "test-model")),
                patch("google.genai.Client") as client_factory,
            ):
                with self.assertRaises(FileNotFoundError):
                    invoke_invoice_file_parser(missing_path, "image/png", "missing.png")
                with self.assertRaises(ValueError):
                    invoke_invoice_file_parser(temp_path, "image/png", "folder")
                with self.assertRaises(ValueError):
                    invoke_invoice_file_parser(valid_path, "image/bmp", "invoice.bmp")
                with self.assertRaises(ValueError):
                    invoke_invoice_file_parser(large_path, "application/pdf", "large.pdf")

        client_factory.assert_not_called()

    def test_rate_limit_detection_uses_structured_exception_details(self) -> None:
        """Quota detection should use status/code attributes and flexible retry text."""
        from desktop_app.services.parsing.ai_client import clean_ai_error_message, is_rate_limit_error

        class StatusError(RuntimeError):
            status = "RESOURCE_EXHAUSTED"

        class CodeError(RuntimeError):
            code = 429

        self.assertTrue(is_rate_limit_error(StatusError("quota unavailable")))
        self.assertTrue(is_rate_limit_error(CodeError("too many requests")))
        self.assertIn(
            "Retry after about 4.25 seconds",
            clean_ai_error_message(RuntimeError("Please retry after 4.25 seconds"), "Gemini quota or rate limit reached"),
        )
        self.assertIn(
            "Retry after about 7 seconds",
            clean_ai_error_message(RuntimeError('retryDelay: "7s"'), "Gemini quota or rate limit reached"),
        )

    def test_document_preview_accepts_image_path(self) -> None:
        """Image invoices should render into preview image paths."""
        from PIL import Image

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invoice.png"
            Image.new("RGB", (80, 60), "white").save(path)
            pages = render_document_to_images(path)

        self.assertEqual(len(pages), 1)
        self.assertTrue(pages[0].exists())

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

    def test_ai_normalization_extracts_item_identity_from_long_description(self) -> None:
        """Long descriptions should populate item_name, HSN/SAC, and unit without losing text."""
        description = (
            "VPS Custom Configuration 1 Year Plan\n"
            "Username : Vishalagarwal103, Anandkumar103\n"
            "Folder Name : PI From 15-May-2026 to 14-May-2027\n"
            "154.210.197.98:61004 HSN: 997315"
        )
        data = normalize_extracted_data({
            "line_items": [{
                "description": description,
                "quantity": 1,
                "rate": 22750,
                "discount": 1137,
                "taxable_value": 21613,
            }],
        })

        item = data["line_items"][0]
        self.assertEqual(item["item_name"], "VPS Custom Configuration")
        self.assertEqual(item["hsn_sac"], "997315")
        self.assertEqual(item["unit"], "Year")
        expected_description = (
            "1 Year Plan\n"
            "Username : Vishalagarwal103, Anandkumar103\n"
            "Folder Name : PI From 15-May-2026 to 14-May-2027\n"
            "154.210.197.98:61004 HSN: 997315"
        )
        self.assertEqual(item["description"], expected_description)

    def test_ai_normalization_defaults_missing_unit_to_pcs_when_quantity_exists(self) -> None:
        """Quantity-bearing lines without visible UOM should default to PCS for item-wise posting."""
        data = normalize_extracted_data({
            "line_items": [
                {"description": "Cloud hosting service", "quantity": 1, "rate": 100, "taxable_value": 100},
                {"description": "Reviewed item", "quantity": 2, "unit": "NOS", "rate": 50, "taxable_value": 100},
                {"description": "Support 1 Year Plan", "quantity": 1, "rate": 1200, "taxable_value": 1200},
                {"description": "Zero quantity service", "quantity": 0, "rate": 0, "taxable_value": 0},
            ],
        })

        items = data["line_items"]
        self.assertEqual(items[0]["unit"], "PCS")
        self.assertEqual(items[1]["unit"], "NOS")
        self.assertEqual(items[2]["unit"], "Year")
        self.assertNotIn("unit", items[3])

    def test_ai_normalization_keeps_existing_line_identity_fields(self) -> None:
        """Reviewed or explicit identity fields should not be overwritten from description."""
        data = normalize_extracted_data({
            "line_items": [{
                "item_name": "Reviewed Name",
                "description": "Different Service HSN: 998434 1 Year Plan",
                "hsn_sac": "1111",
                "unit": "NOS",
                "quantity": 1,
                "rate": 100,
                "taxable_value": 100,
            }],
        })

        item = data["line_items"][0]
        self.assertEqual(item["item_name"], "Reviewed Name")
        self.assertEqual(item["hsn_sac"], "1111")
        self.assertEqual(item["unit"], "NOS")

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
