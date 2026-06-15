from __future__ import annotations

"""Helpers for normalizing TallyPrime XML responses."""

from dataclasses import dataclass
from xml.etree import ElementTree


@dataclass(frozen=True)
class TallyResponse:
    """Parsed Tally import response."""

    success: bool
    created: int = 0
    altered: int = 0
    errors: int = 0
    exceptions: int = 0
    cancelled: int = 0
    last_voucher_id: str | None = None
    messages: tuple[str, ...] = ()
    raw_xml: str = ""

    @property
    def summary(self) -> str:
        """Return a compact human-readable response summary."""
        parts = [f"created={self.created}", f"altered={self.altered}", f"errors={self.errors}"]
        if self.exceptions:
            parts.append(f"exceptions={self.exceptions}")
        if self.cancelled:
            parts.append(f"cancelled={self.cancelled}")
        if self.last_voucher_id:
            parts.append(f"last_voucher_id={self.last_voucher_id}")
        if self.messages:
            parts.append("; ".join(self.messages))
        elif not self.success and self.raw_xml.strip():
            parts.append(f"raw_response={compact_xml_excerpt(self.raw_xml)}")
        return ", ".join(parts)


def parse_tally_response(xml_text: str) -> TallyResponse:
    """Parse TallyPrime response XML into a predictable result object."""
    try:
        root = ElementTree.fromstring(xml_text.strip())
    except ElementTree.ParseError as exc:
        return TallyResponse(success=False, errors=1, messages=(f"Malformed Tally response: {exc}",), raw_xml=xml_text)

    created = first_int(root, "CREATED")
    altered = first_int(root, "ALTERED")
    errors = first_int(root, "ERRORS")
    exceptions = first_int(root, "EXCEPTIONS")
    cancelled = first_int(root, "CANCELLED")
    last_voucher_id = first_text(root, "LASTVCHID")
    messages = tuple(text for text in collect_texts(root, {"LINEERROR", "LASTIMPORTERROR", "ERROR", "RESPONSE"}) if text)
    success = errors == 0 and exceptions == 0 and cancelled == 0 and (created > 0 or altered > 0)
    return TallyResponse(
        success=success,
        created=created,
        altered=altered,
        errors=errors,
        exceptions=exceptions,
        cancelled=cancelled,
        last_voucher_id=last_voucher_id,
        messages=messages,
        raw_xml=xml_text,
    )


def first_int(root: ElementTree.Element, tag: str) -> int:
    """Read the first integer value for a tag anywhere in the response."""
    text = first_text(root, tag)
    if text is None:
        return 0
    try:
        return int(float(text.strip()))
    except ValueError:
        return 0


def first_text(root: ElementTree.Element, tag: str) -> str | None:
    """Read the first non-empty text for a tag anywhere in the response."""
    wanted = tag.upper()
    for element in root.iter():
        if element.tag.upper() == wanted and element.text and element.text.strip():
            return element.text.strip()
    return None


def collect_texts(root: ElementTree.Element, tags: set[str]) -> list[str]:
    """Collect non-empty text values for any of the requested tag names."""
    wanted = {tag.upper() for tag in tags}
    values: list[str] = []
    for element in root.iter():
        if element.tag.upper() in wanted and element.text and element.text.strip():
            text = element.text.strip()
            if text not in values:
                values.append(text)
    return values


def compact_xml_excerpt(xml_text: str, limit: int = 400) -> str:
    """Return a single-line response excerpt for unlabelled Tally failures."""
    compact = " ".join(xml_text.split())
    return compact[:limit] + ("..." if len(compact) > limit else "")
