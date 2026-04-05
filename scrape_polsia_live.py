"""
Polsia Live Scraper — lightweight version (no browser needed)
Uses the public API: polsia.com/api/public/live/dashboard
1. Fetches company names from the API
2. Scrapes each *.polsia.app site
3. Keeps only real startup sites
4. Saves to startups.json (status: "live" or "pending")
5. Retries pending companies on each cycle (up to 48h)
6. Auto git push if changes found

Can run as a loop (every 5 min) or single shot with --once flag.
"""

import asyncio
import aiohttp
import json
import os
import re
import subprocess
import sys
import time
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = os.path.join(DIR, "startups.json")
API_URL = "https://polsia.com/api/public/live/dashboard"
MAX_CONCURRENT = 10
PENDING_MAX_AGE = timedelta(hours=48)


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


# ── Step 1: Get companies from API ───────────────────────────

async def get_live_companies(session):
    """Fetch company list from Polsia public API."""
    try:
        async with session.get(API_URL, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                data = await resp.json()
                companies = data.get("companies", [])
                return [(c["name"], c["slug"]) for c in companies if c.get("slug")]
    except Exception as e:
        print(f"  API error: {e}")
    return []


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
        "status": "live",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def make_pending_entry(name, slug):
    return {
        "name": name,
        "slug": f"polsia-{slug}",
        "website": f"https://{slug}.polsia.app",
        "source": "polsia",
        "status": "pending",
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


# ── Git push ──────────────────────────────────────────────────

def git_push():
    try:
        subprocess.run(["git", "add", "startups.json"], cwd=DIR, check=True,
                        capture_output=True)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        subprocess.run(["git", "commit", "-m", f"auto: polsia update {now}"], cwd=DIR, check=True,
                        capture_output=True)
        subprocess.run(["git", "push"], cwd=DIR, check=True, capture_output=True)
        print("  Pushed to GitHub.")
    except subprocess.CalledProcessError:
        print("  Git push skipped (no changes or error).")


# ── Main ──────────────────────────────────────────────────────

async def run_once():
    data = load_existing()
    existing_slugs = {s["slug"] for s in data["startups"]}
    changes = 0

    async with aiohttp.ClientSession() as session:
        # Step 1: Get names from API
        live_companies = await get_live_companies(session)
        print(f"  Found {len(live_companies)} companies from API")

        # Filter to companies not already in startups.json
        new_companies = []
        for name, slug in live_companies:
            slug_clean = slug.lower().replace(" ", "")
            if f"polsia-{slug_clean}" not in existing_slugs:
                new_companies.append((name, slug_clean))

        if new_companies:
            print(f"  {len(new_companies)} new companies to check...")

            # Scrape new companies
            semaphore = asyncio.Semaphore(MAX_CONCURRENT)
            for name, slug in new_companies:
                async with semaphore:
                    url = f"https://{slug}.polsia.app"
                    html = await fetch(session, url)
                    if html and is_real_site(html):
                        detail = parse_polsia_site(html, slug, name)
                        data["startups"].append(detail)
                        print(f"    + {detail['name']}: {detail.get('description', '')[:60]}...")
                        changes += 1
                    else:
                        # Site not ready — save as pending for retry
                        pending = make_pending_entry(name, slug)
                        data["startups"].append(pending)
                        print(f"    ~ {name} ({slug}): site not ready, saved as pending")
                        changes += 1
        else:
            print("  No new companies.")

        # Step 2: Retry pending companies (younger than 48h)
        now = datetime.now(timezone.utc)
        pending_indices = []
        for i, entry in enumerate(data["startups"]):
            if entry.get("status") == "pending":
                scraped_at = datetime.fromisoformat(entry["scraped_at"])
                age = now - scraped_at
                if age <= PENDING_MAX_AGE:
                    pending_indices.append(i)

        if pending_indices:
            print(f"  Retrying {len(pending_indices)} pending companies...")
            semaphore = asyncio.Semaphore(MAX_CONCURRENT)
            for i in pending_indices:
                entry = data["startups"][i]
                slug = entry["slug"].removeprefix("polsia-")
                async with semaphore:
                    url = f"https://{slug}.polsia.app"
                    html = await fetch(session, url)
                    if html and is_real_site(html):
                        updated = parse_polsia_site(html, slug, entry["name"])
                        data["startups"][i] = updated
                        print(f"    ✓ {updated['name']}: now live!")
                        changes += 1

    # Save if anything changed
    if changes > 0:
        save_data(data)

    live_count = sum(1 for s in data["startups"] if s.get("status") == "live")
    pending_count = sum(1 for s in data["startups"] if s.get("status") == "pending")
    print(f"  {changes} changes. DB: {live_count} live, {pending_count} pending")
    return changes


def main():
    single_run = "--once" in sys.argv
    interval = 300  # 5 minutes

    if single_run:
        print("[Single run mode]")
        changes = asyncio.run(run_once())
        if changes > 0:
            git_push()
        return

    print("=" * 60)
    print("Polsia Live Scraper — checking every 5 minutes")
    print("Using API: no browser needed")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    total_changes = 0
    cycles = 0

    while True:
        cycles += 1
        now = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{now}] Cycle #{cycles}")

        try:
            changes = asyncio.run(run_once())
            total_changes += changes
            if changes > 0:
                git_push()
        except Exception as e:
            print(f"  Error: {e}")

        print(f"  Total changes this session: {total_changes}")
        print(f"  Next check in {interval}s...")

        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopped.")
            break


if __name__ == "__main__":
    main()
