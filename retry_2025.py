# Retry 2025 only — streams the large Final roll to disk in chunks
# (the 2025 Final roll is 70MB+; the original cell timed out downloading it)
import requests, subprocess, zipfile, os
from google.colab import files

os.makedirs("data", exist_ok=True)

URL = "https://www.scarsdale.gov/DocumentCenter/View/11023/2025-FINAL-ASSESSMENT-ROLL-09-15-2025"

print("Downloading 2025 (large file — this takes ~30-60 seconds)...")
with requests.get(URL, headers={"User-Agent": "Mozilla/5.0"}, stream=True, timeout=300) as r:
    r.raise_for_status()
    downloaded = 0
    with open("2025.pdf", "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):  # 1MB chunks
            f.write(chunk)
            downloaded += len(chunk)
            print(f"  {downloaded / 1024 / 1024:.1f} MB downloaded...", end="\r")

print(f"\nDownloaded: {downloaded / 1024 / 1024:.1f} MB")

if open("2025.pdf", "rb").read(4) != b"%PDF":
    raise SystemExit("Did not get a PDF — check the URL or try again.")

print("Parsing 2025...")
subprocess.run(["python", "extract.py", "2025.pdf", "data/2025.json"], check=True)

print("\nDone. Downloading 2025.json...")
files.download("data/2025.json")
