from dataclasses import dataclass

@dataclass(frozen=True, kw_only=True)
class AgencyRecord:
    source_url: str #required - the directory page the record was scraped from
    agency_name: str = ""
    address_line: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    phone: str = ""
    website_url: str = ""
    email: str = ""

@dataclass(frozen=True, kw_only=True)
class EnrichedLead:
    """One person at one agency. Output row of Stage 2.

    Field order is intentionally NOT the Instantly CSV column order — the CSV
    writer is the one place that decides column ordering. Fields here are
    grouped logically (identity, contact, address, attribution).
    """

    source_url: str                  # required — Stage 1 row this lead came from

    # Agency-level fields, carried from AgencyRecord
    company_name: str = ""
    phone: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    website: str = ""

    # Person-level fields, the new stuff Stage 2 produces
    email: str = ""
    first_name: str = ""
    last_name: str = ""

    # Provenance / status
    name_source: str = ""           # team_page | contact_page | email_local_part | no_name_found
    enrichment_status: str = ""     # found | no_email_found | no_name_found | fetch_failed
