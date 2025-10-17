 # watcher_playwright.py
import os, time, json
from datetime import datetime, timedelta, timezone
import httpx

from playwright.sync_api import sync_playwright

WEBHOOK_URL       = os.getenv("WEBHOOK_URL", "").strip()   # esim. https://.../webhook?token=XYZ
SCREENER_URL      = os.getenv("SCREENER_URL", "").strip()  # TradingView-screenerisi URL
SCAN_INTERVAL_SEC = int(os.getenv("SCAN_INTERVAL_SEC", "60"))
COOLDOWN_MIN      = int(os.getenv("COOLDOWN_MIN", "30"))
QTY_PER_TRADE     = float(os.getenv("QTY_PER_TRADE", "1"))

# (valinnainen) suodatuksia
MIN_PRICE         = float(os.getenv("MIN_PRICE", "0"))       # 0 = ei alarajaa
MAX_PRICE         = float(os.getenv("MAX_PRICE", "1000000")) # iso = ei ylärajaa

# Jos screeneri vaatii kirjautumisen, lisää tähän Chromesta eksportatut evästeet JSON-muodossa.
# Muoto: TV_COOKIES_JSON='[{"name":"sessionid","value":"...","domain":".tradingview.com","path":"/","expires":9999999999,"httpOnly":true,"secure":true,"sameSite":"Lax"}, ...]'
TV_COOKIES_JSON   = os.getenv("TV_COOKIES_JSON", "").strip()

# Valinnainen whitelist: "AAPL,TSLA,NVDA"
SYMBOLS_WHITELIST = {s.strip().upper() for s in os.getenv("SYMBOLS_WHITELIST", "").split(",") if s.strip()}

last_sent = {}  # symbol -> last send time (UTC)

def now_utc():
    return datetime.now(timezone.utc)

def log(*a):
    ts = now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}]", *a, flush=True)

def should_send(sym):
    t = last_sent.get(sym)
    return not t or (now_utc() - t) > timedelta(minutes=COOLDOWN_MIN)

def mark_sent(sym):
    last_sent[sym] = now_utc()

def send_webhook(symbol):
    if not WEBHOOK_URL:
        log("WEBHOOK_URL puuttuu.")
        return False
    body = {"symbol": symbol, "side": "buy", "qty": QTY_PER_TRADE}
    try:
        with httpx.Client(timeout=20) as c:
            r = c.post(WEBHOOK_URL, json=body)
            if r.status_code == 200:
                log(f"[OK] webhook → {symbol} | resp {r.text[:160]}")
                return True
            else:
                log(f"[ERR] webhook {r.status_code} {r.text[:200]}")
                return False
    except Exception as e:
        log(f"[EXC] webhook: {e}")
        return False

def add_tv_cookies(context):
    if not TV_COOKIES_JSON:
        return
    try:
        cookies = json.loads(TV_COOKIES_JSON)
        # Varmista domain
        for ck in cookies:
            if "domain" not in ck or not ck["domain"]:
                ck["domain"] = ".tradingview.com"
        context.add_cookies(cookies)
        log(f"Evästeet lisätty: {len(cookies)} kpl")
    except Exception as e:
        log(f"[EXC] TV_COOKIES_JSON parse fail: {e}")

def parse_table(page):
    """
    Lukee screenerin taulukosta symbolit ja hinnat.
    TradingView UI elää → käytetään varmoja selektoreita:
      - rivit:         role="row"
      - ticker cellit: [data-symbol] tai 1. sarake
      - price celli:   etsitäan tekstinä sarakkeista
    Palauttaa listan dict: {symbol, price}
    """
    rows = page.locator('[role="row"]').all()
    results = []
    for row in rows:
        # yritä hakea symboli data-attribuutista
        sym = None
        try:
            sym_attr = row.get_attribute("data-symbol")
            if sym_attr:
                sym = sym_attr.split(":")[-1].upper()
        except:
            pass
        # fallback: 1. solun teksti
        if not sym:
            try:
                first_cell = row.locator('[role="gridcell"]').nth(0).inner_text(timeout=200)
                sym = first_cell.split("\n")[0].split(":")[-1].strip().upper()
            except:
                continue

        # hintasolu – skannaa rivin solut ja etsi fiksu luku
        price = None
        try:
            cells = row.locator('[role="gridcell"]').all()
            for cell in cells[:6]:  # eka kuusi solua riittää
                txt = cell.inner_text(timeout=100).replace(",", "").strip()
                # poimi float
                try:
                    val = float(txt)
                    # hinnaksi kelpaa järkevä positiivinen luku
                    if val > 0:
                        price = val
                        break
                except:
                    continue
        except:
            pass

        if sym and price:
            results.append({"symbol": sym, "price": price})
    return results

def filter_picks(items):
    out = []
    for it in items:
        s = it["symbol"].upper()
        p = float(it["price"])
        if SYMBOLS_WHITELIST and s not in SYMBOLS_WHITELIST: 
            continue
        if p < MIN_PRICE or p > MAX_PRICE:
            continue
        out.append(s)
    return out

def run_loop():
    if not SCREENER_URL:
        log("SCREENER_URL puuttuu. Aseta se Environmentissa.")
        return

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(locale="en-US")
        add_tv_cookies(context)
        page = context.new_page()

        while True:
            try:
                log("Avaa:", SCREENER_URL)
                page.goto(SCREENER_URL, wait_until="load", timeout=60000)
                # odota, että taulukko/renderöinti on valmis
                page.wait_for_timeout(2000)

                # vieritä alas, jotta kaikki rivit renderöityvät
                page.mouse.wheel(0, 20000)
                page.wait_for_timeout(500)

                rows = parse_table(page)
                symbols = filter_picks(rows)
                log(f"Löytyi {len(symbols)} symbolia suodatuksen jälkeen.")

                for sym in symbols:
                    if should_send(sym):
                        if send_webhook(sym):
                            mark_sent(sym)

            except Exception as e:
                log(f"[EXC] loop: {e}")

            time.sleep(SCAN_INTERVAL_SEC)

