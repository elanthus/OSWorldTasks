"""D4.11 lifecycle driver: browser-driven, phase-based, records what it observed."""
from __future__ import annotations
import json, os, re, sys, time, urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from playwright.sync_api import sync_playwright, expect

BASE = os.environ["D411_PLATFORM_URL"].rstrip("/")
MLFLOW = os.environ["D411_MLFLOW_URL"].rstrip("/")
OUT = Path(os.environ["D411_OUT"])
SHOTS = OUT / "screenshots"
STATE = OUT / "driver-state.json"
LOG = OUT / "driver-log.jsonl"
SHOTS.mkdir(parents=True, exist_ok=True)

def now(): return datetime.now(UTC).isoformat()
def log(**record):
    record = {"at_utc": now(), **record}
    with LOG.open("a") as f: f.write(json.dumps(record, sort_keys=True) + "\n")
    print(json.dumps(record, sort_keys=True))
def state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}
def save(**kw):
    s = state(); s.update(kw); STATE.write_text(json.dumps(s, indent=2, sort_keys=True) + "\n")
def shot(page, name, full=True):
    path = SHOTS / name
    page.screenshot(path=str(path), full_page=full)
    log(screenshot=name, url=page.url.replace(BASE, "<platform>").replace(MLFLOW, "<mlflow>"))
def csrf(page):
    v = page.locator('meta[name="csrf-token"]').get_attribute("content"); assert v; return v
def policy(page):
    r = page.context.request.get(f"{BASE}/api/v1/policy"); body = r.json() if r.status == 200 else None
    log(api="/api/v1/policy", status=r.status, body=body); return r.status, body

def submit(page, prompt_version, model):
    page.goto(BASE)
    page.select_option('select[name="prompt_version"]', prompt_version)
    page.select_option('select[name="model"]', model)
    page.get_by_role("button", name="Submit fixed evaluation").click()
    page.wait_for_url(f"{BASE}/submissions/submission-*")
    sid = page.url.rsplit("/", 1)[-1]
    status = page.locator("main").inner_text()
    log(submitted=sid, model=model, prompt_version=prompt_version,
        status_text=re.search(r"Status\s+(\w+)", status).group(1) if re.search(r"Status\s+(\w+)", status) else None)
    return sid

def wait_candidate(page, model, wanted, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        page.goto(f"{BASE}/runs")
        row = page.locator("tbody tr", has_text=model)
        if row.count() == 1 and wanted in row.inner_text():
            href = row.locator("a").first.get_attribute("href")
            cid = href.removeprefix("/candidates/")
            log(candidate=cid, model=model, state=wanted); return cid
        page.wait_for_timeout(1000)
    raise SystemExit(f"candidate for {model} never reached {wanted}")

def mlflow_run_id(model):
    exp = json.load(urllib.request.urlopen(f"{MLFLOW}/api/2.0/mlflow/experiments/get-by-name?experiment_name=pixelgym-grounding"))["experiment"]
    req = urllib.request.Request(f"{MLFLOW}/api/2.0/mlflow/runs/search", data=json.dumps({"experiment_ids": [exp["experiment_id"]], "filter": f"params.model = '{model}'", "max_results": 5}).encode(), headers={"Content-Type": "application/json"})
    runs = json.load(urllib.request.urlopen(req))["runs"]
    assert len(runs) == 1, f"expected one MLflow run for {model}, saw {len(runs)}"
    return exp["experiment_id"], runs[0]["info"]["run_id"]

def approve(page, cid, reason):
    page.goto(f"{BASE}/candidates/{cid}")
    expect(page.locator("main")).to_contain_text("Eligible")
    page.locator('form[action$="/approve"] textarea[name="reason"]').fill(reason)
    page.locator('form[action$="/approve"] button').click()
    expect(page.locator("main")).to_contain_text("Approved")
    log(approved=cid, reason=reason)

def deploy(page, cid, reason):
    page.goto(f"{BASE}/candidates/{cid}")
    expect(page.locator("main")).to_contain_text("Approved")
    page.locator('form[action$="/deploy"] textarea[name="reason"]').fill(reason)
    page.locator('form[action$="/deploy"] button').click()
    page.wait_for_url(f"{BASE}/deployment")
    log(deployed=cid, reason=reason)
    return policy(page)[1]

def phase1(page):
    page.goto(f"{BASE}/runs")
    expect(page.locator("main")).to_contain_text("No evaluated candidates")
    shot(page, "01-empty-run-history.png")
    st, body = policy(page)
    save(initial_policy_status=st)
    sid_a = submit(page, "1", "day3-replay-baseline-v1")
    shot(page, "02-submission-a-status.png")
    cid_a = wait_candidate(page, "day3-replay-baseline-v1", "GateFailed")
    page.goto(f"{BASE}/candidates/{cid_a}")
    expect(page.get_by_text("Approval unavailable")).to_be_visible()
    approve_forms = page.locator('form[action$="/approve"]').count()
    log(candidate_a_ui_approve_forms=approve_forms)
    shot(page, "03-candidate-a-gate-blocked.png")
    sid_b = submit(page, "2", "day3-replay-revised-v2")
    cid_b = wait_candidate(page, "day3-replay-revised-v2", "Eligible")
    page.goto(f"{BASE}/compare?" + urlencode([("candidate", cid_a), ("candidate", cid_b)]))
    expect(page.get_by_text("COMPATIBLE", exact=True)).to_be_visible()
    shot(page, "04-compatible-comparison.png")
    save(submission_a=sid_a, submission_b=sid_b, candidate_a=cid_a, candidate_b=cid_b,
         candidate_a_ui_approve_forms=approve_forms)
    phase1b(page)

def phase1b(page):
    """Resume after screenshot 04: MLflow lineage, blocked direct deploy of unapproved B."""
    s = state()
    if "candidate_a" not in s:  # recover ids from the driver log of the interrupted phase 1
        for line in LOG.read_text().splitlines():
            r = json.loads(line)
            if "submitted" in r: s[{"day3-replay-baseline-v1": "submission_a", "day3-replay-revised-v2": "submission_b"}[r["model"]]] = r["submitted"]
            if "candidate" in r: s[{"day3-replay-baseline-v1": "candidate_a", "day3-replay-revised-v2": "candidate_b"}[r["model"]]] = r["candidate"]
            if "candidate_a_ui_approve_forms" in r: s["candidate_a_ui_approve_forms"] = r["candidate_a_ui_approve_forms"]
        save(**s)
    cid_a, cid_b, sid_a, sid_b, approve_forms = s["candidate_a"], s["candidate_b"], s["submission_a"], s["submission_b"], s["candidate_a_ui_approve_forms"]
    exp_id, run_b = mlflow_run_id("day3-replay-revised-v2")
    _, run_a = mlflow_run_id("day3-replay-baseline-v1")
    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto(f"{MLFLOW}/#/experiments/{exp_id}/runs/{run_b}")
    page.wait_for_load_state("load"); page.wait_for_timeout(6000)
    shot(page, "05-mlflow-candidate-b-lineage.png", full=False)
    page.set_viewport_size({"width": 1265, "height": 900})
    # direct deploy of unapproved B must be refused
    page.goto(f"{BASE}/deployment")
    gen = re.search(r"generation ([0-9]+)", page.locator("main").inner_text())
    page.goto(f"{BASE}/candidates/{cid_b}")
    r = page.context.request.post(f"{BASE}/candidates/{cid_b}/deploy",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=urlencode({"csrf_token": csrf(page), "reason": "approval must come first",
                        "expected_deployment_id": "", "expected_generation": gen.group(1) if gen else "0"}),
        max_redirects=0)
    log(blocked_deploy_of_unapproved_b_status=r.status)
    save(submission_a=sid_a, submission_b=sid_b, candidate_a=cid_a, candidate_b=cid_b,
         mlflow_run_a=run_a, mlflow_run_b=run_b, mlflow_experiment_id=exp_id,
         blocked_deploy_of_unapproved_b_status=r.status, candidate_a_ui_approve_forms=approve_forms)

def phase2(page):
    s = state()
    seed = wait_candidate(page, "day3-replay-revised-rollback-seed-v1", "Eligible")
    approve(page, seed, "D4.11 rehearsal: approve distinct rollback seed so an earlier approved exact policy exists")
    seed_policy = deploy(page, seed, "D4.11 rehearsal: deploy rollback seed as generation 1")
    page.goto(f"{BASE}/deployment"); shot(page, "06-rollback-seed-deployed.png")
    page.goto(f"{BASE}/candidates/{s['candidate_b']}")
    expect(page.locator("main")).to_contain_text("Eligible")
    shot(page, "07-candidate-b-eligible.png")
    approve(page, s["candidate_b"], "D4.11 rehearsal: candidate B passed every gate; reviewer approval recorded with reason")
    page.goto(f"{BASE}/candidates/{s['candidate_b']}"); shot(page, "08-candidate-b-approved.png")
    st, still_seed = policy(page)
    assert still_seed["policy_id"] == seed_policy["policy_id"], "approval must not change the active policy"
    b_policy = deploy(page, s["candidate_b"], "D4.11 rehearsal: deploy approved candidate B as generation 2")
    page.goto(f"{BASE}/deployment"); shot(page, "09-candidate-b-deployed.png")
    save(candidate_seed=seed, seed_policy=seed_policy, b_policy=b_policy,
         policy_after_b_approval_unchanged=(still_seed["policy_id"] == seed_policy["policy_id"]))

def phase3(page):
    s = state()
    page.goto(f"{BASE}/deployment")
    page.locator('form[action="/rollback"] textarea[name="reason"]').fill("D4.11 rehearsal: rollback to the immediately previous approved exact policy (rollback seed)")
    page.locator('form[action="/rollback"] button').click()
    page.wait_for_url(f"{BASE}/deployment")
    st, rolled = policy(page)
    shot(page, "10-rollback-restored-seed.png")
    shot(page, "11-rollback-restored-seed-viewport.png", full=False)
    save(rolled_back_policy=rolled,
         rollback_restored_seed_policy=(rolled["policy_id"] == s["seed_policy"]["policy_id"]),
         rollback_new_deployment=(rolled["deployment_id"] not in {s["seed_policy"]["deployment_id"], s["b_policy"]["deployment_id"]}))

def phase4(page):
    # Submission status / cancellation UI, isolated from the canonical A/B lifecycle.
    # prompt v2 + baseline model: a request digest not yet used, so a new submission is created
    sid = submit(page, "2", "day3-replay-baseline-v1")
    expect(page.locator("main")).to_contain_text("Cancel experiment")
    shot(page, "12-submission-c-status-cancellable.png")
    page.locator('form[action$="/cancel"] textarea[name="reason"]').fill("D4.11 rehearsal: exercise cancellation UI; not part of the A/B lifecycle")
    page.locator('form[action$="/cancel"] button').click()
    page.wait_for_url(f"{BASE}/submissions/{sid}")
    expect(page.locator("main")).to_contain_text("Cancelled")
    expect(page.locator("main")).to_contain_text("Cancellation is no longer available")
    shot(page, "13-submission-c-cancelled.png")
    st, after = policy(page)
    page.goto(f"{BASE}/deployment/audit"); shot(page, "14-audit-history.png")
    page.goto(f"{BASE}/runs"); shot(page, "15-run-history-final.png")
    save(submission_c=sid, policy_after_cancellation=after)

if __name__ == "__main__":
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True); page = b.new_page(viewport={"width": 1265, "height": 900})
        try: {"1": phase1, "1b": phase1b, "2": phase2, "3": phase3, "4": phase4}[sys.argv[1]](page)
        finally: b.close()
