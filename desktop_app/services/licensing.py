from __future__ import annotations

"""Signed local license checks for TallyPrime-gated features."""

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..config import INVOICEAI_LICENSE_FILE

PUBLIC_KEY_HEX = "0740a380707f37eaf020750d2b5aeb827b1e1bf2974b784258ba59eb9a6adde8"
LICENSE_BLOCK_MESSAGE = "TallyPrime export is blocked for this license."


class LicenseError(ValueError):
    """Raised when a signed InvoiceAI license is missing or invalid."""


@dataclass(frozen=True)
class InvoiceAILicense:
    """Verified local license payload."""

    customer_name: str
    allowed_tally_serials: tuple[str, ...]
    issued_at: str
    expires_at: str | None = None


def load_license(path: str | Path | None = None) -> InvoiceAILicense:
    """Load and verify the signed local InvoiceAI license file."""
    license_path = Path(path or INVOICEAI_LICENSE_FILE).expanduser()
    if not license_path.exists():
        raise LicenseError(f"InvoiceAI license file not found: {license_path}. {LICENSE_BLOCK_MESSAGE}")
    try:
        payload = json.loads(license_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LicenseError(f"InvoiceAI license file is unreadable or malformed. {LICENSE_BLOCK_MESSAGE}") from exc
    if not isinstance(payload, dict):
        raise LicenseError(f"InvoiceAI license file must contain a JSON object. {LICENSE_BLOCK_MESSAGE}")
    if not verify_license_signature(payload):
        raise LicenseError(f"InvoiceAI license signature is invalid. {LICENSE_BLOCK_MESSAGE}")
    license_data = license_from_payload(payload)
    assert_license_not_expired(license_data)
    return license_data


def verify_license_signature(license_payload: dict[str, Any]) -> bool:
    """Return True when the license payload has a valid Ed25519 signature."""
    signature_text = license_payload.get("signature")
    if not isinstance(signature_text, str) or not signature_text.strip():
        return False
    try:
        signature = decode_signature(signature_text)
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(PUBLIC_KEY_HEX))
        public_key.verify(signature, canonical_license_bytes(license_payload))
        return True
    except (ValueError, UnicodeEncodeError, binascii.Error, InvalidSignature):
        return False


def assert_tally_serial_allowed(actual_serial: str, license_path: str | Path | None = None) -> None:
    """Raise unless the connected TallyPrime serial is allowed by the license."""
    serial = normalize_serial(actual_serial)
    if not serial:
        raise LicenseError(f"Could not verify TallyPrime serial number. {LICENSE_BLOCK_MESSAGE}")
    license_data = load_license(license_path)
    allowed = {normalize_serial(value) for value in license_data.allowed_tally_serials}
    if serial not in allowed:
        raise LicenseError(
            f"TallyPrime serial '{actual_serial}' is not allowed for this InvoiceAI license. {LICENSE_BLOCK_MESSAGE}"
        )


def license_from_payload(payload: dict[str, Any]) -> InvoiceAILicense:
    """Validate a verified license payload and return its typed shape."""
    customer_name = payload.get("customer_name")
    allowed_serials = payload.get("allowed_tally_serials")
    issued_at = payload.get("issued_at")
    expires_at = payload.get("expires_at")
    if not isinstance(customer_name, str) or not customer_name.strip():
        raise LicenseError(f"InvoiceAI license is missing customer_name. {LICENSE_BLOCK_MESSAGE}")
    if not isinstance(allowed_serials, list) or not allowed_serials:
        raise LicenseError(f"InvoiceAI license is missing allowed_tally_serials. {LICENSE_BLOCK_MESSAGE}")
    normalized_serials = tuple(normalize_serial(value) for value in allowed_serials if normalize_serial(value))
    if not normalized_serials:
        raise LicenseError(f"InvoiceAI license does not contain any valid TallyPrime serials. {LICENSE_BLOCK_MESSAGE}")
    if not isinstance(issued_at, str) or not issued_at.strip():
        raise LicenseError(f"InvoiceAI license is missing issued_at. {LICENSE_BLOCK_MESSAGE}")
    if expires_at is not None and not isinstance(expires_at, str):
        raise LicenseError(f"InvoiceAI license expires_at must be a string when present. {LICENSE_BLOCK_MESSAGE}")
    parse_license_datetime(issued_at)
    if expires_at:
        parse_license_datetime(expires_at)
    return InvoiceAILicense(
        customer_name=customer_name.strip(),
        allowed_tally_serials=normalized_serials,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def assert_license_not_expired(license_data: InvoiceAILicense) -> None:
    """Raise when the license has expired."""
    if not license_data.expires_at:
        return
    if parse_license_datetime(license_data.expires_at) < datetime.now(timezone.utc):
        raise LicenseError(f"InvoiceAI license expired on {license_data.expires_at}. {LICENSE_BLOCK_MESSAGE}")


def canonical_license_bytes(payload: dict[str, Any]) -> bytes:
    """Return deterministic JSON bytes used for signing and verification."""
    unsigned = {key: value for key, value in payload.items() if key != "signature"}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def decode_signature(value: str) -> bytes:
    """Decode a base64 or URL-safe-base64 signature value."""
    padded = value.strip() + "=" * (-len(value.strip()) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def encode_signature(value: bytes) -> str:
    """Encode a signature for storage in the license file."""
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def normalize_serial(value: Any) -> str:
    """Normalize serial text for exact allow-list matching after trimming."""
    return str(value or "").strip()


def parse_license_datetime(value: str) -> datetime:
    """Parse an ISO-8601 UTC-ish datetime from a license payload."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LicenseError(f"InvoiceAI license date is invalid: {value}. {LICENSE_BLOCK_MESSAGE}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
