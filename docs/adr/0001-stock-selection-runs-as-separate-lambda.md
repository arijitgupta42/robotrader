---
status: accepted
---

# Stock selection runs as a separate Lambda, triggered by the scout's S3 output

Picking stocks within Signalled Sectors is not a step inside the sector scout's Lambda. It is a second Lambda that starts when the scout writes a successful result to `successful/YYYY-Www/` in S3, and it sends its own email. We chose this so that the scout's weekly-window and hourly-retry logic stays unchanged. It also lets stock selection use its much heavier dependencies (pandas, numpy, yfinance: about 158 MB unzipped) without growing the scout's package, gives it its own 15-minute timeout, and means a Yahoo outage or bad price data can never stop the sector report from being sent.

## Consequences

- The scout's success JSON in S3 is now a contract between the two functions. Changing its shape needs both sides updated.
- The stock picks arrive as a second email, after the sector report.
