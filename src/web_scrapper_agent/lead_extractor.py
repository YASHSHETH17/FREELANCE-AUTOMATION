from __future__ import annotations

import re
from hashlib import sha1
from typing import Any
from urllib.parse import urlparse

from src.web_scrapper_agent.lead_models import BusinessLead, LeadProfile

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{6,}\d)(?!\w)")
_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9][A-Za-z0-9 .,'-]{2,80}\s+"
    r"(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr|Boulevard|Blvd|Way|Parkway|Pkwy)\b",
    re.I,
)
_BUSINESS_TYPES = {
    "organization",
    "corporation",
    "localbusiness",
    "professionalservice",
    "store",
    "restaurant",
    "agency",
    "website",
}


def extract_business_lead(
    page: dict[str, Any],
    profile: LeadProfile,
) -> BusinessLead:
    structured = _business_structured_record(page.get("structured_data", []))
    text = str(page.get("text", ""))
    links = page.get("links", []) if isinstance(page.get("links"), list) else []

    business_name = _first_nonempty(
        structured.get("name"),
        page.get("title"),
        urlparse(str(page.get("final_url", page.get("url", "")))).hostname,
        "Unknown business",
    )
    address = _address_text(structured.get("address")) or _find_address(text)
    email = _first_nonempty(structured.get("email"), _find_email(text), _contact_link(links, "mailto:"))
    phone = _first_nonempty(
        structured.get("telephone"),
        _find_phone(text),
        _contact_link(links, "tel:"),
    )
    website = _first_nonempty(
        structured.get("url"),
        page.get("canonical_url"),
        page.get("final_url"),
    )
    sector = _first_nonempty(
        structured.get("industry"),
        structured.get("category"),
        _matched_sector(text, profile),
        ", ".join(profile.target_sectors),
        "Unknown",
    )

    lowered_text = text.lower()
    matched_terms = [term for term in profile.search_terms() if term in lowered_text]
    score = min(100, 25 + len(set(matched_terms)) * 7)
    if address:
        score += 8
    if phone or email:
        score += 12
    if sector != "Unknown":
        score += 10
    if profile.location and profile.location.lower() in lowered_text:
        score += 10
    score = min(100, score)

    evidence_urls = [str(page.get("final_url", page.get("url", "")))]
    for link in links:
        if not isinstance(link, dict):
            continue
        link_url = str(link.get("url", ""))
        link_text = str(link.get("text", "")).lower()
        if link_url.startswith(("mailto:", "tel:")):
            continue
        if any(word in link_text or word in link_url.lower() for word in ("contact", "about", "service")):
            if link_url not in evidence_urls:
                evidence_urls.append(link_url)

    fit_reason = _fit_reason(profile, matched_terms, address, phone, email, sector)
    approach_comment = _default_approach(profile, business_name, matched_terms, sector)
    lead_id = sha1(website.encode("utf-8")).hexdigest()[:12]
    confidence = min(1.0, 0.35 + sum(bool(value) for value in (business_name, address, phone, email)) * 0.12)

    return BusinessLead(
        lead_id=lead_id,
        business_name=business_name[:200],
        address=address[:300],
        phone=phone[:80],
        email=email[:200],
        website=website[:500],
        sector=sector[:150],
        fit_score=score,
        confidence=round(confidence, 2),
        fit_reason=fit_reason[:500],
        approach_comment=approach_comment[:600],
        source_url=str(page.get("url", page.get("final_url", ""))),
        evidence_urls=evidence_urls[:10],
        scraped_at=str(page.get("fetched_at", "")),
    )


def _business_structured_record(records: Any) -> dict[str, Any]:
    if not isinstance(records, list):
        return {}
    fallback: dict[str, Any] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        raw_type = record.get("@type", "")
        types = raw_type if isinstance(raw_type, list) else [raw_type]
        normalized = {str(value).lower().replace(" ", "") for value in types}
        if normalized & _BUSINESS_TYPES:
            return record
        if not fallback and record.get("name"):
            fallback = record
    return fallback


def _address_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    fields = (
        "streetAddress",
        "addressLocality",
        "addressRegion",
        "postalCode",
        "addressCountry",
    )
    return ", ".join(str(value[field]).strip() for field in fields if value.get(field))


def _find_email(text: str) -> str:
    return _EMAIL_RE.search(text).group(0) if _EMAIL_RE.search(text) else ""


def _find_phone(text: str) -> str:
    for match in _PHONE_RE.findall(text):
        digits = re.sub(r"\D", "", match)
        if 7 <= len(digits) <= 15:
            return " ".join(match.split())
    return ""


def _find_address(text: str) -> str:
    match = _ADDRESS_RE.search(text)
    return " ".join(match.group(0).split()) if match else ""


def _contact_link(links: Any, prefix: str) -> str:
    if not isinstance(links, list):
        return ""
    for link in links:
        if isinstance(link, dict):
            value = str(link.get("url", ""))
            if value.lower().startswith(prefix):
                return value.removeprefix(prefix).split("?", 1)[0]
    return ""


def _matched_sector(text: str, profile: LeadProfile) -> str:
    lowered = text.lower()
    for sector in profile.target_sectors:
        if sector.lower() in lowered:
            return sector
    return ""


def _fit_reason(
    profile: LeadProfile,
    matched_terms: list[str],
    address: str,
    phone: str,
    email: str,
    sector: str,
) -> str:
    signals = []
    if matched_terms:
        signals.append(f"page mentions {', '.join(matched_terms[:5])}")
    if sector != "Unknown":
        signals.append(f"sector appears to be {sector}")
    if address or phone or email:
        signals.append("public contact information was found")
    if profile.location and address:
        signals.append(f"location can be checked against {profile.location}")
    return "; ".join(signals) or "Limited fit evidence was found; verify the business manually."


def _default_approach(
    profile: LeadProfile,
    business_name: str,
    matched_terms: list[str],
    sector: str,
) -> str:
    service = profile.services[0] if profile.services else profile.profession
    evidence = ", ".join(matched_terms[:3]) or sector
    return (
        f"Approach {business_name} with a concise {service} proposal. "
        f"Use the page evidence ({evidence}) to explain one specific improvement, "
        "then ask for a short discovery call."
    )


def _first_nonempty(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""
