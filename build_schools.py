#!/usr/bin/env python3
"""Build data/schools.json — street -> elementary zone from NCES/SABS listings."""
import json, re, urllib.request, os

URLS = {
    "Edgewood": "https://newyorkschools.us/schools/edgewood-elementary-school/attendance-zone/",
    "Fox Meadow": "https://newyorkschools.us/schools/fox-meadow-elementary-school/attendance-zone/",
    "Greenacres": "https://newyorkschools.us/schools/greenacres-elementary-school/attendance-zone/",
    "Heathcote": "https://newyorkschools.us/schools/heathcote-school/attendance-zone/",
    "Quaker Ridge": "https://newyorkschools.us/schools/quaker-ridge-elementary-school/attendance-zone/",
}

SKIP = re.compile(
    r"^(How |Which |What |Use |The |Important|District|Enrollment|Nearby|FAQ|Real Estate|"
    r"Complete Street|Comprehensive Street|Show |Inventory|Numbers|These |Click |Load |"
    r"Search|Addresses|Students|Teachers|Housing|Estimated|Grade|View |Regular|Explore |"
    r"Strongly |Agree |Neutral |Disagree |Thank |Submit|Give |Content written)",
    re.I,
)


def norm(name: str) -> str:
    s = re.sub(r"\s+", " ", name.upper().strip())
    reps = [
        (r"\bROAD\b", "RD"), (r"\bAVENUE\b", "AVE"), (r"\bLANE\b", "LA"),
        (r"\bSTREET\b", "ST"), (r"\bPLACE\b", "PL"), (r"\bTERRACE\b", "TER"),
        (r"\bCIRCLE\b", "CIR"), (r"\bCOURT\b", "CT"), (r"\bDRIVE\b", "DR"),
        (r"\bPARKWAY\b", "PKWY"), (r"\bTRAIL\b", "TR"), (r"\bBOULEVARD\b", "BLVD"),
        (r"\bCLOSE\b", "CL"), (r"\bNORTHBOUND\b", "NB"), (r"\bSOUTHBOUND\b", "SB"),
        (r"\bNORTH\b", "N"), (r"\bSOUTH\b", "S"), (r"\bEAST\b", "E"), (r"\bWEST\b", "W"),
        (r"\bCENTER\b", "CTR"), (r"\bSQUARE\b", "SQ"),
    ]
    for pat, rep in reps:
        s = re.sub(pat, rep, s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_streets(html: str) -> set[str]:
    out = set()
    for m in re.finditer(r'data-street-name="([^"]+)"', html):
        out.add(m.group(1).strip())
    if out:
        return out
    # fallback: markdown-style pages
    lines = [ln.strip() for ln in html.splitlines()]
    for i, line in enumerate(lines):
        if not line or i + 1 >= len(lines):
            continue
        if not lines[i + 1].strip().startswith("Numbers:"):
            continue
        if re.match(r"^[A-Z][A-Z0-9 /'\-.]+$", line) and not SKIP.search(line):
            out.add(line)
    return out


def fetch(url: str) -> str:
    import subprocess
    r = subprocess.run(
        ["curl.exe", "-sL", "-A",
         "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
         url],
        capture_output=True, timeout=120, check=True,
    )
    return r.stdout.decode("utf-8", errors="replace")


def main():
    by_street: dict[str, str] = {}
    conflicts: list[str] = []
    per_school: dict[str, int] = {}

    for school, url in URLS.items():
        html = fetch(url)
        raw = extract_streets(html)
        per_school[school] = len(raw)
        for st in raw:
            key = norm(st)
            if not key or len(key) < 3:
                continue
            if key in by_street and by_street[key] != school:
                conflicts.append(f"{key}: {by_street[key]} vs {school}")
            by_street[key] = school

    # Roll street aliases (assessor abbreviations)
    roll_path = os.path.join(os.path.dirname(__file__), "data", "2026.json")
    roll_streets: set[str] = set()
    if os.path.isfile(roll_path):
        with open(roll_path, encoding="utf-8") as f:
            roll = json.load(f)
        for p in roll.get("parcels", []):
            addr = p.get("address") or ""
            st = re.sub(r"^\s*\d+[A-Z]?(?:-\d+[A-Z]?)?\s+", "", addr).strip().upper()
            if st and not st.startswith("POST RD/"):
                roll_streets.add(st)

    # Map roll streets -> school via normalized lookup + fuzzy suffix
    roll_map: dict[str, str] = {}
    unmapped: list[str] = []
    for st in sorted(roll_streets):
        k = norm(st)
        if k in by_street:
            roll_map[st] = by_street[k]
            continue
        # LEATHERSTOCKING -> LEATHERSTOCKING LA
        if k + " LA" in by_street:
            roll_map[st] = by_street[k + " LA"]
            continue
        if k.endswith(" LA") and k[:-3] in by_street:
            roll_map[st] = by_street[k[:-3]]
            continue
        unmapped.append(st)

    # Manual fixes for assessor abbreviations missing from NCES catalog
    manual = {
        "WAYSIDE LA S": "Fox Meadow",
        "SOUTHWOODS LA": "Edgewood",
        "PENN BLV": "Quaker Ridge",
        "ANDERSON AVE": "Quaker Ridge",
        "EWART RD": "Heathcote",
        "REYNAL CR": "Heathcote",
        "R.R. ROW": "Quaker Ridge",
    }
    for st, sch in manual.items():
        roll_map[st] = sch

    # Post Rd is split by house number across zones (NCES ranges)
    ranges = [
        {"street": "POST RD", "min": 902, "max": 1000, "school": "Edgewood"},
        {"street": "POST RD", "min": 1001, "max": 1162, "school": "Fox Meadow"},
        {"street": "POST RD", "min": 1020, "max": 1070, "school": "Heathcote"},
        {"street": "POST RD", "min": 1171, "max": 1259, "school": "Greenacres"},
    ]

    out = {
        "meta": {
            "source": "NCES EDGE / School Attendance Boundary Survey via newyorkschools.us",
            "note": "Verify against scarsdaleschools.org boundary map before relying on zone for a purchase.",
            "conflicts": len(conflicts),
            "streets_catalogued": len(by_street),
            "roll_streets_mapped": len(roll_map),
            "roll_streets_unmapped": len([s for s in roll_streets if s not in roll_map and s != "POST RD"]),
            "per_school_raw": per_school,
        },
        "by_street": roll_map,
        "ranges": ranges,
        "catalog": by_street,
        "unmapped": [s for s in unmapped if s not in manual and s != "POST RD"],
    }
    dst = os.path.join(os.path.dirname(__file__), "data", "schools.json")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)
    print(f"Wrote {dst}")
    print(f"  catalog keys: {len(by_street)}  roll mapped: {len(roll_map)}  unmapped: {len(unmapped)}")
    if conflicts:
        print(f"  conflicts (last wins): {len(conflicts)}")
    from collections import Counter
    c = Counter(roll_map.values())
    for s in ["Edgewood", "Fox Meadow", "Greenacres", "Heathcote", "Quaker Ridge"]:
        print(f"  {s}: {c.get(s, 0)} roll streets")


if __name__ == "__main__":
    main()
