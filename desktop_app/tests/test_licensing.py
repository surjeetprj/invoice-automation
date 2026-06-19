from __future__ import annotations

"""Tests for signed InvoiceAI local license verification."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from desktop_app.services import licensing
from desktop_app.services.licensing import LicenseError, assert_tally_serial_allowed, load_license


class LicenseServiceTests(unittest.TestCase):
    """Exercise signed license loading and Tally serial allow-list checks."""

    def setUp(self) -> None:
        self.private_key = Ed25519PrivateKey.generate()
        self.public_key_hex = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()
        self.public_key_patch = patch("desktop_app.services.licensing.PUBLIC_KEY_HEX", self.public_key_hex)
        self.public_key_patch.start()

    def tearDown(self) -> None:
        self.public_key_patch.stop()

    def signed_license_path(self, directory: str, **updates) -> Path:
        """Create a signed test license and return its path."""
        payload = {
            "customer_name": "Customer Pvt Ltd",
            "allowed_tally_serials": ["TALLY-12345"],
            "issued_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
        payload.update(updates)
        payload["signature"] = licensing.encode_signature(self.private_key.sign(licensing.canonical_license_bytes(payload)))
        path = Path(directory) / "invoiceai_license.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_valid_signed_license_allows_matching_serial(self) -> None:
        """A signed license should allow an exact matching TallyPrime serial."""
        with TemporaryDirectory() as temp_dir:
            path = self.signed_license_path(temp_dir)
            license_data = load_license(path)
            self.assertEqual(license_data.allowed_tally_serials, ("TALLY-12345",))
            assert_tally_serial_allowed("TALLY-12345", path)

    def test_valid_signed_license_rejects_non_matching_serial(self) -> None:
        """Serials not present in the signed allow-list should be rejected."""
        with TemporaryDirectory() as temp_dir:
            path = self.signed_license_path(temp_dir)
            with self.assertRaisesRegex(LicenseError, "not allowed"):
                assert_tally_serial_allowed("OTHER-999", path)

    def test_malformed_or_missing_signature_rejects(self) -> None:
        """Unsigned or malformed license payloads should fail closed."""
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invoiceai_license.json"
            path.write_text(json.dumps({"customer_name": "Customer", "allowed_tally_serials": ["TALLY-12345"], "issued_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")
            with self.assertRaisesRegex(LicenseError, "signature"):
                load_license(path)

            path.write_text(json.dumps({"customer_name": "Customer", "allowed_tally_serials": ["TALLY-12345"], "issued_at": "2026-01-01T00:00:00Z", "signature": "%%%"}), encoding="utf-8")
            with self.assertRaisesRegex(LicenseError, "signature"):
                load_license(path)

    def test_expired_license_rejects(self) -> None:
        """Expired signed licenses should fail closed."""
        with TemporaryDirectory() as temp_dir:
            expired = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            path = self.signed_license_path(temp_dir, expires_at=expired)
            with self.assertRaisesRegex(LicenseError, "expired"):
                load_license(path)

    def test_missing_license_file_rejects_tally_export(self) -> None:
        """Missing local license files should reject serial validation."""
        with TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.json"
            with self.assertRaisesRegex(LicenseError, "not found"):
                assert_tally_serial_allowed("TALLY-12345", missing)


if __name__ == "__main__":
    unittest.main()
