from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class InvalidLeadProfile(ValueError):
    """Raised when the lead-generation inputs are incomplete or malformed."""


def parse_city_state(location: str) -> tuple[str, str]:
    """Parse the required ``City, State`` location format."""
    parts = [part.strip() for part in str(location).split(",")]
    if len(parts) != 2 or not all(parts):
        raise InvalidLeadProfile(
            "Location must use the format City, State, for example Pune, Maharashtra."
        )
    return parts[0], parts[1]


@dataclass(frozen=True)
class LeadProfile:
    profession: str
    location: str = ""
    services: list[str] = field(default_factory=list)
    target_sectors: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not self.profession.strip():
            raise InvalidLeadProfile("Profession is required.")
        if not any(str(service).strip() for service in self.services):
            raise InvalidLeadProfile("At least one service to sell is required.")
        parse_city_state(self.location)

    def city_state(self) -> tuple[str, str]:
        return parse_city_state(self.location)

    def search_terms(self) -> list[str]:
        values = [self.profession, self.location, *self.services, *self.target_sectors]
        terms: list[str] = []
        for value in values:
            for term in value.lower().replace(",", " ").split():
                cleaned = "".join(character for character in term if character.isalnum())
                if len(cleaned) >= 3 and cleaned not in terms:
                    terms.append(cleaned)
        return terms

    def as_text(self) -> str:
        return "; ".join(
            part
            for part in (
                f"profession: {self.profession}",
                f"location: {self.location}" if self.location else "",
                f"services: {', '.join(self.services)}" if self.services else "",
                f"target sectors: {', '.join(self.target_sectors)}"
                if self.target_sectors
                else "",
            )
            if part
        )


@dataclass
class BusinessLead:
    lead_id: str
    business_name: str
    address: str
    phone: str
    email: str
    website: str
    sector: str
    fit_score: int
    confidence: float
    fit_reason: str
    approach_comment: str
    source_url: str
    evidence_urls: list[str]
    scraped_at: str
    status: str = "New"

    def to_dict(self) -> dict[str, Any]:
        return {
            "lead_id": self.lead_id,
            "business_name": self.business_name,
            "address": self.address,
            "phone": self.phone,
            "email": self.email,
            "website": self.website,
            "sector": self.sector,
            "fit_score": self.fit_score,
            "confidence": self.confidence,
            "fit_reason": self.fit_reason,
            "approach_comment": self.approach_comment,
            "source_url": self.source_url,
            "evidence_urls": self.evidence_urls,
            "scraped_at": self.scraped_at,
            "status": self.status,
        }
