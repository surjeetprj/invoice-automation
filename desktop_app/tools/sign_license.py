from __future__ import annotations

"""Create signed InvoiceAI license files for customer TallyPrime serials.

Usage:
  $env:INVOICEAI_LICENSE_PRIVATE_KEY_HEX="<private-key-hex>"
  python desktop_app\\tools\\sign_license.py --customer "Customer" --serial 123456789 --output invoiceai_license.json
"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from desktop_app.services.licensing import canonical_license_bytes, encode_signature


def main() -> None:
    """Build and sign a local InvoiceAI license JSON file."""
    parser = argparse.ArgumentParser(description="Create a signed InvoiceAI license file.")
    parser.add_argument("--customer", required=True, help="Customer/company display name.")
    parser.add_argument("--serial", action="append", required=True, help="Allowed TallyPrime serial. Repeat for multiple serials.")
    parser.add_argument("--output", required=True, help="Output JSON license path.")
    parser.add_argument("--expires-at", default=None, help="Optional ISO-8601 expiry datetime, for example 2027-03-31T23:59:59Z.")
    args = parser.parse_args()

    private_key_hex = os.getenv("INVOICEAI_LICENSE_PRIVATE_KEY_HEX", "").strip()
    if not private_key_hex:
        raise SystemExit("INVOICEAI_LICENSE_PRIVATE_KEY_HEX is required and must not be committed.")
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    payload = {
        "customer_name": args.customer,
        "allowed_tally_serials": [serial.strip() for serial in args.serial if serial.strip()],
        "issued_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    if args.expires_at:
        payload["expires_at"] = args.expires_at
    payload["signature"] = encode_signature(private_key.sign(canonical_license_bytes(payload)))
    output = Path(args.output).expanduser()
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote signed InvoiceAI license: {output}")


if __name__ == "__main__":
    main()
