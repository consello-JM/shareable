# Wave 1 run guide: Women's Tights, five brands

## Before you run

1. **Public data only.** All five brands, including lululemon, come from their public websites. Fields the sites don't state plainly (franchise, activity, core vs seasonal) will carry errors. Spot-check 10 rows per brand in QA.
2. **Machine.** Run from a laptop or VM with normal internet access. The Claude.ai sandbox cannot reach retail sites.
3. **Keep the collection limits.** The robots.txt check and the 3-second delay stay on. Sites block fast crawlers, and a blocked crawl costs more time than a slow one.

## Setup

```
pip install playwright requests beautifulsoup4 pandas anthropic
playwright install chromium
```

## Run

```
python crawl_wave1.py discover            # collect product links from each leggings page
python crawl_wave1.py fetch --limit 10    # sample: first 10 product pages per brand
python crawl_wave1.py fetch               # download every product page (resumes if stopped)
python crawl_wave1.py extract             # build wave1_catalog.csv
python crawl_wave1.py extract --llm       # optional: Claude fills blank free-text fields (set ANTHROPIC_API_KEY)
```

Run one brand at a time while you debug, e.g. `--brands Vuori`. At one request every 3 seconds, 300 product pages takes about 30 minutes per brand.

## Outputs

| File | What it is |
|---|---|
| `wave1_catalog.csv` | One row per style-color. Same columns as the Wave1_Catalog tab. Paste from row 2. |
| `data/run_summary.json` | Links found, pages fetched, errors, styles in scope, and each brand's own product count if shown. Feeds the QA_Checks tab. |
| `data/history.csv` | First and last seen date per style-color. Rerun weekly to build newness, markdown and core-versus-seasonal history. |
| `data/raw/<brand>/` | Every page as collected. Re-extract without refetching. |

## First-run checklist (expect to fix these)

The extraction logic was tested offline on sample Shopify and schema.org pages. The live sites have not been crawled yet, so confirm each item below on the first run.

- **Athleta seed.** The seed is the Bottoms page (cid=1025878), filtered by product name. Find the leggings sub-category cid and replace the seed.
- **Athleta and lululemon product data.** Both are parsed from schema.org data embedded in the page. If a brand doesn't publish it, parse lululemon's `__NEXT_DATA__` script block or Athleta's product API response instead.
- **Style codes.** Check `brand_style_code` for 10 rows per brand. The rule takes the SKU prefix up to the first dash. Alo and Beyond Yoga may need a brand-specific pattern.
- **Fabric names.** Every blank `fabric_platform` goes into the Fabric_Normalization tab, then into `FABRIC_NAMES` in the script. The Athleta line names in the script are unverified placeholders: confirm or delete them.
- **Reviews.** Review widgets often load after the page. If `rating` and `review_count` come back blank, capture them in the browser render or drop them from Wave 1.
- **Counts.** Compare `unique_styles_in_scope` to the brand's own count in `run_summary.json`. Brands usually count style-colors, not styles, so check what their number counts before calling a gap.

## Claude Code kickoff prompt

Paste this into Claude Code from the folder holding these files:

> You are running Wave 1 of a competitive product catalog: Women's Tights (leggings and capris) for Alo, lululemon, Vuori, Athleta and Beyond Yoga. Read RUN_WAVE1.md and crawl_wave1.py first.
>
> Work one brand at a time, starting with Vuori, because its product pages are already confirmed to load without a browser. For each brand:
> 1. Run discover, then report links found against the brand's own count.
> 2. Fetch 10 pages only, then extract. Show me 3 rows, and list every required field under 90% fill.
> 3. Fix the parsing for that brand until required fields reach 90% on the sample.
> 4. Only then fetch the full list.
>
> Rules: never invent values. Blank beats guessed. Keep the 3-second delay and the robots.txt check. Log every parser change in CHANGELOG.md with the reason. Stop and ask me before changing the output columns, the unit of count (style-color), or the Wave 1 scope rules (capris in, biker shorts out, sets excluded).

## Weekly after Wave 1

Rerun `discover`, `fetch` and `extract` every Tuesday: lululemon drops new product on Tuesdays. After 6 weekly captures, `history.csv` supports three measures: newness rate, markdown depth and timing by brand, and which style-colors are core (present every week) versus seasonal (drop out).
