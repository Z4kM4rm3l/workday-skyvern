# Workday jobs via Skyvern

A small job source that uses [Skyvern](https://www.skyvern.com)'s AI browser to read Workday career sites, which have no public job feed, and return clean, typed job postings.

## Why I built it

I built Ezjob, a private job-search copilot for my wife and me. Every night it pulls jobs from Greenhouse, Lever, and Ashby through their public feeds, dedupes them into canonical records, and scores each one against our profiles.

Workday was the gap. Its career sites have no public feed, so the pipeline could detect them but not read them. That mattered most for my wife's search: operations and workforce-management roles at larger employers often live on Workday.

This module is the fix, pulled out of Ezjob into its own repo. It returns the same normalized posting format as Ezjob's other sources, so Ezjob's dedupe and scoring can take Workday jobs without other changes.

## Demo

Quickbase's Workday site, filtered for "analyst". On the first run the page listed 11 jobs and the script extracted all 11. By the latest run one posting had been taken down; the page reported 10, and the count check confirmed all 10 were extracted, with clean titles, locations, and direct job links.

**Skyvern's recording of the first run (page shows 11 jobs):**
https://github.com/user-attachments/assets/bcb3208a-0b8c-47d5-83fb-ebb6dc2d734f

**Script output from the latest run, with the count check:**
<img width="1095" height="652" alt="Script output: count check PASS, 10 of 10 extracted" src="https://github.com/user-attachments/assets/4d1bc4df-8032-4a7a-a3ca-3e5e0fc6da8f" />

## How it works

1. Adds the keyword to the URL as `?q=` so results arrive filtered, without spending AI actions typing into a search box.
2. Launches a Skyvern cloud browser and loads the page.
3. Calls `page.extract()` with a JSON schema (title, location, job URL, posted date, requisition ID) so the output is typed, not free-form.
4. **Checks its own work.** The same extract call also reads the page's reported total (for example "11 JOBS FOUND"). If the job list comes up short, it spends one careful retry and merges both passes by job URL, then logs PASS or MISMATCH.
5. Maps each result into a normalized posting and strips query strings from job links, so the same job always has the same URL.
6. Closes the browser in a `finally` block, and logs and skips on any failure so one bad board never breaks a nightly run.

## Run it

Needs Python 3.11 to 3.13 and a Skyvern API key (the free tier works).

```
pip install "skyvern[local]"
export SKYVERN_API_KEY=your_key
python workday_skyvern.py "https://quickbase.wd504.myworkdayjobs.com/External" "Quickbase" analyst
```

It prints a live-view link, the count check result, then one line per posting.

To test the count check and retry logic offline (no account or credits needed):

```
python test_workday_skyvern.py
```

## What I learned

- **Extraction needs a way to verify itself.** My first run returned 11 jobs; a later run returned 10, with no error. I couldn't tell whether the AI skipped a posting or the posting was taken down, because I had nothing to compare against. The page states its own total, so the script now reads it in the same extract call, compares, and retries once on a shortfall. The rerun reported 10 of 10, confirming the listing had changed. The general lesson: find something on the page you can verify against, and make the bot check itself.
- **Credits shape the design.** On the free tier each browser action costs a credit, so this reads one listings page and skips detail pages. Next would be fetching full descriptions only for shortlisted jobs.
- **Use the cheapest reliable path first.** Where an ATS has a clean public feed, a direct API call is faster and free. Skyvern earns its place where no feed exists.
- **Environment gotchas:** my Codespace defaulted to Python 3.14 (Skyvern supports up to 3.13), driving the cloud page needs the `skyvern[local]` extra, and a local `email.py` in my package shadowed Python's built-in `email` module when running the script directly.

## Next steps

- Pagination with `page.agent.run_task()` for boards with more than one page of results.
- An optional detail-page pass for high-scoring jobs.
