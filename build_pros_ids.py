#!/usr/bin/env python3
"""Map Scarsdale SBL parcel ids to PROS internal ParcelId for direct deep links."""

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

BASE = "https://townofscarsdale.prosgar.com"
UA = "Mozilla/5.0 (compatible; DaleLedger/1.0; +https://github.com/jrten12/dalere)"


def open_session():
    cj = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [("User-Agent", UA)]
    return opener


def csrf_token(opener):
    html = opener.open(f"{BASE}/PROSSearch/SearchIndex", timeout=30).read().decode(
        "utf-8", "replace"
    )
    m = re.search(
        r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', html
    )
    if not m:
        raise RuntimeError("PROS CSRF token not found")
    return m.group(1)


def lookup_sbl(opener, token, sbl, retries=3):
    payload = {
        "__RequestVerificationToken": token,
        "draw": "1",
        "start": "0",
        "length": "5",
        "sbl": sbl,
        "address": "",
        "owner": "",
        "municipality": "",
        "street": "",
    }
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(f"{BASE}/PROSSearch/GetAjax", data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Referer", f"{BASE}/PROSSearch/SearchIndex?SBL={urllib.parse.quote(sbl)}")

    for attempt in range(retries):
        try:
            body = opener.open(req, timeout=30).read().decode("utf-8", "replace")
            j = json.loads(body)
            rows = j.get("data") or []
            if not rows:
                return None
            row = rows[0]
            if str(row.get("SBL", "")).upper() != sbl.upper():
                for r in rows:
                    if str(r.get("SBL", "")).upper() == sbl.upper():
                        row = r
                        break
            return {"id": row["ParcelId"], "swis": row.get("SWIS") or "555000"}
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, KeyError):
            if attempt + 1 == retries:
                raise
            time.sleep(0.5 * (attempt + 1))
    return None


def parcel_ids_from_roll(path):
    data = json.load(open(path, encoding="utf-8"))
    ids = sorted({p["parcel_id"] for p in data["parcels"] if p.get("parcel_id")})
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roll_json", help="e.g. data/2026.json")
    ap.add_argument("-o", "--output", default="data/pros_ids.json")
    ap.add_argument("--delay", type=float, default=0.15, help="seconds between lookups")
    ap.add_argument("--limit", type=int, default=0, help="max parcels (0 = all)")
    ap.add_argument("--resume", action="store_true", help="merge with existing output")
    args = ap.parse_args()

    sbls = parcel_ids_from_roll(args.roll_json)
    if args.limit:
        sbls = sbls[: args.limit]

    out = {}
    if args.resume:
        try:
            out = json.load(open(args.output, encoding="utf-8"))
        except FileNotFoundError:
            pass

    opener = open_session()
    token = csrf_token(opener)
    ok = skip = fail = 0

    for i, sbl in enumerate(sbls, 1):
        if sbl in out:
            skip += 1
            continue
        try:
            hit = lookup_sbl(opener, token, sbl)
            if hit:
                out[sbl] = hit
                ok += 1
            else:
                fail += 1
                print(f"  no match: {sbl}")
        except Exception as e:
            fail += 1
            print(f"  error {sbl}: {e}")
            token = csrf_token(opener)
        if i % 25 == 0:
            json.dump(out, open(args.output, "w", encoding="utf-8"), indent=2)
            print(f"{i}/{len(sbls)} mapped={ok} skip={skip} fail={fail}")
        time.sleep(args.delay)

    json.dump(out, open(args.output, "w", encoding="utf-8"), indent=2)
    print(f"done: {len(out)} ids -> {args.output} (new={ok}, skip={skip}, fail={fail})")


if __name__ == "__main__":
    main()
