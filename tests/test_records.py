from scraper.records import AgencyRecord

def test_agency_record():
    #1. source_url is required
    try:
        AgencyRecord()
    except TypeError:
        pass
    else:
        assert False, "source_url is required"

    #2. all other fields default to ""
    r = AgencyRecord(source_url="https://example.com/x")
    assert r.agency_name == "" and r.email == "" and r.zip == ""

    #3. asdict produces the CSV row shap we expect
    from dataclasses import asdict
    row = asdict(AgencyRecord(source_url="x", agency_name="ACME", email = "a@b.com"))
    assert set(row.keys()) == {
        "source_url",
        "agency_name",
        "address_line",
        "city",
        "state",
        "zip",
        "phone",
        "website_url",
        "email"
    }
    assert row["agency_name"] == "ACME"

from scraper.records import EnrichedLead


def test_enriched_lead_requires_source_url():
    try:
        EnrichedLead()
    except TypeError:
        pass
    else:
        assert False, "source_url should be required"


def test_enriched_lead_defaults_everything_else_to_empty_string():
    lead = EnrichedLead(source_url="https://example.com/x")
    assert lead.company_name == ""
    assert lead.email == ""
    assert lead.first_name == ""
    assert lead.last_name == ""
    assert lead.name_source == ""
    assert lead.enrichment_status == ""


def test_enriched_lead_asdict_has_all_thirteen_fields():
    from dataclasses import asdict
    row = asdict(EnrichedLead(
        source_url="x",
        company_name="ACME",
        email="a@b.com",
        first_name="Alice",
    ))
    assert set(row.keys()) == {
        "source_url", "company_name", "phone", "address",
        "city", "state", "zip", "website",
        "email", "first_name", "last_name",
        "name_source", "enrichment_status",
    }
    assert row["company_name"] == "ACME"
    assert row["first_name"] == "Alice"

