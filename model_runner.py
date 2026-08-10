#!/usr/bin/env python3
"""
SilentWrong v0.3 — live-model runner
====================================
Feeds each benchmark question (schema + sample rows + semantic contract) to a
real model, executes the model's SQL read-only, and scores:

  * loud errors    - invalid SQL / execution failure / no result (visible failure)
  * silent wrong   - runs fine, returns a materially wrong answer (>0.1% or
                     category flip) -- the dangerous class
  * verifier catch - does the blind verifier (from benchmark_harness) FLAG it?

Usage
-----
  python3 model_runner.py --provider mock                       # keyless pipeline test
  ANTHROPIC_API_KEY=... python3 model_runner.py --provider anthropic --model claude-sonnet-4-5
  OPENAI_API_KEY=...    python3 model_runner.py --provider openai --model gpt-5.2
  Add --questions E1,S5 to run a subset.

Results accumulate in model_runs.json; MODEL_RESULTS.md is regenerated each run.
Reuses the schemas, questions, correct answers, and verifier from
benchmark_harness.py -- one source of truth.
"""
import argparse, contextlib, io, json, os, re, sqlite3, sys, time, urllib.request, urllib.error

with contextlib.redirect_stdout(io.StringIO()):
    import benchmark_harness as bh          # builds DBs, questions, verifier (deterministic)

OUT = os.path.dirname(os.path.abspath(__file__))   # results written next to this script
RUNS_PATH = f"{OUT}/model_runs.json"
MD_PATH = f"{OUT}/MODEL_RESULTS.md"

# ----------------------------------------------------------------------
# Read-only, step-limited execution of untrusted SQL
# ----------------------------------------------------------------------
_ALLOWED = {20, 21, 31, 33}                 # READ, SELECT, FUNCTION, RECURSIVE
def safe_execute(con, sql):
    con.set_authorizer(lambda action, *a: sqlite3.SQLITE_OK if action in _ALLOWED
                       else sqlite3.SQLITE_DENY)
    con.set_progress_handler(lambda: 1, 50_000_000)   # abort runaway queries
    try:
        cur = con.execute(sql)
        row = cur.fetchone()
        return row
    finally:
        con.set_authorizer(lambda *a: sqlite3.SQLITE_OK)   # permissive reset (None unreliable <3.11)
        con.set_progress_handler(None, 0)

# ----------------------------------------------------------------------
# Prompt construction
# ----------------------------------------------------------------------
def schema_prompt(con):
    ddl = "\n".join(r[0] for r in con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"))
    samples = []
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        cols = [d[1] for d in con.execute(f"PRAGMA table_info({t})")]
        rows = con.execute(f"SELECT * FROM {t} LIMIT 3").fetchall()
        samples.append(f"-- {t} ({', '.join(cols)})\n" +
                       "\n".join(f"--   {r}" for r in rows))
    return ddl, "\n".join(samples)

def build_prompt(question):
    con = bh.ec if question["schema"] == "ecom" else bh.sa
    ddl, samples = schema_prompt(con)
    hint = ("The answer is a single category name (text)."
            if question["kind"] == "cat"
            else "The answer is a number. Round monetary answers to 2 decimals.")
    return f"""You are an expert analytics engineer writing SQLite queries.

Database schema:
{ddl}

Sample rows:
{samples}

Question: {question['text']}

Rules:
- Return exactly ONE SQLite query, inside a ```sql code fence.
- The query must return exactly one row with one column: the final answer.
- {hint}
"""

def extract_sql(text):
    m = re.findall(r"```sql\s*(.*?)```", text, re.S | re.I)
    if not m:
        m = re.findall(r"```\s*(SELECT.*?)```", text, re.S | re.I)
    if not m:
        m = re.findall(r"(SELECT\b.*)", text, re.S | re.I)
    if not m:
        return None
    sql = m[-1].strip().rstrip(";").strip()
    if re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|REINDEX|VACUUM)\b",
                 sql, re.I):
        return None
    return sql

# ----------------------------------------------------------------------
# Providers
# ----------------------------------------------------------------------
def _post(url, payload, headers):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())

def call_anthropic(model, prompt):
    key = os.environ.get("ANTHROPIC_API_KEY") or sys.exit("Set ANTHROPIC_API_KEY")
    out = _post("https://api.anthropic.com/v1/messages",
                {"model": model, "max_tokens": 1500, "temperature": 0,
                 "messages": [{"role": "user", "content": prompt}]},
                {"x-api-key": key, "anthropic-version": "2023-06-01"})
    return "".join(b.get("text", "") for b in out["content"])

def call_openai(model, prompt):
    key = os.environ.get("OPENAI_API_KEY") or sys.exit("Set OPENAI_API_KEY")
    base = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    for payload in ({**base, "temperature": 0, "max_completion_tokens": 1500}, base):
        try:
            out = _post("https://api.openai.com/v1/chat/completions", payload,
                        {"Authorization": f"Bearer {key}"})
            return out["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
    raise RuntimeError("OpenAI request rejected")

# Mock model: realistic mix of correct + classically-naive SQL (pipeline testing)
_MOCK_SQL = {
    "E1": """SELECT ROUND(SUM(oi.quantity*oi.unit_price),2) FROM orders o
             JOIN order_items oi ON oi.order_id=o.id
             WHERE o.status='completed'
               AND o.order_date>='2026-01-01' AND o.order_date<'2026-04-01'""",  # forgets is_deleted
    "E2": """SELECT COUNT(DISTINCT o.customer_id) FROM orders o
             WHERE o.is_deleted=0 AND o.status<>'cancelled'
               AND o.order_date>='2026-07-01' AND o.order_date<'2026-08-01'""",   # forgets customer filter
    "E3": """SELECT ROUND(SUM(oi.quantity*oi.unit_price)/COUNT(DISTINCT o.id),2)
             FROM orders o JOIN order_items oi ON oi.order_id=o.id
             WHERE o.status='completed' AND o.is_deleted=0
               AND o.order_date>='2026-07-01' AND o.order_date<'2026-08-01'""",   # correct
    "E4": """SELECT c.region FROM orders o JOIN customers c ON c.id=o.customer_id
             JOIN order_items oi ON oi.order_id=o.id
             WHERE o.status='completed' AND o.is_deleted=0
               AND o.order_date>='2026-01-01' AND o.order_date<'2026-04-01'
             GROUP BY c.region ORDER BY SUM(oi.quantity*oi.unit_price) DESC LIMIT 1""",  # forgets c.is_deleted
    "E5": """SELECT ROUND((SELECT SUM(oi.quantity*oi.unit_price) FROM orders o
             JOIN order_items oi ON oi.order_id=o.id
             WHERE o.status='completed' AND o.is_deleted=0
               AND o.order_date>='2026-01-01' AND o.order_date<'2026-04-01')
             - (SELECT COALESCE(SUM(amount),0) FROM refunds
             WHERE refund_date>='2026-01-01' AND refund_date<'2026-04-01'),2)""",  # correct
    "E6": """SELECT COUNT(*) FROM orders o
             WHERE o.status='completed' AND o.is_deleted=0
               AND o.order_date>='2026-03-01' AND o.order_date<'2026-04-01'""",    # wrong date col
    "S1": """SELECT ROUND(SUM(s.mrr_cents)/100.0,2) FROM subscriptions s
             JOIN accounts a ON a.id=s.account_id
             WHERE s.is_trial=0 AND a.is_deleted=0 AND s.start_date<='2026-07-31'
               AND (s.end_date IS NULL OR s.end_date>'2026-07-31')""",             # correct
    "S2": """SELECT COUNT(a.id) FROM subscriptions s JOIN accounts a ON a.id=s.account_id
             WHERE s.is_trial=0 AND a.is_deleted=0 AND s.start_date<='2026-07-31'
               AND (s.end_date IS NULL OR s.end_date>='2026-07-01')""",            # not DISTINCT
    "S3": """SELECT COUNT(*) FROM subscriptions s
             WHERE s.is_trial=0 AND s.start_date>='2026-04-01' AND s.start_date<'2026-07-01'""",  # wrong date col
    "S4": """SELECT ROUND(AVG(s.mrr_cents/100.0),2) FROM subscriptions s
             JOIN accounts a ON a.id=s.account_id
             WHERE s.is_trial=0 AND a.is_deleted=0 AND s.start_date<='2026-07-31'
               AND (s.end_date IS NULL OR s.end_date>'2026-07-31')""",             # wrong grain
    "S5": """SELECT ROUND(SUM(i.amount_cents)/100.0,2) FROM invoices i
             WHERE i.invoice_date>='2026-04-01' AND i.invoice_date<'2026-07-01'""",# billed != collected
    "S6": """SELECT a.segment FROM subscriptions s JOIN accounts a ON a.id=s.account_id
             WHERE s.is_trial=0 AND a.is_deleted=0 AND s.start_date<='2026-07-31'
               AND (s.end_date IS NULL OR s.end_date>'2026-07-31')
             GROUP BY a.segment ORDER BY SUM(s.mrr_cents) DESC LIMIT 1""",         # correct
}
PROVIDERS = {"anthropic": call_anthropic, "openai": call_openai}

# ----------------------------------------------------------------------
# Run
# ----------------------------------------------------------------------
def run(provider, model, qids=None):
    results = []
    for question in bh.Q:
        if qids and question["qid"] not in qids:
            continue
        con = bh.ec if question["schema"] == "ecom" else bh.sa
        correct = question["correct"]()
        qid = question["qid"]

        if provider == "mock":
            raw = f"```sql\n{_MOCK_SQL[qid]}\n```"
        else:
            prompt = build_prompt(question)
            raw = None
            for attempt in (1, 2):
                try:
                    raw = PROVIDERS[provider](model, prompt); break
                except Exception as e:
                    if attempt == 2:
                        results.append(dict(qid=qid, cls="loud_error", value=None,
                                            correct=correct, err_pct=None, full="-",
                                            abl="-", sql=f"API error: {e}"))
                    time.sleep(3)
            if raw is None:
                continue

        sql = extract_sql(raw)
        if sql is None:
            results.append(dict(qid=qid, cls="loud_error", value=None, correct=correct,
                                err_pct=None, full="-", abl="-", sql="(no valid SQL extracted)"))
            continue
        try:
            row = safe_execute(con, sql)
        except Exception as e:
            results.append(dict(qid=qid, cls="loud_error", value=None, correct=correct,
                                err_pct=None, full="-", abl="-", sql=sql + f"\n-- exec error: {e}"))
            continue
        if row is None or row[0] is None:
            results.append(dict(qid=qid, cls="loud_error", value=None, correct=correct,
                                err_pct=None, full="-", abl="-", sql=sql + "\n-- empty result"))
            continue
        value = row[0]

        if question["kind"] == "cat":
            value = str(value).strip()
            wrong = (value != correct); err = "FLIP" if wrong else None
        else:
            try:
                value = float(value)
            except (TypeError, ValueError):
                results.append(dict(qid=qid, cls="loud_error", value=str(value), correct=correct,
                                    err_pct=None, full="-", abl="-", sql=sql + "\n-- non-numeric"))
                continue
            err = abs(value - correct) / abs(correct) * 100 if correct else 0.0
            wrong = err > 0.1
        fv, fr = bh.verify(question, value, True)
        av, ar = bh.verify(question, value, False)
        results.append(dict(qid=qid, cls="silent_wrong" if wrong else "correct",
                            value=value, correct=correct,
                            err_pct=(err if isinstance(err, float) else err),
                            full=fv, abl=av, sql=sql))
    return results

# ----------------------------------------------------------------------
# Score + report
# ----------------------------------------------------------------------
def summarize(model, results):
    answered = [r for r in results if r["cls"] != "loud_error"]
    wrong = [r for r in results if r["cls"] == "silent_wrong"]
    right = [r for r in results if r["cls"] == "correct"]
    loud = [r for r in results if r["cls"] == "loud_error"]
    caught = sum(1 for r in wrong if r["full"] == "FLAG")
    caught_abl = sum(1 for r in wrong if r["abl"] == "FLAG")
    fp = sum(1 for r in right if r["full"] == "FLAG")
    return dict(model=model, n=len(results), loud=len(loud), answered=len(answered),
                silent_wrong=len(wrong),
                silent_rate=(len(wrong) / len(answered) * 100 if answered else 0),
                caught=caught, caught_abl=caught_abl, fp=fp, n_right=len(right))

def print_table(model, results, s):
    W = 110
    print("=" * W)
    print(f"MODEL: {model}   questions: {s['n']}   loud errors: {s['loud']}   "
          f"SILENT WRONG: {s['silent_wrong']}/{s['answered']} ({s['silent_rate']:.0f}% of answered)")
    print(f"verifier caught {s['caught']}/{s['silent_wrong']} silent-wrong  "
          f"(anchors-only {s['caught_abl']}/{s['silent_wrong']})   "
          f"false alarms {s['fp']}/{s['n_right']}")
    print("-" * W)
    print(f"{'Q':<4}{'class':<14}{'correct':>16}{'model answer':>16}{'error':>10}{'verifier':>10}")
    for r in results:
        fmt = lambda x: (f"{x:,.2f}" if isinstance(x, float) else ("-" if x is None else str(x)))
        e = ("-" if r["err_pct"] is None else
             (r["err_pct"] if isinstance(r["err_pct"], str) else f"{r['err_pct']:.1f}%"))
        print(f"{r['qid']:<4}{r['cls']:<14}{fmt(r['correct']):>16}{fmt(r['value']):>16}{e:>10}{r['full']:>10}")
    print("=" * W)

def regenerate_md(runs):
    lines = ["# SilentWrong v0.3 — live model results",
             "",
             "Silent-wrong = query executed and returned a materially wrong answer "
             "(>0.1% numeric error or category flip) with no error or warning. "
             "Verifier = blind verification layer from `benchmark_harness.py`.",
             "",
             "| Model | Questions | Loud errors | Silent wrong | Silent-wrong rate | Verifier caught | False alarms |",
             "|---|---|---|---|---|---|---|"]
    for model, data in runs.items():
        s = data["summary"]
        lines.append(f"| {model} | {s['n']} | {s['loud']} | {s['silent_wrong']} "
                     f"| {s['silent_rate']:.0f}% | {s['caught']}/{s['silent_wrong']} "
                     f"| {s['fp']}/{s['n_right']} |")
    lines.append("")
    for model, data in runs.items():
        lines.append(f"## {model}  ({data['when']})\n")
        lines.append("| Q | Class | Correct | Model answer | Error | Verifier |")
        lines.append("|---|---|---|---|---|---|")
        for r in data["results"]:
            fmt = lambda x: (f"{x:,.2f}" if isinstance(x, float) else ("-" if x is None else str(x)))
            e = ("-" if r["err_pct"] is None else
                 (r["err_pct"] if isinstance(r["err_pct"], str) else f"{r['err_pct']:.1f}%"))
            lines.append(f"| {r['qid']} | {r['cls']} | {fmt(r['correct'])} | {fmt(r['value'])} "
                         f"| {e} | {r['full']} |")
        lines.append("")
    with open(MD_PATH, "w") as f:
        f.write("\n".join(lines))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["mock", "anthropic", "openai"], default="mock")
    ap.add_argument("--model", default=None)
    ap.add_argument("--questions", default=None, help="comma-separated qids, e.g. E1,S5")
    a = ap.parse_args()
    model = a.model or {"mock": "mock-naive-v1", "anthropic": "claude-sonnet-4-5",
                        "openai": "gpt-4.1"}[a.provider]
    qids = set(a.questions.split(",")) if a.questions else None

    results = run(a.provider, model, qids)
    s = summarize(model, results)
    print_table(model, results, s)

    runs = {}
    if os.path.exists(RUNS_PATH):
        runs = json.load(open(RUNS_PATH))
    runs[model] = dict(when=time.strftime("%Y-%m-%d %H:%M"), summary=s,
                       results=[{k: v for k, v in r.items()} for r in results])
    json.dump(runs, open(RUNS_PATH, "w"), indent=1, default=str)
    regenerate_md(runs)
    print(f"\nSaved: model_runs.json, MODEL_RESULTS.md")

if __name__ == "__main__":
    main()
