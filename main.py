

import os
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN secret is missing")

BASE = f"https://api.telegram.org/bot{TOKEN}"

# Get Telegram Chat ID from GitHub Secrets
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not CHAT_ID:
    raise ValueError("TELEGRAM_CHAT_ID secret is missing")

chat_id = int(CHAT_ID)

def get_market_data(symbol, name):
    df = yf.Ticker(symbol).history(
        period="10d", interval="1h"
    )

    if df.empty or len(df) < 50:
        raise RuntimeError(f"Insufficient hourly data for {name}")

    close = df["Close"].dropna()

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(
        alpha=1/14, adjust=False
    ).mean()
    loss = (-delta.clip(upper=0)).ewm(
        alpha=1/14, adjust=False
    ).mean()

    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))

    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    macd = (
        close.ewm(span=12, adjust=False).mean()
        - close.ewm(span=26, adjust=False).mean()
    )
    signal = macd.ewm(span=9, adjust=False).mean()

    trend = "Bullish" if ema20.iloc[-1] > ema50.iloc[-1] else "Bearish"

    return (
        f"*{name}*\n"
        f"Price: {close.iloc[-1]:,.2f}\n"
        f"RSI (14): {rsi.iloc[-1]:.2f}\n"
        f"EMA 20: {ema20.iloc[-1]:,.2f}\n"
        f"EMA 50: {ema50.iloc[-1]:,.2f}\n"
        f"MACD: {macd.iloc[-1]:.4f}\n"
        f"Signal: {signal.iloc[-1]:.4f}\n"
        f"Trend: {trend}\n"
    )

btc = get_market_data("BTC-USD", "BITCOIN (BTC/USD)")
gold = get_market_data("XAUUSD=X", "INTERNATIONAL GOLD (XAU/USD)")

now = datetime.now(ZoneInfo("Asia/Kolkata"))

report = (
    "📊 *HOURLY MARKET ALERT*\n"
    f"Date: {now:%d-%m-%Y}\n"
    f"Time: {now:%H:%M} IST\n\n"
    f"{btc}\n"
    f"{gold}\n"
    "_Data: Yahoo Finance. Hourly data may be delayed._"
)

response = requests.post(
    f"{BASE}/sendMessage",
    json={
        "chat_id": chat_id,
        "text": report,
        "parse_mode": "Markdown"
    },
    timeout=20
)

response.raise_for_status()

if not response.json().get("ok"):
    raise RuntimeError("Telegram message delivery failed")

print("Telegram market report sent successfully.")
