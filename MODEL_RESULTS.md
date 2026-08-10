# SilentWrong v0.3 — live model results

Silent-wrong = query executed and returned a materially wrong answer (>0.1% numeric error or category flip) with no error or warning. Verifier = blind verification layer from `benchmark_harness.py`.

| Model | Questions | Loud errors | Silent wrong | Silent-wrong rate | Verifier caught | False alarms |
|---|---|---|---|---|---|---|
| mock-naive-v1 | 12 | 0 | 8 | 67% | 8/8 | 0/4 |

## mock-naive-v1  (2026-08-09 16:48)

| Q | Class | Correct | Model answer | Error | Verifier |
|---|---|---|---|---|---|
| E1 | silent_wrong | 2,475,038.70 | 2,548,243.98 | 3.0% | FLAG |
| E2 | silent_wrong | 149 | 159.00 | 6.7% | FLAG |
| E3 | correct | 3,091.55 | 3,091.55 | 0.0% | TRUST |
| E4 | silent_wrong | EMEA | LATAM | FLIP | FLAG |
| E5 | correct | 2,394,539.25 | 2,394,539.25 | 0.0% | TRUST |
| E6 | silent_wrong | 247 | 226.00 | 8.5% | FLAG |
| S1 | correct | 137,748.00 | 137,748.00 | 0.0% | TRUST |
| S2 | silent_wrong | 249 | 333.00 | 33.7% | FLAG |
| S3 | silent_wrong | 18 | 97.00 | 438.9% | FLAG |
| S4 | silent_wrong | 553.20 | 427.79 | 22.7% | FLAG |
| S5 | silent_wrong | 550,325.00 | 664,798.00 | 20.8% | FLAG |
| S6 | correct | ENT | ENT | - | TRUST |
