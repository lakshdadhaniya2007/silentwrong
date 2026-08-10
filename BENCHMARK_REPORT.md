# SilentWrong v0.2 — how often is "AI on your data" confidently wrong, and how much can verification catch?

**Headline: across 23 materially wrong AI-style answers over two realistic business databases, a blind verification layer flagged 23/23 (100%) with 0 false alarms on 12 correct answers. Stripped of its key technique, the same verifier drops to 14/23 (61%) — locating exactly where the defensible technology is.**

## What this benchmark is

AI systems answering questions over enterprise data fail in a specific, dangerous way: the answer is *plausible and wrong* — right syntax, wrong meaning. Published results show accuracy collapsing from ~91% on toy schemas to ~6–21% on real enterprise databases (Spider 2.0). This benchmark reproduces the documented failure modes in controlled form and asks the question that matters commercially: **can a system that never sees the correct answer reliably tell you which answers not to trust?**

Two deterministic schemas (seed=7): an **e-commerce** database with soft-deleted customers, voided orders, partial refunds, order↔line-item fan-out, and order-vs-ship dates; and a **SaaS billing** database with amounts stored in cents, trial subscriptions, multi-subscription accounts, churn dates, and invoiced-vs-collected cash. Twelve business questions, each with a precise semantic contract and a correct query. Twenty-four mutated queries reproduce ten documented failure modes; one mutation changed the value by less than 0.1% and was excluded as immaterial, leaving **23 materially wrong answers** (errors from 0.5% to 9,900%).

## The verifier (blind to ground truth)

Three layers, all model-agnostic. **V1 — dual formulation:** an independent query strategy derived from the semantic contract (pre-aggregation at entity grain, complement counting, partition-and-sum); disagreement beyond 0.5% ⇒ flag. **V2 — governed anchors:** control totals maintained independently at write time, the way a finance ledger or governed metric store actually works (quarterly revenue, MRR snapshot, cash collected, population counts); reconciliation failure ⇒ flag. **V3 — invariants:** bounds and structure (count ≤ population, net ≤ gross, average within min/max, subset ≤ total, valid category).

## Results

| Failure mode | Cases | Mean \|error\| | Full verifier | Anchors+invariants only |
|---|---|---|---|---|
| unit_cents (¢ vs $) | 2 | 9,900% | 2/2 | 2/2 |
| wrong_date_col | 3 | 149% | 3/3 | 1/3 |
| count_not_distinct | 2 | 103% | 2/2 | 2/2 |
| join_fanout | 3 | 71% | 3/3 | 2/3 |
| wrong_grain_avg | 2 | 45% | 2/2 | 1/2 |
| softdelete_leak | 5 | 28% (+2 rank flips) | 5/5 | 2/5 |
| wrong_table (billed vs collected) | 1 | 21% | 1/1 | 1/1 |
| trial_leak | 2 | 6.3% | 2/2 | 2/2 |
| status_leak | 2 | 3.6% | 2/2 | 0/2 |
| boundary_date | 1 | 0.8% | 1/1 | 1/1 |
| **Total** | **23** | — | **23/23 (100%)** | **14/23 (61%)** |

False alarms on the 12 correct answers: **0** for both configurations.

Three findings worth attention. First, **error sizes span four orders of magnitude** — from a 0.8% boundary slip to a 9,900% cents-as-dollars misread — and two mutations flipped a *categorical* answer (which region/segment is #1) while every underlying number looked plausible. No human eyeballing catches all of these. Second, the ablation shows **where the moat is**: governed anchors alone (what a company's existing semantic layer or finance books give you) catch only 61%. The failure modes that evade them — wrong date column, wrong aggregation grain, status/population leaks, rank flips — are precisely the "wrong meaning" errors, and they are caught only by contract-driven independent reformulation. That technique is the product. Third, **exclusion discipline matters**: one boundary mutation happened to change nothing (no churns fell on the excluded day) and was dropped from the tally rather than counted as a win.

## Honest limitations (what v0.3 must do)

Detection of 100% here is an upper bound, earned under favorable assumptions: contracts were precise (real questions are ambiguous — "revenue" booked or collected?); dual formulations were guaranteed-diverse and correct by construction (in production they come from a model and can share the candidate's misreading — correlated failure is the open research problem); anchors were exact and fresh (real ledgers drift and lag, which is where false alarms will come from); and the wrong queries were hand-written reproductions of documented failure modes, not live model outputs. The naive-side numbers therefore measure *failure-mode impact*, not *model failure rates*. v0.3 should run live frontier models against these schemas to measure actual wrong-answer rates, add ambiguous-contract questions where the verifier must detect *underspecification* rather than error, inject anchor drift to measure the false-alarm curve, and scale to 10+ schemas and hundreds of questions.

## Why this matters

The commercial claim this supports: **the trust layer is buildable and measurable.** "Confidently wrong" is not an anecdote — it is a reproducible phenomenon with quantifiable detection rates, a publishable metric (caught-error rate at a fixed false-alarm rate), and a clear technical frontier (ambiguity and correlated formulation error) that favors whoever does the deep work first.

*Reproduce: `python3 benchmark_harness.py` — deterministic, stdlib only. Full per-case data in `benchmark_results.csv`.*
