---
status: accepted
---

# Stock selection runs as a separate Lambda, triggered by the scout's S3 output

Picking stocks within Signalled Sectors is not a step inside the sector scout's Lambda. It is a second Lambda that starts when the scout writes a successful result to `successful/YYYY-Www/` in S3. We chose this so that the scout's weekly-window and hourly-retry logic stays unchanged. It also lets stock selection use its much heavier dependencies (pandas, numpy, yfinance: about 130 MB unzipped) without growing the scout's package, and gives it its own 10-minute timeout.

## Consequences

- The scout's success JSON in S3 is now a contract between the two functions. Changing its shape needs both sides updated.
- **One email per week, sent by the stock-selection Lambda** (amended 2026-10-03; originally the scout sent the sector report and the stock picks arrived as a second email). The email is the scout's sector report, with each Signalled Sector's Picks inside its card. The scout no longer sends any email. This reverses the original guarantee that a Yahoo outage could never affect the sector report, so the stock-selection Lambda has to keep it instead: if the picks cannot be computed for any reason (Yahoo down or hanging, thin coverage, an analysis or snapshot error), it still sends the full sector report with a failure notice in place of the picks. A download attempt is abandoned after at most 180 seconds so a hang cannot run into the Lambda timeout, and if even the report cannot be rendered a short failure email is sent.
- The remaining way to get no email is the stock-selection Lambda not running at all (for example the S3 notification being removed, or SES being unavailable).
