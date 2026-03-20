"""Tests for the release notes generator."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import generate_release_notes
from scripts.generate_release_notes import Commit, find_previous_tag, group_for_subject, render_release_notes


def test_group_for_subject():
    assert group_for_subject("feat: add release notes") == "Features"
    assert group_for_subject("fix(ci): repair tag publishing") == "Fixes"
    assert group_for_subject("chore(deps): update gh image") == "Dependencies"
    assert group_for_subject("docs: explain release flow") == "Documentation"
    assert group_for_subject("style: reformat script") == "Other Changes"


def test_render_release_notes():
    commits = [
        Commit(sha="a" * 40, short_sha="aaaaaaa", subject="feat: add release notes"),
        Commit(sha="b" * 40, short_sha="bbbbbbb", subject="fix(ci): repair tag publishing"),
    ]
    notes = render_release_notes(
        repo="isityael/apple-mail-mcp",
        current_tag="v2.1.2",
        release_date="2026-03-20",
        commits=commits,
        previous_tag="v2.1.1",
    )

    assert "## v2.1.2 - 2026-03-20" in notes
    assert "### Features" in notes
    assert "### Fixes" in notes
    assert "https://github.com/isityael/apple-mail-mcp/compare/v2.1.1...v2.1.2" in notes


def test_find_previous_tag_skips_current():
    original = generate_release_notes.list_semver_tags
    generate_release_notes.list_semver_tags = lambda: ["v2.1.1", "v2.1.0", "v2.0.0"]
    try:
        previous = find_previous_tag("v2.1.1")
    finally:
        generate_release_notes.list_semver_tags = original

    assert previous == "v2.1.0"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
            print(f"  PASS  {test.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {test.__name__}: {exc}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
