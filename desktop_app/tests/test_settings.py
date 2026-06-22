from __future__ import annotations

"""Tests for runtime desktop settings persistence."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from desktop_app.services import settings
from desktop_app.services.settings import get_tally_settings, get_tally_settings_payload, save_tally_settings


class RuntimeSettingsTests(unittest.TestCase):
    """Exercise runtime JSON settings used by customer installs."""

    def test_defaults_load_when_settings_file_is_absent(self) -> None:
        """Missing settings.json should fall back to config/.env defaults."""
        with TemporaryDirectory() as temp_dir:
            with patch("desktop_app.services.settings.SETTINGS_FILE", Path(temp_dir) / "settings.json"):
                loaded = get_tally_settings()

        self.assertEqual(loaded.tally_url, settings.config.TALLY_URL)
        self.assertEqual(loaded.purchase_ledger_name, settings.config.PURCHASE_LEDGER_NAME)
        self.assertEqual(loaded.default_stock_group, settings.config.DEFAULT_STOCK_GROUP)

    def test_saved_runtime_settings_split_global_and_company_mapping(self) -> None:
        """Saved Tally settings should keep global values separate from company mappings."""
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            with patch("desktop_app.services.settings.SETTINGS_FILE", path):
                saved = save_tally_settings(
                    {
                        "tally_url": "http://localhost:9100",
                        "tally_company": "Runtime Company",
                        "purchase_ledger_name": "Runtime Purchase",
                        "default_stock_group": "Runtime Stock Group",
                    }
                )
                loaded = get_tally_settings()
                payload = get_tally_settings_payload()
                persisted = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(saved.tally_company, "Runtime Company")
        self.assertEqual(loaded.tally_url, "http://localhost:9100")
        self.assertEqual(loaded.purchase_ledger_name, "Runtime Purchase")
        self.assertEqual(loaded.default_stock_group, "Runtime Stock Group")
        self.assertEqual(persisted["tally"]["global"]["selected_company"], "Runtime Company")
        self.assertEqual(persisted["tally"]["global"]["tally_url"], "http://localhost:9100")
        self.assertEqual(persisted["tally"]["companies"]["Runtime Company"]["purchase_ledger_name"], "Runtime Purchase")
        self.assertNotIn("tally_serial_number", persisted["tally"]["global"])
        self.assertIn("company_mappings", payload)
        self.assertEqual(payload["default_company_mapping"]["purchase_ledger_name"], settings.config.PURCHASE_LEDGER_NAME)
        self.assertEqual(payload["default_company_mapping"]["default_stock_group"], settings.config.DEFAULT_STOCK_GROUP)

    def test_company_mappings_are_independent(self) -> None:
        """Each selected company should keep its own ledger mapping."""
        with TemporaryDirectory() as temp_dir:
            with patch("desktop_app.services.settings.SETTINGS_FILE", Path(temp_dir) / "settings.json"):
                save_tally_settings(
                    {
                        "tally_company": "SRC Pvt Ltd",
                        "purchase_ledger_name": "SRC Purchase",
                        "default_stock_group": "SRC Stock",
                    }
                )
                save_tally_settings(
                    {
                        "tally_company": "Surjeet Pvt Ltd",
                        "purchase_ledger_name": "Surjeet Purchase",
                        "default_stock_group": "Surjeet Stock",
                    }
                )
                surjeet = get_tally_settings()
                src = save_tally_settings({"selected_company": "SRC Pvt Ltd"})

        self.assertEqual(surjeet.tally_company, "Surjeet Pvt Ltd")
        self.assertEqual(surjeet.purchase_ledger_name, "Surjeet Purchase")
        self.assertEqual(src.tally_company, "SRC Pvt Ltd")
        self.assertEqual(src.purchase_ledger_name, "SRC Purchase")
        self.assertEqual(src.default_stock_group, "SRC Stock")

    def test_legacy_flat_runtime_settings_still_load(self) -> None:
        """Existing flat settings.json files should keep working after the refactor."""
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "tally": {
                            "tally_url": "http://localhost:9100",
                            "tally_company": "Legacy Company",
                            "purchase_ledger_name": "Legacy Purchase",
                            "default_stock_group": "Legacy Stock",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch("desktop_app.services.settings.SETTINGS_FILE", path):
                loaded = get_tally_settings()

        self.assertEqual(loaded.tally_company, "Legacy Company")
        self.assertEqual(loaded.tally_url, "http://localhost:9100")
        self.assertEqual(loaded.purchase_ledger_name, "Legacy Purchase")
        self.assertEqual(loaded.default_stock_group, "Legacy Stock")

    def test_partial_runtime_settings_preserve_defaults(self) -> None:
        """Partial settings payloads should not erase unspecified defaults."""
        with TemporaryDirectory() as temp_dir:
            with patch("desktop_app.services.settings.SETTINGS_FILE", Path(temp_dir) / "settings.json"):
                saved = save_tally_settings({"tally_company": "Only Company", "tally_timeout_seconds": "bad"})

        self.assertEqual(saved.tally_company, "Only Company")
        self.assertEqual(saved.tally_url, settings.config.TALLY_URL)
        self.assertEqual(saved.tally_timeout_seconds, settings.config.TALLY_TIMEOUT_SECONDS)


if __name__ == "__main__":
    unittest.main()

