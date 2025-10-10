import os
import json
from typing import Optional
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

PORT = int(os.environ.get("PORT", "8000"))
WEBHOOK_TOKEN = os.environ.get("WEBHOOK_TOKEN")

ALPACA_API_KEY_ID = os.environ.get("ALPACA_API_KEY_ID")
ALPACA_API_SECRET_KEY = os.environ.get("ALPACA_API_SECRET_KEY")
ALPACA_BASE_URL = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

app = FastAPI(title="TV → Alpaca Webhook")

trading_client = TradingClient(ALPACA_API_KEY_ID, ALPACA_API_SECRET_KEY, paper=True)

class TVPayload(BaseModel):
    symbol: str
    side: str
    qty: Optional[float] = 1

@app.get("/")
def health():
    return {"status": "ok"}

def place_market_order(symbol: str, side: str, qty: float = 1):
    side_enum = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
    order = MarketOrderRequest(
        symbol=symbol.upper(),
        qty=qty,
        side=side_enum,
        time_in_force=TimeInForce.DAY
    )
    return trading_client.submit_order(order_data=order)

@app.post("/webhook")
async def webhook(request: Request):
    token = request.query_params.get("token")
    if not WEBHOOK_TOKEN or token != WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        payload = await request.json()
        if isinstance(payload, str):
            payload = json.loads(payload)
        tv = TVPayload(**payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    try:
        resp = place_market_order(tv.symbol, tv.side, tv.qty or 1)
        return {
            "ok": True,
            "symbol": tv.symbol.upper(),
            "side": tv.side,
            "qty": tv.qty or 1,
            "alpaca_order_id": resp.id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Alpaca error: {e}")
