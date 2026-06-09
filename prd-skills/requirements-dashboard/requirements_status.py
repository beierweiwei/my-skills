#!/usr/bin/env python3
"""Generate and validate the local .scratch requirements dashboard."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".scratch"
ALLOWED_STATUSES = {
    "needs-triage",
    "needs-info",
    "ready-for-agent",
    "ready-for-human",
    "wontfix",
    "verified",
}
REQUIRED_ISSUE_SECTIONS = {
    "## What to build",
    "## Acceptance criteria",
    "## Blocked by",
}

_DATE_IN_COMMENT = re.compile(r"###\s*(?:Verification|Session delta)?\s*(?:—+)?\s*(\d{4}-\d{2}-\d{2})")


@dataclass
class TrackerFile:
    path: str
    kind: str
    status: str | None
    verification_date: str | None = None


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def read_status(text: str) -> str | None:
    match = re.search(r"^Status:\s*(\S+)\s*$", text, re.MULTILINE)
    return match.group(1) if match else None


def parse_verification_date(text: str) -> str | None:
    """Extract the most recent verification date from the Comments section."""
    comments_start = text.find("## Comments")
    if comments_start == -1:
        return None
    comments = text[comments_start:]
    dates: list[str] = []
    for m in _DATE_IN_COMMENT.finditer(comments):
        dates.append(m.group(1))
    return sorted(dates)[-1] if dates else None


def find_tracker_files() -> list[Path]:
    if not SCRATCH.exists():
        return []
    prds = sorted(SCRATCH.glob("*/PRD.md"))
    issues = sorted(SCRATCH.glob("*/issues/*.md"))
    return prds + issues


def classify(path: Path) -> str:
    return "prd" if path.name == "PRD.md" else "issue"


def inspect_file(path: Path) -> tuple[TrackerFile, list[str]]:
    text = path.read_text(encoding="utf-8")
    kind = classify(path)
    status = read_status(text)
    verification_date = parse_verification_date(text)
    item = TrackerFile(path=rel(path), kind=kind, status=status, verification_date=verification_date)
    problems: list[str] = []

    if status is None:
        problems.append(f"{item.path}: missing top-level Status")
    elif status not in ALLOWED_STATUSES:
        problems.append(f"{item.path}: invalid Status '{status}'")

    if kind == "issue":
        for section in sorted(REQUIRED_ISSUE_SECTIONS):
            if section not in text:
                problems.append(f"{item.path}: missing required section {section}")

    if status == "verified":
        if "## Comments" not in text:
            problems.append(f"{item.path}: verified item missing ## Comments")
        evidence_like = any(
            marker in text
            for marker in (
                "Evidence:",
                "验证：",
                "验证运行：",
                "验证后已验收：",
                "验证运行",
            )
        )
        if kind == "issue" and not evidence_like:
            problems.append(f"{item.path}: verified issue missing verification evidence")

    return item, problems


def _build_daily_summary(
    items: list[TrackerFile], today_str: str
) -> dict[str, object]:
    """Group verified items by verification_date, keyed by day label."""
    by_date: dict[str, list[str]] = defaultdict(list)
    for item in items:
        vd = item.verification_date
        if vd and item.status == "verified":
            by_date[vd].append(item.path)

    today_items = by_date.get(today_str, [])
    yesterday_str = (date.fromisoformat(today_str) - timedelta(days=1)).isoformat()
    yesterday_items = by_date.get(yesterday_str, [])

    recent: dict[str, object] = {}
    for d in sorted(by_date, reverse=True):
        recent[d] = {"count": len(by_date[d]), "items": by_date[d]}

    verified_with_date = sum(1 for item in items if item.verification_date and item.status == "verified")
    verified_no_date = sum(
        1 for item in items if item.status == "verified" and not item.verification_date
    )

    return {
        "today": {"date": today_str, "count": len(today_items), "items": today_items},
        "yesterday": {"date": yesterday_str, "count": len(yesterday_items), "items": yesterday_items},
        "recent": recent,
        "verified_with_date": verified_with_date,
        "verified_no_date": verified_no_date,
    }


def build_dashboard(
    today_filter: str | None = None, days: int | None = None
) -> dict[str, object]:
    files = find_tracker_files()
    items: list[TrackerFile] = []
    problems: list[str] = []

    for path in files:
        item, item_problems = inspect_file(path)
        items.append(item)
        problems.extend(item_problems)

    today_str = date.today().isoformat()

    issue_statuses = Counter(
        item.status or "missing" for item in items if item.kind == "issue"
    )
    prd_statuses = Counter(
        item.status or "missing" for item in items if item.kind == "prd"
    )

    human_items = [
        item.path
        for item in items
        if item.kind == "issue" and item.status == "ready-for-human"
    ]
    completed_issues = issue_statuses.get("verified", 0) + issue_statuses.get("wontfix", 0)

    daily = _build_daily_summary(items, today_str)

    result: dict[str, object] = {
        "prd_count": sum(1 for item in items if item.kind == "prd"),
        "issue_count": sum(1 for item in items if item.kind == "issue"),
        "prd_statuses": dict(sorted(prd_statuses.items())),
        "issue_statuses": dict(sorted(issue_statuses.items())),
        "completed_issues": completed_issues,
        "human_or_blocked_items": human_items,
        "format_issue_count": len(problems),
        "format_issues": problems,
        "daily_summary": daily,
        "items": [asdict(item) for item in items],
    }

    if today_filter:
        result["items"] = [
            asdict(item)
            for item in items
            if item.verification_date == today_filter
        ]
        result["filter"] = f"today={today_filter}"

    if days is not None:
        cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
        result["items"] = [
            asdict(item)
            for item in items
            if item.verification_date and item.verification_date >= cutoff
        ]
        result["filter"] = f"days={days}"

    return result


def print_text(dashboard: dict[str, object]) -> None:
    daily = dashboard["daily_summary"]
    today_info = dict(daily["today"])
    yesterday_info = dict(daily["yesterday"])
    filter_label = dashboard.get("filter", "")

    header = "Requirements dashboard"
    if filter_label:
        header += f" ({filter_label})"
    print(header)
    print("=" * len(header))
    print(f"PRDs: {dashboard['prd_count']}  |  Issues: {dashboard['issue_count']}  |  Completed: {dashboard['completed_issues']}")
    print()

    # Today section
    print("─ Today ─────────────────────────────────────")
    today_count = today_info["count"]
    print(f"今日验证: {today_count} 项")
    if today_count:
        for p in today_info["items"]:
            print(f"  ✓ {p}")

    yesterday_count = yesterday_info["count"]
    if yesterday_count:
        print(f"昨日验证: {yesterday_count} 项")
        for p in yesterday_info["items"]:
            print(f"  ✓ {p}")
    print()

    # Status breakdown
    print("─ Status ────────────────────────────────────")
    print("PRD:")
    for status, count in dict(dashboard["prd_statuses"]).items():
        print(f"  {status}: {count}")
    print("Issue:")
    for status, count in dict(dashboard["issue_statuses"]).items():
        print(f"  {status}: {count}")
    print()

    # Blocking items
    human_items = list(dashboard["human_or_blocked_items"])
    if human_items:
        print("─ Needs Human ───────────────────────────────")
        for item in human_items:
            print(f"  ! {item}")
        print()

    # Format issues
    problems = list(dashboard["format_issues"])
    if problems:
        print("─ Format Issues ─────────────────────────────")
        for problem in problems:
            print(f"  ✗ {problem}")
        print()

    # Activity heatmap (last 14 days)
    recent_raw = daily["recent"]
    if recent_raw:
        recent = sorted(recent_raw.items(), reverse=True)[:14]
        print("─ Recent Activity ───────────────────────────")
        max_count = max(info["count"] for _, info in recent) if recent else 1
        for d, info in recent:
            bar = "█" * info["count"] if max_count <= 20 else "█" * min(info["count"], 20)
            print(f"  {d}  {bar} {info['count']}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--today", action="store_true", help="只展示今日验证的条目")
    parser.add_argument("--days", type=int, metavar="N", help="只展示最近 N 天验证的条目")
    args = parser.parse_args()

    today_filter = date.today().isoformat() if args.today else None
    days = args.days if args.days else None

    dashboard = build_dashboard(today_filter=today_filter, days=days)

    if args.format == "json":
        print(json.dumps(dashboard, indent=2, ensure_ascii=False))
    else:
        print_text(dashboard)

    if args.strict and dashboard["format_issue_count"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
