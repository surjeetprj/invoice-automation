from __future__ import annotations

"""Gemini structured-output clients for text and visual invoice parsing."""

import logging
import re
from pathlib import Path
from typing import Any

from ...config import GEMINI_MODEL, GOOGLE_API_KEY
from ...domain.schemas import InvoiceData
from .ai_prompts import SYSTEM_PROMPT, VISUAL_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class AIClientError(RuntimeError):
    """Base exception for AI client failures shown to the workflow."""


class AIRateLimitError(AIClientError):
    """Raised when Gemini rejects a request because quota or rate limits are exhausted."""


def invoke_invoice_parser(raw_markdown: str, vendor_hint: str | None = None) -> dict[str, Any]:
    """Call Gemini once and return a raw InvoiceData-shaped dictionary."""
    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY is not configured. AI parsing cannot run.")

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_google_genai import ChatGoogleGenerativeAI

        llm = ChatGoogleGenerativeAI(
            model=GEMINI_MODEL,
            google_api_key=GOOGLE_API_KEY,
            temperature=0.0,
            retries=0,
        )
        structured_llm = llm.with_structured_output(InvoiceData)
        result = structured_llm.invoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=f"Invoice text:\n\n{raw_markdown}\n\nVendor hint: {vendor_hint or 'Unknown'}"),
        ])
        return result.model_dump()
    except Exception as exc:
        if is_rate_limit_error(exc):
            message = clean_ai_error_message(exc, prefix="Gemini quota or rate limit reached")
            logger.warning("AI parsing quota/rate limit: %s", message)
            raise AIRateLimitError(message) from exc
        logger.exception("AI parsing failed: %s", exc)
        raise AIClientError(f"AI parsing failed: {exc}") from exc


def invoke_invoice_file_parser(file_path: str | Path, mime_type: str, vendor_hint: str | None = None) -> dict[str, Any]:
    """Call Gemini once with inline file bytes and return an InvoiceData-shaped dictionary."""
    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY is not configured. AI parsing cannot run.")

    path = Path(file_path)
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GOOGLE_API_KEY)
        prompt = (
            f"{VISUAL_SYSTEM_PROMPT}\n\n"
            f"Vendor hint from filename: {vendor_hint or path.name or 'Unknown'}\n"
            "Extract the invoice from the attached file and return only schema-valid JSON."
        )
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_text(text=prompt),
                types.Part.from_bytes(data=path.read_bytes(), mime_type=mime_type),
            ],
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=InvoiceData,
            ),
        )
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, InvoiceData):
            return parsed.model_dump()
        if parsed is not None and hasattr(parsed, "model_dump"):
            return parsed.model_dump()
        text = getattr(response, "text", "") or ""
        return InvoiceData.model_validate_json(text).model_dump()
    except Exception as exc:
        if is_rate_limit_error(exc):
            message = clean_ai_error_message(exc, prefix="Gemini quota or rate limit reached")
            logger.warning("Visual AI parsing quota/rate limit for %s: %s", path, message)
            raise AIRateLimitError(message) from exc
        logger.exception("Visual AI parsing failed for %s: %s", path, exc)
        raise AIClientError(f"Visual AI parsing failed: {exc}") from exc


def is_rate_limit_error(exc: BaseException) -> bool:
    """Return True when an exception chain indicates Gemini quota exhaustion."""
    for current in iter_exception_chain(exc):
        text = f"{current.__class__.__name__}: {current}".lower()
        if "resourceexhausted" in text or "resource_exhausted" in text:
            return True
        if "quota exceeded" in text or "rate limit" in text or "rate-limits" in text:
            return True
        if "429" in text and ("quota" in text or "rate" in text):
            return True
    return False


def iter_exception_chain(exc: BaseException):
    """Yield an exception and its causes/contexts without looping forever."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def clean_ai_error_message(exc: BaseException, prefix: str) -> str:
    """Build a concise user-facing message from a verbose Gemini exception."""
    text = " ".join(str(exc).split())
    retry_match = re.search(r"retry in ([0-9.]+)s", text, flags=re.IGNORECASE)
    retry_text = f" Retry after about {retry_match.group(1)} seconds." if retry_match else ""
    return f"{prefix}.{retry_text} Please wait or switch to a Gemini API key/model with available quota."
