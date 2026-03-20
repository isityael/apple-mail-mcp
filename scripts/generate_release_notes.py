#!/usr/bin/env python3
"""Generate release notes from git history for tag-triggered releases."""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

SEMVER_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")

GROUP_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Features", re.compile(r"^(feat)(\(.+\))?:\s+", re.IGNORECASE)),
    ("Fixes", re.compile(r"^(fix)(\(.+\))?:\s+", re.IGNORECASE)),
    ("Performance", re.compile(r"^(perf)(\(.+\))?:\s+", re.IGNORECASE)),
    ("Documentation", re.compile(r"^(docs?)(\(.+\))?:\s+", re.IGNORECASE)),
    ("CI/CD", re.compile(r"^(ci|build|chore\(ci\)|feat\(ci\)|fix\(ci\)):\s+", re.IGNORECASE)),
    ("Dependencies", re.compile(r"^chore\(deps\):\s+", re.IGNORECASE)),
    ("Tests", re.compile(r"^test(\(.+\))?:\s+", re.IGNORECASE)),
    ("Refactors", re.compile(r"^refactor(\(.+\))?:\s+", re.IGNORECASE)),
    ("Maintenance", re.compile(r"^chore(\(.+\))?:\s+", re.IGNORECASE)),
)


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    short_sha: str


def run_git(*args: str) -> str:
    """Run a git command and return stripped stdout."""
    result = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def list_semver_tags() -> list[str]:
    """Return semver-like release tags ordered by version descending."""
    tags = run_git("tag", "--sort=-version:refname").splitlines()
    return [tag for tag in tags if SEMVER_TAG_RE.match(tag)]


def find_previous_tag(current_tag: str) -> str | None:
    """Return the previous semver tag relative to the current release tag."""
    for tag in list_semver_tags():
        if tag != current_tag:
            return tag
    return None


def collect_commits(current_tag: str, previous_tag: str | None) -> list[Commit]:
    """Collect non-merge commits for the current release range."""
    revision_range = current_tag if previous_tag is None else f"{previous_tag}..{current_tag}"
    output = run_git("log", "--no-merges", "--format=%H%x09%s", revision_range)
    commits: list[Commit] = []
    for line in output.splitlines():
        sha, subject = line.split("\t", 1)
        commits.append(Commit(sha=sha, subject=subject, short_sha=sha[:7]))
    return commits


def group_for_subject(subject: str) -> str:
    """Map a commit subject to a release-notes group."""
    for group_name, pattern in GROUP_RULES:
        if pattern.match(subject):
            return group_name
    return "Other Changes"


def render_release_notes(
    repo: str,
    current_tag: str,
    release_date: str,
    commits: list[Commit],
    previous_tag: str | None,
) -> str:
    """Render release notes markdown for the current tag."""
    grouped: dict[str, list[Commit]] = {}
    for commit in commits:
        grouped.setdefault(group_for_subject(commit.subject), []).append(commit)

    lines = [f"## {current_tag} - {release_date}", ""]

    for group_name in (
        "Features",
        "Fixes",
        "Performance",
        "Documentation",
        "CI/CD",
        "Dependencies",
        "Tests",
        "Refactors",
        "Maintenance",
        "Other Changes",
    ):
        group_commits = grouped.get(group_name)
        if not group_commits:
            continue
        lines.append(f"### {group_name}")
        for commit in group_commits:
            lines.append(f"- {commit.subject} ([`{commit.short_sha}`](https://github.com/{repo}/commit/{commit.sha}))")
        lines.append("")

    if previous_tag:
        lines.append(f"Compare: https://github.com/{repo}/compare/{previous_tag}...{current_tag}")
    else:
        lines.append(f"Compare: https://github.com/{repo}/commits/{current_tag}")

    return "\n".join(lines).strip() + "\n"


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Generate release notes from git history.")
    parser.add_argument("--repo", required=True, help="GitHub repository in owner/name form.")
    parser.add_argument("--tag", required=True, help="Current release tag, e.g. v2.1.2.")
    parser.add_argument(
        "--release-date",
        default=dt.date.today().isoformat(),
        help="Release date in YYYY-MM-DD format.",
    )
    parser.add_argument("--output", required=True, help="Output path for the release notes markdown.")
    parser.add_argument("--previous-tag", help="Optional explicit previous release tag.")
    return parser.parse_args()


def main() -> None:
    """Generate release notes and write them to disk."""
    args = parse_args()
    previous_tag = args.previous_tag or find_previous_tag(args.tag)
    commits = collect_commits(args.tag, previous_tag)
    notes = render_release_notes(args.repo, args.tag, args.release_date, commits, previous_tag)
    Path(args.output).write_text(notes, encoding="utf-8")


if __name__ == "__main__":
    main()
