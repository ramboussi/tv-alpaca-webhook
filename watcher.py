# watcher.py
import os, time, json, math
from datetime import datetime, timedelta, timezone
import httpx

# ---- ASETUKSET YMPÄRISTÖMUUTTUJISTA (muokkaa Renderissä) ----
WEBHOOK_URL        = os.getenv("WEBHOOK_URL")  # esim: https://.../webhook?token=MYTOKEN
TV_MARKET          = os.getenv("TV_MARKET", "america")  # 'america', 'europe', 'crypto', jne.
SCAN_INTERVAL_SEC  = int(os.getenv("SCAN_INTERVAL_SEC", "60"))
COOLDOWN_MIN       = int(os.getenv("COOLDOWN_MIN", "30"))  # älä lähetä samaa tickeriä uudelleen ennen kuin tämä aika on kulunut

# Suodattimia (voit säätää vapaasti)
MIN_PRICE          = float(os.getenv("MIN_PRICE", "1"))    # vähimmäishinta
MAX_PRICE          = float(os.getenv("MAX_PRICE", "100"))  # enimmäishinta
MIN_CHANGE_PCT     = float(os.getenv("MIN_CHANGE_PCT", "2"))  # väh. +% muutos
MIN_DOLLAR_VOL     = float(os.getenv("MIN_DOLLAR_VOL", "1000000"))  # close * volume vähintään

# ---- TV Scanner endpoint ----
SCAN_URL = f"https://scanner.tradingview.com/{TV_MARKET}/scan"

# Pidä kirjaa mitä on jo lähetetty (symbol -> viimeisin lähetysaika)
last_sent = {}

def log(*args):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}]", *args, flush=True)

def build_payload():
    """
    TradingViewn skanneri käyttää tätä formaattia.
    Muokkaa 'filter' + 'columns' halutessasi.
    """
    payload = {
        "symbols": {
            "query": {"types": []},   # kaikki osakkeet marketissa
            "tickers": []
        },
        "columns": [
            "symbol", "close", "change", "volume", "description"
        ],
        "filter": [
            {"left": "close",   "operation": "greater", "right": MIN_PRICE},
            {"left": "close",   "operation": "less",    "right": MAX_PRICE},
            {"left": "change",  "operation": "greater", "right": MIN_CHANGE_PCT},
            {"left": "volume",  "operation": "greater", "right": 0},
        ],
        "sort": {"sortBy": "volume", "sortOrder": "desc"},
        "range": [0, 150]  # palauta top 150 riviä
    }
    return payload

def parse_rows(items):
    """
    TV:n vastaus: {"data":[{"s":"NASDAQ:AAPL","d":[symbol, close, change, volume, description]}, ...]}
    columns = ["symbol","close","change","volume","description"]
    """
    results = []
    for it in items:
        d = it.get("d", [])
        if len(d) < 4:
            continue
        symbol = d[0]            # esim. "NASDAQ:AAPL" TAI "AAPL" riippuen marketista
        close = float(d[1] or 0)
        change_pct = float(d[2] or 0)  # % muutos
        volume = float(d[3] or 0)
        desc = d[4] if len(d) > 4 else ""
        dollar_vol = close * volume
        # Lisäsuodatus: dollarivolyymi
        if dollar_vol >= MIN_DOLLAR_VOL:
            # Normalisoi symboli muodossa "AAPL" jos tulee "NASDAQ:AAPL"
            if ":" in symbol:
                symbol = symbol.split(":")[-1]
            results.append({
                "symbol": symbol,
                "close": close,
                "change_pct": change_pct,
                "volume": volume,
                "dollar_vol": dollar_vol,
                "description": desc
            })
    return results

def fetch_screener():
    headers = {
        "content-type": "application/json",
        "user-agent": "Mozilla/5.0"
    }
    payload = build_payload()
    with httpx.Client(timeout=20) as client:
        r = client.post(SCAN_URL, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
        return data.get("data", [])

def should_send(sym):
    now = datetime.now(timezone.utc)
    last = last_sent.get(sym)
    if not last:
        return True
    return (now - last) > timedelta(minutes=COOLDOWN_MIN)

def mark_sent(sym):
    last_sent[sym] = datetime.now(timezone.utc)

def send_webhook(symbol, side="buy", qty=1):
    if not WEBHOOK_URL:
        log("WEBHOOK_URL puuttuu — aseta se Renderin Environmentissa.")
        return False
    body = {"symbol": symbol, "side": side, "qty": qty}
    try:
        with httpx.Client(timeout=20) as client:
            r = client.post(WEBHOOK_URL, json=body)
            if r.status_code == 200:
                log(f"[OK] Lähetetty webhook: {body} → {r.text[:200]}")
                return True
            else:
                log(f"[ERR] Webhook status {r.status_code}: {r.text[:200]}")
                return False
    except Exception as e:
        log(f"[EXC] Webhook epäonnistui: {e}")
        return False

def loop():
    log("Watcher käynnissä.", f"MARKET={TV_MARKET}", f"INTERVAL={SCAN_INTERVAL_SEC}s")
    while True:
        try:
            rows = fetch_screener()
            picks = parse_rows(rows)
            log(f"Screener tuloksia: {len(picks)} (suodatettuna dollar_vol >= {MIN_DOLLAR_VOL})")
            for row in picks:
                sym = row["symbol"]
                if should_send(sym):
                    ok = send_webhook(sym, side="buy", qty=1)
                    if ok:
                        mark_sent(sym)
        except Exception as e:
            log(f"[EXC] Scan-loop virhe: {e}")
        time.sleep(SCAN_INTERVAL_SEC)

if __name__ == "__main__":
    loop()
