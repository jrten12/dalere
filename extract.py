#!/usr/bin/env python3
"""
extract.py — fast, complete extraction of a Scarsdale (SWIS 555000) assessment
roll PDF -> JSON. Supersedes parse_roll.py.

Speed: uses PyMuPDF (get_text) or poppler pdftotext — C-backed, ~20-50x faster
than pdfplumber on an 800-page roll. One streaming pass, split on the literal
parcel delimiter, keyword-anchored field regex (robust to column drift).

Completeness: walks every roll section (taxable / franchise / utility / wholly
exempt), captures exemption codes, and reconciles parsed totals against the
roll's own printed grand total so you know nothing was dropped.

Usage:
    python extract.py 2026.pdf data/2026.json
    python extract.py 2025.pdf data/2025.json
    python extract.py 2024.pdf data/2024.json
    # all three in parallel:
    #   for y in 2026 2025 2024; do python extract.py $y.pdf data/$y.json & done; wait

Engines tried in order: pymupdf -> pdftotext -> pdfplumber. Force one with
    EXTRACT_ENGINE=pdftotext python extract.py ...
A .txt of already-extracted text is also accepted (for testing).
"""
import sys, re, json, os, time, subprocess, shutil

# ---------------------------------------------------------------- engines ----
def text_via_fitz(path):
    import fitz
    doc = fitz.open(path)
    return "\n".join(doc[i].get_text("text") for i in range(doc.page_count)), doc.page_count

def text_via_pdftotext(path):
    out = subprocess.run(["pdftotext", "-layout", path, "-"],
                         capture_output=True, text=True, check=True)
    return out.stdout, out.stdout.count("\f") + 1

def text_via_pdfplumber(path):
    import pdfplumber
    parts = []
    with pdfplumber.open(path) as pdf:
        for pg in pdf.pages:
            parts.append(pg.extract_text() or "")
    return "\n".join(parts), len(parts)

def extract_text(path):
    if path.lower().endswith(".txt"):
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(), None, "txt"
    forced = os.environ.get("EXTRACT_ENGINE")
    order = [forced] if forced else ["pymupdf", "pdftotext", "pdfplumber"]
    for eng in order:
        try:
            if eng == "pymupdf":   return (*text_via_fitz(path), "pymupdf")
            if eng == "pdftotext":
                if not shutil.which("pdftotext"): continue
                return (*text_via_pdftotext(path), "pdftotext")
            if eng == "pdfplumber":return (*text_via_pdfplumber(path), "pdfplumber")
        except Exception as e:
            sys.stderr.write(f"[engine {eng} failed: {e}]\n")
    raise RuntimeError("no working PDF text engine (pip install pymupdf)")

# ---------------------------------------------------------------- regex ------
DELIM   = re.compile(r'\*{4,}\s*(\d{2}\.\d{2}\.[0-9A-Za-z]+)\s*\*{4,}')
SECTION = re.compile(r'([A-Z][A-Z ]*?)\s*SECTION OF THE ROLL\s*-\s*(\d+)')
SCHOOL  = r'(?:SCARSDALE CENTRAL|EDGEMONT(?:\s+UFSD)?|MAMARONECK[A-Z ]*|TUCKAHOE[A-Z ]*)'

RE_FULLMKT = re.compile(r'FULL MKT VAL\s+([\d,]+)')
RE_COUNTY  = re.compile(r'COUNTY TAXABLE\s+([\d,]+)')
RE_SCHOOLTAX = re.compile(r'SCHOOL TAXABLE\s+([\d,]+)')
RE_PID    = re.compile(r'\b(\d{2}\.\d{2}\.[0-9A-Za-z]{1,6})\b')
RE_TOTALAV = re.compile(r'(?:FRNT\s+[\d.]+\s+DPTH\s+[\d.]+|DEED BK[\s\dPG]+?)\s+([\d,]{4,})')
RE_LAND    = re.compile(SCHOOL + r'\s+([\d,]+)')
RE_ACREAGE = re.compile(r'ACREAGE\s+([\d.]+)')
RE_FRNT    = re.compile(r'FRNT\s+([\d.]+)\s+DPTH\s+([\d.]+)')
RE_ACCT    = re.compile(r'ACCT:\s*(\d+)')
RE_CLASS   = re.compile(r'\b(\d{3})\s+([A-Z0-9][A-Z0-9 .,&/\'-]+?)\s+'
                        r'(?:COUNTY TAXABLE|TOWN TAXABLE|WHOLLY EXEMPT)')
RE_LOC     = re.compile(r'^(.*?)\s+ACCT:', re.S)
RE_OWNER   = re.compile(r'(?:COUNTY TAXABLE|WHOLLY EXEMPT)\s+[\d,]+\s+'
                        r'([A-Z][A-Z0-9 .,&/%\'-]+?)\s+' + SCHOOL)
RE_OWNER_ALT = re.compile(r'(?:COUNTY TAXABLE|WHOLLY EXEMPT)\s+'
                          r'([A-Z][A-Z0-9 .,&/%\'-]+?)\s+' + SCHOOL)
RE_OWNER2  = re.compile(r'VILLAGE TAXABLE\s+[\d,]+\s+([A-Z][A-Z .\'-]+?)\s+'
                        r'(?:ACREAGE|PRIVATE|FRNT|ACCT|CONTIGUOUS|DEED|\d)')
RE_EXEMPT  = re.compile(r'\b(\d{5})\s+[A-Z][A-Z ]{2,}\s+[\d,]+')  # exemption code + amt
RE_UPCT    = re.compile(r'UNIFORM PERCENT OF VALUE\s*=\s*([\d.]+)')
RE_YEAR    = re.compile(r'\b(20\d{2})\b')
# the roll's own printed grand total (varies by year; we look for the obvious ones)
RE_GTOT_PARCELS = re.compile(r'(?:TOTAL\s+PARCELS|PARCEL\s+COUNT)\s*:?\s*([\d,]+)')
RE_GTOT_ASSESS  = re.compile(r'(?:GRAND\s+TOTAL|TOTAL)[^\n]*ASSESS[^\n]*?([\d,]{6,})')
ST_SUFFIX = (r'(?:RD|ROAD|LA|LN|LANE|AVE|AV|AVENUE|PL|PLACE|ST|STREET|DR|DRIVE|CT|COURT|'
             r'TER|TERRACE|WAY|BLVD|CIR|CIRCLE|PKWY|PKY|XING|SQ|ROW|PATH|TRL|HTS|PK)')
RE_STREET = re.compile(r'\b(\d+[A-Z]? (?:[A-Z][A-Z0-9.\'-]* )*?' + ST_SUFFIX +
                      r'(?: ' + ST_SUFFIX + r')?)\b')

mny  = lambda s: int(s.replace(",", "")) if s else None
ws   = lambda s: re.sub(r'\s+', ' ', s).strip() if s else None

def normalize_roll_text(text):
    """Repair split numbers / line-broken parcel ids (2025 Final roll via pymupdf)."""
    t = re.sub(r"[ \t]*\n[ \t]*", " ", text)
    t = re.sub(r"(?<=\d),\s+(?=\d{3}\b)", ",", t)
    t = re.sub(r"(?<=\d)\.\s+(?=\d)", ".", t)
    t = re.sub(r"\s+", " ", t)
    return t

def pick_assessed(body, full_market, uniform):
    """Prefer SCHOOL TAXABLE when it matches full_market * uniform%."""
    derived = round(full_market * uniform / 100.0) if (full_market and uniform) else None
    best = None
    for grp in RE_SCHOOLTAX.findall(body):
        v = mny(grp)
        if v and derived and abs(v - derived) <= max(2000, derived * 0.01):
            if best is None or abs(v - derived) < abs(best - derived):
                best = v
    if best is not None:
        return best
    cnty = RE_COUNTY.search(body)
    if cnty:
        return mny(cnty.group(1))
    tot = RE_TOTALAV.search(body)
    return mny(tot.group(1)) if tot else derived

def pick_address(prefix, body):
    from collections import Counter
    cands = RE_STREET.findall(prefix + " " + body)
    cands = [re.sub(r"\s+", " ", c).strip() for c in cands]
    if not cands:
        return None
    cnt = Counter(cands)
    return max(cnt, key=lambda k: (cnt[k], len(k)))

def parse_block(pid, body, section, uniform=None, prefix=""):
    addr = pick_address(prefix, body)
    if not addr:
        loc = RE_LOC.search(body)
        addr = ws(loc.group(1)) if loc else None
        if addr:
            addr = re.sub(r'\b' + re.escape(pid) + r'\b', '', addr)
            addr = re.sub(r'^\s*[A-Z]{2}\s+', '', addr)
            addr = ws(addr)
    cls   = RE_CLASS.search(body)
    own   = RE_OWNER.search(body) or RE_OWNER_ALT.search(body)
    own2  = RE_OWNER2.search(body)
    co    = ws(own2.group(1)) if own2 else None
    if co and re.match(r'^\d', co): co = None
    land_m  = RE_LAND.search(body)
    land_val = mny(land_m.group(1)) if land_m else None
    fmkt  = RE_FULLMKT.search(body)
    full_market = mny(fmkt.group(1)) if fmkt else None
    acre  = RE_ACREAGE.search(body)
    frnt  = RE_FRNT.search(body)
    acct  = RE_ACCT.search(body)
    assessed = pick_assessed(body, full_market, uniform)
    if land_val is None:
        for grp in RE_COUNTY.findall(body):
            v = mny(grp)
            if v and assessed and 1000 <= v <= assessed:
                land_val = v
                break
    exempt = sorted(set(RE_EXEMPT.findall(body)))
    return {
        "parcel_id":  pid,
        "section":    section,
        "acct":       acct.group(1) if acct else None,
        "address":    addr,
        "owner":      ws(own.group(1)) if own else None,
        "co_owner":   co,
        "class_code": cls.group(1) if cls else None,
        "class_desc": ws(cls.group(2)) if cls else None,
        "land":       land_val,
        "assessed":   assessed,
        "full_market":full_market,
        "acreage":    float(acre.group(1)) if acre else None,
        "frontage":   float(frnt.group(1)) if frnt else None,
        "exemptions": exempt or None,
    }

def parse_via_ids(text, uniform):
    """Fallback for rolls without star-delimited parcel ids (2025 Final layout)."""
    section = 1
    parcels, seen = [], set()
    ids = list(RE_PID.finditer(text))
    for i, m in enumerate(ids):
        pid = m.group(1)
        start = m.end()
        end = ids[i + 1].start() if i + 1 < len(ids) else len(text)
        prefix = text[max(0, m.start() - 100):m.start()]
        seg = text[start:end]
        if not RE_FULLMKT.search(seg) or pid in seen:
            continue
        sec_m = SECTION.search(text[max(0, m.start() - 200):m.start()])
        if sec_m:
            section = int(sec_m.group(2))
        rec = parse_block(pid, seg, section, uniform, prefix)
        if rec["assessed"] is None and rec["full_market"] is None and not rec["exemptions"]:
            continue
        seen.add(pid)
        parcels.append(rec)
    return parcels

def parse(path):
    t0 = time.time()
    text, pages, engine = extract_text(path)
    text = normalize_roll_text(text)
    upct = RE_UPCT.search(text)
    yr   = RE_YEAR.search(text)
    uniform = float(upct.group(1)) if upct else None

    # walk sections and parcels in one pass, in document order
    section = 1
    parcels, seen = [], set()
    # interleave section markers with parcel delimiters by scanning positions
    marks = [(m.start(), "sec", m.group(2)) for m in SECTION.finditer(text)]
    marks += [(m.start(), "par", m.group(1), m.end()) for m in DELIM.finditer(text)]
    marks.sort(key=lambda x: x[0])
    for i, mk in enumerate(marks):
        if mk[1] == "sec":
            section = int(mk[2]); continue
        pid, start = mk[2], mk[3]
        if pid in seen: continue
        # body runs to the next mark of any kind
        end = marks[i+1][0] if i+1 < len(marks) else len(text)
        rec = parse_block(pid, text[start:end], section, uniform)
        if rec["assessed"] is None and rec["full_market"] is None and not rec["exemptions"]:
            continue
        seen.add(pid); parcels.append(rec)

    if len(parcels) < 100:
        parcels = parse_via_ids(text, uniform)

    # reconciliation against the roll's own printed totals (best-effort)
    parsed_assessed = sum(p["assessed"] or 0 for p in parcels)
    pm = RE_GTOT_PARCELS.search(text)
    am = RE_GTOT_ASSESS.search(text)
    recon = {
        "parsed_parcels": len(parcels),
        "parsed_assessed_sum": parsed_assessed,
        "roll_printed_parcels": mny(pm.group(1)) if pm else None,
        "roll_printed_assessed": mny(am.group(1)) if am else None,
    }
    meta = {
        "source": os.path.basename(path),
        "year": int(yr.group(1)) if yr else None,
        "uniform_pct_of_value": float(upct.group(1)) if upct else None,
        "pages": pages, "engine": engine,
        "count": len(parcels),
        "parse_seconds": round(time.time() - t0, 2),
        "reconciliation": recon,
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
    m = data["meta"]; r = m["reconciliation"]
    print(f"{src} -> {dst}")
    print(f"  engine={m['engine']} pages={m['pages']} time={m['parse_seconds']}s")
    print(f"  parsed {m['count']} parcels, year={m['year']}, uniform%={m['uniform_pct_of_value']}")
    print(f"  assessed sum = ${r['parsed_assessed_sum']:,}")
    if r["roll_printed_parcels"]:
        d = m["count"] - r["roll_printed_parcels"]
        print(f"  roll says {r['roll_printed_parcels']:,} parcels  ->  delta {d:+,}"
              + ("  OK" if d == 0 else "  *** CHECK ***"))
    else:
        print("  (no printed parcel total auto-found — eyeball the roll's totals page)")
