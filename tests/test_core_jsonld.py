"""Tests for scraper.core.jsonld — iter_jsonld_blocks + find_jsonld."""

from bs4 import BeautifulSoup

from scraper.core.jsonld import find_jsonld, iter_jsonld_blocks


def _soup(html):
    return BeautifulSoup(html, "html.parser")


# --- iter_jsonld_blocks ----------------------------------------------------

def test_iter_yields_single_top_level_object():
    html = """
    <script type="application/ld+json">
    {"@type": "Thing", "name": "X"}
    </script>
    """
    blocks = list(iter_jsonld_blocks(_soup(html)))
    assert blocks == [{"@type": "Thing", "name": "X"}]


def test_iter_unwraps_graph_arrays():
    html = """
    <script type="application/ld+json">
    {"@graph": [{"@type": "A", "name": "a"}, {"@type": "B", "name": "b"}]}
    </script>
    """
    blocks = list(iter_jsonld_blocks(_soup(html)))
    assert blocks == [
        {"@type": "A", "name": "a"},
        {"@type": "B", "name": "b"},
    ]


def test_iter_handles_top_level_array():
    html = """
    <script type="application/ld+json">
    [{"@type": "A"}, {"@type": "B"}]
    </script>
    """
    blocks = list(iter_jsonld_blocks(_soup(html)))
    assert blocks == [{"@type": "A"}, {"@type": "B"}]


def test_iter_skips_non_dict_items_in_graph():
    # schema.org sometimes inserts null/string entries between objects
    html = """
    <script type="application/ld+json">
    {"@graph": [{"@type": "A"}, null, "literal", {"@type": "B"}]}
    </script>
    """
    blocks = list(iter_jsonld_blocks(_soup(html)))
    assert blocks == [{"@type": "A"}, {"@type": "B"}]


def test_iter_yields_from_multiple_script_tags():
    html = """
    <script type="application/ld+json">{"@type": "A"}</script>
    <script type="application/ld+json">{"@type": "B"}</script>
    """
    blocks = list(iter_jsonld_blocks(_soup(html)))
    assert blocks == [{"@type": "A"}, {"@type": "B"}]


def test_iter_skips_malformed_json_silently():
    # One block is broken, one is valid — only the valid one yields
    html = """
    <script type="application/ld+json">{not valid json}</script>
    <script type="application/ld+json">{"@type": "Valid"}</script>
    """
    blocks = list(iter_jsonld_blocks(_soup(html)))
    assert blocks == [{"@type": "Valid"}]


def test_iter_returns_empty_when_no_jsonld_present():
    assert list(iter_jsonld_blocks(_soup("<html><body>nothing</body></html>"))) == []


def test_iter_skips_scripts_without_jsonld_type():
    # Plain <script> tags shouldn't be parsed
    html = """
    <script>var x = {"foo": "bar"};</script>
    <script type="application/ld+json">{"@type": "Real"}</script>
    """
    blocks = list(iter_jsonld_blocks(_soup(html)))
    assert blocks == [{"@type": "Real"}]


# --- find_jsonld -----------------------------------------------------------

def test_find_returns_first_matching_block():
    html = """
    <script type="application/ld+json">
    {"@graph": [{"@type": "A", "n": 1}, {"@type": "B", "n": 2}, {"@type": "A", "n": 3}]}
    </script>
    """
    result = find_jsonld(_soup(html), lambda item: item.get("@type") == "A")
    assert result == {"@type": "A", "n": 1}


def test_find_returns_none_when_nothing_matches():
    html = '<script type="application/ld+json">{"@type": "X"}</script>'
    assert find_jsonld(_soup(html), lambda item: item.get("@type") == "Missing") is None


def test_find_returns_none_when_no_jsonld_at_all():
    assert find_jsonld(_soup("<html></html>"), lambda item: True) is None
