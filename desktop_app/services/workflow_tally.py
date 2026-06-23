from __future__ import annotations

"""TallyPrime workflow guard helpers."""

import logging

from .settings import get_tally_settings
from .tally import TallyClient

logger = logging.getLogger(__name__)


def assert_tally_company_selected(client: TallyClient) -> None:
    """Block direct Tally actions unless the selected company is available."""
    selected_company = (get_tally_settings().tally_company or "").strip()
    if not selected_company:
        raise ValueError("Select a TallyPrime company from Settings > Refresh Companies before posting or syncing to TallyPrime.")
    logger.info("Tally company verification started for %s", selected_company)
    available_companies = {str(company).strip() for company in client.fetch_company_names() if str(company).strip()}
    if selected_company not in available_companies:
        available_text = ", ".join(sorted(available_companies, key=str.casefold)) or "none returned by TallyPrime"
        logger.warning(
            "Tally company verification failed: selected=%s available=%s",
            selected_company,
            available_text,
        )
        raise ValueError(
            "Selected TallyPrime company was not found in the running TallyPrime instance. "
            "Use Settings > Refresh Companies and select the exact company before exporting. "
            f"Selected: {selected_company}. Available: {available_text}."
        )
    logger.info("Tally company verification passed for %s", selected_company)
