# SilentWrong

**A benchmark for the most dangerous failure in AI analytics: answers that are confidently wrong — and a blind verification layer that catches them.**

When AI answers questions over real business databases, it doesn't fail loudly. It returns a plausible number with no warning: right syntax, wrong meaning. Published results show accuracy collapsing from ~91% on toy schemas to ~6–21% on real enterprise databases (Spider 2.0). SilentWrong reproduces the documented failure modes in controlled, deterministic form and measures whether a verifier **that never sees the correct answer** can tell you which answers not to trust.

## Headline results (v0.2)

Across 23 materially wrong answers over two realistic schemas (10 documented failure modes, errors from 0.5% to 9,900%, including two categorical rank flips):

| Configuration | Detection | False alarms |
|---|---|---|
| Full verifier (dual formulation + governed anchors + invariants) | **23/23 (100%)** | **0/12** |
| Ablation: anchors + invariants only | 14/23 (61%) | 0/12 |

The ablation is the finding: control totals alone (what a finance ledger or semantic layer gives you) miss the "wrong meaning" errors — wrong date column, wrong aggregation grain, population leaks, rank flips. Those are caught only by independent reformulation from a semantic contract.

## Live model results (v0.3)

Silent-wrong = query executed and returned a materially wrong answer (>0.1% or category flip) with no error.

| Model | Questions | Loud errors | Silent wrong | Verifier caught | False alarms |
|---|---|---|---|---|---|
| **gemini-flash-latest** | 12 | 1 | **2 (18% of answered)** | 2/2 | 0/9 |
| mock-naive-v1 (pipeline test) | 12 | 0 | 8 (67%) | 8/8 | 0/4 |

A live frontier model was silently wrong on roughly **1 in 5** answered questions. Both failures are the dangerous kind: asked for *cash collected* in Q2 it queried the invoices table instead of payments (**45.6% overstatement** — the billed-vs-collected trap), and ARPA came back **1.2%** off, small enough that no reviewer would ever question it. Neither produced an error or warning. The verifier flagged both and did not false-alarm on any of the 9 correct answers.

Caveat on sample size: n=2 wrong answers is far too small to claim a detection rate. In this run the governed anchors alone caught both, so it does *not* independently reproduce the dual-formulation advantage seen in the v0.2 mutation study — that needs more questions and more models.

Run your own (results append to `MODEL_RESULTS.md`):

```bash
ANTHROPIC_API_KEY=... python3 model_runner.py --provider anthropic --model claude-sonnet-4-5
OPENAI_API_KEY=...    python3 model_runner.py --provider openai --model gpt-4.1
GOOGLE_API_KEY=...    python3 model_runner.py --provider google   # FREE tier (aistudio.google.com, no card)
```

No budget? Google AI Studio and Groq both offer free API tiers with no card required. `--provider google` uses Gemini directly; setting `OPENAI_BASE_URL` points the openai provider at any OpenAI-compatible endpoint (Groq, OpenRouter, local Ollama).

## How it works

Two deterministic SQLite schemas (seed=7, stdlib only, no dependencies): **e-commerce** — soft-deleted customers, voided orders, partial refunds, order↔line-item fan-out, order-vs-ship dates; **SaaS billing** — amounts in cents, trials, multi-subscription accounts, churn dates, invoiced-vs-collected cash. Twelve business questions, each with a precise semantic contract and a known-correct answer.

The verifier issues TRUST/FLAG using three model-agnostic layers: **dual formulation** (an independent query strategy derived from the contract — pre-aggregation, complement counting, partition-and-sum; disagreement ⇒ flag), **governed anchors** (control totals maintained independently at write time, like a finance ledger; reconciliation failure ⇒ flag), and **invariants** (count ≤ population, net ≤ gross, average within min/max, valid category).

```bash
python3 benchmark_harness.py    # v0.2: failure-mode benchmark + verifier + ablation
python3 model_runner.py --provider mock    # v0.3 pipeline test, no API key needed
```

Model SQL runs read-only (SQLite authorizer + keyword filter + step limit).

## Honest limitations

100% detection is an upper bound earned under favorable assumptions: precise contracts (real questions are ambiguous), guaranteed-diverse dual formulations (in production they come from a model and can share the candidate's misreading), and exact fresh anchors (real ledgers drift — that's where false alarms will come from). The roadmap: ambiguous-contract questions where the verifier must detect underspecification, anchor-drift injection to measure the false-alarm curve, more schemas, live model runs at scale.

## Files

`benchmark_harness.py` — schemas, questions, mutations, verifier, ablation. `model_runner.py` — live-model runner (Anthropic/OpenAI/mock). `BENCHMARK_REPORT.md` — full v0.2 analysis. `MODEL_RESULTS.md` — accumulated live results. `benchmark_results.csv` — per-case data.

## License

MIT — see LICENSE.
