"""Tests for scraper.core.csv_io — append_row and load_seen_keys."""

import csv

from scraper.core.csv_io import append_row, load_seen_keys


# --- append_row -------------------------------------------------------------

def test_append_row_writes_header_on_new_file(tmp_path):
    path = tmp_path / "out.csv"
    append_row(str(path), {"a": "1", "b": "2"}, ["a", "b"])
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "a,b"
    assert lines[1] == "1,2"


def test_append_row_appends_to_existing_file(tmp_path):
    path = tmp_path / "out.csv"
    append_row(str(path), {"a": "1", "b": "2"}, ["a", "b"])
    append_row(str(path), {"a": "3", "b": "4"}, ["a", "b"])
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == ["a,b", "1,2", "3,4"]


def test_append_row_with_extra_keys_silently_drops_them(tmp_path):
    path = tmp_path / "out.csv"
    append_row(str(path), {"a": "1", "b": "2", "extra": "junk"}, ["a", "b"])
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == ["a,b", "1,2"]


def test_append_row_with_missing_keys_writes_empty_strings(tmp_path):
    path = tmp_path / "out.csv"
    append_row(str(path), {"a": "1"}, ["a", "b"])  # 'b' missing
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == ["a,b", "1,"]


def test_append_row_quotes_values_with_commas(tmp_path):
    path = tmp_path / "out.csv"
    append_row(str(path), {"name": "Smith, John, Inc."}, ["name"])
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == ["name", '"Smith, John, Inc."']


def test_append_row_quotes_values_with_newlines(tmp_path):
    path = tmp_path / "out.csv"
    append_row(str(path), {"addr": "Line 1\nLine 2"}, ["addr"])
    # CSV reader should round-trip this back to the original value
    with open(path, "r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows == [{"addr": "Line 1\nLine 2"}]


def test_append_row_treats_zero_byte_file_as_new(tmp_path):
    path = tmp_path / "out.csv"
    path.touch()  # exists but empty
    append_row(str(path), {"a": "1"}, ["a"])
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == ["a", "1"]


# --- load_seen_keys ---------------------------------------------------------

def test_load_seen_keys_empty_when_no_file(tmp_path):
    path = tmp_path / "missing.csv"
    assert load_seen_keys(str(path), ("a",)) == set()


def test_load_seen_keys_empty_when_only_header(tmp_path):
    path = tmp_path / "header_only.csv"
    path.write_text("a,b\n", encoding="utf-8")
    assert load_seen_keys(str(path), ("a",)) == set()


def test_load_seen_keys_returns_single_column_keys(tmp_path):
    path = tmp_path / "single.csv"
    path.write_text(
        "source_url\nhttps://a.com/\nhttps://b.com/\n",
        encoding="utf-8",
    )
    assert load_seen_keys(str(path), ("source_url",)) == {
        ("https://a.com/",),
        ("https://b.com/",),
    }


def test_load_seen_keys_returns_multi_column_keys(tmp_path):
    path = tmp_path / "multi.csv"
    path.write_text(
        "source_url,email\n"
        "https://a.com/,jane@a.com\n"
        "https://a.com/,john@a.com\n",
        encoding="utf-8",
    )
    assert load_seen_keys(str(path), ("source_url", "email")) == {
        ("https://a.com/", "jane@a.com"),
        ("https://a.com/", "john@a.com"),
    }


def test_load_seen_keys_skips_rows_with_empty_key_value(tmp_path):
    path = tmp_path / "partial.csv"
    path.write_text(
        "source_url,email\n"
        "https://a.com/,jane@a.com\n"
        "https://b.com/,\n",  # empty email
        encoding="utf-8",
    )
    assert load_seen_keys(str(path), ("source_url", "email")) == {
        ("https://a.com/", "jane@a.com"),
    }


def test_load_seen_keys_handles_quoted_values_correctly(tmp_path):
    path = tmp_path / "quoted.csv"
    path.write_text('name\n"Smith, John, Inc."\n', encoding="utf-8")
    assert load_seen_keys(str(path), ("name",)) == {("Smith, John, Inc.",)}