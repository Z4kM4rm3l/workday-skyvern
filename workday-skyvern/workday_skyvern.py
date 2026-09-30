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

# Typed output so the AI's answer matches Ezjob's NormalizedPosting shape
# instead of whatever keys it would invent on its own.
JOB_LIST_SCHEMA = {
    "type": "object",
    "properties": {
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
    "required": ["jobs"],
}

EXTRACT_PROMPT = (
    "This is a Workday careers page. List every job posting visible in the "
    "search results on this page. For each job, give its title, location, "
    "full job URL, posted date text, requisition ID if shown, and any short "
    "description text shown. Only include real job postings, not navigation "
    "links or filters."
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
        result = await page.extract(EXTRACT_PROMPT, schema=JOB_LIST_SCHEMA)
    finally:
        await browser.close()  # always free the cloud browser
        # Give Playwright's background process a moment to shut down while
        # the event loop is still open; otherwise Python prints a harmless
        # "Event loop is closed" traceback on exit.
        await asyncio.sleep(0.25)

    jobs = (result or {}).get("jobs") or []
    postings = [_to_posting(j, company_name, careers_url) for j in jobs]
    return [p for p in postings if p["title"] and p["apply_url"]]


def fetch_workday_board(careers_url, company_name, search_term=None):
    """Sync entry point for Ezjob's nightly job. Returns a list of
    NormalizedPosting dicts. Never raises into the pipeline: a failed board
    is logged and skipped, same as a stale Greenhouse token."""
    try:
        return asyncio.run(_fetch(careers_url, company_name, search_term))
    except Exception as e:  # noqa: BLE001
        print(f"board workday/{company_name} failed: {e}")
        return []


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    url, company = sys.argv[1], sys.argv[2]
    term = sys.argv[3] if len(sys.argv) > 3 else None
    found = fetch_workday_board(url, company, term)
    print(f"\n{len(found)} postings from {company}")
    for p in found:
        print(f"  {p['title']} | {p['location']} | {p['apply_url']}")
