#!/usr/bin/env python3
"""Fetch planning / zoning board items and recent building permits for the ledger.

Sources:
  - Scarsdale CivicEngage Agenda Center (Planning, BAR, ZBA, Historic Preservation)
  - data/permits.json (recent BUILDING permits from PROS scrape)

Writes data/news.json. Re-run on a schedule (cron, GitHub Action) to refresh.
Optional: set OPENAI_API_KEY for one-line AI summaries on project-specific items.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "news.json"
PERMITS = ROOT / "data" / "permits.json"
BASE = "https://www.scarsdale.gov"
UA = "Mozilla/5.0 (compatible; DaleLedger/1.0)"

BOARDS = [
    {"key": "planning", "name": "Planning Board", "slug": "Planning-Board-3"},
    {"key": "bar", "name": "Board of Architectural Review", "slug": "Board-of-Architectural-Review-5"},
    {"key": "zba", "name": "Zoning Board of Appeals", "slug": "Zoning-Board-of-Appeals-2"},
    {"key": "historic", "name": "Historic Preservation", "slug": "Committee-for-Historic-Preservation-4"},
]

ADDR = re.compile(
    r"(\d+\s+[\w'.-]+(?:\s+[\w'.-]+)*\s+"
    r"(?:Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr|Place|Pl|Court|Ct|Way|Blvd|Terrace|Ter)\.?)",
    re.I,
)
PROJECT_KW = re.compile(
    r"public meeting|site plan|subdivision|variance|demolition|renovation|addition|"
    r"construction|alteration|extension|deck|garage|pool|dormer|fence|tear.?down|"
    r"new (?:home|house|building|dwelling)|special permit|historic preservation|"
    r"adjourned|snow event",
    re.I,
)
GENERIC_MEETING = re.compile(
    r"(regular meeting|meeting agenda and (results|decisions)|agenda and results for|"
    r"meeting (and results )?for [a-z]+ \d|committee for historic preservation (meeting|agenda))",
    re.I,
)
MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def fetch(url: str) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.URLError:
        proc = subprocess.run(["curl.exe", "-sL", url], capture_output=True, timeout=90)
        return proc.stdout.decode("utf-8", "replace")


def parse_date(label: str) -> str | None:
    if not label:
        return None
    label = re.sub(r"\s+", " ", html.unescape(label)).strip()
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", label)
    if not m:
        return None
    month = MONTHS.get(m.group(1).lower())
    if not month:
        return None
    return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(2)):02d}"


def clean_title(title: str, board_name: str) -> str:
    t = re.sub(r"\s+", " ", html.unescape(title)).strip()
    for prefix in (
        board_name,
        "Planning Board",
        "Board of Architectural Review",
        "Zoning Board of Appeals",
        "Committee for Historic Preservation",
    ):
        t = re.sub(rf"^{re.escape(prefix)}\s*[-–—]\s*", "", t, flags=re.I)
    t = re.sub(
        r"\s*(Public Meeting )?Agenda( and Decisions)?$",
        "",
        t,
        flags=re.I,
    )
    t = re.sub(r"\s*Meeting Agenda.*$", "", t, flags=re.I)
    return t.strip(" -–—") or title.strip()


def extract_address(title: str) -> str | None:
    m = ADDR.search(title)
    return m.group(1).strip() if m else None


def item_kind(board_key: str, title: str) -> str:
    low = title.lower()
    if board_key == "zba" or "variance" in low or "appeal" in low:
        return "variance"
    if board_key == "bar" or "architectural" in low:
        return "architectural"
    if board_key == "historic":
        return "historic"
    if "public meeting" in low:
        return "public_meeting"
    if "decisions" in low or "minutes" in low:
        return "decisions"
    return "agenda"


def is_relevant(title: str) -> bool:
    if ADDR.search(title):
        return True
    if GENERIC_MEETING.search(title):
        return False
    scrubbed = re.sub(
        r"committee for historic preservation|board of architectural review|"
        r"planning board|zoning board of appeals",
        "",
        title,
        flags=re.I,
    )
    return bool(PROJECT_KW.search(scrubbed))


def summarize_heuristic(item: dict) -> str:
    board = item["board"]
    title = item.get("title_clean") or item["title"]
    addr = item.get("address")
    kind = item.get("kind", "agenda")
    low = title.lower()

    if item.get("source") == "permits":
        desc = item.get("description") or "building work"
        where = f" at {addr}" if addr else ""
        return f"Building permit on file{where}: {desc.rstrip('.')}."

    if addr:
        if kind == "public_meeting" or "public meeting" in low:
            return f"{board} scheduled a public meeting on work at {addr}."
        if kind == "variance":
            return f"{board} posted a variance matter for {addr}."
        if kind == "architectural":
            return f"{board} listed an architectural review for {addr}."
        if kind == "historic":
            return f"{board} listed a historic-preservation item for {addr}."
        if "decisions" in low:
            return f"{board} posted decisions touching {addr}."
        return f"{board} posted an agenda item for {addr}."

    if "adjourned" in low or "snow" in low:
        return f"{board} session rescheduled — see village agenda for the new date."
    if "regular meeting" in low and item.get("has_minutes"):
        return f"{board} regular session — minutes posted on the village site."
    if "decisions" in low:
        return f"{board} regular session — agenda and decisions posted."
    return f"{board}: {title}."


def ai_summarize(items: list[dict], api_key: str) -> dict[str, str]:
    """Return id -> one-sentence summary for items that benefit from AI."""
    need = [it for it in items if it.get("address") and it.get("source") != "permits"][:12]
    if not need:
        return {}
    lines = []
    for it in need:
        lines.append(f"- id={it['id']}: {it['board']} on {it['date']}: {it['title_clean']}")
    prompt = (
        "Write one brief neutral sentence per item (max 22 words each). "
        "Plain village-newspaper tone. No hype. Format: id|sentence\n\n"
        + "\n".join(lines)
    )
    body = json.dumps(
        {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "You summarize local planning board agenda lines."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 500,
        }
    ).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode())
        text = data["choices"][0]["message"]["content"]
    except Exception as exc:
        print(f"AI summarize skipped: {exc}")
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "|" not in line:
            continue
        i, _, sent = line.partition("|")
        i = i.strip().removeprefix("-").strip()
        if i.startswith("id="):
            i = i[3:]
        sent = sent.strip()
        if i and sent:
            out[i] = sent
    return out


def parse_board_page(board: dict, html_text: str) -> list[dict]:
    rows = re.findall(r'<tr id="row[^"]+" class="catAgendaRow">(.*?)</tr>', html_text, re.S)
    items = []
    for row in rows:
        date_m = re.search(r'aria-label="Agenda for ([^"]+)"', row)
        title_m = re.search(
            r'href="/AgendaCenter/ViewFile/Agenda/(_[^"?]+)[^"]*"[^>]*>\s*([^<]+?)\s*</a>',
            row,
        )
        if not title_m:
            continue
        slug_id = title_m.group(1)
        raw_title = re.sub(r"\s+", " ", title_m.group(2)).strip()
        if not is_relevant(raw_title):
            continue
        date_label = date_m.group(1) if date_m else ""
        iso = parse_date(date_label)
        minutes_m = re.search(r'href="/AgendaCenter/ViewFile/Minutes/(_[^"?]+)', row)
        agenda_url = f"{BASE}/AgendaCenter/ViewFile/Agenda/{slug_id}"
        minutes_url = (
            f"{BASE}/AgendaCenter/ViewFile/Minutes/{minutes_m.group(1)}" if minutes_m else None
        )
        title_clean = clean_title(raw_title, board["name"])
        addr = extract_address(raw_title) or extract_address(title_clean)
        item = {
            "id": f"{board['key']}-{slug_id.lstrip('_')}",
            "source": "agenda",
            "board_key": board["key"],
            "board": board["name"],
            "date": iso or date_label,
            "date_label": date_label,
            "title": raw_title,
            "title_clean": title_clean,
            "address": addr,
            "kind": item_kind(board["key"], raw_title),
            "has_minutes": bool(minutes_m),
            "url": agenda_url,
            "minutes_url": minutes_url,
        }
        item["summary"] = summarize_heuristic(item)
        items.append(item)
    return items


def permits_as_news(limit: int = 50) -> list[dict]:
    if not PERMITS.exists():
        return []
    try:
        data = json.loads(PERMITS.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Permits: skipped ({exc}) — scrape may be mid-write")
        return []
    rows = []
    for pid, pack in (data.get("parcels") or {}).items():
        for p in pack.get("permits") or []:
            if p.get("type") != "BUILDING":
                continue
            desc = (p.get("description") or "").strip()
            if not desc or desc.upper() == "PERMIT APPLIED":
                continue
            rows.append(
                {
                    "pid": pid,
                    "address": pack.get("address") or pid,
                    "permit": p,
                }
            )

    def permit_iso(d: str) -> str | None:
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", d or "")
        if not m:
            return None
        mo, da, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if yr < 2020 or yr > 2030 or mo < 1 or mo > 12 or da < 1 or da > 31:
            return None
        return f"{yr:04d}-{mo:02d}-{da:02d}"

    rows = [r for r in rows if permit_iso(r["permit"].get("date") or "")]

    def permit_sort_key(r):
        iso = permit_iso(r["permit"].get("date") or "") or ""
        return iso

    rows.sort(key=permit_sort_key, reverse=True)
    out = []
    for r in rows[:limit]:
        p = r["permit"]
        d = p.get("date") or ""
        iso = permit_iso(d)
        addr = re.sub(r",\s*SCARSDALE.*$", "", r["address"] or "", flags=re.I).strip()
        item = {
            "id": f"permit-{r['pid']}-{d.replace('/', '-')}",
            "source": "permits",
            "board_key": "permits",
            "board": "Building permits",
            "date": iso or d,
            "date_label": d,
            "title": p.get("description") or "Building permit",
            "title_clean": p.get("description") or "Building permit",
            "address": addr or r["address"],
            "kind": "permit",
            "description": p.get("description"),
            "status": p.get("status"),
            "pid": r["pid"],
            "url": None,
        }
        item["summary"] = summarize_heuristic(item)
        out.append(item)
    return out


def sort_key(item: dict):
    d = item.get("date") or ""
    if re.match(r"\d{4}-\d{2}-\d{2}", d):
        return d
    return "0000-00-00"


def build(use_ai: bool = False) -> dict:
    items: list[dict] = []
    for board in BOARDS:
        url = f"{BASE}/AgendaCenter/{board['slug']}"
        print(f"Fetching {board['name']}…")
        page = fetch(url)
        found = parse_board_page(board, page)
        print(f"  {len(found)} relevant items")
        items.extend(found)

    permit_items = permits_as_news()
    print(f"Permits: {len(permit_items)} recent building records")
    items.extend(permit_items)

    items.sort(key=sort_key, reverse=True)

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if use_ai and api_key:
        ai = ai_summarize(items, api_key)
        for it in items:
            if it["id"] in ai:
                it["summary"] = ai[it["id"]]
                it["summary_ai"] = True

    return {
        "meta": {
            "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "item_count": len(items),
            "sources": ["agenda_center", "permits"],
            "boards": [b["name"] for b in BOARDS],
        },
        "items": items,
    }


def main():
    ap = argparse.ArgumentParser(description="Build data/news.json from village agendas + permits")
    ap.add_argument("--ai", action="store_true", help="Use OPENAI_API_KEY for brief summaries")
    args = ap.parse_args()
    payload = build(use_ai=args.ai)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUT} ({payload['meta']['item_count']} items)")


if __name__ == "__main__":
    main()
