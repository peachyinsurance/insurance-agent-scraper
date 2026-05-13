"""Generic JSON-LD reader: iterate every schema.org block on a page.

Site adapters provide their own *predicate* to pick the block they want
(e.g. Travelers prefers a Yext verifiable credential over a plain
InsuranceAgency block). This module just handles the boring parts:

- finding every <script type="application/ld+json">
- skipping blocks that don't parse as JSON
- unwrapping @graph wrappers and bare arrays
- yielding only dict items (skipping null/string entries in @graph)
"""

import json
from collections.abc import Callable, Iterator


def iter_jsonld_blocks(soup) -> Iterator[dict]:
    """Yield each parsed JSON-LD dict from the page.

    Handles three shapes seen in the wild:
      1. A single top-level object              -> yield as-is
      2. A list of objects (rare)               -> yield each dict
      3. {"@graph": [obj, obj, ...]}            -> yield each dict from the list

    Non-dict items inside @graph or top-level arrays (e.g. nulls, strings,
    schema.org sometimes inserts `null` between items) are silently skipped.
    Malformed JSON in a script tag is silently skipped — bad markup on one
    block shouldn't kill the rest of the page.
    """
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        if isinstance(data, dict):
            graph = data.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    if isinstance(item, dict):
                        yield item
            else:
                yield data
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    yield item


def find_jsonld(soup, predicate: Callable[[dict], bool]) -> dict | None:
    """Return the first JSON-LD dict where predicate(item) is True, else None.

    Convenience over iter_jsonld_blocks for the common 'pick the first match'
    case. Callers that need multi-pass preference logic (e.g. 'prefer A but
    fall back to B') should iterate themselves.
    """
    for item in iter_jsonld_blocks(soup):
        if predicate(item):
            return item
    return None
