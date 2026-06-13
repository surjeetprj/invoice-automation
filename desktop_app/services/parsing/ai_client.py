from __future__ import annotations

"""Gemini structured-output clients for text and visual invoice parsing."""

import logging
from pathlib import Path
from typing import Any

from ...config import GOOGLE_API_KEY
from ...domain.schemas import InvoiceData
from .ai_prompts import SYSTEM_PROMPT, VISUAL_SYSTEM_PROMPT

logger = logging.getLogger(__name__)
GEMINI_MODEL = "gemini-2.5-flash-lite"


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
        logger.exception("Visual AI parsing failed for %s: %s", path, exc)
        raise RuntimeError(f"Visual AI parsing failed: {exc}") from exc
