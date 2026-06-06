# ============================================================
#  THE DALE LEDGER — make the three data files (run in Colab)
#  Paste this whole block into one Colab cell and press ▶.
#  When the "Choose Files" button appears, pick extract.py.
#  It will download data.zip to your computer at the end.
# ============================================================

# 1) install the PDF reader
!pip -q install pymupdf requests

# 2) upload extract.py  (a "Choose Files" button will pop up — pick it)
from google.colab import files
print(">>> Click 'Choose Files' and select extract.py")
files.upload()

# 3) download the three rolls, parse each to JSON
import subprocess, requests, os, zipfile
os.makedirs("data", exist_ok=True)
ROLLS = {
    "2026": "https://www.scarsdale.gov/DocumentCenter/View/11798/2026-TENTATIVE-ASSESSMENT-ROLL-6-1-2026",
    "2025": "https://www.scarsdale.gov/DocumentCenter/View/11023/2025-FINAL-ASSESSMENT-ROLL-09-15-2025",
    "2024": "https://www.scarsdale.gov/DocumentCenter/View/10176/2024-TENTATIVE-ASSESSMENT-ROLL-6-1-2024-PDF",
}
for year, url in ROLLS.items():
    print(f"\n--- {year}: downloading ---")
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=120)
    if r.content[:4] != b"%PDF":
        raise SystemExit(f"{year}: did not get a PDF (got a web page instead). Check the link.")
    open(f"{year}.pdf", "wb").write(r.content)
    print(f"--- {year}: parsing ---")
    subprocess.run(["python", "extract.py", f"{year}.pdf", f"data/{year}.json"], check=True)

# 4) zip the data folder and download it
with zipfile.ZipFile("data.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for year in ROLLS:
        z.write(f"data/{year}.json")
print("\n>>> Downloading data.zip — save it, then unzip into your project's data/ folder.")
files.download("data.zip")
