#!/usr/bin/env python3
"""
Part 2 Agent (robust)
- Accepts either:
    * Single LinkedIn job posting URL (https://.../jobs/view/ID)
    * LinkedIn jobs/search page (https://www.linkedin.com/jobs or /jobs/search/...)
- For each job found (or the single job):
    - Extract company name
    - Extract/resolve company website (avoid linkedin short-links)
    - Find career page on the company site
    - Extract one open position URL (or return career page if ATS)
- Save validated rows to part2_results.csv with columns:
    company_name,company_website,career_page,job_url
"""
import asyncio
import csv
import logging
import os
import re
import sys
import time
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

# Playwright
try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False

# ---------- Config ----------
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
REQUEST_TIMEOUT = 12
MAX_RETRIES = 3
OUTPUT_CSV = "part2_results.csv"

CAREER_KEYWORDS = ["career", "careers", "jobs", "join", "vacancies", "openings", "join-us", "work-with-us"]
JOB_KEYWORDS = ["job", "position", "apply", "opening", "/jobs/", "/open-positions/"]
ATS_HOSTS = ["lever.co", "greenhouse.io", "workday.com", "smartrecruiters.com", "apply.workable.com", "jobvite.com"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ---------- Helpers ----------
def safe_get(url, allow_redirects=True, timeout=REQUEST_TIMEOUT):
    headers = HEADERS.copy()
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=allow_redirects)
            r.raise_for_status()
            return r
        except Exception as e:
            logging.debug(f"safe_get: attempt {attempt+1} for {url} failed: {e}")
            time.sleep(1 + attempt)
    return None

def resolve_final_url(url):
    """
    Resolve redirects/short links (e.g. lnkd.in) to final destination using HEAD then GET fallback.
    """
    if not url:
        return None
    try:
        r = requests.head(url, headers=HEADERS, allow_redirects=True, timeout=REQUEST_TIMEOUT)
        if r and r.url:
            return r.url
    except Exception:
        pass
    # fallback to GET
    try:
        r = requests.get(url, headers=HEADERS, allow_redirects=True, timeout=REQUEST_TIMEOUT)
        if r and r.url:
            return r.url
    except Exception:
        pass
    return url

def is_linkedin_domain(url):
    if not url:
        return False
    host = urlparse(url).netloc.lower()
    return "linkedin.com" in host or "lnkd.in" in host

def normalize_url(base, href):
    if not href:
        return None
    href = href.strip()
    if href.startswith("#") or href.lower().startswith("javascript:") or href.lower().startswith("mailto:"):
        return None
    if href.startswith("//"):
        parsed_base = urlparse(base)
        scheme = parsed_base.scheme or "https"
        return scheme + ":" + href
    return urljoin(base, href)

def is_ats_url(url):
    if not url:
        return False
    host = urlparse(url).netloc.lower()
    return any(ats in host for ats in ATS_HOSTS)

def first_external_link(soup, avoid_domain=None):
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("http") and (not avoid_domain or avoid_domain not in href):
            return href
    return None

# ---------- LinkedIn job extraction ----------
async def render_linkedin_job_with_playwright(job_url, timeout_ms=10000):
    """Render LinkedIn job posting and return (company_name, candidate_company_website_or_None)."""
    if not PLAYWRIGHT_AVAILABLE:
        return None, None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
            page = await browser.new_page(user_agent=HEADERS["User-Agent"])
            await page.goto(job_url, timeout=timeout_ms)
            await page.wait_for_timeout(1200)
            content = await page.content()
            await browser.close()
            soup = BeautifulSoup(content, "html.parser")
            # company name heuristics
            company_name = None
            og = soup.find("meta", {"property": "og:site_name"}) or soup.find("meta", {"name": "og:site_name"})
            if og and og.get("content"):
                company_name = og["content"].strip()
            if not company_name and soup.title:
                title = soup.title.string or ""
                # title patterns: "Job Title at Company | LinkedIn"
                if " at " in title:
                    company_name = title.split(" at ")[-1].split("|")[0].strip()
            # company website heuristics: anchor text 'Company website' or first external link (not linkedin)
            candidate_site = None
            for a in soup.find_all("a", href=True):
                txt = (a.get_text() or "").lower()
                href = a["href"]
                if "company website" in txt or txt.strip() == "website":
                    candidate_site = href
                    break
            if not candidate_site:
                candidate_site = first_external_link(soup, avoid_domain="linkedin.com")
            return company_name, candidate_site
    except PlaywrightTimeoutError:
        logging.warning("Playwright timeout rendering LinkedIn job.")
    except Exception as e:
        logging.debug(f"playwright render error: {e}")
    return None, None

def extract_linkedin_job_requests(job_url):
    """Requests fallback to extract company name and first external link."""
    r = safe_get(job_url)
    if not r:
        return None, None
    soup = BeautifulSoup(r.text, "html.parser")
    company_name = None
    og = soup.find("meta", {"property": "og:site_name"}) or soup.find("meta", {"name": "og:site_name"})
    if og and og.get("content"):
        company_name = og["content"].strip()
    if not company_name and soup.title:
        title = soup.title.string or ""
        if " at " in title:
            company_name = title.split(" at ")[-1].split("|")[0].strip()
    company_site = first_external_link(soup, avoid_domain="linkedin.com")
    return company_name, company_site

# ---------- Search web for company site (DuckDuckGo) ----------
def search_company_site_duckduckgo(company_name):
    if not company_name:
        return None
    query = f"{company_name} official website"
    url = "https://duckduckgo.com/html/"
    try:
        resp = requests.post(url, data={"q": query}, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        logging.debug(f"DuckDuckGo search failed: {e}")
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    # result anchor
    a = soup.find("a", {"class": "result__a"})
    if a and a.get("href"):
        return a["href"]
    # fallback first external
    return first_external_link(soup)

# ---------- Career page finder / job-on-career extractor ----------
def find_career_page(company_site_url):
    if not company_site_url:
        return None
    # resolve final
    company_site_url = resolve_final_url(company_site_url)
    parsed = urlparse(company_site_url)
    if not parsed.scheme:
        company_site_url = "https://" + company_site_url
        parsed = urlparse(company_site_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    # try common paths
    paths = ["/careers", "/careers/", "/jobs", "/jobs/", "/about/careers", "/company/careers", "/join-us"]
    for p in paths:
        candidate = urljoin(base, p)
        r = safe_get(candidate)
        if r and any(k in r.text.lower() for k in CAREER_KEYWORDS + JOB_KEYWORDS):
            logging.info(f"Career page found by path: {candidate}")
            return candidate
    # scan homepage
    r = safe_get(base)
    if not r:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    anchors = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        txt = (a.get_text() or "").lower()
        full = normalize_url(base, href)
        if not full:
            continue
        if any(k in href.lower() for k in CAREER_KEYWORDS) or any(k in txt for k in CAREER_KEYWORDS):
            anchors.append((full, txt))
    for full, txt in anchors:
        rr = safe_get(full)
        if rr and any(k in rr.text.lower() for k in CAREER_KEYWORDS + JOB_KEYWORDS):
            logging.info(f"Career page discovered: {full}")
            return full
    # footer
    footer = soup.find("footer")
    if footer:
        for a in footer.find_all("a", href=True):
            full = normalize_url(base, a["href"])
            if full and any(k in full.lower() for k in CAREER_KEYWORDS):
                rr = safe_get(full)
                if rr:
                    return full
    # script scanning for ATS endpoints
    for s in soup.find_all("script"):
        txt = s.string or ""
        for ats in ATS_HOSTS:
            m = re.search(r"https?://[^\s'\"<>]*" + re.escape(ats) + r"[^\s'\"<>]*", txt)
            if m:
                return m.group(0)
    return None

def extract_one_job_from_career(career_url):
    if not career_url:
        return None
    r = safe_get(career_url)
    if not r:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        txt = (a.get_text() or "").lower()
        if any(k in href.lower() for k in JOB_KEYWORDS) or any(k in txt for k in JOB_KEYWORDS):
            candidate = normalize_url(career_url, href)
            if candidate and not candidate.lower().startswith("javascript:") and "mailto:" not in candidate:
                return candidate
    if is_ats_url(career_url):
        return career_url
    parsed = urlparse(career_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    for p in ["/jobs", "/openings", "/careers/jobs"]:
        candidate = urljoin(base, p)
        rr = safe_get(candidate)
        if rr and any(k in rr.text.lower() for k in JOB_KEYWORDS):
            return candidate
    return None

# ---------- Scrape LinkedIn jobs search page (collect /jobs/view/ URLs) ----------
async def scrape_jobs_from_search_page(search_url, max_scrolls=20):
    job_urls = set()
    if not PLAYWRIGHT_AVAILABLE:
        logging.warning("Playwright not available; cannot scrape search page reliably.")
        return []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(user_agent=HEADERS["User-Agent"])
        await page.goto(search_url, timeout=30000)
        prev = None
        for i in range(max_scrolls):
            await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
            await page.wait_for_timeout(1500)
            content = await page.content()
            soup = BeautifulSoup(content, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                # standard LinkedIn job posting paths
                if "/jobs/view/" in href:
                    full = normalize_url(search_url, href.split("?")[0])
                    job_urls.add(full)
            curr = len(job_urls)
            if prev is not None and curr == prev:
                break
            prev = curr
        await browser.close()
    return list(job_urls)

# ---------- Pipeline for a single job URL ----------
async def process_single_job(job_url, use_playwright=True):
    logging.info(f"Processing job: {job_url}")
    # Step A: Try Playwright render extraction
    company_name = None
    company_website = None
    if use_playwright and PLAYWRIGHT_AVAILABLE:
        cname, csite = await render_linkedin_job_with_playwright(job_url)
        company_name = cname or company_name
        company_website = csite or company_website
    # Step B: fallback requests extraction
    if (not company_name or not company_website) and True:
        cname2, csite2 = extract_linkedin_job_requests(job_url)
        company_name = company_name or cname2
        company_website = company_website or csite2
    # Step C: resolve short / redirect links and avoid linkedin links as company site
    if company_website:
        final_site = resolve_final_url(company_website)
        if is_linkedin_domain(final_site):
            logging.info("Detected company_website is linkedin domain after resolve; ignoring.")
            company_website = None
        else:
            company_website = final_site
    # Step D: If still no company_website, try searching by company_name
    if not company_website and company_name:
        found = search_company_site_duckduckgo(company_name)
        if found:
            final = resolve_final_url(found)
            if not is_linkedin_domain(final):
                company_website = final
    # Step E: last resort guess from company name
    if not company_website and company_name:
        guessed = "https://www." + re.sub(r"[^a-z0-9\-\.]", "", company_name.lower())
        company_website = guessed
        logging.info(f"Guessed company website: {company_website}")
    # Step F: find career page
    career_page = find_career_page(company_website) if company_website else None
    # Sanity: don't accept career_page that is linkedin domain
    if career_page and is_linkedin_domain(career_page):
        logging.info("Career page resolves to LinkedIn domain; ignoring.")
        career_page = None
    # Step G: from career page extract one job
    job_opening = extract_one_job_from_career(career_page) if career_page else None
    if job_opening and is_linkedin_domain(job_opening):
        logging.info("Detected job opening resolved to LinkedIn; ignoring.")
        job_opening = None
    # Save only if career_page found (per task requirement to return company name, career page URL, open position URL)
    if not career_page:
        logging.warning(f"No valid career page for company '{company_name}'. Skipping save.")
        return None
    row = {
        "company_name": company_name or "",
        "company_website": company_website or "",
        "career_page": career_page or "",
        "job_url": job_opening or ""
    }
    save_row(row)
    logging.info(f"Saved: {row}")
    # print required triple
    print(f"{row['company_name']},{row['career_page']},{row['job_url']}")
    return row

def save_row(row):
    exists = os.path.exists(OUTPUT_CSV)
    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["company_name","company_website","career_page","job_url"])
        if not exists:
            writer.writeheader()
        writer.writerow(row)

# ---------- Top-level runner ----------
async def run_main(input_url, use_playwright=True):
    input_url = input_url.strip()
    # Decide if it's a job posting (contains /jobs/view/) or a search/list page (/jobs or /jobs/search)
    if "/jobs/view/" in input_url:
        # single job posting
        await process_single_job(input_url, use_playwright=use_playwright)
    else:
        # treat as search/list page: scrape job URLs and process each
        if not PLAYWRIGHT_AVAILABLE and use_playwright:
            logging.warning("Playwright requested but not available; scraping may fail. Proceeding with requests fallback (single job behavior).")
        job_urls = await scrape_jobs_from_search_page(input_url) if use_playwright and PLAYWRIGHT_AVAILABLE else []
        if not job_urls:
            logging.warning("No job URLs discovered on the provided page.")
            return
        logging.info(f"Discovered {len(job_urls)} job URLs; processing up to them.")
        for i, j in enumerate(job_urls, start=1):
            logging.info(f"[{i}/{len(job_urls)}] Processing {j}")
            await process_single_job(j, use_playwright=use_playwright)
            # polite delay
            time.sleep(1)

# ---------- CLI ----------
def usage_and_exit():
    print("Usage: python part2_agent.py <linkedIn_job_or_search_url> [--no-playwright]")
    sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        usage_and_exit()
    url = sys.argv[1]
    use_playwright = True
    if len(sys.argv) > 2 and sys.argv[2] == "--no-playwright":
        use_playwright = False
    # Use asyncio.run to avoid deprecation warning
    asyncio.run(run_main(url, use_playwright=use_playwright))
