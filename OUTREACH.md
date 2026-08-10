# Design-partner outreach template

*Send after the repo is public and live numbers are in. Target: data/analytics leads at companies running "chat with your data" (BI tools with AI, internal text-to-SQL, analytics agents). Personalize the first line; keep the rest short.*

---

**Subject: How often is your AI analytics silently wrong? I measure it.**

Hi [NAME],

[One personalized line — e.g., "Saw your post about rolling out [TOOL] to your analysts."]

AI over real databases fails in a specific way: plausible number, no error, wrong meaning — wrong date column, forgotten soft-deletes, double-counted joins. On realistic-schema benchmarks, frontier models are silently wrong [LIVE X]% of the time.

I built an open-source benchmark that reproduces these failures and a verification layer that — without ever seeing the correct answer — flagged 23/23 wrong answers with zero false alarms: https://github.com/lslsls6969/silentwrong

I'm looking for 3–5 design partners to measure this on real workloads (anonymized schemas + query logs, read-only, NDA fine). You get: your actual silent-wrong rate, which failure modes bite you, and first access to the verification layer. I get: real-world validation.

15 minutes this week?

[YOUR NAME]
lakshdadhaniya2007@gmail.com

---

## Where to find design partners

Warm intros first: anyone you know at companies with data teams. Then: data engineering communities (dbt Slack, Locally Optimistic, r/dataengineering), LinkedIn posts showing the benchmark table (the rank-flip example lands hardest), and replies to people complaining about AI analytics accuracy on X/LinkedIn. The benchmark table IS the pitch — lead with the numbers, not the vision.
