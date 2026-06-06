#!/usr/bin/env python3
"""
extract_chunks.py — build data/<year>.json from a chunked-JSONL export of a
Scarsdale assessment roll PDF (the "universal_chunks" / "vector_db_upsert_ready"
exports), for years where only the chunked text is available rather than the
original PDF.

Why this exists alongside extract.py: those exports tokenise the PDF one word
(and number fragment) per line, so the column-aware regexes in extract.py do not
apply. This script reconstructs the roll text from the chunks, repairs the split
numbers/ids, then walks it in parcel-id order to the same output schema extract.py
produces, so index.html consumes data/<year>.json unchanged.

Usage:
    python extract_chunks.py <chunks.jsonl> <data/2025.json>

The text field is read from top-level "text" (universal_chunks) or
metadata.text (vector_db_upsert_ready), whichever is present.
"""
import sys, re, json, os

SCHOOL = r'(?:SCARSDALE CENTRAL|EDGEMONT(?:\s+UFSD)?|MAMARONECK[A-Z ]*|TUCKAHOE[A-Z ]*)'

def load_text(path):
    chunks = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            md = d.get("metadata", {}) or {}
            text = d.get("text") or md.get("text") or ""
            cid = md.get("chunk_id") or d.get("id") or ""
            m = re.search(r'CHUNK(\d+)', cid)
            order = int(m.group(1)) if m else len(chunks)
            chunks.append((order, text))
    chunks.sort(key=lambda r: r[0])
    full = "\n".join(t for _, t in chunks)
    # collapse the one-token-per-line layout to spaces
    t = re.sub(r'[ \t]*\n[ \t]*', ' ', full)
    # repair numbers split at commas / decimals across former line breaks
    t = re.sub(r'(?<=\d),\s+(?=\d{3}\b)', ',', t)   # 875, 000   -> 875,000
    t = re.sub(r'(?<=\d)\.\s+(?=\d)', '.', t)         # 0. 27 / 01. 01. 1 -> 0.27 / 01.01.1
    t = re.sub(r'\s+', ' ', t)
    return t, len(chunks)

mny = lambda s: int(s.replace(",", "")) if s else None

RE_FULLMKT = re.compile(r'FULL MKT VAL ([\d,]+)')
RE_SCHOOLTAX = re.compile(r'SCHOOL TAXABLE ([\d,]+)')
RE_COUNTYTAX = re.compile(r'COUNTY TAXABLE ([\d,]+)')
RE_ACRE = re.compile(r'ACREAGE ([\d.]+)')
RE_CLASS = re.compile(r'\b(\d{3}) ([A-Z0-9][A-Z0-9 .,&/\'-]+?) '
                      r'(?:' + SCHOOL + r'|COUNTY TAXABLE|TOWN TAXABLE|WHOLLY EXEMPT|VILLAGE TAXABLE)')
# page-top variant: ... <code?> <class> COUNTY/WHOLLY <OWNER> <SCHOOL>
RE_OWNER_TOP = re.compile(r'(?:COUNTY TAXABLE|WHOLLY EXEMPT) ([A-Z][A-Z0-9 .,&/%\'-]+?) ' + SCHOOL)
# normal variant: pid (and optional 2-letter column code) is followed by the
# OWNER [+ CO-OWNER] name block, ending at the start of the mailing street address.
RE_OWNER_NORMAL = re.compile(r'^([A-Z][A-Z0-9.&\'/-]+(?: [A-Z0-9][A-Z0-9.&\'/-]*){0,6}?) '
                             r'(?=\d+[A-Z]? [A-Z])')
RE_LEAD_CODE = re.compile(r'^[A-Z]{2} (?=[A-Z@])')

def extract_owner(seg):
    s = seg.strip()
    s = RE_LEAD_CODE.sub('', s)              # drop a leading 2-letter column code
    if re.match(r'(?:[A-Z]{1,2} )?\d{3} ', s):  # page-top: starts with class code
        ot = RE_OWNER_TOP.search(seg)
        owner = ot.group(1).strip() if ot else None
    else:
        nb = RE_OWNER_NORMAL.match(s)
        owner = nb.group(1).strip() if nb else None
    if not owner:
        return None
    if re.match(r'^(?:ACREAGE|DEED|ACCT|FRNT|FULL|BANK|PRIVATE|' + ST_SUFFIX + r')\b', owner):
        return None
    if re.fullmatch(r'[A-Z@]{1,2}', owner) or owner.startswith('@'):
        return None
    if 'TAXABLE' in owner or re.search(r'\d{4,}', owner):
        return None  # leaked recap / value tokens, not a name
    return owner
ST_SUFFIX = (r'(?:RD|ROAD|LA|LN|LANE|AVE|AV|AVENUE|PL|PLACE|ST|STREET|DR|DRIVE|CT|COURT|'
             r'TER|TERRACE|WAY|BLVD|CIR|CIRCLE|PKWY|PKY|XING|SQ|ROW|PATH|TRL|HTS|PK)')
# a street token: house number, name words, a suffix, and an optional 2nd suffix
# (e.g. "5 CIRCLE RD") so the fuller form is preferred over a truncated one.
RE_STREET = re.compile(r'\b(\d+[A-Z]? (?:[A-Z][A-Z0-9.\'-]* )*?' + ST_SUFFIX +
                       r'(?: ' + ST_SUFFIX + r')?)\b')

def pick_assessed(seg, full_market, uniform):
    """assessed = roll's value when it agrees with full_market*uniform%, else derived."""
    derived = round(full_market * uniform / 100.0) if (full_market and uniform) else None
    best = None
    for grp in RE_SCHOOLTAX.findall(seg):
        v = mny(grp)
        if v and derived and abs(v - derived) <= max(2000, derived * 0.01):
            if best is None or abs(v - derived) < abs(best - derived):
                best = v
    return best if best is not None else derived

def pick_address(seg, pre):
    """Property location = the most-repeated street-like token in/around the record."""
    cands = RE_STREET.findall(seg) + RE_STREET.findall(pre)
    cands = [re.sub(r'\s+', ' ', c).strip() for c in cands]
    if not cands:
        return None
    from collections import Counter
    c = Counter(cands)
    # prefer the most frequent; ties break to the longest (full "CIRCLE RD",
    # not a truncated "CIRCLE")
    top = max(c, key=lambda k: (c[k], len(k)))
    return top

def parse(path):
    t, nchunks = load_text(path)
    ym = re.search(r'\b(20\d{2})\b', t)
    year = int(ym.group(1)) if ym else None
    um = re.search(r'UNIFORM PERCENT OF VALUE = ([\d.]+)', t)
    uniform = float(um.group(1)) if um else None

    # split into segments on every parcel-id occurrence; a *detail* segment is
    # one that carries a FULL MKT VAL (recap "<id> ACCT:" segments do not).
    ids = list(re.finditer(r'\b(\d{2}\.\d{2}\.[0-9A-Za-z]{1,6})\b', t))
    parcels, seen = [], set()
    for i, m in enumerate(ids):
        pid = m.group(1)
        start = m.end()
        end = ids[i + 1].start() if i + 1 < len(ids) else len(t)
        seg = t[start:end]
        fm = RE_FULLMKT.search(seg)
        if not fm or pid in seen:
            continue
        full_market = mny(fm.group(1))
        assessed = pick_assessed(seg, full_market, uniform)
        cls = RE_CLASS.search(seg)
        acre = RE_ACRE.search(seg)
        # land: a COUNTY TAXABLE value that is plausibly the land assessment
        land = None
        for grp in RE_COUNTYTAX.findall(seg):
            v = mny(grp)
            if v and assessed and 1000 <= v <= assessed:
                land = v
                break
        # owner: normal (right after pid) or page-top (after COUNTY TAXABLE) layout
        owner = extract_owner(seg)
        addr = pick_address(seg, t[max(0, m.start() - 60):m.start()])
        seen.add(pid)
        parcels.append({
            "parcel_id": pid,
            "address": addr,
            "owner": owner,
            "co_owner": None,
            "class_code": cls.group(1) if cls else None,
            "class_desc": re.sub(r'\s+', ' ', cls.group(2)).strip() if cls else None,
            "land": land,
            "assessed": assessed,
            "full_market": full_market,
            "acreage": float(acre.group(1)) if acre else None,
        })

    meta = {
        "source": os.path.basename(path),
        "year": year,
        "uniform_pct_of_value": uniform,
        "count": len(parcels),
        "chunks": nchunks,
        "note": "Derived from chunked-JSONL roll export; assessed taken from the "
                "roll's SCHOOL TAXABLE value, else full_market * uniform%.",
    }
    return {"meta": meta, "parcels": parcels}

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__); sys.exit(1)
    src, dst = sys.argv[1], sys.argv[2]
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    data = parse(src)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    m = data["meta"]
    print(f"{src} -> {dst}")
    print(f"  year={m['year']} uniform%={m['uniform_pct_of_value']} parcels={m['count']}")
