"""
Workday board source for Ezjob, powered by Skyvern.

Why this exists:
  Greenhouse, Lever and Ashby expose clean public job feeds, so Ezjob reads
  them directly. Workday career sites (*.myworkdayjobs.com) have no public
  feed, so the pipeline could only mark them "assisted" and move on. This
  source uses Skyvern's AI browser to read a Workday listings page visually
  and return the same NormalizedPosting dicts the other sources return, so
  Job.upsert_from_posting dedupes Workday jobs like everything else.

Credit budget (Skyvern Free = 5,000 one-time credits, ~1 credit per action):
  - Reads ONE listings page per board by default (Workday shows ~20 jobs).
  - Pass a search term to land on filtered results via the URL, so the AI
    does not spend credits typing into the search box.
  - Does NOT open each job's detail page. Descriptions are the short
    listing text only. Detail fetching can be added later, per job, for
    shortlisted roles only.

Setup:
  Python 3.11 - 3.13 (Skyvern does not support 3.14 yet)
  pip install "skyvern[local]"   (the [local] extra is needed to drive the cloud browser page)
  Codespaces secret: SKYVERN_API_KEY

Try one board:
  python workday_skyvern.py "https://COMPANY.wd5.myworkdayjobs.com/en-US/External" "Company Name" analyst
"""

import asyncio
import os
import sys
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

SOURCE_NAME = "workday_skyvern"

# Workday shows this many jobs per results page. The count check only
# expects up to one page, since this source reads a single page.
WORKDAY_PAGE_SIZE = 20

# How many extra extract passes to spend when the count check fails.
# Each pass costs credits, so keep this small.
MAX_RETRIES = 1

# Typed output so the AI's answer matches Ezjob's NormalizedPosting shape
# instead of whatever keys it would invent on its own.
JOB_LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "reported_total": {
            "type": "integer",
            "description": "The total the page itself reports in its results "
                           "header, e.g. 11 for '11 JOBS FOUND'. Use -1 if not shown.",
        },
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "location": {"type": "string"},
                    "job_url": {"type": "string", "description": "Full link to the job posting"},
                    "posted": {"type": "string", "description": "Posted date text, e.g. 'Posted 3 Days Ago'"},
                    "job_id": {"type": "string", "description": "Requisition ID if shown, e.g. R12345"},
                    "summary": {"type": "string", "description": "Any short description text visible on the listing"},
                },
                "required": ["title", "job_url"],
            },
        }
    },
    "required": ["reported_total", "jobs"],
}

EXTRACT_PROMPT = (
    "This is a Workday careers page. First, read the total number of jobs the "
    "page reports in its results header (for example '11 JOBS FOUND'). Then "
    "list every job posting visible in the search results on this page. For "
    "each job, give its title, location, full job URL, posted date text, "
    "requisition ID if shown, and any short description text shown. Only "
    "include real job postings, not navigation links or filters."
)

RETRY_PROMPT = (
    EXTRACT_PROMPT
    + " A previous pass found {found} jobs but the page reports {expected}. "
    "Go through the results list carefully from top to bottom and include "
    "every posting, including any that were missed."
)


def _with_search(careers_url, search_term):
    """Add a keyword to the Workday URL (?q=...) so results arrive
    pre-filtered. Workday career sites generally accept q as the search
    parameter; if a site ignores it, you just get the unfiltered list."""
    if not search_term:
        return careers_url
    parts = urlparse(careers_url)
    query = dict(parse_qsl(parts.query))
    query["q"] = search_term
    return urlunparse(parts._replace(query=urlencode(query)))


def _absolute(url, base):
    """Make the job link absolute and drop the search query (?q=...), so the
    stored apply_url is the clean canonical posting link."""
    if not url:
        return ""
    if not url.startswith("http"):
        b = urlparse(base)
        url = f"{b.scheme}://{b.netloc}{url if url.startswith('/') else '/' + url}"
    return urlunparse(urlparse(url)._replace(query="", fragment=""))


def _to_posting(job, company_name, careers_url):
    """Map one extracted job into Ezjob's NormalizedPosting dict."""
    location = (job.get("location") or "").strip()
    return {
        "title": (job.get("title") or "").strip(),
        "company": company_name,
        "location": location,
        "description": (job.get("summary") or "").strip(),
        "apply_url": _absolute(job.get("job_url"), careers_url),
        "source": SOURCE_NAME,
        "publisher": "workday",
        "raw": job,  # kept for debugging, same as the other sources
    }


async def _fetch(careers_url, company_name, search_term=None):
    from skyvern import Skyvern  # imported here so Ezjob still loads without it

    api_key = os.getenv("SKYVERN_API_KEY")
    if not api_key:
        raise RuntimeError("SKYVERN_API_KEY is not set (add it as a Codespaces secret).")

    skyvern = Skyvern(api_key=api_key)
    browser = await skyvern.launch_cloud_browser()
    try:
        page = await browser.get_working_page()
        print(f"[workday] watch live: https://app.skyvern.com/browser-session/{browser.browser_session_id}")
        url = _with_search(careers_url, search_term)
        await page.goto(url)

        result = await page.extract(EXTRACT_PROMPT, schema=JOB_LIST_SCHEMA) or {}
        reported = result.get("reported_total")
        postings = _merge({}, result.get("jobs") or [], company_name, careers_url)
        expected = _expected_count(reported)

        # Count check: the page tells us how many jobs it has. If we came up
        # short, spend one careful retry and merge both passes by job URL.
        attempts = 0
        while expected is not None and len(postings) < expected and attempts < MAX_RETRIES:
            attempts += 1
            print(f"[workday] count check: found {len(postings)} of {expected}, retrying ({attempts}/{MAX_RETRIES})")
            prompt = RETRY_PROMPT.format(found=len(postings), expected=expected)
            retry = await page.extract(prompt, schema=JOB_LIST_SCHEMA) or {}
            postings = _merge(postings, retry.get("jobs") or [], company_name, careers_url)
    finally:
        await browser.close()  # always free the cloud browser
        # Give Playwright's background process a moment to shut down while
        # the event loop is still open; otherwise Python prints a harmless
        # "Event loop is closed" traceback on exit.
        await asyncio.sleep(0.25)

    check = _check_summary(reported, expected, len(postings), attempts)
    print(f"[workday] {check}")
    return list(postings.values()), check


def _expected_count(reported):
    """How many jobs we should see on the one page we read, or None when the
    page did not report a usable total."""
    if not isinstance(reported, int) or reported < 0:
        return None
    return min(reported, WORKDAY_PAGE_SIZE)


def _merge(postings, jobs, company_name, careers_url):
    """Add extracted jobs to a dict keyed by clean apply_url, so repeat
    passes never create duplicates."""
    merged = dict(postings)
    for j in jobs:
        p = _to_posting(j, company_name, careers_url)
        if p["title"] and p["apply_url"] and p["apply_url"] not in merged:
            merged[p["apply_url"]] = p
    return merged


def _check_summary(reported, expected, found, attempts):
    if expected is None:
        return f"count check skipped: page total not found ({found} extracted)"
    retry_note = f" after {attempts} retry" if attempts == 1 else (f" after {attempts} retries" if attempts else "")
    if reported > WORKDAY_PAGE_SIZE:
        scope = f"page reports {reported} jobs; this source reads the first {expected}"
    else:
        scope = f"page reports {reported} jobs"
    status = "PASS" if found >= expected else "MISMATCH"
    return f"count check {status}: {found} of {expected} extracted{retry_note} ({scope})"


def fetch_workday_board(careers_url, company_name, search_term=None):
    """Sync entry point for Ezjob's nightly job. Returns a list of
    NormalizedPosting dicts. Never raises into the pipeline: a failed board
    is logged and skipped, same as a stale Greenhouse token. The count check
    result is logged; call fetch_workday_board_checked() to get it back."""
    postings, _ = fetch_workday_board_checked(careers_url, company_name, search_term)
    return postings


def fetch_workday_board_checked(careers_url, company_name, search_term=None):
    """Same as fetch_workday_board, but also returns the count check line."""
    try:
        return asyncio.run(_fetch(careers_url, company_name, search_term))
    except Exception as e:  # noqa: BLE001
        print(f"board workday/{company_name} failed: {e}")
        return [], "count check skipped: board failed"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    url, company = sys.argv[1], sys.argv[2]
    term = sys.argv[3] if len(sys.argv) > 3 else None
    found, check = fetch_workday_board_checked(url, company, term)
    print(f"\n{len(found)} postings from {company}")
    print(check)
    for p in found:
        print(f"  {p['title']} | {p['location']} | {p['apply_url']}")
