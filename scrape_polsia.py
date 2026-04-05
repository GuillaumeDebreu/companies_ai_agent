"""
Polsia Scraper
Fetches company subdomains from polsia.com/live (requires a names file),
then scrapes each *.polsia.app site for real startup content.
Skips sites that are just the default Polsia homepage.
Appends to the same startups.json used by NanoList scraper.
"""

import asyncio
import aiohttp
import json
import os
import re
import time
from bs4 import BeautifulSoup
from datetime import datetime, timezone

JSON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "startups.json")
POLSIA_NAMES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "polsia_names.txt")
MAX_CONCURRENT = 10


def load_existing():
    if os.path.exists(JSON_FILE):
        with open(JSON_FILE, "r") as f:
            return json.load(f)
    return {"last_updated": None, "startups": []}


def save_data(data):
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(JSON_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def is_real_site(html):
    """Check if the HTML is a real startup site, not the Polsia default/homepage."""
    if not html:
        return False
    lower = html.lower()
    # Default Polsia page markers
    polsia_markers = [
        "welcome to your app",
        "autonomous ai platform that builds and runs companies",
        "ai that runs your company while you sleep",
        "polsia is an autonomous ai",
    ]
    for marker in polsia_markers:
        if marker in lower:
            return False
    # Must have some real content
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(strip=True)
    return len(text) > 200


def parse_polsia_site(html, slug):
    """Extract startup info from a polsia.app subdomain site."""
    soup = BeautifulSoup(html, "html.parser")

    # Name: try og:title, then <title>, then h1
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
    # Clean name: remove trailing " - ..." or " | ..."
    name = re.split(r'\s*[-|–]\s*', name)[0].strip() if name else slug.capitalize()

    # Description: og:description > meta description > longest paragraph
    description = ""
    og_desc = soup.find("meta", property="og:description")
    if og_desc and og_desc.get("content"):
        description = og_desc["content"]
    if not description:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            description = meta_desc["content"]
    if not description:
        for p in soup.find_all("p"):
            t = p.get_text(strip=True)
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


async def scrape_one(session, slug, semaphore, results, progress, total):
    async with semaphore:
        progress["i"] += 1
        idx = progress["i"]

        url = f"https://{slug}.polsia.app"
        html = await fetch(session, url)

        if html and is_real_site(html):
            detail = parse_polsia_site(html, slug)
            results.append(detail)
            status = "OK"
        else:
            status = "skip"

        if idx % 20 == 0 or idx == total:
            print(f"  [{idx}/{total}] {len(results)} real sites found...")


def load_names():
    """Load company names from polsia_names.txt (one per line)."""
    if not os.path.exists(POLSIA_NAMES_FILE):
        print(f"ERROR: {POLSIA_NAMES_FILE} not found.")
        print("How to use:")
        print("  1. Add company names to polsia_names.txt (one per line)")
        print("  2. Run this script again")
        print("")
        print("You can get names from polsia.com/live by running:")
        print("  python3 scrape_polsia_live.py")
        return []

    with open(POLSIA_NAMES_FILE, "r") as f:
        names = [line.strip().lower().replace(" ", "") for line in f if line.strip()]
    return list(dict.fromkeys(names))  # deduplicate preserving order


async def main():
    data = load_existing()
    existing_slugs = {s["slug"] for s in data["startups"]}

    names = load_names()
    if not names:
        return

    new_names = [n for n in names if f"polsia-{n}" not in existing_slugs]
    print(f"Names in file: {len(names)} | Already scraped: {len(names) - len(new_names)} | New: {len(new_names)}")

    if not new_names:
        print("Nothing new. Done.")
        return

    async with aiohttp.ClientSession() as session:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        results = []
        progress = {"i": 0}

        tasks = [
            scrape_one(session, slug, semaphore, results, progress, len(new_names))
            for slug in new_names
        ]
        await asyncio.gather(*tasks)

        if results:
            data["startups"].extend(results)
            save_data(data)

        print(f"Done! {len(results)} new Polsia startups added. Total in DB: {len(data['startups'])}")


if __name__ == "__main__":
    start = time.time()
    asyncio.run(main())
    print(f"Time: {time.time() - start:.0f}s")
