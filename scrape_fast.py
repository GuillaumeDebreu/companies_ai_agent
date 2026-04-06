"""
Fast NanoList Scraper — parallel async version
Scrapes ~2000+ startups in ~3-4 minutes using 10 concurrent requests.
"""

import asyncio
import aiohttp
import json
import os
import time
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from deep_translator import GoogleTranslator


def translate_to_fr(text):
    if not text:
        return ""
    try:
        return GoogleTranslator(source='en', target='fr').translate(text)
    except Exception:
        return ""

BASE_URL = "https://nanolist.nanocorp.app"
JSON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "startups.json")
MAX_CONCURRENT = 10  # parallel requests


def load_existing():
    if os.path.exists(JSON_FILE):
        with open(JSON_FILE, "r") as f:
            return json.load(f)
    return {"last_updated": None, "startups": []}


def save_data(data):
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(JSON_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def parse_detail(html, slug):
    soup = BeautifulSoup(html, "html.parser")
    name = ""
    h1 = soup.find("h1")
    if h1:
        name = h1.get_text(strip=True)

    description = ""
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        description = meta["content"]
    else:
        for p in soup.find_all("p"):
            t = p.get_text(strip=True)
            if len(t) > len(description):
                description = t

    category = ""
    for el in soup.find_all(["div", "span"]):
        t = el.get_text(strip=True)
        if t.startswith("Category"):
            category = t.replace("Category", "", 1).strip()
            break

    website = ""
    for a in soup.find_all("a", href=True):
        if "nanocorp.app" in a["href"] and "/company/" not in a["href"]:
            website = a["href"]
            break

    description_fr = translate_to_fr(description)

    return {
        "name": name,
        "slug": slug,
        "description": description,
        "description_fr": description_fr,
        "category": category,
        "website": website,
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


async def get_all_slugs(session):
    """Get total pages, then scrape all listing pages for slugs."""
    html = await fetch(session, BASE_URL)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    max_page = 1
    for a in soup.find_all("a", href=True):
        if "?page=" in a["href"]:
            try:
                p = int(a["href"].split("?page=")[1].split("&")[0])
                max_page = max(max_page, p)
            except ValueError:
                pass

    print(f"Total listing pages: {max_page}")

    # Fetch all listing pages in parallel
    tasks = []
    for page in range(1, max_page + 1):
        url = BASE_URL if page == 1 else f"{BASE_URL}/?page={page}"
        tasks.append(fetch(session, url))

    pages_html = await asyncio.gather(*tasks)

    slugs = []
    seen = set()
    for page_html in pages_html:
        if not page_html:
            continue
        soup = BeautifulSoup(page_html, "html.parser")
        for a in soup.find_all("a", href=True):
            if a["href"].startswith("/company/"):
                slug = a["href"].replace("/company/", "").strip("/")
                if slug and slug not in seen:
                    seen.add(slug)
                    slugs.append(slug)

    return slugs


async def scrape_batch(session, slugs, semaphore, results, progress):
    """Scrape a single company detail page with concurrency limit."""
    async with semaphore:
        slug = slugs[progress["i"]]
        progress["i"] += 1
        idx = progress["i"]

        url = f"{BASE_URL}/company/{slug}"
        html = await fetch(session, url)
        if html:
            detail = parse_detail(html, slug)
            if detail["name"]:
                results.append(detail)

        if idx % 50 == 0 or idx == len(slugs):
            print(f"  [{idx}/{len(slugs)}] scraped...")


async def main():
    data = load_existing()
    existing_slugs = {s["slug"] for s in data["startups"]}
    print(f"Existing startups in JSON: {len(existing_slugs)}")

    async with aiohttp.ClientSession() as session:
        # Step 1: Get all slugs
        print("Fetching listing pages...")
        all_slugs = await get_all_slugs(session)
        new_slugs = [s for s in all_slugs if s not in existing_slugs]
        print(f"Total unique: {len(all_slugs)} | New to scrape: {len(new_slugs)}")

        if not new_slugs:
            print("Nothing new. Done.")
            save_data(data)
            return

        # Step 2: Scrape all detail pages with concurrency
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        results = []
        progress = {"i": 0}

        tasks = [
            scrape_batch(session, new_slugs, semaphore, results, progress)
            for _ in new_slugs
        ]
        await asyncio.gather(*tasks)

        data["startups"].extend(results)
        save_data(data)
        print(f"Done! Total startups in database: {len(data['startups'])}")


if __name__ == "__main__":
    start = time.time()
    asyncio.run(main())
    print(f"Time: {time.time() - start:.0f}s")
