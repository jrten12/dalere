# The Dale Ledger — Scarsdale Assessment Almanac

Searchable broadsheet over the Village of Scarsdale assessment rolls (2026 / 2025 / 2024). Three views: a smart Lookup, an editorial Honor Roll of superlatives, and a Browse mode that slices the roll by street, school zone, value band, or class. Per-parcel 3-year assessed-value chart, estimated property tax on every card, and shareable URLs.

## Files
- `index.html` — the app. Single file, no build. Ships with a 12-parcel demo so it runs immediately; the real data replaces it the moment the JSON exists.
- `extract.py` — fast roll PDF → JSON (use this; supersedes parse_roll.py).
- `extract_chunks.py` — fallback parser for chunked JSONL exports when only that format is available.
- `build_schools.py` — builds `data/schools.json` from NCES boundaries.
- `data/tax_rates.json` — county / school / village mill rates for estimated tax (not official bills).

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

Expect ~5,500 parcels/year. The 2025 Final roll uses a different PDF layout; `extract.py` normalizes text and falls back to parcel-id walking automatically. If a year shows a few dozen parcels, the download returned an HTML error page — check file size (2025.pdf is ~100MB).

## 3. Run / deploy
    python -m http.server 8000      # then open http://localhost:8000

Static site — JSON data + one HTML file, no backend. Deploy to Netlify Drop, Vercel, Cloudflare Pages, or GitHub Pages.

### Deploy notes
- Serve the repo root (where `index.html` lives). All fetches are relative (`data/2026.json`, etc.).
- Ensure `data/*.json` is included in the deploy artifact (not gitignored).
- After deploy, test a deep link: `?year=2026&q=heathcote&parcel=13.01.15`

## Smart search (deterministic, no model)
The Lookup box parses plain phrases and stacks them with AND:
  - price:   over 3m · under 1.5m · 2m-4m · 5m+
  - tax:     tax over 80k · tax under 50k · biggest tax · lowest tax
  - school:  heathcote · edgewood · greenacres · fox meadow · quaker ridge · unzoned
  - class:   vacant · condo · house
  - sort:    biggest · cheapest · highest tax
  - name / street / parcel id: anything else is matched as text

Matched filters show as chips. URL query params (`year`, `panel`, `q`, `parcel`) sync as you search.

## School zones
Elementary zones come from `data/schools.json` (built via `build_schools.py` from NCES attendance boundaries). The roll itself lists every parcel as "SCARSDALE CENTRAL"; the app maps streets to Edgewood, Fox Meadow, Greenacres, Heathcote, or Quaker Ridge. A small number of edge parcels may show as Unzoned — confirm with the registrar (914-721-2444) before relying on them.

## Property tax estimates
Cards show **estimated** annual tax (county + school + village) from `data/tax_rates.json`. STAR is applied when exemption codes appear on the roll. Sewer, refuse, and special districts are not included. Every card links to [PROS](https://townofscarsdale.prosgar.com/) for official bills.

## Ad slots
Marked `.notice` in index.html: one inline in Lookup (after result 5), one on the Honor Roll page.

## Board news & permits (automated)

GitHub Actions refresh public data on a schedule (no server required):

| Workflow | Schedule | What it does |
|----------|----------|----------------|
| [refresh-news.yml](.github/workflows/refresh-news.yml) | Daily | `python build_news.py` → updates `data/news.json` from village agendas + recent permits |
| [refresh-permits.yml](.github/workflows/refresh-permits.yml) | Sundays | `python build_permits.py --resume` then `build_news.py` → updates `data/permits.json` and news |

Both workflows commit to `main` when data changes; GitHub Pages redeploys automatically.

Manual run: **Actions** tab → pick a workflow → **Run workflow**.

Optional: add repository secret `OPENAI_API_KEY` to enable `--ai` one-line summaries in automated runs.

Local one-off:

    python build_news.py
    python build_permits.py --resume

## Notes
- Commercial parcels are filtered out on load — the ledger is residential-only.
- Uniform % of value differs by year (2026 66.91%, 2025 69.73%, 2024 74.94%); read per-roll.
- Owner names are public record, shown as published.
- Unofficial tool. Verify against the official roll / PROS before relying on figures.
