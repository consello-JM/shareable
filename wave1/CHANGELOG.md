# Wave 1 crawler changelog

Every parser or collection change, with the reason. Output columns, unit of count (style-color) and Wave 1 scope rules are unchanged by all entries below.

## 2026-09-30: pre-first-run fixes (offline review, no live crawl yet)

Found by running the script's own functions on synthetic pages. The five retail hosts were not reachable from the review environment, so none of this is confirmed against live pages.

| # | Change | Reason | Where |
|---|---|---|---|
| 1 | User agent changed to `AssortmentResearchBot/0.1 (competitive assortment research)`. Firm name and contact email removed. | Requested by engagement lead. Still a declared bot, not a browser impersonation, so robots.txt rules still apply to it. | `UA` |
| 2 | robots.txt check now fails closed. Unreachable or 5xx means the host is skipped; 401/403 means disallow all; other 4xx means no robots.txt, allow all (RFC 9309). The robots.txt fetch uses the crawler's own user agent. | Before, a network error returned `allowed() = True` for every URL, so the check only worked when the site answered. Confirmed in test. | `allowed()` |
| 3 | A `Crawl-delay` in robots.txt is honored when longer than 3 seconds. | The 3-second floor stays; a site asking for more now gets more. | `allowed()`, `polite_wait()` |
| 4 | Fit, fabric, construction, claims and New badge fields now parse product-only text: header, nav, footer, aside and recommendation blocks are removed, and text is cut at the first "You may also like"-type heading. Rating and review count still read the whole page, because review widgets often sit below recommendations. | Recommendation tiles leaked another product's pockets, recycled claim and UPF into the row. Confirmed in test; after the fix those fields come back blank, as they should. Removing the nav also stops a "New" menu item from setting `is_new_flag = Y` on every row. | `product_text()`, `text_fields()`, both row builders |
| 5 | `--llm` fill reads the same product-only text. | Same leak as #4, one step later. | `extract()` |
| 6 | Sitewide promo text ignores sign-up, first-order, email/SMS, app and referral offers, and partial sales ("select styles", "up to X% off"). | Beyond Yoga's "SMS sign-up 20% off first order" would have set `on_promo_flag = Y` on every Beyond Yoga row. Partial-sale markdowns already show in each item's own price. Athleta's "30% off everything" still counts. | `promo_text()`, `NOT_SITEWIDE` |
| 7 | New `fetch --limit N` option: fetch only the first N discovered links per brand. | The kickoff's 10-page sample step had no way to run. | `fetch()`, CLI |
| 8 | Fetch retries Shopify product data (`/products/<handle>.js`) missing from an earlier run, and logs it as `product_json_missing` in `run_summary.json`. Non-200 responses are logged with their status. | Before, if the page saved but its product data didn't, reruns skipped it forever and extraction silently fell back to weaker page parsing. | `fetch()`, `req_get()` |
| 9 | `extract --brands X` keeps other brands' rows already in `wave1_catalog.csv`. | Running one brand at a time wiped the previous brands' rows from the output. | `extract()` |

Known and not changed: list price is the highest price across a style-color's sizes and selling price the lowest, so a style with only some sizes marked down shows the deepest markdown. Check this in QA before relying on `markdown_pct`.
