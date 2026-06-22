from __future__ import annotations

"""Focused tests for the TallyPrime HTTP client and serial probe."""

import unittest
from unittest.mock import patch

from desktop_app.services.tally import TallyClient
from desktop_app.services.tally.responses import parse_tally_response
from desktop_app.services.tally.serial import build_tally_about_page_xml, parse_tally_about_page_serial_number


class TallyClientTests(unittest.TestCase):
    """Exercise Tally response parsing and Product AboutPage serial verification."""

    def test_tally_response_parses_success_failure_and_malformed_xml(self) -> None:
        """Tally responses should normalize success and failure cases."""
        success = parse_tally_response("<RESPONSE><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></RESPONSE>")
        self.assertTrue(success.success)
        self.assertEqual(success.created, 1)

        failure = parse_tally_response("<RESPONSE><CREATED>0</CREATED><ERRORS>1</ERRORS><LINEERROR>Missing ledger</LINEERROR></RESPONSE>")
        self.assertFalse(failure.success)
        self.assertEqual(failure.errors, 1)
        self.assertIn("Missing ledger", failure.messages)

        malformed = parse_tally_response("not xml")
        self.assertFalse(malformed.success)
        self.assertEqual(malformed.errors, 1)

    def test_tally_client_parses_serial_number_from_about_page_response(self) -> None:
        """Product AboutPage XML should expose the TallyPrime serial field."""
        about_xml = """
        <ENVELOPE>
          <ABOUTPAGEPROMPT>Application</ABOUTPAGEPROMPT>
          <ABOUTPAGEINFO>TallyPrime</ABOUTPAGEINFO>
          <ABOUTPAGEPROMPT>Serial Number</ABOUTPAGEPROMPT>
          <ABOUTPAGEINFO>764401410</ABOUTPAGEINFO>
        </ENVELOPE>
        """
        self.assertEqual(parse_tally_about_page_serial_number(about_xml), "764401410")
        about_request = build_tally_about_page_xml()
        self.assertIn(b"<TYPE>Data</TYPE>", about_request)
        self.assertIn(b"<ID>Product AboutPage</ID>", about_request)
        self.assertIn(b"$$SysName:XML", about_request)

    def test_tally_client_uses_only_about_page_probe(self) -> None:
        """The Product AboutPage report should be the only connected serial probe."""
        client = TallyClient()
        about_xml = """
        <ENVELOPE>
          <ABOUTPAGEPROMPT>Serial Number</ABOUTPAGEPROMPT>
          <ABOUTPAGEINFO>TALLY-12345</ABOUTPAGEINFO>
        </ENVELOPE>
        """
        with self.assertLogs("desktop_app.services.tally.client", level="INFO") as logs:
            with patch.object(client, "post_xml", return_value=about_xml) as post_xml:
                self.assertEqual(client.fetch_tally_serial_number(), "TALLY-12345")

        self.assertEqual(post_xml.call_count, 1)
        self.assertIn(b"Product AboutPage", post_xml.call_args.args[0])
        self.assertIn("Tally serial verified using Product AboutPage probe", "\n".join(logs.output))

    def test_tally_client_fails_closed_when_about_page_has_no_serial(self) -> None:
        """Missing Product AboutPage serial should block TallyPrime export."""
        client = TallyClient()
        with self.assertLogs("desktop_app.services.tally.client", level="ERROR") as logs:
            with patch.object(
                client,
                "post_xml",
                return_value="<ENVELOPE><ABOUTPAGEPROMPT>Application</ABOUTPAGEPROMPT><ABOUTPAGEINFO>TallyPrime</ABOUTPAGEINFO></ENVELOPE>",
            ) as post_xml:
                with self.assertRaisesRegex(ConnectionError, "Product AboutPage did not expose"):
                    client.fetch_tally_serial_number()

        self.assertEqual(post_xml.call_count, 1)
        self.assertIn("Product AboutPage", "\n".join(logs.output))




if __name__ == "__main__":
    unittest.main()
