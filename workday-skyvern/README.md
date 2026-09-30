# Workday jobs via Skyvern

A small job source that uses [Skyvern](https://www.skyvern.com)'s AI browser to read Workday career sites, which have no public job feed, and return clean, typed job postings.

## Why I built it

I built Ezjob, a private job-search copilot for my wife and me. Every night it pulls jobs from Greenhouse, Lever, and Ashby through their public feeds, dedupes them into canonical records, and scores each one against our profiles.

Workday was the gap. Its career sites have no public feed, so the pipeline could detect them but not read them. That mattered most for my wife's search: operations and workforce-management roles at larger employers often live on Workday.

This module is the fix, pulled out of Ezjob into its own repo. It returns the same normalized posting format as Ezjob's other sources, so dedupe and scoring handle Workday jobs with no other changes.

## Demo

Quickbase's Workday site, filtered for "analyst": the page reports **11 jobs found**, and the script extracted **all 11** with correct titles, locations, and direct job links.

[Screen recording and terminal output go here]

## How it works

1. Adds the keyword to the URL as `?q=` so results arrive filtered, without spending AI actions typing into a search box.
2. Launches a Skyvern cloud browser and loads the page.
3. Calls `page.extract()` with a JSON schema (title, location, job URL, posted date, requisition ID) so the output is typed, not free-form.
4. Maps each result into a normalized posting and strips query strings from job links, so the same job always has the same URL.
5. Closes the browser in a `finally` block, and logs and skips on any failure so one bad board never breaks a nightly run.

## Run it

Needs Python 3.11 to 3.13 and a Skyvern API key (the free tier works).

```
pip install "skyvern[local]"
export SKYVERN_API_KEY=your_key
python workday_skyvern.py "https://quickbase.wd504.myworkdayjobs.com/External" "Quickbase" analyst
```

It prints a live-view link, then one line per posting.

## What I learned

- **Workday's search is loose.** `?q=analyst` matches full descriptions, so a Product Manager and a Data Engineer came back too. Extraction and relevance are separate jobs; in Ezjob the scorer ranks those down.
- **Credits shape the design.** On the free tier each browser action costs a credit, so this reads one listings page and skips detail pages. Next would be fetching full descriptions only for shortlisted jobs.
- **Use the cheapest reliable path first.** Where an ATS has a clean public feed, a direct API call is faster and free. Skyvern earns its place where no feed exists.
- **Environment gotchas:** my Codespace defaulted to Python 3.14 (Skyvern supports up to 3.13), driving the cloud page needs the `skyvern[local]` extra, and a local `email.py` in my package shadowed Python's built-in `email` module when running the script directly.

## Next steps

- Pagination with `page.agent.run_task()` for boards with more than one page of results.
- An optional detail-page pass for high-scoring jobs.
