#!/usr/bin/env python3
"""
Part 2: AI Job Source Agent - LinkedIn Jobs Search Scraper
- Scrapes all job posts from a LinkedIn Jobs search page (no login required)
- For each job post:
    - Extract company name
    - Extract company website (if available)
    - Find career page
    - Find one open position URL
- Saves results to CSV: linkedin_jobs_results.csv
"""

import asyncio
import csv
import logging
import re
import sys
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# Playwright for dynamic content
try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False

# ---------- Config ----------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3
CAREER_KEYWORDS = [
    "careers", "jobs", "join-us", "joinus", "work-with-us", "vacancies", "open-positions", "opportunities",
    "roles", "positions", "join", "hiring"
]
JOB_KEYWORDS = [
    "job", "position", "openings", "apply", "career", "careers", "roles", "/jobs/", "/open-positions/"
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ---------- Utilities ----------
def safe_get(url, headers=None, timeout=REQUEST_TIMEOUT):
    headers = headers or HEADERS
    for i in range(MAX_RETRIES):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            resp.raise_for_status()
            return resp
        except Exception as e:
            logging.debug(f"GET {url} failed attempt {i+1}/{MAX_RETRIES}: {e}")
            time.sleep(1 + i)
    return None

def normalize_url(base, href):
    if not href:
        return None
    href = href.strip()
    if href.startswith("//"):
        parsed_base = urlparse(base)
        scheme = parsed_base.scheme or "https"
        return f"{scheme}:{href}"
    return urljoin(base, href)

def domain_of(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""

# ---------- Step 1: Parse LinkedIn Job Listing ----------
async def parse_linkedin_job(linkedin_job_url, use_playwright=True, timeout=10000):
    company_name = None
    company_website = None

    if use_playwright and PLAYWRIGHT_AVAILABLE:
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
                page = await browser.new_page(user_agent=HEADERS["User-Agent"])
                await page.goto(linkedin_job_url, timeout=timeout)
                await page.wait_for_timeout(1200)
                content = await page.content()
                soup = BeautifulSoup(content, "html.parser")

                meta_org = soup.find("meta", {"property": "og:site_name"}) or soup.find("meta", {"name": "og:site_name"})
                if meta_org and meta_org.get("content"):
                    company_name = meta_org["content"].strip()

                if not company_name and soup.title:
                    company_name = re.sub(r"\s*\|\s*LinkedIn.*$", "", soup.title.string).strip()

                # Check for company website links
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    txt = (a.get_text() or "").lower()
                    if "http" in href and "linkedin.com" not in href:
                        if "website" in txt or "company website" in txt or domain_of(href).count('.') >= 1:
                            company_website = normalize_url(linkedin_job_url, href)
                            break

                await browser.close()
        except Exception as e:
            logging.warning(f"Playwright error parsing job: {e}")

    # Fallback: requests + BS
    if not company_name or not company_website:
        r = safe_get(linkedin_job_url)
        if r:
            soup = BeautifulSoup(r.text, "html.parser")
            if not company_name:
                og_site = soup.find("meta", {"property": "og:site_name"}) or soup.find("meta", {"name": "og:site_name"})
                if og_site and og_site.get("content"):
                    company_name = og_site["content"].strip()
            if not company_name and soup.title:
                company_name = re.sub(r"\s*\|\s*LinkedIn.*$", "", soup.title.string).strip()
            for a in soup.find_all("a", href=True):
                href = a["href"]
                txt = (a.get_text() or "").lower()
                if href.startswith("http") and "linkedin.com" not in href:
                    if "website" in txt or "company website" in txt or domain_of(href).count('.') >= 1:
                        company_website = normalize_url(linkedin_job_url, href)
                        break
    return company_name, company_website

# ---------- Step 2: Find career page ----------
def find_career_page(company_site_url):
    if not company_site_url:
        return None
    base = company_site_url if company_site_url.startswith("http") else "https://" + company_site_url
    parsed = urlparse(base)
    base_origin = f"{parsed.scheme}://{parsed.netloc}"

    # Common paths
    for p in ["/careers", "/jobs", "/careers/", "/jobs/", "/careers.html", "/careers/positions", "/careers/openings", "/careers-us"]:
        candidate = urljoin(base_origin, p)
        r = safe_get(candidate)
        if r and r.status_code == 200 and any(k in r.text.lower() for k in CAREER_KEYWORDS):
            return candidate

    # Scan homepage
    r = safe_get(base_origin)
    if r:
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            txt = (a.get_text() or "").lower()
            full = normalize_url(base_origin, href)
            if any(kw in href.lower() for kw in CAREER_KEYWORDS) or any(kw in txt for kw in CAREER_KEYWORDS):
                rr = safe_get(full)
                if rr and rr.status_code == 200:
                    return full
    return None

# ---------- Step 3: Extract one job from career page ----------
def extract_one_job_from_career(career_url):
    r = safe_get(career_url)
    if not r:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        txt = (a.get_text() or "").lower()
        if any(kw in href.lower() for kw in JOB_KEYWORDS) or any(kw in txt for kw in JOB_KEYWORDS):
            candidate = normalize_url(career_url, href)
            if candidate and not candidate.startswith("javascript:") and "mailto:" not in candidate:
                return candidate
    return career_url  # fallback to career page itself if ATS

# ---------- Step 4: Extract all job post URLs from search page ----------
async def scrape_linkedin_jobs_search(search_url, use_playwright=True):
    job_urls = set()
    if use_playwright and PLAYWRIGHT_AVAILABLE:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
            page = await browser.new_page(user_agent=HEADERS["User-Agent"])
            await page.goto(search_url)
            prev_height = None
            for _ in range(20):  # scroll 20 times max
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1500)
                curr_height = await page.evaluate("document.body.scrollHeight")
                if prev_height == curr_height:
                    break
                prev_height = curr_height
            # extract job URLs
            content = await page.content()
            soup = BeautifulSoup(content, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/jobs/view/" in href:
                    full = normalize_url(search_url, href)
                    job_urls.add(full)
            await browser.close()
    return list(job_urls)

# ---------- Step 5: Orchestrator ----------
async def run_agent(search_url):
    logging.info(f"Scraping LinkedIn Jobs search page: {search_url}")
    job_urls = await scrape_linkedin_jobs_search(search_url)
    logging.info(f"Found {len(job_urls)} job posts.")

    results = []
    for i, job_url in enumerate(job_urls, 1):
        logging.info(f"[{i}/{len(job_urls)}] Processing job: {job_url}")
        company_name, company_website = await parse_linkedin_job(job_url)
        if not company_website and company_name:
            company_website = "https://www." + re.sub(r"\s+", "", company_name).lower() + ".com"
        career_page = find_career_page(company_website)
        job_post_url = extract_one_job_from_career(career_page) if career_page else None
        results.append({
            "company_name": company_name or "",
            "company_website": company_website or "",
            "career_page": career_page or "",
            "job_url": job_post_url or ""
        })
    # Save to CSV
    with open("linkedin_jobs_results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["company_name","company_website","career_page","job_url"])
        writer.writeheader()
        writer.writerows(results)
    logging.info("Results saved to linkedin_jobs_results.csv")

# ---------- CLI ----------
def usage_and_exit():
    print("Usage: python part2_agent.py <linkedin_jobs_search_url> [--no-playwright]")
    sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        usage_and_exit()
    search_url = sys.argv[1]
    use_playwright = True
    if len(sys.argv) >= 3 and sys.argv[2] == "--no-playwright":
        use_playwright = False

    loop = asyncio.get_event_loop()
    loop.run_until_complete(run_agent(search_url))
