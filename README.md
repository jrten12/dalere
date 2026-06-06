# The Dale Ledger — Scarsdale Assessment Almanac

Searchable broadsheet over the Village of Scarsdale assessment rolls (2026 / 2025
/ 2024). Three views: a smart Lookup, an editorial Honor Roll of superlatives, and
a Browse mode that slices the roll by street, school zone, value band, or class.
Per-parcel 3-year assessed-value chart. Two ad units. Hand-built seal in the footer.

## Files
- `index.html`  — the app. Single file, no build. Ships with a 12-parcel demo so it
                   runs immediately; the real data replaces it the moment the JSON exists.
- `extract.py`  — fast roll PDF -> JSON (use this; supersedes parse_roll.py).
- `parse_roll.py` — earlier/simpler parser, kept for reference.

## 1. Download the three rolls (your machine, not the build box)
    curl -L -o 2026.pdf "https://www.scarsdale.gov/DocumentCenter/View/11798/2026-TENTATIVE-ASSESSMENT-ROLL-6-1-2026"
    curl -L -o 2025.pdf "https://www.scarsdale.gov/DocumentCenter/View/11023/2025-FINAL-ASSESSMENT-ROLL-09-15-2025"
    curl -L -o 2024.pdf "https://www.scarsdale.gov/DocumentCenter/View/10176/2024-TENTATIVE-ASSESSMENT-ROLL-6-1-2024-PDF"

## 2. Parse to JSON
    pip install pymupdf
    mkdir data
    python extract.py 2026.pdf data/2026.json
    python extract.py 2025.pdf data/2025.json
    python extract.py 2024.pdf data/2024.json
Expect ~5,800 parcels/year. If a year shows a few dozen, the download returned an
HTML error page, not the PDF — check the file size.  (Windows: use `py` if `python`
isn't found.)

## 3. Run / deploy
    python -m http.server 8000      # then open http://localhost:8000
Static site — three JSON files + one HTML file, no backend. Deploy to Netlify Drop,
Vercel, Cloudflare Pages, or Railway static.

## Smart search (deterministic, no model)
The Lookup box parses plain phrases and stacks them with AND:
  - price:   over 3m · under 1.5m · 2m-4m · 5m+
  - school:  heathcote · edgewood · greenacres · fox meadow · quaker ridge · unzoned
  - class:   vacant · commercial · office · condo · house
  - sort:    biggest · cheapest
  - name / street / parcel id: anything else is matched as text
Matched filters show as chips so it's transparent what it understood.

## School zones — IMPORTANT, edit before trusting
The roll does NOT carry the elementary attendance zone (every parcel reads
"SCARSDALE CENTRAL"). Assignment is driven by the `SCHOOL_BY_STREET` map near the
top of the <script> in index.html: street name (UPPERCASE, no house number) -> school.
It's seeded only with the demo streets. Unmapped streets land in an "Unzoned" bucket
so you can see exactly what's left to fill in.

The district publishes no clean street list — they direct people to a boundary map
and the registrar (914-721-2444). To complete it accurately: get the district's
attendance-area map, then fill in SCHOOL_BY_STREET. (Send me that source and I'll
generate the full map.)

## Ad slots
Marked `.notice` in index.html: one inline in Lookup (after result 5), one on the
Honor Roll page. Set data-filled="true" and replace the inner <a> with your creative
or an ad-network tag.

## Notes
- Uniform % of value differs by year (2026 66.91%, 2024 74.94%); read per-roll, not recomputed.
- Owner names are public record, shown as published.
- Unofficial tool. Verify against the official roll / PROS before relying on figures.
