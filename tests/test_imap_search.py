"""Tests for IMAP search helpers (criteria building, date conversion)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from apple_mail_mcp.imap import _iso_to_imap_date, build_imap_search_criteria


def test_iso_to_imap_date_basic():
    assert _iso_to_imap_date("2026-04-07") == "07-Apr-2026"


def test_iso_to_imap_date_january():
    assert _iso_to_imap_date("2026-01-15") == "15-Jan-2026"


def test_iso_to_imap_date_december():
    assert _iso_to_imap_date("2025-12-01") == "01-Dec-2025"


def test_build_criteria_empty():
    assert build_imap_search_criteria() == "ALL"


def test_build_criteria_to():
    result = build_imap_search_criteria(to="sm@m0sh1.cc")
    assert result == 'TO "sm@m0sh1.cc"'


def test_build_criteria_from():
    result = build_imap_search_criteria(from_addr="github.com")
    assert result == 'FROM "github.com"'


def test_build_criteria_subject():
    result = build_imap_search_criteria(subject="invoice")
    assert result == 'SUBJECT "invoice"'


def test_build_criteria_body():
    result = build_imap_search_criteria(body="payment")
    assert result == 'BODY "payment"'


def test_build_criteria_since():
    result = build_imap_search_criteria(since="2026-03-01")
    assert result == "SINCE 01-Mar-2026"


def test_build_criteria_before():
    result = build_imap_search_criteria(before="2026-04-01")
    assert result == "BEFORE 01-Apr-2026"


def test_build_criteria_unseen():
    assert build_imap_search_criteria(unseen=True) == "UNSEEN"
    assert build_imap_search_criteria(unseen=False) == "SEEN"


def test_build_criteria_flagged():
    assert build_imap_search_criteria(flagged=True) == "FLAGGED"
    assert build_imap_search_criteria(flagged=False) == "UNFLAGGED"


def test_build_criteria_combined():
    result = build_imap_search_criteria(
        to="sm@m0sh1.cc",
        from_addr="github.com",
        since="2026-03-01",
        unseen=True,
    )
    assert 'TO "sm@m0sh1.cc"' in result
    assert 'FROM "github.com"' in result
    assert "SINCE 01-Mar-2026" in result
    assert "UNSEEN" in result


def test_build_criteria_cc():
    result = build_imap_search_criteria(cc="team@example.com")
    assert result == 'CC "team@example.com"'


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
