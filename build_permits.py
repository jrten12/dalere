#!/usr/bin/env python3
"""Scrape building permits from Scarsdale PROS parcel pages.

PROS embeds permit history on each parcel (table#parcel-permits). There is no
village-wide permit API or bulk export; this script walks pros_ids.json and
caches results in data/permits.json.

Note: PROS lists permit type, status, and description — not dollar fees paid.
See data/permit_fees.json for the published fee schedule (estimates only).
"""

import argparse
import html
import json
import re
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

BASE = "https://townofscarsdale.prosgar.com"
UA = "Mozilla/5.0 (compatible; DaleLedger/1.0; +https://daleledger.com)"
ROOT = Path(__file__).resolve().parent


def open_session():
    cj = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [("User-Agent", UA)]
    return opener


def clean_cell(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\r", "\n")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_permits(page_html: str) -> list[dict]:
    m = re.search(r'<table[^>]*id="parcel-permits"[^>]*>(.*?)</table>', page_html, re.S | re.I)
    if not m:
        return []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S | re.I)
    out = []
    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)
        if len(cells) < 7:
            continue
        date = clean_cell(cells[1])
        if not date or date.lower() == "date":
            continue
        permit_type = clean_cell(cells[2])
        status = clean_cell(cells[3])
        co_date = clean_cell(cells[4])
        co_num = clean_cell(cells[5])
        desc = clean_cell(cells[6])
        out.append(
            {
                "date": date,
                "type": permit_type,
                "status": status,
                "co_date": co_date or None,
                "co_number": co_num or None,
                "description": desc,
                "is_pool": bool(re.search(r"\bpool\b", desc, re.I) or re.search(r"\bpool\b", permit_type, re.I)),
            }
        )
    return out


def parse_improvements(page_html: str) -> list[dict]:
    m = re.search(r'<table[^>]*id="parcel-improvements"[^>]*>(.*?)</table>', page_html, re.S | re.I)
    if not m:
        return []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S | re.I)
    out = []
    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)
        if len(cells) < 7:
            continue
        code = clean_cell(cells[1])
        if not code or code.lower().startswith("structure"):
            continue
        out.append(
            {
                "code": code,
                "sqft": clean_cell(cells[2]) or None,
                "year_built": clean_cell(cells[6]) or None,
                "is_pool": bool(re.search(r"\bpool\b", code, re.I)),
            }
        )
    return out


def fetch_parcel(opener, parcel_id: int, swis: str = "555000") -> str:
    url = f"{BASE}/PROSParcel/Parcel/{parcel_id}?swis={swis}"
    return opener.open(url, timeout=30).read().decode("utf-8", "replace")


def scrape_one(opener, sbl: str, meta: dict) -> dict:
    page = fetch_parcel(opener, meta["id"], meta.get("swis") or "555000")
    addr_m = re.search(r"Property Details\s*-\s*([^\-]+?)\s*-\s*([\d.]+)\s*-", page, re.I)
    address = clean_cell(addr_m.group(1)) if addr_m else None
    sbl_on_page = addr_m.group(2).strip() if addr_m else None
    permits = parse_permits(page)
    improvements = parse_improvements(page)
    has_pool = any(p["is_pool"] for p in permits) or any(i["is_pool"] for i in improvements)
    return {
        "parcel_id": sbl,
        "pros_id": meta["id"],
        "address": address,
        "sbl_verified": sbl_on_page,
        "has_pool": has_pool,
        "permits": permits,
        "improvements": improvements,
        "permit_count": len(permits),
        "pool_permits": [p for p in permits if p["is_pool"]],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pros-ids", default="data/pros_ids.json")
    ap.add_argument("-o", "--output", default="data/permits.json")
    ap.add_argument("--delay", type=float, default=0.2, help="seconds between PROS requests")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--pool-only", action="store_true", help="only write parcels with pool permits/improvements")
    args = ap.parse_args()

    pros_path = ROOT / args.pros_ids
    out_path = ROOT / args.output
    pros_ids = json.load(open(pros_path, encoding="utf-8"))
    items = sorted(pros_ids.items())
    if args.limit:
        items = items[: args.limit]

    existing = {"parcels": {}, "meta": {}}
    if args.resume and out_path.is_file():
        existing = json.load(open(out_path, encoding="utf-8"))

    parcels = existing.get("parcels") or {}
    opener = open_session()
    ok = skip = fail = 0

    for i, (sbl, meta) in enumerate(items, 1):
        if sbl in parcels:
            skip += 1
            continue
        try:
            rec = scrape_one(opener, sbl, meta)
            if args.pool_only and not rec["has_pool"]:
                skip += 1
            else:
                parcels[sbl] = rec
                ok += 1
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            fail += 1
            print(f"  error {sbl}: {e}")
        if i % 25 == 0:
            _write(out_path, parcels, pros_ids, ok, skip, fail, partial=True)
            print(f"{i}/{len(items)} scraped={ok} skip={skip} fail={fail}")
        time.sleep(args.delay)

    _write(out_path, parcels, pros_ids, ok, skip, fail, partial=False)
    pool_parcels = sum(1 for p in parcels.values() if p.get("has_pool"))
    print(f"done: {len(parcels)} parcels -> {out_path} (pool={pool_parcels}, new={ok}, skip={skip}, fail={fail})")


def _write(path, parcels, pros_ids, ok, skip, fail, partial):
    all_permits = sum(p.get("permit_count", 0) for p in parcels.values())
    pool_permits = sum(len(p.get("pool_permits") or []) for p in parcels.values())
    out = {
        "meta": {
            "source": "townofscarsdale.prosgar.com PROSParcel/Parcel",
            "note": "Permit fees are not published on PROS; descriptions and status only.",
            "pros_ids_total": len(pros_ids),
            "parcels_scraped": len(parcels),
            "permits_total": all_permits,
            "pool_permit_records": pool_permits,
            "pool_parcels": sum(1 for p in parcels.values() if p.get("has_pool")),
            "partial": partial,
            "last_run": {"ok": ok, "skip": skip, "fail": fail},
        },
        "parcels": parcels,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
