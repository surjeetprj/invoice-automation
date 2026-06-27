from __future__ import annotations

"""Shared XML helpers for TallyPrime HTTP/XML integration."""

import re
import uuid

INVALID_XML_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
XML_CHAR_REF_RE = re.compile(r"&#(?:x([0-9A-Fa-f]+)|([0-9]+));")


def unique_collection_name(base_name: str) -> str:
    """Return a non-cached Tally collection name for dynamic export requests."""
    return f"{base_name}_{uuid.uuid4().hex[:12]}"


def sanitize_xml_text(xml_text: str) -> str:
    """Remove only XML 1.0-invalid control characters and character references."""
    text = INVALID_XML_CHAR_RE.sub("", str(xml_text or ""))

    def replace_invalid_reference(match: re.Match[str]) -> str:
        value = int(match.group(1), 16) if match.group(1) else int(match.group(2))
        return match.group(0) if is_valid_xml_codepoint(value) else ""

    return XML_CHAR_REF_RE.sub(replace_invalid_reference, text)


def is_valid_xml_codepoint(value: int) -> bool:
    """Return True when a codepoint is allowed by XML 1.0."""
    return (
        value in {0x09, 0x0A, 0x0D}
        or 0x20 <= value <= 0xD7FF
        or 0xE000 <= value <= 0xFFFD
        or 0x10000 <= value <= 0x10FFFF
    )
