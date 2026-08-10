#!/usr/bin/env python3
"""
SilentWrong benchmark v0.2
==========================
Measures how often "AI answers over business data" are CONFIDENTLY WRONG, and how
much of that a blind verification layer catches.

Design
------
* Two realistic schemas: e-commerce (soft-deletes, voided orders, refunds, fan-out
  traps) and SaaS billing (cents-vs-dollars, trials, multi-sub accounts,
  billed-vs-collected, churn dates).
* Each question has a precise semantic CONTRACT, one correct query, and several
  MUTATED queries reproducing documented text-to-SQL failure modes.
* A VERIFIER that never sees the correct answer issues TRUST/FLAG using:
    V1 dual formulation  - an independent query strategy derived from the contract
    V2 governed anchors  - control totals maintained independently at write time
                           (like a finance ledger / governed metric store)
    V3 invariants        - bounds, populations, partitions, non-negativity
* ABLATION: verifier without V1 (anchors+invariants only) shows which failure
  modes require contract-driven reformulation.

Honesty rules
-------------
* Mutations that do not materially change the value (<0.1% and no category flip)
  are excluded from the "wrong" tally and reported as immaterial.
* False alarms are measured by running the verifier on every CORRECT answer.
* Deterministic: seed=7.
"""
import sqlite3, random, datetime as dt, csv, collections, os

random.seed(7)
OUT = os.path.dirname(os.path.abspath(__file__))   # results written next to this script

def close(a, b):
    return abs(a - b) <= max(0.05, 0.005 * max(abs(a), abs(b)))

# =====================================================================
# SCHEMA 1: E-COMMERCE
# =====================================================================
ec = sqlite3.connect(":memory:")
ec.executescript("""
CREATE TABLE customers(id INTEGER PRIMARY KEY, region TEXT, is_deleted INT);
CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INT, order_date TEXT,
                    ship_date TEXT, status TEXT, is_deleted INT);
CREATE TABLE order_items(id INTEGER PRIMARY KEY, order_id INT, quantity INT, unit_price REAL);
CREATE TABLE refunds(id INTEGER PRIMARY KEY, order_id INT, amount REAL, refund_date TEXT);
""")
regions = ["NA", "EMEA", "APAC", "LATAM"]
cust_rows = []
for i in range(1, 201):
    deleted = 1 if random.random() < 0.06 else 0
    cust_rows.append((i, random.choice(regions), deleted))
cust_rows[199] = (200, "LATAM", 1)          # soft-deleted LATAM "whale"
ec.executemany("INSERT INTO customers VALUES(?,?,?)", cust_rows)
cust_deleted = {c[0]: c[2] for c in cust_rows}
cust_region  = {c[0]: c[1] for c in cust_rows}

statuses = ["completed"]*3 + ["cancelled", "refunded", "pending"]
orders, items, refunds = [], [], []
oid = iid = rid = 1
def add_order(cust, odate, status, voided, n_items, whale=False):
    global oid, iid
    ship = odate + dt.timedelta(days=random.randint(1, 10))
    orders.append((oid, cust, odate.isoformat(), ship.isoformat(), status, voided))
    tot = 0.0
    for _ in range(n_items):
        q = 5 if whale else random.randint(1, 8)
        p = 400.0 if whale else round(random.uniform(50, 400), 2)
        items.append((iid, oid, q, p)); tot += q * p; iid += 1
    oid += 1
    return oid - 1, tot

order_total = {}
for _ in range(3000):
    cust = random.randint(1, 200)
    odate = dt.date(2026, 1, 1) + dt.timedelta(days=random.randint(0, 211))
    st = random.choice(statuses)
    voided = 1 if random.random() < 0.04 else 0
    o, tot = add_order(cust, odate, st, voided, random.randint(1, 5), whale=(cust == 200))
    order_total[o] = (odate, st, voided, tot, cust)
for _ in range(60):                          # whale's big completed Feb orders (not voided)
    odate = dt.date(2026, 2, 1) + dt.timedelta(days=random.randint(0, 27))
    o, tot = add_order(200, odate, "completed", 0, 5, whale=True)
    order_total[o] = (odate, "completed", 0, tot, 200)

for o, (odate, st, voided, tot, cust) in order_total.items():
    if voided:
        continue
    if st == "refunded":
        refunds.append((rid, o, round(random.uniform(20, 300), 2), odate.isoformat())); rid += 1
    elif st == "completed" and random.random() < 0.08:      # partial refunds on completed
        for _ in range(random.randint(1, 2)):
            rdate = odate + dt.timedelta(days=random.randint(5, 60))
            refunds.append((rid, o, round(tot * random.uniform(0.10, 0.30), 2),
                            rdate.isoformat())); rid += 1
ec.executemany("INSERT INTO orders VALUES(?,?,?,?,?,?)", orders)
ec.executemany("INSERT INTO order_items VALUES(?,?,?,?)", items)
ec.executemany("INSERT INTO refunds VALUES(?,?,?,?)", refunds)
ec.commit()

# ---- governed anchors (maintained in Python at write time, independent of SQL) ----
def quarter(d): return (d.year, (d.month - 1) // 3 + 1)
ec_ledger = collections.defaultdict(float)
for o, (odate, st, voided, tot, cust) in order_total.items():
    if st == "completed" and not voided:
        if quarter(odate) == (2026, 1): ec_ledger["rev_2026Q1"] += tot
for (r, o, amt, rdate) in refunds:
    d = dt.date.fromisoformat(rdate)
    if quarter(d) == (2026, 1): ec_ledger["refunds_2026Q1"] += amt
ec_ledger["active_customers"] = sum(1 for c in cust_rows if c[2] == 0)
ec_ledger["total_orders"] = len(orders)

# =====================================================================
# SCHEMA 2: SAAS BILLING
# =====================================================================
sa = sqlite3.connect(":memory:")
sa.executescript("""
CREATE TABLE accounts(id INTEGER PRIMARY KEY, segment TEXT, is_deleted INT);
CREATE TABLE subscriptions(id INTEGER PRIMARY KEY, account_id INT, start_date TEXT,
    end_date TEXT, is_trial INT, status TEXT, mrr_cents INT);
CREATE TABLE invoices(id INTEGER PRIMARY KEY, subscription_id INT, invoice_date TEXT,
    amount_cents INT, status TEXT);
CREATE TABLE payments(id INTEGER PRIMARY KEY, invoice_id INT, paid_date TEXT, amount_cents INT);
""")
acc_rows, sub_rows = [], []
sid = 1
def seg_of(i):
    return "SMB" if i <= 180 else ("MM" if i <= 270 else "ENT")
for i in range(1, 301):
    acc_rows.append((i, seg_of(i), 1 if random.random() < 0.05 else 0))
for i in range(301, 311):                    # deleted SMB "load-test" whale accounts
    acc_rows.append((i, "SMB", 1))
acc_deleted = {a[0]: a[2] for a in acc_rows}
acc_seg     = {a[0]: a[1] for a in acc_rows}

def add_sub(acct, start, end, trial, mrr):
    global sid
    status = "trial" if trial else ("churned" if end else "active")
    sub_rows.append((sid, acct, start.isoformat(), end.isoformat() if end else None,
                     1 if trial else 0, status, mrr))
    sid += 1

for (i, seg, deld) in acc_rows:
    if i > 300:                              # whale: 5 big active subs each
        for _ in range(5):
            add_sub(i, dt.date(2025, 7, 1), None, False, 199900)
        continue
    nsubs = 1 if seg == "SMB" else (random.randint(1, 2) if seg == "MM" else random.randint(2, 5))
    for _ in range(nsubs):
        start = dt.date(2025, 6, 1) + dt.timedelta(days=random.randint(0, 409))  # ..2026-07-15
        trial = random.random() < 0.10
        mrr = random.choice({"SMB": [2900, 9900], "MM": [9900, 49900],
                             "ENT": [49900, 199900]}[seg])
        end = None
        if random.random() < 0.25:
            e = start + dt.timedelta(days=random.randint(30, 400))
            if e <= dt.date(2026, 8, 8) and e > start + dt.timedelta(days=7):
                end = e
        add_sub(i, start, end, trial, mrr)
sa.executemany("INSERT INTO accounts VALUES(?,?,?)", acc_rows)
sa.executemany("INSERT INTO subscriptions VALUES(?,?,?,?,?,?,?)", sub_rows)

inv_rows, pay_rows = [], []
iidx = pidx = 1
for (s, acct, sd, ed, trial, status, mrr) in sub_rows:
    if trial:
        continue
    start = dt.date.fromisoformat(sd)
    end = dt.date.fromisoformat(ed) if ed else None
    for m in range(1, 8):                    # Jan..Jul 2026, invoice on the 1st if active then
        d1 = dt.date(2026, m, 1)
        if start <= d1 and (end is None or end > d1):
            inv_rows.append((iidx, s, d1.isoformat(), mrr, "issued"))
            if random.random() < 0.85:
                pd = d1 + dt.timedelta(days=random.randint(0, 40))
                pay_rows.append((pidx, iidx, pd.isoformat(), mrr)); pidx += 1
            iidx += 1
sa.executemany("INSERT INTO invoices VALUES(?,?,?,?,?)", inv_rows)
sa.executemany("INSERT INTO payments VALUES(?,?,?,?)", pay_rows)
sa.commit()

# ---- governed anchors ----
sa_ledger = {}
D731, D701 = dt.date(2026, 7, 31), dt.date(2026, 7, 1)
mrr_tot = 0; paying = set()
for (s, acct, sd, ed, trial, status, mrr) in sub_rows:
    if trial or acc_deleted[acct]:
        continue
    start = dt.date.fromisoformat(sd); end = dt.date.fromisoformat(ed) if ed else None
    if start <= D731 and (end is None or end > D731):
        mrr_tot += mrr
    if start <= D731 and (end is None or end >= D701):
        paying.add(acct)
sa_ledger["mrr_2026_07"] = round(mrr_tot / 100.0, 2)
sa_ledger["paying_accounts_2026_07"] = len(paying)
cash = 0
for (p, inv, pd, amt) in pay_rows:
    d = dt.date.fromisoformat(pd)
    if quarter(d) == (2026, 2): cash += amt
sa_ledger["cash_2026Q2"] = round(cash / 100.0, 2)
sa_ledger["total_subs"] = len(sub_rows)

# =====================================================================
# QUESTIONS: contract, correct SQL, mutations, verifier checks
# =====================================================================
def S(con, sql): return con.execute(sql).fetchone()[0]

Q = []
def q(schema, qid, text, correct, muts, dual, anchors, invariants, kind="num"):
    Q.append(dict(schema=schema, qid=qid, text=text, correct=correct, muts=muts,
                  dual=dual, anchors=anchors, invariants=invariants, kind=kind))

# ---------------- E-COMMERCE ----------------
EC_REV_Q1 = """SELECT ROUND(SUM(oi.quantity*oi.unit_price),2)
 FROM orders o JOIN order_items oi ON oi.order_id=o.id
 WHERE o.status='completed' AND o.is_deleted=0
   AND o.order_date>='2026-01-01' AND o.order_date<'2026-04-01'"""

q("ecom", "E1", "Total completed revenue, Q1 2026 (contract: completed, non-voided orders, booked by order_date)",
  lambda: S(ec, EC_REV_Q1),
  [("softdelete_leak", lambda: S(ec, """SELECT ROUND(SUM(oi.quantity*oi.unit_price),2)
      FROM orders o JOIN order_items oi ON oi.order_id=o.id
      WHERE o.status='completed' AND o.order_date>='2026-01-01' AND o.order_date<'2026-04-01'""")),
   ("boundary_date", lambda: S(ec, """SELECT ROUND(SUM(oi.quantity*oi.unit_price),2)
      FROM orders o JOIN order_items oi ON oi.order_id=o.id
      WHERE o.status='completed' AND o.is_deleted=0
        AND o.order_date>='2026-01-01' AND o.order_date<='2026-04-01'""")),
   ("join_fanout", lambda: S(ec, """SELECT ROUND(SUM(oi.quantity*oi.unit_price),2)
      FROM orders o JOIN order_items oi ON oi.order_id=o.id
      LEFT JOIN refunds r ON r.order_id=o.id
      WHERE o.status='completed' AND o.is_deleted=0
        AND o.order_date>='2026-01-01' AND o.order_date<'2026-04-01'"""))],
  dual=lambda: S(ec, """SELECT ROUND(SUM(t),2) FROM (
      SELECT o.id, SUM(oi.quantity*oi.unit_price) t
      FROM orders o JOIN order_items oi ON oi.order_id=o.id
      WHERE o.status='completed' AND o.is_deleted=0
        AND o.order_date>='2026-01-01' AND o.order_date<'2026-04-01' GROUP BY o.id)"""),
  anchors=[("ledger_rev_Q1", lambda v: abs(v - ec_ledger["rev_2026Q1"]) < 0.05)],
  invariants=[("nonneg", lambda v: v >= 0)])

q("ecom", "E2", "Active customers who placed a non-cancelled order in July 2026",
  lambda: S(ec, """SELECT COUNT(DISTINCT o.customer_id)
      FROM orders o JOIN customers c ON c.id=o.customer_id
      WHERE o.is_deleted=0 AND o.status<>'cancelled' AND c.is_deleted=0
        AND o.order_date>='2026-07-01' AND o.order_date<'2026-08-01'"""),
  [("count_not_distinct", lambda: S(ec, """SELECT COUNT(o.customer_id) FROM orders o
      WHERE o.order_date>='2026-07-01' AND o.order_date<'2026-08-01'""")),
   ("softdelete_leak", lambda: S(ec, """SELECT COUNT(DISTINCT o.customer_id)
      FROM orders o WHERE o.is_deleted=0 AND o.status<>'cancelled'
        AND o.order_date>='2026-07-01' AND o.order_date<'2026-08-01'""")),
   ("status_leak", lambda: S(ec, """SELECT COUNT(DISTINCT o.customer_id)
      FROM orders o JOIN customers c ON c.id=o.customer_id
      WHERE o.is_deleted=0 AND c.is_deleted=0
        AND o.order_date>='2026-07-01' AND o.order_date<'2026-08-01'"""))],
  dual=lambda: S(ec, """SELECT COUNT(*) FROM (
      SELECT o.customer_id FROM orders o JOIN customers c ON c.id=o.customer_id
      WHERE o.is_deleted=0 AND o.status<>'cancelled' AND c.is_deleted=0
        AND o.order_date>='2026-07-01' AND o.order_date<'2026-08-01'
      GROUP BY o.customer_id)"""),
  anchors=[("pop_bound", lambda v: v <= ec_ledger["active_customers"])],
  invariants=[("nonneg", lambda v: v >= 0)])

q("ecom", "E3", "Average ORDER value, July 2026 (completed, non-voided)",
  lambda: S(ec, """SELECT ROUND(SUM(oi.quantity*oi.unit_price)/COUNT(DISTINCT o.id),2)
      FROM orders o JOIN order_items oi ON oi.order_id=o.id
      WHERE o.status='completed' AND o.is_deleted=0
        AND o.order_date>='2026-07-01' AND o.order_date<'2026-08-01'"""),
  [("wrong_grain_avg", lambda: S(ec, """SELECT ROUND(AVG(oi.quantity*oi.unit_price),2)
      FROM orders o JOIN order_items oi ON oi.order_id=o.id
      WHERE o.status='completed' AND o.is_deleted=0
        AND o.order_date>='2026-07-01' AND o.order_date<'2026-08-01'""")),
   ("status_leak", lambda: S(ec, """SELECT ROUND(SUM(oi.quantity*oi.unit_price)/COUNT(DISTINCT o.id),2)
      FROM orders o JOIN order_items oi ON oi.order_id=o.id
      WHERE o.is_deleted=0
        AND o.order_date>='2026-07-01' AND o.order_date<'2026-08-01'"""))],
  dual=lambda: S(ec, """SELECT ROUND(AVG(t),2) FROM (
      SELECT o.id, SUM(oi.quantity*oi.unit_price) t
      FROM orders o JOIN order_items oi ON oi.order_id=o.id
      WHERE o.status='completed' AND o.is_deleted=0
        AND o.order_date>='2026-07-01' AND o.order_date<'2026-08-01' GROUP BY o.id)"""),
  anchors=[],
  invariants=[("within_order_minmax", lambda v: S(ec, """SELECT MIN(t) FROM (
        SELECT SUM(oi.quantity*oi.unit_price) t FROM orders o
        JOIN order_items oi ON oi.order_id=o.id
        WHERE o.status='completed' AND o.is_deleted=0
          AND o.order_date>='2026-07-01' AND o.order_date<'2026-08-01' GROUP BY o.id)""")
        <= v <= S(ec, """SELECT MAX(t) FROM (
        SELECT SUM(oi.quantity*oi.unit_price) t FROM orders o
        JOIN order_items oi ON oi.order_id=o.id
        WHERE o.status='completed' AND o.is_deleted=0
          AND o.order_date>='2026-07-01' AND o.order_date<'2026-08-01' GROUP BY o.id)"""))])

def _region_rank(include_deleted):
    extra = "" if include_deleted else " AND c.is_deleted=0"
    rows = ec.execute(f"""SELECT c.region, SUM(oi.quantity*oi.unit_price) rev
        FROM orders o JOIN customers c ON c.id=o.customer_id
        JOIN order_items oi ON oi.order_id=o.id
        WHERE o.status='completed' AND o.is_deleted=0
          AND o.order_date>='2026-01-01' AND o.order_date<'2026-04-01'{extra}
        GROUP BY c.region ORDER BY rev DESC""").fetchall()
    return rows[0][0]
q("ecom", "E4", "Top region by Q1 revenue from ACTIVE customers",
  lambda: _region_rank(False),
  [("softdelete_leak", lambda: _region_rank(True))],
  dual=lambda: max(regions, key=lambda rg: S(ec, f"""SELECT COALESCE(SUM(oi.quantity*oi.unit_price),0)
      FROM orders o JOIN customers c ON c.id=o.customer_id
      JOIN order_items oi ON oi.order_id=o.id
      WHERE o.status='completed' AND o.is_deleted=0 AND c.is_deleted=0 AND c.region='{rg}'
        AND o.order_date>='2026-01-01' AND o.order_date<'2026-04-01'""")),
  anchors=[], invariants=[("valid_region", lambda v: v in regions)], kind="cat")

q("ecom", "E5", "NET revenue Q1 2026 (gross completed minus refunds issued in Q1)",
  lambda: S(ec, f"""SELECT ROUND(({EC_REV_Q1}) - (SELECT COALESCE(SUM(amount),0) FROM refunds
      WHERE refund_date>='2026-01-01' AND refund_date<'2026-04-01'),2)"""),
  [("join_fanout", lambda: S(ec, """SELECT ROUND(SUM(oi.quantity*oi.unit_price - COALESCE(r.amount,0)),2)
      FROM orders o JOIN order_items oi ON oi.order_id=o.id
      LEFT JOIN refunds r ON r.order_id=o.id
      WHERE o.status='completed' AND o.is_deleted=0
        AND o.order_date>='2026-01-01' AND o.order_date<'2026-04-01'""")),
   ("wrong_date_col", lambda: S(ec, f"""SELECT ROUND(({EC_REV_Q1}) - (SELECT COALESCE(SUM(r.amount),0)
      FROM refunds r JOIN orders o ON o.id=r.order_id
      WHERE o.order_date>='2026-01-01' AND o.order_date<'2026-04-01'),2)"""))],
  dual=lambda: round(S(ec, """SELECT ROUND(SUM(t),2) FROM (
      SELECT o.id, SUM(oi.quantity*oi.unit_price) t
      FROM orders o JOIN order_items oi ON oi.order_id=o.id
      WHERE o.status='completed' AND o.is_deleted=0
        AND o.order_date>='2026-01-01' AND o.order_date<'2026-04-01' GROUP BY o.id)""")
      - S(ec, """SELECT COALESCE(SUM(amount),0) FROM refunds
      WHERE refund_date>='2026-01-01' AND refund_date<'2026-04-01'"""), 2),
  anchors=[("ledger_net_Q1", lambda v: abs(v - (ec_ledger["rev_2026Q1"] - ec_ledger["refunds_2026Q1"])) < 0.05)],
  invariants=[("net_le_gross", lambda v: v <= ec_ledger["rev_2026Q1"] + 0.05)])

q("ecom", "E6", "Completed, non-voided orders SHIPPED in March 2026",
  lambda: S(ec, """SELECT COUNT(*) FROM orders o
      WHERE o.status='completed' AND o.is_deleted=0
        AND o.ship_date>='2026-03-01' AND o.ship_date<'2026-04-01'"""),
  [("wrong_date_col", lambda: S(ec, """SELECT COUNT(*) FROM orders o
      WHERE o.status='completed' AND o.is_deleted=0
        AND o.order_date>='2026-03-01' AND o.order_date<'2026-04-01'""")),
   ("join_fanout", lambda: S(ec, """SELECT COUNT(*) FROM orders o
      JOIN order_items oi ON oi.order_id=o.id
      WHERE o.status='completed' AND o.is_deleted=0
        AND o.ship_date>='2026-03-01' AND o.ship_date<'2026-04-01'"""))],
  dual=lambda: S(ec, """SELECT SUM(c) FROM (
      SELECT o.ship_date, COUNT(*) c FROM orders o
      WHERE o.status='completed' AND o.is_deleted=0
        AND o.ship_date>='2026-03-01' AND o.ship_date<'2026-04-01' GROUP BY o.ship_date)"""),
  anchors=[("orders_bound", lambda v: v <= ec_ledger["total_orders"])],
  invariants=[("nonneg", lambda v: v >= 0)])

# ---------------- SAAS ----------------
SA_MRR = """SELECT ROUND(SUM(s.mrr_cents)/100.0,2)
 FROM subscriptions s JOIN accounts a ON a.id=s.account_id
 WHERE s.is_trial=0 AND a.is_deleted=0 AND s.start_date<='2026-07-31'
   AND (s.end_date IS NULL OR s.end_date>'2026-07-31')"""

q("saas", "S1", "MRR (in dollars) as of July 31, 2026 (paying subs, non-deleted accounts)",
  lambda: S(sa, SA_MRR),
  [("unit_cents", lambda: S(sa, SA_MRR.replace("/100.0", "*1.0"))),
   ("trial_leak", lambda: S(sa, SA_MRR.replace("s.is_trial=0 AND ", ""))),
   ("softdelete_leak", lambda: S(sa, SA_MRR.replace("a.is_deleted=0 AND ", "")))],
  dual=lambda: round(
      S(sa, """SELECT COALESCE(SUM(s.mrr_cents),0) FROM subscriptions s
          JOIN accounts a ON a.id=s.account_id
          WHERE s.is_trial=0 AND a.is_deleted=0 AND s.start_date<='2026-07-31'""") / 100.0
      - S(sa, """SELECT COALESCE(SUM(s.mrr_cents),0) FROM subscriptions s
          JOIN accounts a ON a.id=s.account_id
          WHERE s.is_trial=0 AND a.is_deleted=0 AND s.start_date<='2026-07-31'
            AND s.end_date IS NOT NULL AND s.end_date<='2026-07-31'""") / 100.0, 2),
  anchors=[("ledger_mrr", lambda v: abs(v - sa_ledger["mrr_2026_07"]) < 0.05)],
  invariants=[("nonneg", lambda v: v >= 0)])

q("saas", "S2", "Paying accounts active during July 2026 (non-deleted)",
  lambda: S(sa, """SELECT COUNT(DISTINCT a.id)
      FROM subscriptions s JOIN accounts a ON a.id=s.account_id
      WHERE s.is_trial=0 AND a.is_deleted=0 AND s.start_date<='2026-07-31'
        AND (s.end_date IS NULL OR s.end_date>='2026-07-01')"""),
  [("count_not_distinct", lambda: S(sa, """SELECT COUNT(a.id)
      FROM subscriptions s JOIN accounts a ON a.id=s.account_id
      WHERE s.is_trial=0 AND a.is_deleted=0 AND s.start_date<='2026-07-31'
        AND (s.end_date IS NULL OR s.end_date>='2026-07-01')""")),
   ("trial_leak", lambda: S(sa, """SELECT COUNT(DISTINCT a.id)
      FROM subscriptions s JOIN accounts a ON a.id=s.account_id
      WHERE a.is_deleted=0 AND s.start_date<='2026-07-31'
        AND (s.end_date IS NULL OR s.end_date>='2026-07-01')"""))],
  dual=lambda: S(sa, """SELECT COUNT(*) FROM (
      SELECT s.account_id FROM subscriptions s JOIN accounts a ON a.id=s.account_id
      WHERE s.is_trial=0 AND a.is_deleted=0 AND s.start_date<='2026-07-31'
        AND (s.end_date IS NULL OR s.end_date>='2026-07-01')
      GROUP BY s.account_id)"""),
  anchors=[("ledger_accounts", lambda v: v == sa_ledger["paying_accounts_2026_07"])],
  invariants=[("nonneg", lambda v: v >= 0)])

q("saas", "S3", "Paying subscriptions churned in Q2 2026 (end_date in Q2)",
  lambda: S(sa, """SELECT COUNT(*) FROM subscriptions s
      WHERE s.is_trial=0 AND s.end_date>='2026-04-01' AND s.end_date<'2026-07-01'"""),
  [("wrong_date_col", lambda: S(sa, """SELECT COUNT(*) FROM subscriptions s
      WHERE s.is_trial=0 AND s.start_date>='2026-04-01' AND s.start_date<'2026-07-01'""")),
   ("boundary_date", lambda: S(sa, """SELECT COUNT(*) FROM subscriptions s
      WHERE s.is_trial=0 AND s.end_date>='2026-04-01' AND s.end_date<'2026-06-30'"""))],
  dual=lambda: S(sa, """SELECT (SELECT COUNT(*) FROM subscriptions s
      WHERE s.is_trial=0 AND s.end_date IS NOT NULL AND s.end_date<'2026-07-01')
      - (SELECT COUNT(*) FROM subscriptions s
      WHERE s.is_trial=0 AND s.end_date IS NOT NULL AND s.end_date<'2026-04-01')"""),
  anchors=[("subs_bound", lambda v: v <= sa_ledger["total_subs"])],
  invariants=[("nonneg", lambda v: v >= 0)])

q("saas", "S4", "ARPA (average revenue per paying account), July 2026",
  lambda: round(S(sa, SA_MRR) / S(sa, """SELECT COUNT(DISTINCT a.id)
      FROM subscriptions s JOIN accounts a ON a.id=s.account_id
      WHERE s.is_trial=0 AND a.is_deleted=0 AND s.start_date<='2026-07-31'
        AND (s.end_date IS NULL OR s.end_date>='2026-07-01')"""), 2),
  [("wrong_grain_avg", lambda: S(sa, """SELECT ROUND(AVG(s.mrr_cents/100.0),2)
      FROM subscriptions s JOIN accounts a ON a.id=s.account_id
      WHERE s.is_trial=0 AND a.is_deleted=0 AND s.start_date<='2026-07-31'
        AND (s.end_date IS NULL OR s.end_date>'2026-07-31')"""))],
  dual=lambda: round(sa_ledger["mrr_2026_07"] / sa_ledger["paying_accounts_2026_07"], 2),
  anchors=[("ledger_arpa", lambda v: close(v, sa_ledger["mrr_2026_07"] / sa_ledger["paying_accounts_2026_07"]))],
  invariants=[("nonneg", lambda v: v >= 0)])

q("saas", "S5", "Cash COLLECTED in Q2 2026 (payments received, dollars)",
  lambda: S(sa, """SELECT ROUND(SUM(p.amount_cents)/100.0,2) FROM payments p
      WHERE p.paid_date>='2026-04-01' AND p.paid_date<'2026-07-01'"""),
  [("wrong_table", lambda: S(sa, """SELECT ROUND(SUM(i.amount_cents)/100.0,2) FROM invoices i
      WHERE i.invoice_date>='2026-04-01' AND i.invoice_date<'2026-07-01'""")),
   ("unit_cents", lambda: S(sa, """SELECT ROUND(SUM(p.amount_cents)*1.0,2) FROM payments p
      WHERE p.paid_date>='2026-04-01' AND p.paid_date<'2026-07-01'"""))],
  dual=lambda: round(sum(S(sa, f"""SELECT COALESCE(SUM(p.amount_cents),0) FROM payments p
      WHERE p.paid_date>='2026-0{m}-01' AND p.paid_date<'2026-0{m+1}-01'""") for m in (4, 5, 6)) / 100.0, 2),
  anchors=[("ledger_cash", lambda v: abs(v - sa_ledger["cash_2026Q2"]) < 0.05)],
  invariants=[("nonneg", lambda v: v >= 0)])

SEGS = ["SMB", "MM", "ENT"]
def _seg_rank(include_deleted):
    extra = "" if include_deleted else " AND a.is_deleted=0"
    rows = sa.execute(f"""SELECT a.segment, SUM(s.mrr_cents) FROM subscriptions s
        JOIN accounts a ON a.id=s.account_id
        WHERE s.is_trial=0 AND s.start_date<='2026-07-31'
          AND (s.end_date IS NULL OR s.end_date>'2026-07-31'){extra}
        GROUP BY a.segment ORDER BY 2 DESC""").fetchall()
    return rows[0][0]
q("saas", "S6", "Top segment by MRR, July 31 2026 (non-deleted accounts)",
  lambda: _seg_rank(False),
  [("softdelete_leak", lambda: _seg_rank(True))],
  dual=lambda: max(SEGS, key=lambda sg: S(sa, f"""SELECT COALESCE(SUM(s.mrr_cents),0)
      FROM subscriptions s JOIN accounts a ON a.id=s.account_id
      WHERE s.is_trial=0 AND a.is_deleted=0 AND a.segment='{sg}'
        AND s.start_date<='2026-07-31' AND (s.end_date IS NULL OR s.end_date>'2026-07-31')""")),
  anchors=[], invariants=[("valid_segment", lambda v: v in SEGS)], kind="cat")

# =====================================================================
# RUN + VERIFY
# =====================================================================
def verify(question, value, use_dual=True):
    """Blind verifier: returns (verdict, reason). Never sees the correct answer."""
    for name, fn in question["invariants"]:
        try:
            if not fn(value): return "FLAG", f"invariant:{name}"
        except Exception:
            return "FLAG", f"invariant_error:{name}"
    for name, fn in question["anchors"]:
        try:
            if not fn(value): return "FLAG", f"anchor:{name}"
        except Exception:
            return "FLAG", f"anchor_error:{name}"
    if use_dual:
        expect = question["dual"]()
        if question["kind"] == "cat":
            if value != expect: return "FLAG", "dual_formulation"
        else:
            if not close(float(value), float(expect)): return "FLAG", "dual_formulation"
    return "TRUST", ""

rows = []
for question in Q:
    cval = question["correct"]()
    fv, fr = verify(question, cval, True)
    av, ar = verify(question, cval, False)
    rows.append(dict(schema=question["schema"], qid=question["qid"], case="correct",
                     mode="-", value=cval, correct=cval, err_pct=0.0, material=False,
                     full=fv, full_reason=fr, abl=av, abl_reason=ar))
    for mode, fn in question["muts"]:
        mval = fn()
        if question["kind"] == "cat":
            material = (mval != cval); err = "FLIP" if material else "same"
        else:
            err = abs(mval - cval) / abs(cval) * 100 if cval else 0.0
            material = err > 0.1
        fv, fr = verify(question, mval, True)
        av, ar = verify(question, mval, False)
        rows.append(dict(schema=question["schema"], qid=question["qid"], case="mutated",
                         mode=mode, value=mval, correct=cval,
                         err_pct=err, material=material,
                         full=fv, full_reason=fr, abl=av, abl_reason=ar))

# =====================================================================
# SCORE
# =====================================================================
wrong = [r for r in rows if r["case"] == "mutated" and r["material"]]
immaterial = [r for r in rows if r["case"] == "mutated" and not r["material"]]
correct_rows = [r for r in rows if r["case"] == "correct"]

full_caught = sum(1 for r in wrong if r["full"] == "FLAG")
abl_caught  = sum(1 for r in wrong if r["abl"] == "FLAG")
full_fp = sum(1 for r in correct_rows if r["full"] == "FLAG")
abl_fp  = sum(1 for r in correct_rows if r["abl"] == "FLAG")

by_mode = collections.defaultdict(lambda: dict(n=0, errs=[], full=0, abl=0))
for r in wrong:
    m = by_mode[r["mode"]]; m["n"] += 1
    if isinstance(r["err_pct"], float): m["errs"].append(r["err_pct"])
    m["full"] += (r["full"] == "FLAG"); m["abl"] += (r["abl"] == "FLAG")

W = 118
print("=" * W)
print("SILENTWRONG BENCHMARK v0.2  --  2 schemas, 12 questions, "
      f"{len(wrong)} materially-wrong AI answers ({len(immaterial)} immaterial excluded)")
print("=" * W)
print(f"{'Q':<4}{'failure mode':<20}{'correct':>16}{'AI answer':>16}{'error':>10}"
      f"{'full verifier':>15}{'anchors-only':>14}")
print("-" * W)
for r in wrong:
    e = f"{r['err_pct']:.1f}%" if isinstance(r["err_pct"], float) else r["err_pct"]
    fmt = lambda x: f"{x:,.2f}" if isinstance(x, float) else str(x)
    print(f"{r['qid']:<4}{r['mode']:<20}{fmt(r['correct']):>16}{fmt(r['value']):>16}{e:>10}"
          f"{r['full'] + ('*' if r['full']=='FLAG' else ''):>15}{r['abl']:>14}")
print("-" * W)
print(f"{'BY FAILURE MODE':<24}{'cases':>7}{'mean |err|':>12}{'full detect':>13}{'anchors-only':>14}")
for mode, m in sorted(by_mode.items()):
    me = f"{sum(m['errs'])/len(m['errs']):.1f}%" if m["errs"] else "flip"
    print(f"  {mode:<22}{m['n']:>7}{me:>12}{str(m['full'])+'/'+str(m['n']):>13}{str(m['abl'])+'/'+str(m['n']):>14}")
print("-" * W)
print(f"HEADLINE  wrong answers: {len(wrong)}   "
      f"full-verifier detection: {full_caught}/{len(wrong)} ({full_caught/len(wrong)*100:.0f}%)   "
      f"anchors-only: {abl_caught}/{len(wrong)} ({abl_caught/len(wrong)*100:.0f}%)")
print(f"          false alarms on correct answers: full {full_fp}/{len(correct_rows)}, "
      f"anchors-only {abl_fp}/{len(correct_rows)}")
if immaterial:
    print(f"          immaterial mutations (excluded): "
          + ", ".join(f"{r['qid']}/{r['mode']} ({r['err_pct'] if isinstance(r['err_pct'],str) else round(r['err_pct'],3)}%)" for r in immaterial))
print("=" * W)

# CSV
with open(f"{OUT}/benchmark_results.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["schema", "qid", "case", "mode", "value", "correct",
                                      "err_pct", "material", "full", "full_reason", "abl", "abl_reason"])
    w.writeheader()
    for r in rows: w.writerow(r)
print(f"\nSaved: benchmark_results.csv ({len(rows)} rows)")

# Report data for the writer
summary = dict(n_wrong=len(wrong), full=full_caught, abl=abl_caught,
               fp_full=full_fp, fp_abl=abl_fp, n_correct=len(correct_rows),
               by_mode={k: dict(n=v["n"], mean_err=(sum(v["errs"])/len(v["errs"]) if v["errs"] else None),
                                full=v["full"], abl=v["abl"]) for k, v in by_mode.items()},
               immaterial=[(r["qid"], r["mode"]) for r in immaterial])
import json as _json
with open(f"{OUT}/benchmark_summary.json", "w") as f:
    _json.dump(summary, f, indent=1, default=str)
print("Saved: benchmark_summary.json")
