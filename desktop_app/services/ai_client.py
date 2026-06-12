from __future__ import annotations

"""Gemini structured-output client for invoice parsing."""

import logging
from typing import Any

from ..config import GOOGLE_API_KEY
from ..domain.schemas import InvoiceData
from .ai_prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def invoke_invoice_parser(raw_markdown: str, vendor_hint: str | None = None) -> dict[str, Any]:
    """Call Gemini once and return a raw InvoiceData-shaped dictionary."""
    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY is not configured. AI parsing cannot run.")

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_google_genai import ChatGoogleGenerativeAI

        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash-lite",
            google_api_key=GOOGLE_API_KEY,
            temperature=0.0,
        )
        structured_llm = llm.with_structured_output(InvoiceData)
        result = structured_llm.invoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=f"Invoice text:\n\n{raw_markdown}\n\nVendor hint: {vendor_hint or 'Unknown'}"),
        ])
        return result.model_dump()
    except Exception as exc:
        logger.exception("AI parsing failed: %s", exc)
        raise RuntimeError(f"AI parsing failed: {exc}") from exc
