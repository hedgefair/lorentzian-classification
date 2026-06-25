#!/usr/bin/env python3
"""Refresh the TradingView badge deck in the repository README.

Fetches the publication metrics for the Lorentzian Classification indicator
from TradingView's public JSON endpoint (the endpoint its publication popup
API calls; the full script page also embeds the same fields in
``application/prs.init-data+json``), renders a compact, logo-backed shields.io
TradingView block, and rewrites the README section between the
``tradingview-badges`` markers.

This script depends only on the standard library. Exit status is 0 on success
(whether or not the README changed) and 1 on any fetch, parse, or marker error.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_UID = "WhBzgfDu"
METRICS_URL = f"https://www.tradingview.com/chart/-/{SCRIPT_UID}/json/"
SCRIPT_PAGE = (
    f"https://www.tradingview.com/script/{SCRIPT_UID}"
    "-Machine-Learning-Lorentzian-Classification/"
)
AWARDS_PAGE = (
    "https://www.tradingview.com/chart/BTCUSD/"
    "LYCOEW6Z-TradingView-Community-Awards-2023/"
)
TRADINGVIEW_BLUE = "2962FF"
BADGE_STYLE = "flat-square"
HERO_STYLE = "for-the-badge"
BADGE_LABEL_COLOR = "4A4A4A"
TROPHY_LOGO = (
    "data:image/svg+xml;base64,"
    "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIg"
    "ZmlsbD0id2hpdGUiPjxwYXRoIGQ9Ik0xOSA1aC0yVjNIN3YySDVjLTEuMSAwLTIgLjktMiAydjFjMCAy"
    "LjU1IDEuOTIgNC42MyA0LjM5IDQuOTQuNjMgMS41IDEuOTggMi42MyAzLjYxIDIuOTZWMTlIN3YyaDEw"
    "di0yaC00di0zLjFjMS42My0uMzMgMi45OC0xLjQ2IDMuNjEtMi45NkMxOS4wOCAxMi42MyAyMSAxMC41"
    "NSAyMSA4VjdjMC0xLjEtLjktMi0yLTJ6TTUgOFY3aDJ2My44MkM1Ljg0IDEwLjQgNSA5LjMgNSA4em0x"
    "NCAwYzAgMS4zLS44NCAyLjQtMiAyLjgyVjdoMnYxeiIvPjwvc3ZnPg=="
)
EYE_LOGO = (
    "data:image/svg+xml;base64,"
    "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIg"
    "ZmlsbD0id2hpdGUiPjxwYXRoIGQ9Ik0xMiA0LjVDNyA0LjUgMi43MyA3LjYxIDEgMTJjMS43MyA0LjM5"
    "IDYgNy41IDExIDcuNXM5LjI3LTMuMTEgMTEtNy41Yy0xLjczLTQuMzktNi03LjUtMTEtNy41ek0xMiAx"
    "N2MtMi43NiAwLTUtMi4yNC01LTVzMi4yNC01IDUtNSA1IDIuMjQgNSA1LTIuMjQgNS01IDV6bTAtOGMt"
    "MS42NiAwLTMgMS4zNC0zIDNzMS4zNCAzIDMgMyAzLTEuMzQgMy0zLTEuMzQtMy0zLTN6Ii8+PC9zdmc+"
)
SPEECH_BUBBLE_LOGO = (
    "data:image/svg+xml;base64,"
    "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIg"
    "ZmlsbD0id2hpdGUiPjxwYXRoIGQ9Ik0yMCAySDRjLTEuMSAwLTIgLjktMiAydjE4bDQtNGgxNGMxLjEg"
    "MCAyLS45IDItMlY0YzAtMS4xLS45LTItMi0yeiIvPjwvc3ZnPg=="
)
START_MARKER = "<!-- tradingview-badges:start -->"
END_MARKER = "<!-- tradingview-badges:end -->"
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; lorentzian-classification-badges)",
    "Accept": "application/json",
}
ATTEMPTS = 3


def fetch_publication(timeout: float) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(ATTEMPTS):
        if attempt:
            time.sleep(2**attempt)
        try:
            request = urllib.request.Request(METRICS_URL, headers=REQUEST_HEADERS)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            last_error = error
    raise RuntimeError(f"failed to fetch {METRICS_URL}: {last_error}")


def require_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError(f"unexpected value for {field!r}: {value!r}")
    return value


def require_nested_int(payload: dict[str, Any], container: str, field: str) -> int:
    nested = payload.get(container)
    if not isinstance(nested, dict):
        raise RuntimeError(f"unexpected value for {container!r}: {nested!r}")
    return require_int(nested, field)


def require_nested_bool(payload: dict[str, Any], container: str, field: str) -> bool:
    nested = payload.get(container)
    if not isinstance(nested, dict):
        raise RuntimeError(f"unexpected value for {container!r}: {nested!r}")
    value = nested.get(field)
    if not isinstance(value, bool):
        raise RuntimeError(f"unexpected value for {container}.{field}: {value!r}")
    return value


def require_datetime(payload: dict[str, Any], field: str) -> datetime:
    value = payload.get(field)
    if not isinstance(value, str):
        raise RuntimeError(f"unexpected value for {field!r}: {value!r}")
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise RuntimeError(f"unexpected timestamp for {field!r}: {value!r}") from error


def publication_data(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "boosts": require_int(payload, "likes_count"),
        "comments": require_int(payload, "comments_count"),
        "views": require_int(payload, "views"),
        "pine_version": require_nested_int(payload, "script", "version_maj"),
        "is_picked": require_nested_bool(payload, "flags", "is_picked"),
        "updated_at": require_datetime(payload, "updated_at"),
    }


def format_exact(count: int) -> str:
    return f"{count:,}"


def format_compact(count: int) -> str:
    if count >= 1_000_000:
        value = f"{count / 1_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{value}M"
    if count >= 10_000:
        value = f"{count / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{value}K"
    if count >= 1_000:
        value = f"{count / 1_000:.2f}".rstrip("0").rstrip(".")
        return f"{value}K"
    return str(count)


def format_date(value: datetime) -> str:
    return f"{value.strftime('%b')} {value.day}, {value.year}"


def shield_path_part(text: str) -> str:
    # shields.io uses '-' and '_' as path-level separators, so escape those
    # first, then percent-encode punctuation such as commas and apostrophes.
    escaped = text.replace("-", "--").replace("_", "__")
    return urllib.parse.quote(escaped, safe="")


def shield_url(
    label: str, message: str, color: str, logo: str, style: str = BADGE_STYLE
) -> str:
    path = f"{shield_path_part(label)}-{shield_path_part(message)}-{color}"
    query = urllib.parse.urlencode(
        {
            "style": style,
            "logo": logo,
            "logoColor": "white",
            "labelColor": BADGE_LABEL_COLOR,
        }
    )
    return f"https://img.shields.io/badge/{path}?{query}"


def badge(
    label: str,
    message: str,
    color: str,
    logo: str,
    alt: str,
    style: str = BADGE_STYLE,
    href: str = SCRIPT_PAGE,
) -> str:
    image = shield_url(label, message, color, logo, style)
    return (
        f'<a href="{html.escape(href)}">'
        f'<img alt="{html.escape(alt, quote=False)}" '
        f'src="{html.escape(image, quote=False)}"></a>'
    )


def render_block(data: dict[str, Any]) -> str:
    status = "Editors' Picks" if data["is_picked"] else "Community Script"
    hero = badge(
        "TradingView",
        status,
        TRADINGVIEW_BLUE,
        "tradingview",
        f"TradingView {status}",
        style=HERO_STYLE,
    )
    award = badge(
        "Community Awards",
        "Most Valuable PineScript (2023)",
        TRADINGVIEW_BLUE,
        TROPHY_LOGO,
        "Community Awards: Most Valuable PineScript 2023",
        href=AWARDS_PAGE,
    )
    stats = [
        badge(
            "Views",
            format_compact(data["views"]),
            TRADINGVIEW_BLUE,
            EYE_LOGO,
            f"{format_exact(data['views'])} TradingView views",
        ),
        badge(
            "Boosts",
            format_compact(data["boosts"]),
            TRADINGVIEW_BLUE,
            "rocket",
            f"{format_exact(data['boosts'])} TradingView boosts",
        ),
        badge(
            "Comments",
            format_compact(data["comments"]),
            TRADINGVIEW_BLUE,
            SPEECH_BUBBLE_LOGO,
            f"{format_exact(data['comments'])} TradingView comments",
        ),
    ]
    lines = [
        START_MARKER,
        "<p>",
        f"  {hero}",
        "</p>",
        "<p>",
        f"  {award}",
        "</p>",
        "<p>",
        *(f"  {stat}" for stat in stats),
        "</p>",
        END_MARKER,
    ]
    return "\n".join(lines)


def update_readme(readme: Path, data: dict[str, Any]) -> bool:
    text = readme.read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER), flags=re.DOTALL
    )
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one {START_MARKER} ... {END_MARKER} block "
            f"in {readme}, found {len(matches)}"
        )
    updated = pattern.sub(lambda _: render_block(data), text)
    if updated == text:
        return False
    readme.write_text(updated, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--readme",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "README.md",
        help="README to rewrite (default: the repository README)",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    try:
        payload = fetch_publication(args.timeout)
        data = publication_data(payload)
        changed = update_readme(args.readme, data)
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    summary = ", ".join(
        (
            f"boosts={data['boosts']}",
            f"comments={data['comments']}",
            f"views={data['views']}",
            f"pine=v{data['pine_version']}",
            f"picked={data['is_picked']}",
            f"updated={format_date(data['updated_at'])}",
        )
    )
    print(f"fetched {summary}")
    print("README updated" if changed else "README already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
