"""
Offline tests for the Workday source: no Skyvern account, network, or
credits needed. A fake Skyvern client plays back scripted extract results.

Run: python test_workday_skyvern.py
"""
import sys, types, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["SKYVERN_API_KEY"] = "x"
BASE = "https://quickbase.wd504.myworkdayjobs.com/en-US/External/job/"
ALL = [{"title": f"Job {i}", "location": "Remote - US", "job_url": BASE + f"J{i}?q=analyst"} for i in range(11)]

def make(scenarios):
    calls = {"n": 0}
    class Page:
        async def goto(self, u): pass
        async def extract(self, prompt, schema=None):
            r = scenarios[calls["n"]]; calls["n"] += 1; return r
    class Browser:
        browser_session_id = "pbs_test"
        async def get_working_page(self): return Page()
        async def close(self): pass
    class Skyvern:
        def __init__(self, api_key): pass
        async def launch_cloud_browser(self): return Browser()
    sys.modules["skyvern"] = types.SimpleNamespace(Skyvern=Skyvern)
    return calls

import workday_skyvern as w

def run(name, scenarios):
    calls = make(scenarios)
    posts, check = w.fetch_workday_board_checked("https://quickbase.wd504.myworkdayjobs.com/External", "Quickbase", "analyst")
    print(f"{name}: {len(posts)} postings, {calls['n']} extract calls | {check}")
    assert all("?" not in p["apply_url"] for p in posts)
    return posts, calls

# 1. first pass perfect: no retry
p, c = run("perfect", [{"reported_total": 11, "jobs": ALL}]); assert len(p)==11 and c["n"]==1
# 2. first pass misses one, retry finds it (with overlap): merged, no dupes
p, c = run("miss+fix", [{"reported_total": 11, "jobs": ALL[:10]}, {"reported_total": 11, "jobs": ALL[5:]}]); assert len(p)==11 and c["n"]==2
# 3. retry still short: stops after one retry, reports MISMATCH
p, c = run("still short", [{"reported_total": 11, "jobs": ALL[:9]}, {"reported_total": 11, "jobs": ALL[:9]}]); assert len(p)==9 and c["n"]==2
# 4. big board: 45 reported, 20 on page = pass
BIG = [{"title": f"B{i}", "location": "x", "job_url": BASE+f"B{i}"} for i in range(20)]
p, c = run("big board", [{"reported_total": 45, "jobs": BIG}]); assert len(p)==20 and c["n"]==1
# 5. no total shown: skipped, no retry
p, c = run("no total", [{"reported_total": -1, "jobs": ALL[:3]}]); assert c["n"]==1
# 6. extract raises: board fails gracefully
calls = make([])  # extract will IndexError
posts, check = w.fetch_workday_board_checked("u", "Quickbase")
print("failure:", len(posts), check); assert posts == []
print("ALL TESTS PASSED")
