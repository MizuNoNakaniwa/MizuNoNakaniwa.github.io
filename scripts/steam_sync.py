#!/usr/bin/env python3
"""Safely mirror three public Steam review sources into Hugo Markdown."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from markdownify import markdownify

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "content" / "posts" / "game-reviews-and-research"
STATE_PATH = ROOT / "data" / "steam-sync.json"

PROFILE_URL = "https://steamcommunity.com/id/HerbertLawrence/recommended/"
CURATOR_ID = "46240801"
CURATOR_URL = "https://store.steampowered.com/curator/46240801/"
GROUP_LIST_URL = "https://steamcommunity.com/groups/FAPGR/announcements/listing"
GROUP_RSS_URL = "https://steamcommunity.com/groups/FAPGR/rss/"

START_MARKER = "<!-- STEAM-SYNC-START -->"
END_MARKER = "<!-- STEAM-SYNC-END -->"
MAX_DIRECT_PAGES = 100
MAX_GROUP_PAGES = 100

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; MizuNoNakaniwa-SteamSync/1.0; +https://github.com/MizuNoNakaniwa/MizuNoNakaniwa.github.io)",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
})
SESSION.cookies.update({
    "Steam_Language": "schinese",
    "birthtime": "0",
    "lastagecheckage": "1-January-1970",
    "wants_mature_content": "1",
})
APP_NAME_CACHE: dict[str, str] = {}
APP_LIST_CACHE: dict[str, str] | None = None


def fail(message: str) -> None:
    raise RuntimeError(message)


def get(url: str, **kwargs: Any) -> requests.Response:
    response = SESSION.get(url, timeout=35, **kwargs)
    response.raise_for_status()
    return response


def normalize_space(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def bbcode_to_markdown(text: str) -> str:
    if not text:
        return ""
    replacements = [
        (r"\[b\](.*?)\[/b\]", r"**\1**"),
        (r"\[i\](.*?)\[/i\]", r"*\1*"),
        (r"\[u\](.*?)\[/u\]", r"\1"),
        (r"\[strike\](.*?)\[/strike\]", r"~~\1~~"),
        (r"\[h1\](.*?)\[/h1\]", r"# \1"),
        (r"\[h2\](.*?)\[/h2\]", r"## \1"),
        (r"\[h3\](.*?)\[/h3\]", r"### \1"),
        (r"\[spoiler\](.*?)\[/spoiler\]", r"> **Spoiler:** \1"),
        (r"\[quote(?:=[^\]]+)?\](.*?)\[/quote\]", r"> \1"),
        (r"\[code\](.*?)\[/code\]", r"\n    \1\n"),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text, flags=re.I | re.S)
    text = re.sub(r"\[url=([^\]]+)\](.*?)\[/url\]", r"[\2](\1)", text, flags=re.I | re.S)
    text = re.sub(r"\[url\](.*?)\[/url\]", r"<\1>", text, flags=re.I | re.S)
    text = re.sub(r"\[img\](.*?)\[/img\]", r"![](\1)", text, flags=re.I | re.S)
    text = re.sub(r"\[\*\]\s*", "- ", text, flags=re.I)
    text = re.sub(r"\[/?list(?:=[^\]]+)?\]", "", text, flags=re.I)
    return normalize_space(text)


def html_to_markdown(fragment: str) -> str:
    if not fragment:
        return ""
    soup = BeautifulSoup(fragment, "html.parser")
    for selector in (".gradient", ".view_more", "script", "style"):
        for node in soup.select(selector):
            node.decompose()
    text = markdownify(str(soup), heading_style="ATX", bullets="-")
    return bbcode_to_markdown(text)


def parse_date(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(
        r"^\s*(Posted|Recommended|Not Recommended|Informational)\s*:?\s*",
        "",
        value,
        flags=re.I,
    ).strip()
    try:
        now = datetime.now(timezone.utc)
        default = now.replace(month=1, day=1, hour=12, minute=0, second=0, microsecond=0)
        dt = date_parser.parse(value, fuzzy=True, default=default)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt > now and (dt - now).days > 7:
            dt = dt.replace(year=dt.year - 1)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def safe_appid(value: Any) -> str | None:
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    return match.group(0) if match else None


def app_name(appid: str) -> str:
    global APP_LIST_CACHE
    if appid in APP_NAME_CACHE:
        return APP_NAME_CACHE[appid]

    if APP_LIST_CACHE is None:
        try:
            response = get("https://api.steampowered.com/ISteamApps/GetAppList/v2/")
            apps = response.json().get("applist", {}).get("apps", [])
            APP_LIST_CACHE = {str(item.get("appid")): item.get("name", "") for item in apps if item.get("appid")}
        except Exception:
            APP_LIST_CACHE = {}

    name = APP_LIST_CACHE.get(appid) if APP_LIST_CACHE else None
    if not name:
        try:
            response = get(
                "https://store.steampowered.com/api/appdetails",
                params={"appids": appid, "filters": "basic", "l": "english", "cc": "US"},
            )
            payload = response.json().get(str(appid), {})
            if payload.get("success"):
                name = payload.get("data", {}).get("name")
        except Exception:
            pass

    if not name:
        try:
            page = get(f"https://store.steampowered.com/app/{appid}/", params={"l": "english", "cc": "US"})
            soup = BeautifulSoup(page.text, "html.parser")
            node = soup.select_one(".apphub_AppName")
            if node:
                name = node.get_text(" ", strip=True)
            if not name:
                meta = soup.select_one("meta[property='og:title']")
                if meta:
                    name = meta.get("content", "").strip()
            if not name and soup.title:
                name = re.sub(r"\s+on Steam\s*$", "", soup.title.get_text(" ", strip=True), flags=re.I)
        except Exception:
            pass

    APP_NAME_CACHE[appid] = name or f"Steam App {appid}"
    return APP_NAME_CACHE[appid]


def record_hash(record: dict[str, Any]) -> str:
    material = json.dumps(
        {k: record.get(k) for k in ("source", "source_id", "appid", "title", "body", "published", "url", "status")},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def fetch_direct_reviews() -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for page in range(1, MAX_DIRECT_PAGES + 1):
        response = get(PROFILE_URL, params={"p": page, "l": "english"})
        soup = BeautifulSoup(response.text, "html.parser")
        boxes = soup.select("div.review_box")
        if not boxes:
            if page == 1:
                fail("Steam direct-review page returned no review boxes.")
            break

        new_on_page = 0
        for box in boxes:
            rec_link = box.find("a", href=re.compile(r"/recommended/\d+/?"))
            if not rec_link:
                continue
            href = urljoin("https://steamcommunity.com", rec_link.get("href", ""))
            app_match = re.search(r"/recommended/(\d+)/?", href)
            if not app_match:
                continue
            appid = app_match.group(1)
            content = box.select_one(".rightcol .content") or box.select_one(".content")
            if not content:
                continue

            review_container = box.find(id=re.compile(r"^ReviewContent"))
            remote_id = None
            if review_container and review_container.get("id"):
                match = re.search(r"(\d+)$", review_container["id"])
                remote_id = match.group(1) if match else None

            posted_node = box.select_one(".postedDate") or box.select_one(".date_posted")
            status_node = box.select_one(".vote_header .title") or box.select_one(".title")
            body = html_to_markdown(str(content))
            if not body:
                continue

            source_id = f"direct:{appid}"
            if source_id not in found:
                new_on_page += 1
            found[source_id] = {
                "source": "direct",
                "source_id": source_id,
                "remote_id": remote_id,
                "appid": appid,
                "title": app_name(appid),
                "body": body,
                "published": parse_date(posted_node.get_text(" ", strip=True) if posted_node else None),
                "url": href,
                "status": status_node.get_text(" ", strip=True) if status_node else None,
            }
        if new_on_page == 0:
            break

    if not found:
        fail("No Steam direct reviews could be parsed.")
    return list(found.values())


def parse_curator_cards(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    records: list[dict[str, Any]] = []
    for card in soup.select("div.recommendation"):
        appid = safe_appid(card.get("data-ds-appid"))
        if not appid:
            node = card.select_one("[data-ds-appid]")
            appid = safe_appid(node.get("data-ds-appid")) if node else None
        if not appid:
            app_link = card.find("a", href=re.compile(r"store\.steampowered\.com/app/\d+"))
            if app_link:
                match = re.search(r"/app/(\d+)", app_link.get("href", ""))
                appid = match.group(1) if match else None
        if not appid:
            continue

        desc = card.select_one(".recommendation_desc")
        if not desc:
            continue
        date_node = card.select_one(".curator_review_date")
        readmore = card.select_one(".recommendation_readmore a")
        source_url = (
            urljoin("https://store.steampowered.com", readmore.get("href"))
            if readmore and readmore.get("href")
            else f"https://store.steampowered.com/app/{appid}/?curator_clanid={CURATOR_ID}"
        )
        status_node = card.select_one(".recommendation_type")
        body = html_to_markdown(str(desc))
        if not body:
            continue
        records.append({
            "source": "curator",
            "source_id": f"curator:{appid}",
            "remote_id": appid,
            "appid": appid,
            "title": app_name(appid),
            "body": body,
            "published": parse_date(date_node.get_text(" ", strip=True) if date_node else None),
            "url": source_url,
            "status": status_node.get_text(" ", strip=True) if status_node else None,
        })
    return records


def fetch_curator_reviews() -> list[dict[str, Any]]:
    endpoint = f"https://store.steampowered.com/curator/{CURATOR_ID}/ajaxgetfilteredrecommendations/"
    found: dict[str, dict[str, Any]] = {}
    start = 0
    total: int | None = None

    for _ in range(20):
        response = get(
            endpoint,
            params={
                "query": "",
                "start": start,
                "count": 100,
                "filter": "recent",
                "tagids": "",
                "sort": "recent",
                "app_types": "",
                "curations": "",
                "reset": "false",
                "excluded_tagids": "",
                "required_tagids": "",
                "l": "schinese",
                "cc": "US",
            },
        )
        data = response.json()
        if not data.get("success", True):
            fail("Steam Curator endpoint reported failure.")
        html = data.get("results_html", "")
        cards = parse_curator_cards(html)
        for record in cards:
            found[record["source_id"]] = record

        if total is None:
            try:
                total = int(data.get("total_count", 0))
            except Exception:
                total = 0
        start += 100
        if not html or not cards or (total and start >= total):
            break

    if not found:
        page = get(CURATOR_URL, params={"l": "schinese", "cc": "US"})
        for record in parse_curator_cards(page.text):
            found[record["source_id"]] = record

    if not found:
        fail("No Steam Curator reviews could be parsed.")
    if total and len(found) < max(1, int(total * 0.75)):
        fail(f"Curator parser only found {len(found)} of {total} reported reviews; refusing a partial sync.")
    return list(found.values())


def rss_entries_by_id() -> dict[str, dict[str, Any]]:
    response = get(GROUP_RSS_URL, params={"l": "schinese"})
    feed = feedparser.parse(response.content)
    entries: dict[str, dict[str, Any]] = {}
    for item in feed.entries:
        link = item.get("link", "")
        match = re.search(r"/announcements/detail/(\d+)", link)
        if not match:
            continue
        announcement_id = match.group(1)
        if item.get("content"):
            body_html = item.get("content", [{}])[0].get("value", "")
        else:
            body_html = item.get("description", "")
        entries[announcement_id] = {
            "title": item.get("title", "").strip(),
            "body": html_to_markdown(body_html or ""),
            "published": parse_date(item.get("published") or item.get("updated")),
            "url": link,
        }
    return entries


def announcement_from_detail(
    url: str,
    rss_item: dict[str, Any] | None,
    listing_published: str | None = None,
) -> dict[str, Any] | None:
    match = re.search(r"/announcements/detail/(\d+)", url)
    if not match:
        return None
    announcement_id = match.group(1)

    # Prefer Steam's public partner-event JSON: it contains exact headline,
    # BBCode body and Unix post time, so dates do not depend on localized HTML.
    try:
        response = get(
            "https://store.steampowered.com/events/ajaxgetpartnerevent",
            params={
                "clan_accountid": CURATOR_ID,
                "announcement_gid": announcement_id,
                "l": "english",
                "cc": "US",
            },
        )
        event = response.json().get("event", {})
        announcement = event.get("announcement_body") or {}
        body = bbcode_to_markdown(announcement.get("body", ""))
        if body:
            posttime = announcement.get("posttime")
            published = None
            if posttime:
                published = datetime.fromtimestamp(int(posttime), tz=timezone.utc).isoformat()
            return {
                "source": "announcement",
                "source_id": f"announcement:{announcement_id}",
                "remote_id": announcement_id,
                "appid": None,
                "title": announcement.get("headline") or (rss_item or {}).get("title") or f"Steam 群组公告 {announcement_id}",
                "body": body,
                "published": published or (rss_item or {}).get("published") or listing_published,
                "url": url,
                "status": None,
            }
    except Exception as exc:
        print(f"warning: partner-event JSON failed for {announcement_id}: {exc}", file=sys.stderr)

    response = get(url, params={"l": "english"})
    soup = BeautifulSoup(response.text, "html.parser")

    title = ""
    for selector in (".headline", ".announcement_headline", "h1", "meta[property='og:title']"):
        node = soup.select_one(selector)
        if not node:
            continue
        title = node.get("content", "").strip() if node.name == "meta" else node.get_text(" ", strip=True)
        if title:
            break
    if (not title or title.lower().startswith("steam community")) and rss_item:
        title = rss_item.get("title", "")

    body_node = None
    for selector in (".announcement_body", ".body", ".eventDescription", "[class*='announcement_body']", "[class*='announcementBody']"):
        body_node = soup.select_one(selector)
        if body_node:
            break
    body = html_to_markdown(str(body_node)) if body_node else ""
    if not body and rss_item:
        body = rss_item.get("body", "")
    if not body:
        return None

    published = None
    time_node = soup.select_one("time[datetime]")
    if time_node:
        published = parse_date(time_node.get("datetime"))
    if not published:
        for selector in (".date", ".eventDate", ".announcement_date", "[class*='date']"):
            node = soup.select_one(selector)
            if node:
                published = parse_date(node.get_text(" ", strip=True))
                if published:
                    break
    if not published and rss_item:
        published = rss_item.get("published")
    if not published:
        published = listing_published

    return {
        "source": "announcement",
        "source_id": f"announcement:{announcement_id}",
        "remote_id": announcement_id,
        "appid": None,
        "title": title or f"Steam 群组公告 {announcement_id}",
        "body": body,
        "published": published,
        "url": url,
        "status": None,
    }


def fetch_group_announcements() -> list[dict[str, Any]]:
    rss = rss_entries_by_id()
    links: list[str] = []
    seen: set[str] = set()
    listing_dates: dict[str, str] = {}

    for page in range(1, MAX_GROUP_PAGES + 1):
        response = get(GROUP_LIST_URL, params={"p": page, "l": "english"})
        soup = BeautifulSoup(response.text, "html.parser")
        page_links: list[str] = []
        for a in soup.find_all("a", href=re.compile(r"/announcements/detail/\d+")):
            href = urljoin("https://steamcommunity.com", a.get("href", ""))
            if not href:
                continue

            match = re.search(r"/announcements/detail/(\d+)", href)
            announcement_id = match.group(1) if match else ""
            if announcement_id and announcement_id not in listing_dates:
                container = a.find_parent(["div", "article", "li"])
                candidates: list[str] = []
                if container:
                    for node in container.find_all(attrs={"data-timestamp": True}):
                        raw = node.get("data-timestamp")
                        if raw and str(raw).isdigit():
                            try:
                                listing_dates[announcement_id] = datetime.fromtimestamp(
                                    int(raw), tz=timezone.utc
                                ).isoformat()
                                break
                            except Exception:
                                pass
                    if announcement_id not in listing_dates:
                        for node in container.find_all(["time", "span", "div"]):
                            classes = " ".join(node.get("class", []))
                            if node.name == "time" or re.search(r"date|time", classes, flags=re.I):
                                if node.get("datetime"):
                                    candidates.append(node.get("datetime"))
                                candidates.append(node.get_text(" ", strip=True))
                        for raw in candidates:
                            parsed = parse_date(raw)
                            if parsed:
                                listing_dates[announcement_id] = parsed
                                break

            if href not in seen:
                seen.add(href)
                page_links.append(href)
                links.append(href)
        if not page_links:
            break

    for item in rss.values():
        href = item.get("url", "")
        if href and href not in seen:
            seen.add(href)
            links.insert(0, href)

    if not links:
        fail("No FAPGR announcement links could be parsed.")

    records: list[dict[str, Any]] = []
    for url in links:
        match = re.search(r"/announcements/detail/(\d+)", url)
        announcement_id = match.group(1) if match else ""
        try:
            record = announcement_from_detail(
                url,
                rss.get(announcement_id),
                listing_dates.get(announcement_id),
            )
        except Exception as exc:
            print(f"warning: announcement {announcement_id} detail fetch failed: {exc}", file=sys.stderr)
            record = None
        if record:
            records.append(record)

    if not records:
        fail("No FAPGR announcements could be parsed.")
    if len(records) < max(1, int(len(links) * 0.75)):
        fail(f"Only parsed {len(records)} of {len(links)} FAPGR announcements; refusing a partial sync.")
    return records


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"version": 1, "records": {}, "source_counts": {}}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        fail("data/steam-sync.json is invalid JSON.")
    data.setdefault("version", 1)
    data.setdefault("records", {})
    data.setdefault("source_counts", {})
    return data


def validate_counts(state: dict[str, Any], records: list[dict[str, Any]]) -> None:
    current: dict[str, int] = defaultdict(int)
    for record in records:
        current[record["source"]] += 1
    previous = state.get("source_counts", {})
    for source in ("direct", "curator", "announcement"):
        count = current.get(source, 0)
        if count <= 0:
            fail(f"Source {source} returned zero records.")
        old = int(previous.get(source, 0) or 0)
        if old and count < max(1, int(old * 0.60)):
            fail(f"Source {source} suddenly dropped from {old} to {count}; refusing to rewrite pages.")
    state["source_counts"] = dict(sorted(current.items()))


def page_path(records: list[dict[str, Any]]) -> Path:
    appid = next((r.get("appid") for r in records if r.get("appid")), None)
    if appid:
        return OUT_DIR / f"steam-app-{appid}.md"
    remote_id = records[0].get("remote_id") or re.sub(r"\D+", "", records[0]["source_id"])
    return OUT_DIR / f"steam-announcement-{remote_id}.md"


def page_title(records: list[dict[str, Any]]) -> str:
    appid = next((r.get("appid") for r in records if r.get("appid")), None)
    if appid:
        return f"《{app_name(str(appid))}》评测"
    return records[0].get("title") or "Steam 群组公告评测"


def source_section(record: dict[str, Any]) -> str:
    if record["source"] == "direct":
        heading = "Steam 直接评测"
    elif record["source"] == "curator":
        heading = "Steam 鉴赏家评测"
    else:
        heading = f"Steam 群组公告评测：{record.get('title', '')}".rstrip("：")

    lines = [f"## {heading}", ""]
    if record.get("url"):
        lines += [f"[Steam 原文]({record['url']})", ""]
    if record.get("status"):
        lines += [f"Steam 状态：{record['status']}", ""]
    lines += [record["body"].strip(), ""]
    return "\n".join(lines).strip()


def sync_block(records: list[dict[str, Any]]) -> str:
    order = {"direct": 0, "curator": 1, "announcement": 2}
    sorted_records = sorted(records, key=lambda r: (order.get(r["source"], 99), r.get("published") or "", r["source_id"]))
    sections = "\n\n".join(source_section(record) for record in sorted_records)
    return f"{START_MARKER}\n\n{sections}\n\n{END_MARKER}"


def initial_page(records: list[dict[str, Any]]) -> str:
    title = page_title(records)
    appid = next((r.get("appid") for r in records if r.get("appid")), None)
    dates = [r["published"] for r in records if r.get("published")]
    published = max(dates) if dates else datetime.now(timezone.utc).isoformat()
    sources = sorted({r["source"] for r in records})
    lines = [
        "---",
        f"title: {yaml_quote(title)}",
        f"date: {yaml_quote(published)}",
        "steam_sync: true",
    ]
    if appid:
        lines.append(f"steam_appid: {appid}")
    lines.append("steam_sources:")
    for source in sources:
        lines.append(f"  - {source}")
    lines += ["---", "", sync_block(records), ""]
    return "\n".join(lines)


def update_marked_page(path: Path, records: list[dict[str, Any]], repair_frontmatter: bool = False) -> bool:
    block = sync_block(records)
    if not path.exists():
        path.write_text(initial_page(records), encoding="utf-8")
        return True

    original = path.read_text(encoding="utf-8")
    existing = original
    if repair_frontmatter and "steam_sync: true" in existing:
        existing = re.sub(r"(?m)^title:.*$", f"title: {yaml_quote(page_title(records))}", existing, count=1)
        dates = [r["published"] for r in records if r.get("published")]
        if dates:
            existing = re.sub(r"(?m)^date:.*$", f"date: {yaml_quote(max(dates))}", existing, count=1)

    if START_MARKER not in existing or END_MARKER not in existing:
        print(f"skip: {path.relative_to(ROOT)} exists but has no Steam sync markers", file=sys.stderr)
        return False
    pattern = re.compile(re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER), re.S)
    updated = pattern.sub(block, existing, count=1)
    if updated != original:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    direct = fetch_direct_reviews()
    curator = fetch_curator_reviews()
    announcements = fetch_group_announcements()
    all_records = direct + curator + announcements

    state = load_state()
    repair_frontmatter = int(state.get("version", 1) or 1) < 4
    validate_counts(state, all_records)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in all_records:
        key = f"app:{record['appid']}" if record.get("appid") else record["source_id"]
        groups[key].append(record)

    changed_pages = 0
    for _, records in sorted(groups.items()):
        path = page_path(records)
        if update_marked_page(path, records, repair_frontmatter=repair_frontmatter):
            changed_pages += 1
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        for record in records:
            state["records"][record["source_id"]] = {
                "path": rel,
                "hash": record_hash(record),
                "source": record["source"],
                "appid": record.get("appid"),
            }

    state["version"] = 4
    serialized = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if not STATE_PATH.exists() or STATE_PATH.read_text(encoding="utf-8") != serialized:
        STATE_PATH.write_text(serialized, encoding="utf-8")

    print(
        f"Steam sync ready: direct={len(direct)}, curator={len(curator)}, "
        f"announcements={len(announcements)}, groups={len(groups)}, changed_pages={changed_pages}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Steam sync aborted: {exc}", file=sys.stderr)
        sys.exit(1)
