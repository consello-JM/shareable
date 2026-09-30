#!/usr/bin/env python3
"""
Wave 1 crawler: Women's Tights (leggings + capris) for Alo, lululemon, Vuori, Athleta, Beyond Yoga.

Stages (run in order, or `all`):
  discover  Render each brand's leggings category page in a browser, scroll to load every tile,
            collect product page links, log the brand's own product count if shown.
  fetch     Download each product page. Shopify brands: page HTML + /products/<handle>.js.
            Other brands: browser-rendered HTML. Everything raw is saved to data/raw/ for audit.
  extract   Parse raw files into one row per style-color and write wave1_catalog.csv,
            using the same columns as the Wave1_Catalog tab in the schema workbook.
            Add --llm to fill free-text fields the rules could not (needs ANTHROPIC_API_KEY).

Setup:
  pip install playwright requests beautifulsoup4 pandas anthropic
  playwright install chromium

Rules this script follows:
  - Checks robots.txt before every request and skips disallowed paths.
  - One request every DELAY seconds per site. Descriptive user agent.
  - Nothing is inferred from memory. Blank means not found on the page.

Public data only. Status: extraction logic tested offline on sample pages;
live selectors and URL patterns need confirming on the first run (see RUN_WAVE1.md).
"""
import argparse, csv, json, os, re, sys, time, hashlib, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
from urllib import robotparser

DATA = Path("data"); RAW = DATA / "raw"
DELAY = 3.0
UA = "AssortmentResearchBot/0.1 (competitive assortment research)"
TODAY = datetime.date.today().isoformat()

BRANDS = {
    "Alo": {
        "home": "https://www.aloyoga.com/",
        "seeds": ["https://www.aloyoga.com/collections/womens-leggings"],
        "product_re": r"/products/[^/?#]+",
        "shopify": True,
    },
    "lululemon": {
        "home": "https://shop.lululemon.com/",
        "seeds": ["https://shop.lululemon.com/c/women-leggings/n1udsq",
                  "https://shop.lululemon.com/c/women-capris/n1ml6n"],
        "product_re": r"/p/[^/?#]+/[^/?#]+",
        "shopify": False,
    },
    "Vuori": {
        "home": "https://vuoriclothing.com/",
        "seeds": ["https://vuoriclothing.com/collections/womens-leggings"],
        "product_re": r"/products/[^/?#]+",
        "shopify": True,
    },
    "Athleta": {
        "home": "https://athleta.gap.com/",
        # Bottoms. Confirm the leggings sub-category cid on first run and replace this seed.
        "seeds": ["https://athleta.gap.com/browse/category.do?cid=1025878"],
        "product_re": r"/browse/product\.do\?pid=\d+",
        "shopify": False,
        "name_filter": r"legging|tight|capri|7/8",   # Bottoms page mixes classes
    },
    "Beyond Yoga": {
        "home": "https://beyondyoga.com/",
        "seeds": ["https://beyondyoga.com/collections/leggings",
                  "https://beyondyoga.com/collections/pedal-pushers-capris"],
        "product_re": r"/products/[^/?#]+",
        "shopify": True,
    },
}

FIELDS = ["capture_date","brand","source_url","brand_style_code","style_group_key","style_name","color_name",
 "style_color_id","length_variant","gender","brand_category_path","lulu_division","lulu_class","lulu_subclass",
 "activity","brand_line","list_price_usd","selling_price_usd","markdown_pct","on_promo_flag","sitewide_promo_text",
 "is_new_flag","size_range","size_count","sizes_available","sizes_out_of_stock","extended_size_flag","inseam_in",
 "rise","length_descriptor","fit_descriptor","waistband_type","pockets","front_seam","fabric_platform",
 "fiber_content_raw","primary_fiber","primary_fiber_pct","elastane_pct","recycled_flag","claimed_properties",
 "colour_family","print_or_solid","colors_for_style","rating","review_count","fit_score","first_seen_date",
 "line_segment_inferred","extraction_method","qa_status"]

# Fabric and line names observed on the sites (Fabric_Normalization tab). Extend as crawls find more.
FABRIC_NAMES = {
    "lululemon": ["Nulu", "Nulux", "Luon", "Luxtreme", "Everlux", "Smooth Spacer", "SuperLoft", "Scuba"],
    "Alo": ["Airbrush", "Airlift", "Softsculpt"],
    "Vuori": ["BlissBlend Form", "BlissBlend", "DreamKnit Move", "DreamKnit", "BreatheInterlock", "Meta"],
    "Beyond Yoga": ["Spacedye", "LuxeFleece 2.0", "LuxeFleece", "PowerBeyond", "Featherweight"],
    "Athleta": [],
}
LINE_NAMES = {
    "lululemon": ["Align", "Wunder Train", "Fast and Free", "Swift Speed", "Define", "Dance Studio", "Base Pace", "InStill"],
    "Alo": ["Accolade", "Airlift", "Airbrush"],
    "Vuori": ["FormRefined", "AllTheFeels", "AllTheForm", "Halo", "Daily", "Clementine", "Studio"],
    "Beyond Yoga": ["Caught In The Midi", "Out Of Pocket", "glowzone"],
    "Athleta": ["Salutation", "Elation", "Ultimate", "Transcend", "Rainier"],   # unverified; confirm on crawl
}

SIZE_ORDER = ["XXXS","XXS","XS","S","M","L","XL","XXL","2XL","XXXL","3XL","1X","2X","3X","4X"] + \
             [str(n) for n in range(0, 32, 2)]
EXT_SIZES = {"XXL","2XL","XXXL","3XL","1X","2X","3X","4X"}

COLOUR_RULES = [  # checked in order, first match wins
    ("Multi", r"multi|print|camo|floral|stripe|leopard|tie[- ]dye|space ?dye"),
    ("Off White", r"ivory|cream|bone|ecru|porcelain|oat|alabaster|vanilla|natural"),
    ("Navy", r"navy|midnight|true navy|nocturnal"),
    ("Olive", r"olive|army|moss|sage|khaki"),
    ("Black", r"black|onyx|jet|obsidian"),
    ("White", r"white"),
    ("Grey", r"grey|gray|heather|charcoal|graphite|slate|steel|silver|ash"),
    ("Blue", r"blue|cobalt|denim|sky|aqua|teal|marine|sapphire|indigo"),
    ("Green", r"green|ivy|forest|emerald|jade|mint|pine"),
    ("Pink", r"pink|rose|blush|ballet|flamingo"),
    ("Purple", r"purple|violet|plum|lilac|lavender|raisin|grape|amethyst"),
    ("Red", r"red|burgundy|wine|cherry|crimson|merlot|mahogany|bordeaux"),
    ("Orange", r"orange|rust|terracotta|coral|papaya|apricot"),
    ("Yellow", r"yellow|lemon|butter|gold|mustard"),
    ("Brown", r"brown|espresso|roast|mocha|chocolate|java|coffee|cocoa|chestnut|walnut"),
    ("Tan", r"tan|beige|sand|almond|camel|taupe|toast|nude|latte|mushroom"),
]
CLAIMS = {"4-way stretch": r"4[- ]way stretch|four[- ]way stretch", "Moisture wicking": r"moisture[- ]wick|sweat[- ]wick",
          "Quick dry": r"quick[- ]dry", "Anti-odor": r"anti[- ]odou?r|odou?r[- ]resist", "UPF": r"\bupf\b",
          "Squat proof": r"squat[- ]proof", "Compression": r"compressi(on|ve)", "Breathable": r"breathab"}

# ---------------------------------------------------------------- helpers
_robots = {}
_last_hit = {}
_host_delay = {}

def allowed(url):
    """Fails closed: if robots.txt can't be read (network error, 5xx), nothing on that host is fetched.
    401/403 = disallow all; other 4xx = no robots.txt, allow all (RFC 9309)."""
    import requests
    p = urlparse(url); base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = robotparser.RobotFileParser(); rp.set_url(base + "/robots.txt")
        try:
            r = requests.get(base + "/robots.txt", headers={"User-Agent": UA}, timeout=30)
            if r.status_code == 200: rp.parse(r.text.splitlines())
            elif r.status_code in (401, 403): rp.disallow_all = True
            elif 400 <= r.status_code < 500: rp.allow_all = True
            else: rp = None; log(f"robots.txt returned {r.status_code} for {base}: skipping host")
        except Exception as e:
            rp = None; log(f"robots.txt unreachable for {base} ({e}): skipping host")
        _robots[base] = rp
        cd = rp.crawl_delay(UA) if rp is not None else None
        _host_delay[p.netloc] = max(DELAY, float(cd or 0))
    rp = _robots[base]
    return False if rp is None else rp.can_fetch(UA, url)

def polite_wait(url):
    host = urlparse(url).netloc
    wait = _host_delay.get(host, DELAY) - (time.time() - _last_hit.get(host, 0))
    if wait > 0: time.sleep(wait)
    _last_hit[host] = time.time()

def key_for(url):
    return hashlib.sha1(url.encode()).hexdigest()[:16]

def clean_url(url, brand):
    p = urlparse(url)
    if brand == "Athleta":   # keep pid, drop tracking
        m = re.search(r"pid=(\d+)", p.query)
        return f"{p.scheme}://{p.netloc}{p.path}?pid={m.group(1)}" if m else url
    return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))

def log(msg): print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)

def req_get(url):
    import requests
    if not allowed(url): log(f"robots.txt disallows {url}"); return None
    polite_wait(url)
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en-US"}, timeout=30)
        if r.status_code != 200: log(f"GET {r.status_code} {url}")
        return r if r.status_code == 200 else None
    except Exception as e:
        log(f"GET failed {url}: {e}"); return None

def browser():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    b = pw.chromium.launch(headless=True)
    ctx = b.new_context(user_agent=UA, locale="en-US", viewport={"width": 1400, "height": 1000})
    return pw, b, ctx

def render(ctx, url, scroll=False):
    if not allowed(url): log(f"robots.txt disallows {url}"); return None
    polite_wait(url)
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        if scroll:
            last, stable = 0, 0
            while stable < 3:
                page.mouse.wheel(0, 4000); page.wait_for_timeout(1500)
                for sel in ["button:has-text('Load More')", "button:has-text('Show More')", "button:has-text('View More')"]:
                    try:
                        if page.locator(sel).first.is_visible(): page.locator(sel).first.click(); page.wait_for_timeout(2000)
                    except Exception: pass
                h = page.evaluate("document.body.scrollHeight")
                stable = stable + 1 if h == last else 0; last = h
        return page.content()
    except Exception as e:
        log(f"render failed {url}: {e}"); return None
    finally:
        page.close()

# ---------------------------------------------------------------- stage 1: discover
def discover(brands):
    pw, b, ctx = browser()
    summary = load_summary()
    try:
        for name in brands:
            cfg = BRANDS[name]; found = {}; shown = []
            home = render(ctx, cfg["home"])
            summary.setdefault(name, {})["sitewide_promo_text"] = promo_text(home or "")
            for seed in cfg["seeds"]:
                html = render(ctx, seed, scroll=True)
                if not html: continue
                (RAW / name).mkdir(parents=True, exist_ok=True)
                (RAW / name / f"category_{key_for(seed)}.html").write_text(html)
                m = re.search(r"(\d[\d,]*)\s+(?:items|products|results|styles)\b", strip_html(html), re.I)
                if m: shown.append({"seed": seed, "brand_count_text": m.group(0)})
                for href in re.findall(r'href="([^"]+)"', html):
                    if re.search(cfg["product_re"], href):
                        u = clean_url(urljoin(seed, href.replace("&amp;", "&")), name)
                        found.setdefault(u, seed)
            (DATA / f"urls_{name}.json").write_text(json.dumps(found, indent=1))
            summary[name].update({"urls_discovered": len(found), "brand_counts_shown": shown})
            log(f"{name}: {len(found)} product links; brand count shown: {shown or 'not found'}")
    finally:
        save_summary(summary); b.close(); pw.stop()

# Offers that are not a sitewide price cut: sign-up / first-order / app / referral, and partial sales
# ("select styles", "up to X% off") whose markdown already shows in each item's own price.
NOT_SITEWIDE = r"sign[- ]?up|first (order|purchase)|new customer|e-?mail|\bsms\b|\btext\b|subscribe|newsletter|\bapp\b|refer|\bselect(ed)?\b|\bup to\b"

def promo_text(html):
    t = strip_html(html)[:4000]
    hits = re.findall(r"[^.\n]{0,60}\b\d{1,2}% off[^.\n]{0,60}", t, re.I)
    hits = [h.strip() for h in hits if not re.search(NOT_SITEWIDE, h, re.I)]
    return " | ".join(dict.fromkeys(hits))[:300]

# ---------------------------------------------------------------- stage 2: fetch
def fetch(brands, limit=None):
    need_browser = any(not BRANDS[n]["shopify"] for n in brands)
    pw = b = ctx = None
    if need_browser: pw, b, ctx = browser()
    summary = load_summary()
    try:
        for name in brands:
            cfg = BRANDS[name]; out = RAW / name; out.mkdir(parents=True, exist_ok=True)
            urls = json.loads((DATA / f"urls_{name}.json").read_text())
            todo = list(urls)[:limit] if limit else list(urls)   # --limit N: first N links, discovery order
            ok = err = no_pj = 0
            for u in todo:
                k = key_for(u)
                have_html = (out / f"{k}.html").exists()
                pjf = out / f"{k}.product.json"
                if cfg["shopify"] and not pjf.exists():   # also retries product data missed on an earlier run
                    js = req_get(u + ".js")
                    if js is not None: pjf.write_text(js.text)
                    else: no_pj += 1
                if have_html: ok += 1; continue   # resume safely
                html = (lambda r: r.text if r is not None else None)(req_get(u)) if cfg["shopify"] else render(ctx, u)
                if html:
                    (out / f"{k}.html").write_text(html)
                    (out / f"{k}.meta.json").write_text(json.dumps({"url": u, "seed": urls[u], "fetched": TODAY}))
                    ok += 1
                else:
                    err += 1
            summary.setdefault(name, {}).update({"pages_fetched": ok, "fetch_errors": err,
                "product_json_missing": no_pj, "fetch_limit": limit or ""})
            log(f"{name}: fetched {ok} of {len(todo)}, errors {err}" + (f", product data missing {no_pj}" if cfg["shopify"] else ""))
    finally:
        save_summary(summary)
        if b: b.close(); pw.stop()

# ---------------------------------------------------------------- stage 3: extract
def strip_html(html):
    try:
        from bs4 import BeautifulSoup
        s = BeautifulSoup(html, "html.parser")
        for t in s(["script", "style", "noscript", "svg"]): t.decompose()
        return re.sub(r"\n\s*\n+", "\n", s.get_text("\n"))
    except ImportError:
        return re.sub(r"<[^>]+>", " ", html)

# Page regions that describe other products or the site, not this product.
OFF_PRODUCT_RE = re.compile(r"recommend|also[-_ ]?like|upsell|cross[-_]?sell|related|recently[-_]?viewed|"
                            r"complete[-_]?the[-_]?look|pairs?[-_]?with|you[-_]?may|carousel|mega[-_]?menu", re.I)
OFF_PRODUCT_HEADINGS = r"\n\s*(you may also like|you might also like|complete the look|recently viewed|" \
                       r"customers also (bought|viewed)|pairs well with|shop the look|related products)\b"

def product_text(html):
    """Text of the product itself: drops header, nav, footer and recommendation blocks, then cuts at the
    first recommendation heading. Used for fit, fabric, claims and badges so other products can't leak in."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return strip_html(html)
    s = BeautifulSoup(html, "html.parser")
    for t in s(["script", "style", "noscript", "svg", "header", "nav", "footer", "aside"]): t.decompose()
    for t in s.find_all(True):
        if t.attrs is None: continue   # already removed with a parent
        tag_id = " ".join([t.get("id") or ""] + (t.get("class") or []))
        if tag_id and OFF_PRODUCT_RE.search(tag_id): t.decompose()
    text = re.sub(r"\n\s*\n+", "\n", s.get_text("\n"))
    return re.split(OFF_PRODUCT_HEADINGS, "\n" + text, maxsplit=1, flags=re.I)[0]

def json_ld_products(html):
    out = []
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try: data = json.loads(block.strip())
        except Exception: continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if isinstance(d, dict):
                t = d.get("@type"); t = t if isinstance(t, list) else [t]
                if "Product" in t or "ProductGroup" in t: out.append(d)
                for v in d.values():
                    if isinstance(v, (list, dict)): stack.extend(v if isinstance(v, list) else [v])
            elif isinstance(d, list): stack.extend(d)
    return out

def money(v, cents=False):
    """Shopify /products/<handle>.js returns integer cents; JSON-LD returns dollars."""
    if v is None or v == "": return None
    try:
        v = float(str(v).replace("$", "").replace(",", ""))
        return round(v / 100, 2) if cents else v
    except ValueError: return None

def style_code(sku, brand):
    if not sku: return ""
    s = str(sku).upper().split("-")[0].split("_")[0]
    m = re.match(r"^([A-Z]{0,4}\d{3,6})", s)
    return m.group(1) if m else s

def size_summary(sizes):
    sizes = [s for s in dict.fromkeys(x.strip() for x in sizes if x and x.strip())]
    idx = lambda s: SIZE_ORDER.index(s.upper()) if s.upper() in SIZE_ORDER else 999
    ordered = sorted(sizes, key=idx)
    ext = any(s.upper() in EXT_SIZES or (s.isdigit() and int(s) > 14) for s in sizes)
    rng = f"{ordered[0]}-{ordered[-1]}" if len(ordered) > 1 else (ordered[0] if ordered else "")
    return ordered, rng, "Y" if ext else ("N" if sizes else "")

def parse_fiber(text):
    lines = [l.strip() for l in text.split("\n") if "%" in l and re.search(
        r"nylon|polyamide|polyester|elastane|spandex|lycra|cotton|lyocell|tencel|modal|wool|viscose|rayon", l, re.I)]
    raw = "; ".join(dict.fromkeys(lines))[:400]
    if not raw: return "", "", "", ""
    shell = re.split(r"\(\s*shell\s*\)|lining|liner|gusset|pocket|trim", raw, flags=re.I)[0]
    pairs = [(float(p), f.lower()) for p, f in re.findall(r"(\d{1,3}(?:\.\d)?)\s*%\s*([A-Za-z]+)", shell)]
    if not pairs: return raw, "", "", ""
    ela = sum(p for p, f in pairs if f in ("elastane", "spandex", "lycra"))
    rest = [(p, f) for p, f in pairs if f not in ("elastane", "spandex", "lycra")]
    prim, pf = (max(rest) if rest else (None, ""))
    fam = {"nylon": "Nylon/Polyamide", "polyamide": "Nylon/Polyamide", "polyester": "Polyester", "cotton": "Cotton",
           "lyocell": "Lyocell", "tencel": "Lyocell", "modal": "Modal", "wool": "Wool"}.get(pf, "Other" if pf else "")
    return raw, fam, (round(prim / 100, 3) if prim else ""), (round(ela / 100, 3) if pairs else "")

def first(pattern, text, flags=re.I, group=0):
    m = re.search(pattern, text, flags); return m.group(group) if m else ""

def text_fields(text, name, brand, review_text=None):
    """text: product-only text. review_text: wider page text, used only for the review widget."""
    t = text; low = t.lower(); f = {}; rt = review_text or t
    f["rise"] = first(r"\b(super[- ]high|high|mid|low)[- ]rise\b", t).title().replace("-", " ").replace(" Rise", "") or "Not stated"
    f["length_descriptor"] = ("7/8" if "7/8" in low or "7/8" in name else
        first(r"\b(full[- ]length|ankle|capri|flare|crop(?:ped)?)\b", name + " " + t).title() or "Not stated")
    f["fit_descriptor"] = first(r"\b(stretch to fit|tight fit|relaxed fit|slim fit|flare)\b", t).capitalize() or "Not stated"
    f["inseam_in"] = first(r"(\d{2}(?:\.\d)?)\s*(?:\"|”|''|-inch|in\b)", name + " " + first(r"inseam[^\n]{0,40}", t), group=1)
    f["front_seam"] = "No" if re.search(r"no front (rise )?seam|without a front seam", low) else "Not stated"
    wb = first(r"\b(bonded|seamless|v[- ]front|crossover|fold[- ]over|drawcord|high[- ]waisted)\b[^\n]{0,15}waist", t)
    f["waistband_type"] = wb.split()[0].title() if wb else ""
    pk = first(r"[^\n.]{0,40}\bpockets?\b[^\n.]{0,40}", t)
    f["pockets"] = pk.strip()[:80] if pk else ""
    f["recycled_flag"] = "Y" if re.search(r"recycled", low) else "N"
    f["claimed_properties"] = "; ".join(k for k, rx in CLAIMS.items() if re.search(rx, low))
    f["fabric_platform"] = next((n for n in sorted(FABRIC_NAMES.get(brand, []), key=len, reverse=True)
                                 if re.search(r"\b" + re.escape(n) + r"\b", t, re.I)), "")
    f["brand_line"] = next((n for n in LINE_NAMES.get(brand, []) if n.lower() in name.lower()), "")
    f["rating"] = first(r"\b([1-5]\.\d{1,2})\b(?=[^\n]{0,40}\n?[^\n]{0,40}review)", rt, group=1)
    f["review_count"] = (first(r"based on\s+(\d[\d,]*)\s+reviews", rt, group=1) or first(r"\b(\d[\d,]*)\s+reviews\b", rt, group=1)).replace(",", "")
    fs = first(r"fit[^\n]{0,40}?(\d\.\d{1,2}) out of 5", rt, group=1)
    f["fit_score"] = f"{fs} of 5" if fs else ""
    f["is_new_flag"] = "Y" if re.search(r"\bnew arrival\b|\bnew\b\s*\n", low[:3000]) else "N"
    f["fiber_content_raw"], f["primary_fiber"], f["primary_fiber_pct"], f["elastane_pct"] = parse_fiber(t)
    return f

def colour_family(color):
    c = (color or "").lower()
    for fam, rx in COLOUR_RULES:
        if re.search(rx, c): return fam
    return ""

def print_or_solid(color):
    c = (color or "").lower()
    if re.search(r"heather", c): return "Heather"
    if re.search(r"space ?dye", c): return "Space dye"
    if re.search(r"print|camo|floral|stripe|leopard|dye|multi", c): return "Print"
    if re.search(r"colou?r ?block", c): return "Colorblock"
    return "Solid" if c else ""

def taxonomy(name):
    n = name.lower()
    if re.search(r"\bshorts?\b|biker", n): return "Womens Shorts/Skirts", "Womens Shorts", "Out of Wave 1 scope: short"
    if re.search(r"\bset\b|bundle|\bpack\b|gift card", n): return "", "", "Excluded: set or bundle"
    return "Womens Pants", "Womens Tights", ""

def rows_from_shopify(brand, url, html, pj, promo):
    text = strip_html(html); name = pj.get("title", "")
    tf = text_fields(product_text(html) + "\n" + strip_html(pj.get("description", "") or ""), name, brand, text)
    opts = [o.get("name", o) if isinstance(o, dict) else o for o in pj.get("options", [])]
    ci = next((i for i, o in enumerate(opts) if re.search(r"colou?r", str(o), re.I)), None)
    si = next((i for i, o in enumerate(opts) if re.search(r"size", str(o), re.I)), None)
    by_color = {}
    for v in pj.get("variants", []):
        vals = [v.get(f"option{i+1}") for i in range(3)]
        color = vals[ci] if ci is not None else first(r"colou?r:\s*([^\n]+)", text, group=1).strip()
        by_color.setdefault(color or "", []).append((vals[si] if si is not None else "", v))
    out = []
    for color, vs in by_color.items():
        sizes = [s for s, _ in vs]; avail = [s for s, v in vs if v.get("available")]
        oos = [s for s, v in vs if v.get("available") is False]
        ordered, rng, ext = size_summary(sizes)
        prices = [money(v.get("price"), True) for _, v in vs if money(v.get("price"), True)]
        lists = [max(money(v.get("compare_at_price"), True) or 0, money(v.get("price"), True) or 0) for _, v in vs]
        sell = min(prices) if prices else None; lst = max(lists) if lists else None
        sku = next((v.get("sku") for _, v in vs if v.get("sku")), "")
        out.append(base_row(brand, url, name, color, style_code(sku, brand), lst, sell, ordered, rng, ext,
                            avail, oos, tf, promo, "Structured (Shopify) + Regex"))
    return out

def rows_from_jsonld(brand, url, html, promo):
    text = strip_html(html); prods = json_ld_products(html)
    groups = [p for p in prods if "ProductGroup" in str(p.get("@type"))]
    prods = groups or prods[:1]   # a ProductGroup already contains its variants; avoid double counting
    name = next((p.get("name") for p in prods if p.get("name")), "") or first(r"<title>([^<|]+)", html, group=1).strip()
    tf = text_fields(product_text(html), name, brand, text)
    offers = []
    for p in prods:
        variants = p.get("hasVariant") or [p]
        for v in variants:
            o = v.get("offers") or {}
            for off in (o if isinstance(o, list) else [o]):
                offers.append({"color": v.get("color") or p.get("color") or "", "size": v.get("size") or "",
                               "sku": v.get("sku") or p.get("sku") or p.get("productID") or "",
                               "price": off.get("price") or off.get("lowPrice"),
                               "list": (off.get("priceSpecification") or {}).get("price") if isinstance(off.get("priceSpecification"), dict) else None,
                               "avail": str(off.get("availability", "")).lower()})
    if not offers: offers = [{"color": first(r"colou?r:\s*([^\n]+)", text, group=1).strip(), "size": "", "sku": "", "price": None, "list": None, "avail": ""}]
    by_color, seen = {}, set()
    for o in offers:
        if (o["color"], o["size"]) in seen: continue
        seen.add((o["color"], o["size"])); by_color.setdefault(o["color"], []).append(o)
    code = url.rstrip("/").split("/")[-1].split("?")[0] if brand == "lululemon" else ""
    out = []
    for color, os_ in by_color.items():
        sizes = [o["size"] for o in os_]
        ordered, rng, ext = size_summary(sizes)
        avail = [o["size"] for o in os_ if "instock" in o["avail"]]
        oos = [o["size"] for o in os_ if "outofstock" in o["avail"]]
        prices = [money(o["price"]) for o in os_ if money(o["price"])]
        lists = [money(o["list"]) for o in os_ if money(o["list"])]
        sell = min(prices) if prices else None
        lst = max(lists + prices) if (lists or prices) else None
        sc = code or style_code(next((o["sku"] for o in os_ if o["sku"]), ""), brand)
        out.append(base_row(brand, url, name, color, sc, lst, sell, ordered, rng, ext, avail, oos, tf, promo,
                            "Structured (JSON-LD) + Regex"))
    return out

def base_row(brand, url, name, color, code, lst, sell, ordered, rng, ext, avail, oos, tf, promo, method):
    length = first(r"\*\s*(Regular|Short|Tall|Petite)", name, group=1) or "n/a"
    clean_name = re.sub(r"\s*\*\s*(Regular|Short|Tall|Petite).*$", "", name).strip()
    cls, sub, note = taxonomy(clean_name)
    group_name = re.sub(r'\s*\d{1,2}(?:\.\d)?\s*(?:"|”|in\b).*$', "", clean_name).strip()   # inseam options = same style
    group = f"{brand.lower()}|{group_name.lower()}" if brand == "lululemon" else f"{brand.lower()}|{(code or clean_name).lower()}"
    r = {k: "" for k in FIELDS}
    r.update(tf)
    r.update({"capture_date": TODAY, "brand": brand, "source_url": url, "brand_style_code": code,
        "style_group_key": group, "style_name": clean_name, "color_name": color,
        "style_color_id": f"{brand.lower()}|{(code or clean_name).lower()}|{(color or '').lower()}",
        "length_variant": length, "gender": "Women", "brand_category_path": "Women > Leggings",
        "lulu_division": "lulu Womens" if cls else "", "lulu_class": cls, "lulu_subclass": sub,
        "list_price_usd": lst or "", "selling_price_usd": sell or "",
        "markdown_pct": round(1 - sell / lst, 3) if (lst and sell) else "",
        "on_promo_flag": "Y" if ((lst and sell and sell < lst) or promo) else "N", "sitewide_promo_text": promo,
        "size_range": rng, "size_count": len(ordered) or "", "sizes_available": ";".join(avail),
        "sizes_out_of_stock": ";".join(oos), "extended_size_flag": ext,
        "colour_family": colour_family(color), "print_or_solid": print_or_solid(color),
        "activity": "Unassigned", "line_segment_inferred": "Too early", "extraction_method": method,
        "qa_status": "Unreviewed"})
    if note: r["qa_status"] = note
    return r

LLM_FIELDS = ["brand_line", "fabric_platform", "waistband_type", "pockets", "activity", "colour_family", "print_or_solid", "claimed_properties"]

def llm_fill(row, page_text):
    """Fill blank free-text fields from page text only. Returns row. Requires ANTHROPIC_API_KEY."""
    blanks = [f for f in LLM_FIELDS if row.get(f) in ("", "Unassigned")]
    if not blanks: return row
    import anthropic
    client = anthropic.Anthropic()
    prompt = (
        "You extract product attributes from one apparel product page. Use ONLY the page text below. "
        "If a value is not stated, return an empty string. Do not guess from brand knowledge.\n"
        f"Brand: {row['brand']}\nProduct: {row['style_name']}\nColor: {row['color_name']}\n"
        f"Return JSON with exactly these keys: {blanks}.\n"
        "Allowed values: activity in [Yoga, Train, Run, Tennis, Golf, Lounge, Social, Sweatlife, Recovery, Hike, Swim, Travel, Unassigned]; "
        "colour_family in [Black, Navy, Grey, White, Off White, Blue, Green, Olive, Pink, Purple, Red, Orange, Yellow, Brown, Tan, Multi]; "
        "print_or_solid in [Solid, Heather, Print, Colorblock, Space dye]; claimed_properties as a semicolon list.\n"
        "Respond with the JSON object only.\n\nPAGE TEXT:\n" + page_text[:6000])
    try:
        msg = client.messages.create(model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5"), max_tokens=500,
                                     messages=[{"role": "user", "content": prompt}])
        txt = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        data = json.loads(re.sub(r"```(json)?", "", txt).strip())
        for k in blanks:
            if data.get(k): row[k] = data[k]
        row["extraction_method"] += " + LLM"
    except Exception as e:
        log(f"LLM fill failed for {row['style_color_id']}: {e}")
    return row

def extract(brands, use_llm=False):
    summary = load_summary(); all_rows = []
    hist_path = DATA / "history.csv"; hist = {}
    if hist_path.exists():
        with open(hist_path) as fh: hist = {r["style_color_id"]: r for r in csv.DictReader(fh)}
    for name in brands:
        promo = summary.get(name, {}).get("sitewide_promo_text", "")
        rows = []
        for meta in sorted((RAW / name).glob("*.meta.json")):
            k = meta.name.split(".")[0]; m = json.loads(meta.read_text())
            html = (RAW / name / f"{k}.html").read_text()
            pjf = RAW / name / f"{k}.product.json"
            try:
                if BRANDS[name]["shopify"] and pjf.exists():
                    new = rows_from_shopify(name, m["url"], html, json.loads(pjf.read_text()), promo)
                else:
                    new = rows_from_jsonld(name, m["url"], html, promo)
            except Exception as e:
                log(f"extract failed {m['url']}: {e}"); continue
            nf = BRANDS[name].get("name_filter")
            if nf: new = [r for r in new if re.search(nf, r["style_name"], re.I)]
            if use_llm:
                txt = product_text(html); new = [llm_fill(r, txt) for r in new]
            rows.extend(new)
        dedup = {}
        for r in rows: dedup.setdefault(r["style_color_id"], r)   # Beyond Yoga pockets subset, repeat seeds
        rows = list(dedup.values())
        colors = {}
        for r in rows: colors.setdefault(r["style_group_key"], set()).add(r["color_name"])
        for r in rows:
            r["colors_for_style"] = len(colors[r["style_group_key"]])
            h = hist.get(r["style_color_id"])
            r["first_seen_date"] = h["first_seen_date"] if h else TODAY
            prev = int(h.get("captures", 0)) if h else 0
            seen = prev if (h and h.get("last_seen_date") == TODAY) else prev + 1   # re-runs on one day count once
            # Core vs seasonal needs drop-outs over time; flag candidates here, classify in analysis.
            if seen >= 6: r["line_segment_inferred"] = "Core candidate"
            hist[r["style_color_id"]] = {"style_color_id": r["style_color_id"], "first_seen_date": r["first_seen_date"],
                                         "last_seen_date": TODAY, "captures": seen}
        in_scope = [r for r in rows if r["lulu_subclass"] == "Womens Tights"]
        summary.setdefault(name, {}).update({"rows_style_color": len(rows), "rows_in_wave1_scope": len(in_scope),
            "unique_styles_in_scope": len({r["style_group_key"] for r in in_scope})})
        log(f"{name}: {len(rows)} style-colors, {summary[name]['unique_styles_in_scope']} styles in scope")
        all_rows.extend(rows)
    out = Path("wave1_catalog.csv")   # keep rows for brands not in this run (one-brand-at-a-time workflow)
    if out.exists():
        with open(out, newline="") as fh:
            all_rows = [r for r in csv.DictReader(fh) if r["brand"] not in brands] + all_rows
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS); w.writeheader(); w.writerows(all_rows)
    with open(hist_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["style_color_id", "first_seen_date", "last_seen_date", "captures"])
        w.writeheader(); w.writerows(hist.values())
    save_summary(summary)
    report_fill(all_rows)

REQUIRED = ["source_url","brand_style_code","style_name","color_name","list_price_usd","selling_price_usd",
            "size_range","sizes_available","rise","length_descriptor","fabric_platform","fiber_content_raw",
            "primary_fiber","elastane_pct","claimed_properties","colour_family","brand_line"]

def report_fill(rows):
    print("\nFill rate on required fields (target 90%):")
    for b in BRANDS:
        rs = [r for r in rows if r["brand"] == b and r["lulu_subclass"] == "Womens Tights"]
        if not rs: continue
        low = [(f, sum(1 for r in rs if r[f] not in ("", "Not stated")) / len(rs)) for f in REQUIRED]
        misses = ", ".join(f"{f} {p:.0%}" for f, p in low if p < 0.9)
        print(f"  {b:12s} rows={len(rs):4d}  below target: {misses or 'none'}")

def load_summary():
    p = DATA / "run_summary.json"
    return json.loads(p.read_text()) if p.exists() else {}

def save_summary(s):
    DATA.mkdir(exist_ok=True); (DATA / "run_summary.json").write_text(json.dumps(s, indent=1))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["discover", "fetch", "extract", "all"])
    ap.add_argument("--brands", nargs="*", default=list(BRANDS), help="Subset, e.g. --brands Vuori Alo")
    ap.add_argument("--llm", action="store_true", help="Fill blank free-text fields with Claude")
    ap.add_argument("--limit", type=int, help="fetch only the first N product links per brand (sample run)")
    a = ap.parse_args()
    bad = [b for b in a.brands if b not in BRANDS]
    if bad: sys.exit(f"Unknown brand(s): {bad}. Use: {list(BRANDS)}")
    RAW.mkdir(parents=True, exist_ok=True)
    if a.stage in ("discover", "all"): discover(a.brands)
    if a.stage in ("fetch", "all"): fetch(a.brands, a.limit)
    if a.stage in ("extract", "all"): extract(a.brands, a.llm)
