from __future__ import annotations

import asyncio
import logging

from src.web_scrapper_agent.agent import generate_lead_workbook
from src.web_scrapper_agent.lead_models import InvalidLeadProfile, LeadProfile

logger = logging.getLogger(__name__)


async def _ainput(prompt: str = "") -> str:
    """Read terminal input without blocking the event loop."""
    return (await asyncio.to_thread(input, prompt)).strip()


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    print("Lead-generation agent ready.")
    print("The MCP server must be running in another terminal.")
    print("Type 'quit' at the profession prompt to exit.\n")

    while True:
        profession = await _ainput("Profession: ")
        if profession.lower() in {"quit", "exit"}:
            break
        if not profession:
            print("Profession is required.\n")
            continue

        location = await _ainput("Target location (City, State): ")
        try:
            _, _ = LeadProfile(
                profession=profession,
                location=location,
                services=["validation"],
            ).city_state()
        except InvalidLeadProfile as error:
            print(f"{error}\n")
            continue

        services = _split_values(
            await _ainput("Services to sell (comma-separated, required): ")
        )
        if not services:
            print("At least one service to sell is required.\n")
            continue
        target_sectors = _split_values(
            await _ainput("Target sectors (comma-separated, optional): ")
        )

        max_leads = _positive_int(await _ainput("Maximum leads [20]: "), 20)
        profile = LeadProfile(
            profession=profession,
            location=location,
            services=services,
            target_sectors=target_sectors,
        )

        print("\nCollecting and qualifying leads...\n")
        try:
            result = await generate_lead_workbook(
                profile,
                max_leads=max_leads,
            )
        except Exception as error:
            logger.exception("Lead generation failed.")
            print(f"Lead generation failed: {error}\n")
            continue

        print(
            f"Generated {result.get('lead_count', 0)} leads. "
            f"Workbook: {result['workbook_path']}\n"
        )
        for lead in result.get("leads", [])[:5]:
            print(
                f"- {lead.get('business_name', 'Unknown')} | "
                f"fit {lead.get('fit_score', 0)} | "
                f"{lead.get('website', '')}"
            )
        print()


def _split_values(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _positive_int(value: str, default: int) -> int:
    try:
        return max(1, min(int(value or default), 100))
    except ValueError:
        return default


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
