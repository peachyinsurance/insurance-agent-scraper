import csv
import os
from collections.abc import Iterable

def append_row(
        csv_path: str,
        row: dict[str,str],
        columns: list[str],

) -> None:
    
    """Append one row to a CSV. Writes the header on first call.

    - If the file doesn't exist (or is zero bytes), writes the header line first.
    - Uses csv.DictWriter with extrasaction='ignore', so extra keys in row are
      silently dropped rather than raising. Missing keys become empty strings.
    - Opens, writes, closes per call — every row is on disk before the function
      returns. Caller can Ctrl+C without losing in-flight rows.
    """
    new_file = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
    with open(csv_path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        if new_file:
            writer.writeheader()
        writer.writerow(row)

def load_seen_keys(
        csv_path: str,
        key_column: tuple[str],
) -> set[tuple[str]]:
    
    """Return the set of key tuples already present in csv_path.

    File missing or empty: returns empty set.
    Rows where any key-column value is empty/missing: skipped (treated as
    incomplete; the row will be re-processed on resume, which is idempotent).
    """

    seen: set[tuple[str]] = set()
    if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:  
        return seen
    with open(csv_path, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                values = tuple(row.get(col, '') for col in key_column)
            except KeyError:
                continue
            if all(values):
                seen.add(values)
    return seen