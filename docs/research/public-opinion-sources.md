# Public-opinion / retail-attention sources to supplement the sector scout

Date: 2026-10-31. Scope: free or near-free inputs for a weekly AWS Lambda pipeline, sector-level first, stock-level second, 2-6 week swing horizon.

Marker legend: **[V]** = verified on a primary source I could open today. **[U]** = unverified (secondary source only, or not reachable). **[P]** = verified by a live probe from my machine (not from Lambda).

---

## (a) Bottom line and ranked recommendation

**Bottom line.** There is no free, legal, stable replacement for Reddit RSS that gives *signed retail sentiment* at sector level. The two big retail-sentiment venues are closed to a new hobby project: Stocktwits has stopped new API registrations and bans scraping, and X is pay-per-use. What *is* free, official, unauthenticated and Lambda-friendly is **attention volume** (Wikipedia pageviews, GDELT news volume/tone, Bluesky post counts, HN). Attention is non-directional, and the academic evidence says retail attention is at best a short-horizon momentum signal that reverses, and in the Robinhood evidence a contrarian "crowded trade" flag. So: add these as context/crowding inputs to the consolidation pass, **log the raw numbers weekly from day one**, and let the quarterly methodology review test whether they add anything before they carry weight. Separately, Reddit RSS itself is at risk: Reddit's own help centre says traffic from a hosted-provider netblock needs OAuth or login [V], and Lambda egress is exactly that.

**Ranked top 3 to add first**

| # | Source | Why first | Cost | Requests/week |
|---|---|---|---|---|
| 1 | Wikimedia Pageviews (per-article, daily) | Official, CC0, documented limits, no key, closest analogue to the Google-SVI literature; works at both company and sector (aggregate of constituents) level | $0 | ~250 (31 sub-sectors x ~8 articles; each call returns up to many days) |
| 2 | GDELT DOC 2.0 (timeline volume + tone) | Free, "unrestricted" use, sector-level tone/volume from keyword queries you already have in `SECTORS`; independent aggregation vs your 37 RSS feeds (but overlapping publishers) | $0 | ~25-60 (2 per sector; run only for sectors the news pass flagged) |
| 3 | Bluesky public search (post counts per cashtag/ticker, last 7d vs prior 7d) | Free, unauthenticated, returns `hitsTotal` so one call = one attention count; stock-level; replaces some of the Reddit/Stocktwits role | $0 | ~80 (top ~40 tickers in flagged sectors x 2 weeks) |

**Optional 4th (cheap, tech sectors only):** Hacker News via Algolia (`nbHits` over 7 days for sector keywords) - $0, ~10 calls/week, tech/AI/semis only.
**Apply-and-wait (zero cost):** Google Trends official alpha (needs an application form; see section c6).
**Skip:** Stocktwits, X (as a core input), unofficial Google Trends, CNN Fear & Greed, CBOE put/call, AAII (paid history), YouTube comments, Telegram/Discord, Mastodon/Lemmy, Reddit Data API (approval route effectively closed to this use). Reasons below.

### Suggested minimal integration (all three picks)

New module `attention_fetcher.py` (sibling of `reddit_fetcher.py`), run in the scout cycle on a thread like the Reddit background job, **after the news pass and before consolidation**:

1. **Wikipedia.** Static map in `config.py`: sub-sector -> 5-10 article titles (leaders, ETF/index articles). Per article: one GET for the last ~70 days daily views. Compute per sector: mean of last 7 days vs trailing 8-week median, as a z-score; also flag the top single-article spikers. Weak points: title redirects and disambiguation (`Nvidia` worked [P], others need checking), UK mid-caps often have thin pages. Request budget ~250/week at <=3 concurrent, well under the 200 req/min limit [V]. Cost $0.
2. **GDELT.** Build the query from the sector's `SECTORS[...]` keywords (OR inside one parenthesis group; nesting unsupported [V]) plus `sourcelang:eng`. Two calls per sector: `mode=timelinevolraw` (volume plus normaliser) and `mode=timelinetone`, `timespan=8weeks` (daily resolution beyond a week [V]). One call took **15.8 s** in my probe [P], so run with 3-4 workers and only for flagged sectors, or you will blow Lambda's 15-minute cap (62 sequential calls ~ 16 min). Output: "news volume z-score" and "tone delta" per sector. Cost $0.
3. **Bluesky.** For top-N tickers surfaced by the stock stage, call `app.bsky.feed.searchPosts?q=$TICKER&since=..&until=..&limit=25`; read `hitsTotal` for the week and the prior week; keep the 25 sample texts only if you want an LLM tone read. Cost $0, ~80 calls/week, ~0.2-0.9 s each [P].

**How it feeds the LLM passes:** do *not* add a fourth LLM pass. Append one compact table ("attention context: sector, wiki z, news-volume z, GDELT tone delta, top attention spikes, Bluesky counts") to the **consolidation** prompt, with an explicit instruction that attention is unsigned and extreme retail attention is a *crowding/reversal* caution, not confirmation. Persist every raw value to S3 (JSON per week) so the quarterly backtest can regress forward 2-6 week sector returns on it. Total extra Lambda time ~3-6 min with concurrency; no new AWS service, so cost stays on the free tier (outbound HTTPS only).

---

## (b) Comparison table

| Source | Gives | Auth | Official limit | Cost | Automated-use legal status | Sector fit | Stock fit | Lambda egress | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| Reddit Data API | posts/comments | OAuth, approval needed | 100 QPM/client id (free tier) [V] | free if approved | non-commercial only via approval; no AI/ML training; research banned outside RFR [V] | good | good | ok with OAuth | Not obtainable in practice |
| Reddit RSS (current) | posts | none | ~1 req/min/IP (your measurement) | free | grey; hosted netblock needs OAuth/login per help centre [V] | good | ok | **at risk** | Keep, but don't depend on it |
| Stocktwits | cashtag streams, sentiment tags | n/a | n/a | n/a | new API registrations closed; scraping banned [V] | - | - | - | Skip |
| Bluesky search | post text, counts | none (api.bsky.app) | "generous", unpublished for public endpoints [V] | free | ToS not read [U] | weak | ok (US large caps) | public.api 403 from my IP; api.bsky.app 200 [P]; Lambda untested | Add (#3) |
| Bluesky Jetstream | live firehose | none for live tail [V] | 100 collections / 10k DIDs per sub [V] | free | not read [U] | - | - | needs persistent WebSocket | Skip (not Lambda-shaped) |
| X API | post counts/posts | paid credits | pay-per-use [V] | $0.005/post read; $0.005/recent-count request [V] | developer agreement not read [U] | ok | good | ok | Skip as core; optional paid add-on |
| Wikimedia pageviews | daily views per article | none, UA required | 200 req/min compliant bot UA [V] | free, CC0 [V] | explicit open licence [V] | good | good | not documented as blocked [U] | **Add (#1)** |
| Google Trends official | consistently scaled search interest, 5y rolling | alpha application | not published [V] | unknown | not published [V] | good | ok | n/a | Apply, don't plan on it |
| pytrends (unofficial) | same, scraped | none | unknown; ~60 s sleeps reported [V] | free | "not official or supported"; repo archived 2025-04-17 [V] | ok | ok | 429s widely reported [U] | Skip |
| Hacker News | stories/comments | none | Firebase: none stated; Algolia: 10,000/h/IP [V] | free | not read [U] | tech only | tech only | ok [P] | Optional |
| Mastodon / Lemmy | public hashtag timelines | none (instance-dependent) | 300 req/5 min/IP (Mastodon default) [V] | free | per instance | tiny finance volume | tiny | ok | Skip |
| GDELT DOC 2.0 | news volume + tone | none | none published [V] | free, unrestricted use [V] | attribution if you redistribute [V] | good | ok | untested, slow (15.8 s) [P] | **Add (#2)** |
| AAII sentiment | weekly bull/bear/neutral | site | n/a | latest week free on page; history $198/yr [V] | "for individual use" [V] | market-wide only | no | scraping HTML | Skip |
| CNN Fear & Greed | composite index | none officially | n/a | free | no documented API [U] | market-wide | no | - | Skip |
| CBOE put/call | volume ratios | none | n/a | free CSVs end 2019; today's page only [V] | site T&Cs | market-wide | no | - | Skip |
| FINRA short interest | short interest per stock | OAuth credential | 1,200 req/min/IP sync [V] | free tier unclear [U] | account terms | no | good (US only, no UK) | ok | Maybe later (not opinion) |
| UMich sentiment | consumer sentiment | none | n/a | free | n/a | macro only | no | - | Skip (monthly, macro) |
| YouTube Data API | video comments | API key | 10,000 units/day [V] | free | ToS not read [U] | weak | weak | ok | Skip |
| SEC EDGAR | filings, insider forms | none, UA required | 10 req/s [V] | free | fair-access policy [V] | no | good | ok | Out of scope (not opinion) |

---

## (c) Per-source sections

### c1. Reddit official Data API / approval route
- **What is verified.** Help-centre Responsible Builder Policy: "You must request access and get explicit approval before accessing any Reddit data through our API." Developers are told to use Devvit; Data API access for mod-tool developers is "granted solely for the purpose of performing moderation actions"; commercial use needs "explicit written approval"; researchers only via the Reddit for Researchers programme; "No Unapproved Commercialization or AI Training ... extends to commercial and non-commercial mining, scraping, or using data ... to train machine learning or AI models." https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy [V]
- Data API Wiki: "Clients must authenticate with a registered OAuth token. We can and will freely throttle or block unidentified Data API users." Free-tier limit **100 queries per minute per OAuth client id**, averaged over 10 minutes. "Traffic not using OAuth or login credentials will be blocked." Required User-Agent format `<platform>:<app ID>:<version> (by /u/<user>)`. Deleted content must be purged (they recommend within 48 h). https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki [V]
- Developer-services FAQ: "If you're accessing Reddit from an IP address in a hosted service provider's netblock, you will be required to have a valid OAuth token or be logged in" (exceptions at Reddit's discretion). Also: research via developer tools "is a violation"; content may not be used "as an input for any model training without explicit consent." https://support.reddithelp.com/hc/en-us/articles/14945211791892-Developer-Platform-Accessing-Reddit-Data [V]
- **Fit for this project.** A personal, non-commercial analytics bot that reads many subreddits is not one of the listed approved categories (Devvit apps, mod tools, RFR research). Approval is discretionary; third-party reports of denials exist but are anecdotal [U]. Using an LLM to classify posts is inference rather than training, but the policy's "mining, scraping" language makes this a grey zone that only Reddit can resolve [U].
- **The "new requests stop 2026-10-31 / logged-out .json returns 403" claim: UNVERIFIED.** I could not open Reddit's r/redditdev announcement, the Data API Terms (redditinc.com) or developers.reddit.com (blocked in my tooling), and none of the help-centre pages I could read mention that date. Only secondary sites (a Medium post and vendors selling alternatives such as redditapis.com) repeat it; treat as unconfirmed. The help-centre text does support the *spirit*: approval required, OAuth required, hosted netblocks blocked without it.

### c2. Stocktwits
- Official developer page: "we are currently reviewing all of our APIs ... We unfortunately won't be accepting new registrations until we have finished our review"; page footer is (c) 2021, so this has been the status for years. https://api.stocktwits.com/developers [V]
- ToS: "You may not scrape, harvest, mirror, frame, deep-link to, data-mine, or otherwise extract data or content from the Service by automated means except as expressly authorized by us in writing or through an approved API". https://stocktwits.com/about/legal/terms [V]
- Rate limits: not obtainable (no access). RapidAPI listings exist but are third-party resellers of unknown legitimacy [U].
- Fit: it was the best structured retail-sentiment source (self-tagged bull/bear). **Not available. Skip.**

### c3. Bluesky
- **Search.** `app.bsky.feed.searchPosts` on `api.bsky.app` answered unauthenticated in 0.23 s and 0.85 s; query `$NVDA` with `since/until` over 24-31 Oct returned `hitsTotal: 644`, `limit=100` honoured, cursor pagination. [P] `public.api.bsky.app` returned **HTTP 403 (HTML error page)** to the same style of request with a descriptive User-Agent [P] - probably CDN-level blocking of search on the cached public host; I did not investigate further. A `$NVDA` query also returned posts containing "NVDA" without a dollar sign, so counts are token matches, not strict cashtags [P].
- Rate limits: official page says the direct Bluesky API endpoints (`api.bsky.app`, `public.api.bsky.app`) "do not support authentication", have "generous rate-limits" and to contact them if limited; the only numeric limit I could find is 3,000 requests/5 min per IP for PDS-hosted authenticated requests, which is not this path. https://bsky.network/docs/rate-limits/ [V]
- **Cashtags.** TechCrunch (2026-01-16) reports cashtags shipped in app v1.114 alongside Live badges. https://techcrunch.com/2026/01/16/bluesky-rolls-out-cashtags-and-live-badges-amid-a-boost-in-app-installs/ [U - secondary, I did not find Bluesky's own announcement; the "What's Next at Bluesky" blog post (2026-01-26) is cited by GIGAZINE but I could not open it]. Whether the cashtag is indexed as a distinct facet for search is not documented in anything I read.
- **Jetstream.** Live tail needs no auth; filter `collections=app.bsky.feed.post`; max 100 collections / 10,000 DIDs per subscription; public hosts `jetstream{1,2}.us-{west,east}.bsky.network` and v2 `jetstream.us-*.bsky.network`. https://bsky.network/docs/jetstream/ [V]. It is a WebSocket stream, so it does not fit a once-a-week Lambda; replay requires authenticated calls [V]. Skip.
- ToS / developer terms for bulk reading: not read [U].
- Quality: volume is thin (644 posts/week for the most-discussed US stock) and the user base skews news/tech/politics, so UK and mid-cap coverage will be near zero. Manipulation risk: cashtag spam/bots exist on every platform; no Bluesky-specific data seen [U]. Lambda IPs: untested; a 403 on one host already shows CDN rules differ per host.

### c4. X (Twitter) API
- Official pricing page now describes **pay-per-usage only**: post reads $0.005 per resource, user reads $0.010, "Counts: Recent" $0.005 per request, post create $0.015 (URL: $0.200); pay-per-use capped at 3 million post reads per billing cycle; new payers can earn $20 credits after saving a card and a first purchase. https://docs.x.com/x-api/getting-started/pricing [V] I did not find a free tier on that page; developer.x.com returned HTTP 402 to my fetch.
- Recent counts: `GET /2/tweets/counts/recent`, last 7 days, minute/hour/day buckets, `total_tweet_count`. https://docs.x.com/x-api/posts/counts/introduction [V]. Whether the `$` cashtag operator is supported on this plan: not stated there [U].
- Cost to use counts for 31 sectors + 40 tickers weekly: ~71 requests x $0.005 = ~$0.36/week (~$1.5/month) [arithmetic on the verified price]. It is billable, needs a stored card and prepaid credits, so it breaks the "stay free" rule unless you explicitly accept it. Developer Agreement/automated-use terms not read [U]. Bot/pump-group noise on cashtags is well known (e.g. SEC/FINRA actions), but I did not source it here [U].
- Verdict: only worthwhile as a tiny, explicitly flagged paid add-on for counts; not recommended now.

### c5. Wikimedia Pageviews API
- Endpoint `https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{project}/{access}/{agent}/{article}/daily/{start}/{end}`. Probe for `Nvidia`, 2026-10-01..30, `agent=user`, returned 200 in 0.19 s with daily integers (5,277 on 1 Oct, 4,992 on 2 Oct) and `cache-control: max-age=14400` (4 h cache). [P]
- Policy: every request needs a descriptive User-Agent `<client>/<version> (<contact>)` or it "may be blocked without notice"; data licensed **CC0 1.0**; bulk dumps available. https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/documentation/access-policy.html [V]
- Rate limits (general Wikimedia APIs page): unidentified 10 req/min; unauthenticated bot with compliant UA 200 req/min; recommend <=3 concurrent requests and honouring `Retry-After`. The page does not say how general cloud IPs are treated and does not give pageviews-specific numbers. https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits [V for the table; Lambda behaviour U]
- Signal: unsigned attention for a company/topic. Known weaknesses: bots/automated agents (use `agent=user`), spikes from non-financial events (films, scandals, product launches), page renames, language editions (use en for both markets; UK mid-caps have low counts and noisy baselines). Manipulation: low (cannot be bought cheaply at scale, but a viral non-market event inflates it).
- Fit: best for "sector got noticed" as a leading/confirming input; matches the Google-SVI literature (section d), including its short-horizon-then-reverse shape.

### c6. Google Trends
- **Official alpha:** programmatic access, "rolling window of the last 5 years", daily/weekly/monthly/yearly aggregation, regional comparisons, "consistently scaled" data (not 0-100 renormalised, so dozens of terms comparable). Access by application form; priority to developers with a defined use case who can start soon and give feedback. Quotas, terms and GA timeline are **not stated** on the page. https://developers.google.com/search/apis/trends [V]. I did not submit an application (it is a form under your identity). Cost of applying: nil. Treat as upside only.
- **Unofficial (pytrends):** GitHub repo "archived by the owner on Apr 17, 2025"; README: "not an official or supported API"; rate limit "not publicly known", ~60 s sleep suggested once limited. https://github.com/GeneralMills/pytrends [V]. Widespread HTTP 429s from cloud IPs are claimed by vendor blog posts that sell alternatives [U]. Skip.
- Data semantics: samples, normalised to share of searches 0-100 per request (https://support.google.com/trends/answer/4365533 [V]) - another reason the "consistently scaled" alpha matters for week-over-week comparison.

### c7. Hacker News
- Firebase API `https://hacker-news.firebaseio.com/v0/` (top/new/best stories, items, updates): "There is currently no rate limit"; no auth. https://github.com/HackerNews/API [V]. `maxitem.json` returned 200 in 0.28 s [P].
- Algolia search API: `search_by_date?query=..&tags=story&numericFilters=created_at_i>X`, returns `nbHits`; "limiting the number of API requests from a single IP to **10,000 per hour**". https://hn.algolia.com/api [V]. Probe returned 200 in 0.35 s (note `exhaustiveNbHits:false`, so counts can be approximate) [P].
- Fit: only semis/AI/software/cyber sectors; audience is developers not retail traders. Manipulation: vote rings are known but I have no source here [U]. Legal: ToS not read [U]. Cheap enough to include for tech sub-sectors only.

### c8. Mastodon / Lemmy
- Mastodon hashtag timelines are public unless an instance disables them (then 401), 20 statuses default, 40 max; default limits 300 requests per 5 min per IP. https://docs.joinmastodon.org/methods/timelines/#tag and https://docs.joinmastodon.org/api/rate-limits/ [V]. Each instance federates only part of the network, and there is no mainstream finance community with meaningful volume (my judgement, not sourced). Lemmy not researched beyond this; same judgement. Skip.

### c9. Sentiment-bearing public datasets
- **GDELT DOC 2.0** (https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/ [V]): modes `TimelineVol`, `TimelineVolRaw` (raw counts plus total-monitored `norm`), `TimelineTone` (average tone), `ToneChart` (-100..+100 histogram), `ArtList` (MAXRECORDS up to 250); operators `theme:`, `sourcecountry:`, `sourcelang:`, `domain:`, `tone<`/`tone>`; booleans with OR in a single parenthesis group; default window 3 months rolling (launch post also claims back to 2017 - docs conflict, verify before relying on history); resolution 15 min (<72 h), hourly (72 h-1 week), daily beyond; JSON/CSV output, CORS `*`. No rate limits or auth stated on that page. Licence: free, "unlimited and unrestricted use" incl. commercial; attribution only if you redistribute. https://www.gdeltproject.org/about.html [V]. Probe: `"semiconductor" sourcelang:eng`, `timelinetone`, 14d, JSON returned 200 but took **15.8 s**; daily average tone values around 0.2-1.4 [P]. Caveats: tone is a dictionary-based average and noisy per topic; it measures *news* not retail opinion; publisher overlap with your RSS list; keyword queries need tuning to avoid homonyms.
- **AAII Investor Sentiment Survey:** weekly bullish/neutral/bearish, results Thursdays; the page shows the latest weeks (week ending 2026-10-28: 37.8% / 20.7% / 41.4%), "compiled and depicted for individual use"; full history requires membership ($198/yr Sentiment Investing); no API; market-wide only. https://www.aaii.com/sentimentsurvey [V]. Could be hand-copied from the free weekly email for a market regime flag, but there is no sector content. Skip.
- **CNN Fear & Greed:** I found no official API or terms; only blogs describing a seven-indicator composite whose formulas CNN does not disclose, with conflicting component lists. [U]. It is also built largely from price/volatility inputs you already have. Skip.
- **CBOE put/call:** free CSVs on the historical page stop at 2019-10-04; today's ratios appear on the daily-stats page only, no CSV; use subject to Cboe T&Cs; DataShop is separate (paid). https://www.cboe.com/us/options/market_statistics/historical_data/ [V]. Skip.
- **FINRA short interest:** Query API needs OAuth client credentials created in the API Console; individual accounts can create "Public and Mock" credentials; sync limit 1,200 req/min per IP, 5,000 records per call; whether "Consolidated Short Interest" is on the free public tier, and its cadence, are not stated in the pages I read (doc is huge; I read only the first 100k characters). https://developer.finra.org/docs [V for limits, U for tier/cadence]. US-only, stock-level; useful as a crowding input but it is positioning, not opinion, and it does not help UK names. Revisit after the three picks.
- **Surveys:** University of Michigan consumer sentiment is monthly (final Oct 2026: 48.0; next release 2026-11-06), macro-only. https://www.sca.isr.umich.edu/ [V]. ZEW and UK equivalents (BoE/Ipsos, GfK): I could not open ZEW; not researched further [U]. All are monthly macro, wrong frequency and granularity. Skip.

### c10. Other candidates
- **SEC EDGAR:** 10 requests/second max, declare a User-Agent with contact, JSON APIs on data.sec.gov; no crawling by bots. https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data [V]. Valuable for insider/13F "informed money" but that is not public opinion, and does not cover UK names. Out of scope here; worth a separate research note.
- **YouTube Data API:** page says default 10,000 units/day shared pool, `commentThreads.list` and `videos.list` 1 unit each, and `search.list` capped at 100 calls/day. https://developers.google.com/youtube/v3/determine_quota_cost [V - note the page's `search.list` cost text looked odd in my extract; re-check before building]. Comments on finance videos are low-signal and bot-heavy (my judgement). Skip.
- **Telegram public channels / Discord:** not researched; pump-and-dump groups are the best-known use of both, and Discord servers need an invited bot (my judgement, unsourced) [U]. Skip.

---

## (d) Academic evidence on retail attention / sentiment

Only abstract-level reads were possible for most papers (paywalls); I did not see tables, so effect sizes below are as reported in abstracts or index pages and flagged accordingly.

1. **Da, Engelberg, Gao (2011), "In Search of Attention", Journal of Finance 66(5), 1461-1499.** https://ideas.repec.org/a/bla/jfinan/v66y2011i5p1461-1499.html (citation [V]; the abstract was not on that page). Abstract as relayed by a university repository (https://academicnewsletter.sufe.edu.cn/info/360414) [U-secondary]: Google Search Volume Index for Russell 3000 stocks 2004-2008 captures (likely retail) attention; **higher SVI predicts higher prices over the next two weeks, then a reversal within the year**, and helps explain IPO first-day returns and long-run underperformance. Popular coverage says the authors note no trading costs were considered, so this is not a demonstrated net-of-cost strategy [U]. Implication: attention = short momentum then mean reversion; sample is pre-2009 and pre-social-media.
2. **Barber, Huang, Odean, Schwarz (2022), "Attention-Induced Trading and Returns: Evidence from Robinhood Users", Journal of Finance 77(6), 3141-3190.** https://ideas.repec.org/a/bla/jfinan/v77y2022i6p3141-3190.html [V]. Heavy Robinhood buying of top-bought stocks precedes **average 20-day abnormal returns of about -4.7%**; trading is attention-driven and the app design contributes. This is the closest to your 2-6 week horizon and recent, but it is *contrarian* ("crowded retail buying underperforms") and the underperformance is in the stocks retail piles into, which are often exactly what a bullish scout would flag.
3. **Bradley, Hanousek, Jame, Xiao (2024), "Place Your Bets? The Value of Investment Research on Reddit's Wallstreetbets", Review of Financial Studies 37(5), 1409-1459.** https://ideas.repec.org/a/oup/rfinst/v37y2024i5p1409-1459..html [V]. Before the GameStop squeeze, WSB due-diligence posts predicted returns and cash-flow news; **that predictive power disappears after GME**, with later posts skewed to price-pressure and attention-grabbing stocks. Direct evidence that Reddit-derived signal decayed in recent years.
4. **Chen, De, Hu, Hwang (2014), "Wisdom of Crowds: The Value of Stock Opinions Transmitted Through Social Media", Review of Financial Studies 27(5), 1367-1403.** https://ideas.repec.org/a/oup/rfinst/v27y2014i5p1367-1403..html [V for abstract; horizon and magnitudes U]. Opinions in Seeking Alpha articles and comments predict future returns and earnings surprises (2005-2012, >7,000 firms). One secondary summary reports about 0.38% lower abnormal returns per +1% negative-word fraction in articles [U]. Note it is *curated long-form* opinion, not high-frequency chatter; I could not verify the horizon or whether it survives costs/recent years.
5. **Moat et al. (2013), "Quantifying Wikipedia Usage Patterns Before Stock Market Moves", Scientific Reports 3, 1801.** https://www.nature.com/articles/srep01801 [V - abstract]. Abstract is hedged: changes in views of financial Wikipedia pages "may have contained early signs of stock market moves". I only read the abstract, not the trading-strategy results; the authors are physicists, the horizon is market-index level, and it is a single sample period. Weak/suggestive.
6. (For context) **Sprenger et al. (2014), "Tweets and Trades", European Financial Management 20(5), 926-957.** https://ideas.repec.org/a/bla/eufman/v20y2014i5p926-957.html [V]. Reports tweet sentiment associated with returns, message volume with trading volume, disagreement with volatility; horizon not stated on the page.

**Honest read.** (i) Evidence that attention/sentiment predicts returns is mostly stock-level, pre-2015 and short-horizon (days to ~2 weeks) with reversals; I found **no** peer-reviewed evidence of a net-of-cost, sector-level, 2-6 week edge from free public sentiment feeds. (ii) The one clearly recent result (Barber et al.) says crowded retail buying is a negative signal, the other recent one (Bradley et al.) says WSB's informativeness vanished after 2021. (iii) Volume-type signals (attention, disagreement) relate more reliably to volatility and volume than to direction. Hence the recommendation to use these as unsigned context and crowding flags, and to measure them in your own backtest before giving them weight.

---

## (e) Reddit access status

| Item | Status |
|---|---|
| Approval required before any API access | **Verified** (Responsible Builder Policy) |
| Free tier limit 100 QPM per OAuth client id, 10-min averaging | **Verified** (Data API Wiki) |
| OAuth mandatory; unauthenticated traffic "will be blocked" | **Verified** (Data API Wiki) |
| Hosted-provider IPs (AWS etc.) need OAuth or login | **Verified** (developer-services FAQ) - implies RSS from Lambda is a policy violation risk and may be blocked at any time |
| Developers pointed to Devvit; Data API for mod tools; non-commercial analytics bots not a listed category | **Verified** (policy text); approval odds for you: unknown |
| No AI/ML training on Reddit content; "mining, scraping" language | **Verified** (policy text); whether LLM classification counts: unresolved |
| Research only via RFR programme | **Verified** |
| New public-API requests stop 2026-10-31; remaining public access ends March 2027; logged-out `.json` returns 403 | **UNVERIFIED** (secondary/vendor sites only; Reddit's own announcement and Data API Terms were unreachable for me) |
| Reddit's rules on RSS feeds specifically (User Agreement / Public Content Policy) | **Not read** |
| Self-service app creation at reddit.com/prefs/apps | Reported broken/closed by vendors [U]; not tested (no sign-ups) |

Practical stance: keep RSS as a best-effort input with graceful degradation (the pipeline should produce signals without it), don't build new features that need it, and treat any Reddit-dependent backtest as non-reproducible going forward.

---

## (f) Open questions

1. Do Lambda egress IPs get blocked or throttled by Wikimedia, GDELT, `api.bsky.app`? Nobody documents this; one 5-minute test Lambda invocation against each answers it. (My probes came from a home IP.)
2. Is the GDELT DOC API rate-limited per IP? Nothing is published; my single call took 15.8 s. Test 3-4 parallel calls before designing around concurrency.
3. Does Bluesky search index `$TICKER` as a cashtag facet, and what are the bulk-read terms? Check Bluesky's own docs/ToS and the app 1.114 release notes.
4. Does X's counts endpoint support the `$` cashtag operator on pay-per-use, and is a $0.36/week spend acceptable under the "cost must stay free" rule?
5. Should you apply to the Google Trends alpha (free form under your identity)? It is the only credible searches-based feed; outcome unknowable.
6. Wikipedia title mapping: who curates the sector -> articles map, and does it include UK-listed names with enough views to be useful? A one-off audit of the 31 sub-sectors would tell you.
7. Is FINRA consolidated short interest on the free individual tier, and what is its cadence? Not established today.
8. Will the quarterly backtest have enough weekly history (needs ~2-3 quarters of logged attention values) to say anything? Start logging now.
9. Can Reddit be asked directly (support form linked from the policy) whether a personal non-commercial sector-sentiment bot qualifies? Not attempted; sending the form is your decision.
