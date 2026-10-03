---
name: methodology-review
description: Quarterly review of the robotrader methodology. Use when the user says "run the methodology review", asks to improve the prompts or thresholds from the backtest results, or shares the quarterly backtest report.
---

Run the quarterly methodology review exactly as written in `docs/methodology-review.md`. Read that file and `docs/methodology-changelog.md` first, then `CONTEXT.md` for the terms.

1. Get the report (the emailed copy the user pastes, `reports/YYYY-Qn/backtest.json` in the bucket, or `python stock_selection_lambda/backtest_report.py --bucket sector-scout-results-544611252144`).
2. Apply the evidence bar and the out-of-sample check from the playbook to every candidate change. Replay rule and threshold changes on the stored snapshots; do not tune on the full history.
3. Write the summary the playbook asks for, including what is still too thin to act on.
4. If something clears the bar, make the change on a new branch, bump `METHODOLOGY_VERSION` and/or `PROMPT_VERSION`, update tests and `docs/methodology-changelog.md`, and open a pull request. If nothing clears it, make no code change and add the changelog entry with the watch items.
5. Never merge, deploy or change AWS yourself. List the deploy steps for the user. The pull request is the end of the review.
