"""
NanoList Scraper
Scrapes all startups from nanolist.nanocorp.app and saves to startups.json.
Only adds new companies on subsequent runs (deduplication by slug).
"""

import json
import os
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone

BASE_URL = "https://nanolist.nanocorp.app"
JSON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "startups.json")
DELAY = 1  # seconds between requests to be polite


def load_existing():
    if os.path.exists(JSON_FILE):
        with open(JSON_FILE, "r") as f:
            return json.load(f)
    return {"last_updated": None, "startups": []}


def save_data(data):
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(JSON_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_soup(url):
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def scrape_listing_page(page_num):
    """Scrape one listing page, return list of (name, slug) tuples."""
    url = f"{BASE_URL}/?page={page_num}" if page_num > 1 else BASE_URL
    soup = get_soup(url)
    companies = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if href.startswith("/company/"):
            slug = href.replace("/company/", "").strip("/")
            if slug:
                companies.append(("", slug))  # name will come from detail page
    return companies


def get_total_pages():
    """Detect total number of pages from pagination."""
    soup = get_soup(BASE_URL)
    max_page = 1
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "?page=" in href:
            try:
                page_num = int(href.split("?page=")[1].split("&")[0])
                max_page = max(max_page, page_num)
            except ValueError:
                pass
    return max_page


def scrape_company_detail(slug):
    """Scrape a single company detail page."""
    url = f"{BASE_URL}/company/{slug}"
    try:
        soup = get_soup(url)
    except Exception as e:
        print(f"  Error fetching {slug}: {e}")
        return None

    text = soup.get_text(" ", strip=True)

    # Extract company name from h1 or title
    name = ""
    h1 = soup.find("h1")
    if h1:
        name = h1.get_text(strip=True)

    # Extract description - look for meta description or longest paragraph
    description = ""
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        description = meta_desc["content"]
    else:
        paragraphs = soup.find_all("p")
        for p in paragraphs:
            t = p.get_text(strip=True)
            if len(t) > len(description):
                description = t

    # Extract category - look for "Category{Name}" pattern in divs
    category = ""
    for el in soup.find_all(["div", "span"]):
        t = el.get_text(strip=True)
        if t.startswith("Category"):
            category = t.replace("Category", "", 1).strip()
            break
    # Fallback: look for emoji + category span pattern
    if not category:
        for el in soup.find_all("span"):
            t = el.get_text(strip=True)
            # Remove leading emoji characters
            clean = t.lstrip("\U0001f300\U0001f600\U0001f900\u2600\u2700\U0001fa00\U0001f680\U0001f400\U0001f500\U0001f4e7\U0001f4b0\u26a1\U0001f52e\U0001f3af\U0001f525")
            if clean in [
                "Software & SaaS", "Finance & Fintech", "Marketing & Advertising",
                "Sales & CRM", "Education", "Food & Beverage", "Travel & Hospitality",
                "E-Commerce & Retail", "AI & Machine Learning", "Productivity & Automation",
                "Consulting & Services", "Newsletter & Email", "Other",
                "Health & Wellness", "Real Estate", "Entertainment & Media",
                "Sustainability & Green", "Social & Community", "Logistics & Supply Chain",
            ]:
                category = clean
                break

    # Extract website URL (nanocorp.app subdomain)
    website = ""
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "nanocorp.app" in href and "/company/" not in href:
            website = href
            break

    return {
        "name": name,
        "slug": slug,
        "description": description,
        "category": category,
        "website": website,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    data = load_existing()
    existing_slugs = {s["slug"] for s in data["startups"]}
    print(f"Existing startups: {len(existing_slugs)}")

    # Step 1: Get all company slugs from listing pages
    total_pages = get_total_pages()
    print(f"Total pages to scrape: {total_pages}")

    all_companies = []
    for page in range(1, total_pages + 1):
        print(f"  Listing page {page}/{total_pages}...")
        companies = scrape_listing_page(page)
        all_companies.extend(companies)
        time.sleep(DELAY)

    # Deduplicate
    seen = set()
    unique_slugs = []
    for _, slug in all_companies:
        if slug not in seen:
            seen.add(slug)
            unique_slugs.append(slug)

    new_slugs = [s for s in unique_slugs if s not in existing_slugs]
    print(f"Total unique companies found: {len(unique_slugs)}")
    print(f"New companies to scrape: {len(new_slugs)}")

    if not new_slugs:
        print("No new companies. Done.")
        save_data(data)
        return

    # Step 2: Scrape detail page for each new company
    for i, slug in enumerate(new_slugs):
        print(f"  [{i+1}/{len(new_slugs)}] Scraping {slug}...")
        detail = scrape_company_detail(slug)
        if detail:
            data["startups"].append(detail)
        time.sleep(DELAY)

    save_data(data)
    print(f"Done. Total startups in database: {len(data['startups'])}")


if __name__ == "__main__":
    main()
