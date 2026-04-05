"""
Polsia Live Scraper — runs every 5 minutes
1. Opens polsia.com/live with headless Firefox (Playwright)
2. Extracts company names from the Companies section
3. Fetches each *.polsia.app site
4. Keeps only real startup sites (filters out default Polsia pages)
5. Appends to startups.json + polsia_names.txt
"""

import asyncio
import aiohttp
import json
import os
import re
import subprocess
import time
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = os.path.join(DIR, "startups.json")
NAMES_FILE = os.path.join(DIR, "polsia_names.txt")
MAX_CONCURRENT = 10


# ── Data helpers ──────────────────────────────────────────────

def load_existing():
    if os.path.exists(JSON_FILE):
        with open(JSON_FILE, "r") as f:
            return json.load(f)
    return {"last_updated": None, "startups": []}


def save_data(data):
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(JSON_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_known_names():
    """Load all previously seen names from polsia_names.txt."""
    if not os.path.exists(NAMES_FILE):
        return set()
    with open(NAMES_FILE, "r") as f:
        return {line.strip().lower().replace(" ", "") for line in f if line.strip()}


def append_names(names):
    """Append new names to polsia_names.txt."""
    with open(NAMES_FILE, "a") as f:
        for name in names:
            f.write(name + "\n")


# ── Step 1: Read polsia.com/live with headless browser ────────

def get_live_companies():
    """Use Playwright Firefox to extract company names from polsia.com/live."""
    print("Opening polsia.com/live with headless Firefox...")
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        page = browser.new_page()
        page.goto("https://polsia.com/live", wait_until="networkidle", timeout=30000)

        links = page.eval_on_selector_all(
            'a[href*=".polsia.app"]',
            'els => els.map(e => ({name: e.textContent.trim(), href: e.href}))'
        )
        browser.close()

    companies = []
    for link in links:
        name = link["name"]
        href = link["href"]
        # Extract slug from URL: https://xxx.polsia.app/ → xxx
        match = re.match(r"https://([a-z0-9-]+)\.polsia\.app", href.lower())
        if match:
            slug = match.group(1)
            companies.append((name, slug))

    return companies


# ── Step 2: Fetch and parse each site ─────────────────────────

def is_real_site(html):
    if not html:
        return False
    lower = html.lower()
    polsia_markers = [
        "welcome to your app",
        "autonomous ai platform that builds and runs companies",
        "ai that runs your company while you sleep",
        "polsia is an autonomous ai",
    ]
    for marker in polsia_markers:
        if marker in lower:
            return False
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(strip=True)
    return len(text) > 200


def parse_polsia_site(html, slug, original_name):
    soup = BeautifulSoup(html, "html.parser")

    name = ""
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        name = og_title["content"]
    if not name:
        title_tag = soup.find("title")
        if title_tag:
            name = title_tag.get_text(strip=True)
    if not name:
        h1 = soup.find("h1")
        if h1:
            name = h1.get_text(strip=True)
    name = re.split(r'\s*[-|–]\s*', name)[0].strip() if name else original_name

    description = ""
    og_desc = soup.find("meta", property="og:description")
    if og_desc and og_desc.get("content"):
        description = og_desc["content"]
    if not description:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            description = meta_desc["content"]
    if not description:
        for p_tag in soup.find_all("p"):
            t = p_tag.get_text(strip=True)
            if len(t) > len(description):
                description = t

    return {
        "name": name,
        "slug": f"polsia-{slug}",
        "description": description,
        "category": "",
        "website": f"https://{slug}.polsia.app",
        "source": "polsia",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


async def fetch(session, url):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                return await resp.text()
    except Exception:
        pass
    return None


async def scrape_sites(companies):
    """Fetch all company sites in parallel, return list of valid startups."""
    results = []
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def scrape_one(session, name, slug):
        async with semaphore:
            url = f"https://{slug}.polsia.app"
            html = await fetch(session, url)
            if html and is_real_site(html):
                detail = parse_polsia_site(html, slug, name)
                results.append(detail)
                return True
            return False

    async with aiohttp.ClientSession() as session:
        tasks = [scrape_one(session, name, slug) for name, slug in companies]
        await asyncio.gather(*tasks)

    return results


# ── Main loop ─────────────────────────────────────────────────

def run_once():
    """Single scrape cycle."""
    data = load_existing()
    existing_slugs = {s["slug"] for s in data["startups"]}
    known_names = load_known_names()

    # Step 1: Get names from live page
    live_companies = get_live_companies()
    print(f"  Found {len(live_companies)} companies on /live")

    # Filter to only new ones
    new_companies = []
    new_name_entries = []
    for name, slug in live_companies:
        if slug not in known_names and f"polsia-{slug}" not in existing_slugs:
            new_companies.append((name, slug))
            new_name_entries.append(slug)

    if not new_companies:
        print("  No new companies. Skipping.")
        return 0

    print(f"  {len(new_companies)} new companies to check...")

    # Step 2: Fetch sites
    results = asyncio.run(scrape_sites(new_companies))

    # Step 3: Save
    append_names(new_name_entries)  # Track all names (even if site not ready yet)

    if results:
        data["startups"].extend(results)
        save_data(data)
        for r in results:
            print(f"    + {r['name']}: {r['description'][:60]}...")

    print(f"  Added {len(results)} real sites. Total in DB: {len(data['startups'])}")
    return len(results)


def git_push():
    """Auto-commit and push changes to GitHub."""
    try:
        subprocess.run(["git", "add", "startups.json", "polsia_names.txt"], cwd=DIR, check=True)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        subprocess.run(["git", "commit", "-m", f"auto: polsia update {now}"], cwd=DIR, check=True)
        subprocess.run(["git", "push"], cwd=DIR, check=True)
        print("  Pushed to GitHub.")
    except subprocess.CalledProcessError:
        print("  Git push skipped (no changes or error).")


def main():
    interval = 300  # 5 minutes
    print("=" * 60)
    print("Polsia Live Scraper — checking every 5 minutes")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    total_added = 0
    cycles = 0

    while True:
        cycles += 1
        now = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{now}] Cycle #{cycles}")

        try:
            added = run_once()
            total_added += added
            if added > 0:
                git_push()
        except Exception as e:
            print(f"  Error: {e}")

        print(f"  Total added this session: {total_added}")
        print(f"  Next check in {interval}s...")

        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopped.")
            break


if __name__ == "__main__":
    main()
