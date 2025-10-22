import os
import json
import time
import datetime
import traceback
from playwright.sync_api import sync_playwright
import httpx

# --- asetukset ympäristömuuttujista ---
SCREENER_URL = os.getenv("SCREENER_URL")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
SCAN_INTERVAL_SEC = int(os.getenv("SCAN_INTERVAL_SEC", "60"))
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", "15"))
QTY_PER_TRADE = float(os.getenv("QTY_PER_TRADE", "1"))
TV_COOKIES_JSON = os.getenv("TV_COOKIES_JSON", "")
HEADLESS = True

# symbolien duplikaattisuodatus
sent_symbols = {}

def now_utc():
    return datetime.datetime.utcnow()

def log(*a):
    ts = now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[PW {ts}]", *a, flush=True)

# --- cookies ---
def add_tv_cookies(context):
    if not TV_COOKIES_JSON.strip():
        log("Ei TV_COOKIES_JSON asetettu – jatketaan ilman kirjautumista.")
        return
    try:
        cookies = json.loads(TV_COOKIES_JSON)
        context.add_cookies(cookies)
        log(f"Lisättiin {len(cookies)} evästettä TradingViewiin.")
    except Exception as e:
        log(f"Virhe cookiesien lisäämisessä: {e}")

# --- selainkäynnistys ---
def launch_browser(pw):
    for attempt in range(3):
        try:
            return pw.chromium.launch(
                headless=HEADLESS,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-background-timer-throttling",
                    "--disable-renderer-backgrounding",
                    "--disable-backgrounding-occluded-windows",
                ],
            )
        except Exception as e:
            log(f"Chromium launch failed (try {attempt+1}/3): {e}")
            time.sleep(5)
    raise RuntimeError("Chromium failed to launch after retries")

# --- login-check ---
def is_login_page(page):
    url = page.url
    if "signin" in url or "auth" in url:
        return True
    try:
        page.locator('input[name="username"], input[name="email"]').first.wait_for(timeout=1500)
        return True
    except:
        return False

# --- turvallinen goto ---
def safe_goto(page, url):
    log("Avaa:", url)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)
    if is_login_page(page):
        raise RuntimeError("TradingView vaatii loginin – lisää TV_COOKIES_JSON Environmentiin.")
    if "screener" not in page.url:
        log(f"Varoitus: päädyttiin eri urliin: {page.url}")

# --- taulukon luku ---
def parse_table(page):
    try:
        rows = page.locator("tr").all()
        symbols = []
        for r in rows:
            try:
                tds = r.locator("td").all()
                if len(tds) >= 2:
                    symbol = tds[0].inner_text().strip()
                    change = tds[1].inner_text().strip()
                    if symbol and len(symbol) < 10:
                        symbols.append((symbol, change))
            except:
                pass
        return symbols
    except Exception as e:
        log(f"parse_table virhe: {e}")
        return []

# --- yksinkertainen suodatus (voit muokata logiikkaa) ---
def filter_picks(rows):
    picks = []
    for symbol, change in rows:
        if change.startswith("+"):
            picks.append(symbol)
    return picks

# --- webhook-lähetys ---
def send_webhook(symbol):
    try:
        payload = {"symbol": symbol, "side": "buy", "qty": QTY_PER_TRADE}
        r = httpx.post(WEBHOOK_URL, json=payload, timeout=10)
        if r.status_code == 200:
            log(f"Lähetetty webhook: {symbol}")
            return True
        else:
            log(f"Webhook epäonnistui ({r.status_code}): {r.text}")
    except Exception as e:
        log(f"Webhook virhe: {e}")
    return False

def should_send(symbol):
    last = sent_symbols.get(symbol)
    if not last:
        return True
    diff_min = (now_utc() - last).total_seconds() / 60
    return diff_min >= COOLDOWN_MIN

def mark_sent(symbol):
    sent_symbols[symbol] = now_utc()

# --- päälooppi ---
def run_loop():
    while True:
        try:
            with sync_playwright() as pw:
                browser = launch_browser(pw)
                context = browser.new_context(locale="en-US")
                add_tv_cookies(context)
                page = context.new_page()

                while True:
                    try:
                        safe_goto(page, SCREENER_URL)
                        page.wait_for_timeout(2000)
                        page.mouse.wheel(0, 20000)
                        page.wait_for_timeout(500)

                        rows = parse_table(page)
                        symbols = filter_picks(rows)
                        log(f"Löytyi {len(symbols)} symbolia suodatuksen jälkeen.")

                        for sym in symbols:
                            if should_send(sym) and send_webhook(sym):
                                mark_sent(sym)
                    except Exception as inner:
                        log(f"loop error: {inner}")
                        traceback.print_exc()
                    time.sleep(SCAN_INTERVAL_SEC)
        except Exception as outer:
            log(f"top-level error, restarting playwright: {outer}")
            traceback.print_exc()
            time.sleep(10)

if __name__ == "__main__":
    log("Käynnistetään watcher_playwright.py")
    run_loop()
