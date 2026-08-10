# Your AI analytics tool is confidently wrong, and nobody can tell which answers to trust

Ask an AI "what was our revenue last quarter?" and it will give you a clean, plausible number. Ask it on a real database — soft-deleted customers, voided orders, refunds, amounts stored in cents, three different date columns — and a large fraction of the time that clean number is wrong. Not visibly wrong. Not error-message wrong. *Board-deck* wrong: correct syntax, wrong meaning, no warning.

This isn't an anecdote. On Spider 2.0, a benchmark built from real enterprise databases, frontier systems solve roughly a fifth of tasks — down from ~91% on the toy benchmarks that preceded it. Yet "chat with your data" is being deployed everywhere right now. The gap between those two facts is the most dangerous failure mode in applied AI today, and almost everyone is working on the wrong side of it: making generation *better* rather than making answers *checkable*.

I built [SilentWrong](https://github.com/lakshdadhaniya2007/silentwrong) to measure the checkable side.

## The benchmark

Two deterministic, realistically messy SQLite schemas: an e-commerce database (soft-deletes, voided orders, partial refunds, an orders↔line-items fan-out trap, order-vs-ship dates) and a SaaS billing database (amounts in cents, trial subscriptions, multi-sub accounts, churn dates, invoiced-vs-collected cash). Twelve business questions with precise semantic contracts and known-correct answers. Twenty-three materially wrong answers reproducing ten documented failure modes — errors spanning 0.5% to 9,900%, including two cases where the *ranking* flips (wrong region crowned #1) while every number involved looks plausible.

Then I ran a live frontier model against it. Gemini Flash was **silently wrong on 2 of the 11 questions it answered — 18%**, with no error and no warning. Asked for cash collected in Q2, it queried the invoices table instead of payments and overstated by **45.6%**; asked for ARPA, it returned a figure **1.2%** off — small enough that no reviewer would ever question it. The verifier flagged both and false-alarmed on none of the 9 correct answers. (Two wrong answers is a small sample, and in this run the control totals alone caught both, so it doesn't yet reproduce the dual-formulation advantage the mutation study showed.)

## The verifier

The interesting question is not "how often is AI wrong" — it's "can a system that never sees the correct answer tell you which answers not to trust?" SilentWrong's verifier issues TRUST or FLAG using three model-agnostic layers: an independent second formulation derived from the question's semantic contract (a different query strategy — pre-aggregation, complement counting, partition-and-sum — that must agree within tolerance); reconciliation against governed anchors (control totals maintained at write time, the way finance ledgers actually work); and structural invariants (counts can't exceed populations, net can't exceed gross, averages must sit within min/max).

Result: **23/23 wrong answers flagged, zero false alarms on correct answers.**

The ablation is the finding that matters. Strip out contract-driven reformulation and keep only anchors and invariants — roughly what a company's existing semantic layer gives you — and detection drops to 61%. What slips through is precisely the "wrong meaning" class: wrong date column, wrong aggregation grain, population leaks, rank flips. Reconciliation infrastructure alone cannot catch the errors that matter most; independent reformulation can. That's where the hard, defensible work is.

## What this doesn't show (yet)

100% detection is an upper bound, earned under favorable assumptions: the contracts were precise (real questions are ambiguous — "revenue": booked or collected?); the dual formulations were guaranteed-diverse (in production they come from a model and can share the candidate's misreading — correlated failure is the open research problem); the anchors were exact and fresh (real ledgers drift and lag, which is where false alarms will come from). The next versions attack exactly those: ambiguity detection, correlated-failure resistance, and the false-alarm curve under anchor drift.

## Why I'm building this

Every enterprise deploying AI over its data is one silent error away from a wrong number in a filing. The fix isn't a better model — it's a verification layer that turns "plausible" into "checkable": a verified answer with a confidence score, or an honest "I can't verify this, here's why."

The benchmark is open source: [https://github.com/lakshdadhaniya2007/silentwrong]. If your team runs natural-language analytics and you want to know your real silent-wrong rate — I'm looking for 3–5 design partners to run this against production workloads (anonymized). Reach me: lakshdadhaniya2007@gmail.com.
