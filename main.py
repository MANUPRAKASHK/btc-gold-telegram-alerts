
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

CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not CHAT_ID:
    raise ValueError("TELEGRAM_CHAT_ID secret is missing")

chat_id = int(CHAT_ID)


def send_telegram(message):
    response = requests.post(
        f"{BASE}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown"
        },
        timeout=20
    )

    if not response.ok:
        try:
            details = response.json()
        except ValueError:
            details = response.text

        raise RuntimeError(
            f"Telegram API error {response.status_code}: {details}"
        )

    if not response.json().get("ok"):
        raise RuntimeError("Telegram message delivery failed")


def get_market_data(symbol, name):

    df = yf.Ticker(symbol).history(
        period="10d",
        interval="1h"
    )

    if df.empty:
        raise RuntimeError(f"No hourly data for {name}")

    # Use completed hourly candles only
    idx = pd.DatetimeIndex(df.index)

    if idx.tz is None:
        raise RuntimeError(
            f"Hourly timestamps have no timezone for {name}"
        )

    completed = (
        idx.tz_convert("UTC") + pd.Timedelta(hours=1)
    ) <= pd.Timestamp.now(tz="UTC")

    df = df.loc[completed].copy()

    if len(df) < 50:
        raise RuntimeError(
            f"Insufficient completed hourly data for {name}"
        )

    close = df["Close"].dropna()
    high = df["High"].reindex(close.index)
    low = df["Low"].reindex(close.index)

    # Indicators
    delta = close.diff()

    gain = delta.clip(lower=0).ewm(
        alpha=1/14, adjust=False
    ).mean()

    loss = (-delta.clip(upper=0)).ewm(
        alpha=1/14, adjust=False
    ).mean()

    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))

    ema20 = close.ewm(
        span=20, adjust=False
    ).mean()

    ema50 = close.ewm(
        span=50, adjust=False
    ).mean()

    macd = (
        close.ewm(span=12, adjust=False).mean()
        - close.ewm(span=26, adjust=False).mean()
    )

    signal = macd.ewm(
        span=9, adjust=False
    ).mean()

    trend = (
        "Bullish"
        if ema20.iloc[-1] > ema50.iloc[-1]
        else "Bearish"
    )

    report = (
        f"*{name}*\n"
        f"Price: {close.iloc[-1]:,.2f}\n"
        f"RSI (14): {rsi.iloc[-1]:.2f}\n"
        f"EMA 20: {ema20.iloc[-1]:,.2f}\n"
        f"EMA 50: {ema50.iloc[-1]:,.2f}\n"
        f"MACD: {macd.iloc[-1]:.4f}\n"
        f"Signal: {signal.iloc[-1]:.4f}\n"
        f"Trend: {trend}\n"
    )

    # Four price-action conditions
    conditions = {
        "BULLISH MOMENTUM": (
            close > close.shift(1),
            "Bullish: Current hourly close is above previous hourly close.",
            close.iloc[-2]
        ),

        "BULLISH BREAKOUT": (
            close > high.shift(1),
            "Bullish breakout: Current hourly close is above previous hourly high.",
            high.iloc[-2]
        ),

        "BEARISH MOMENTUM": (
            close < close.shift(1),
            "Bearish: Current hourly close is below previous hourly close.",
            close.iloc[-2]
        ),

        "BEARISH BREAKDOWN": (
            close < low.shift(1),
            "Bearish breakdown: Current hourly close is below previous hourly low.",
            low.iloc[-2]
        )
    }

    alerts = []

    for title, (condition, description, reference) in conditions.items():

        # Alert only when condition changes from FALSE to TRUE
        current_signal = bool(condition.iloc[-1])
        previous_signal = bool(condition.iloc[-2])

        if current_signal and not previous_signal:

            direction = (
                "🟢" if "BULLISH" in title else "🔴"
            )

            alerts.append(
                f"{direction} *{name}*\n"
                f"*{title}*\n\n"
                f"{description}\n"
                f"Previous reference: {reference:,.2f}\n"
                f"Current hourly close: {close.iloc[-1]:,.2f}"
            )

    return report, alerts


# Fetch BTC and Gold data
btc, btc_alerts = get_market_data(
    "BTC-USD",
    "BITCOIN (BTC/USD)"
)

gold, gold_alerts = get_market_data(
    "GC=F",
    "COMEX GOLD FUTURES (USD/oz)"
)

now = datetime.now(ZoneInfo("Asia/Kolkata"))

# Existing hourly report
report = (
    "📊 *HOURLY MARKET ALERT*\n"
    f"Date: {now:%d-%m-%Y}\n"
    f"Time: {now:%H:%M} IST\n\n"
    f"{btc}\n"
    f"{gold}\n"
    "_Data: Yahoo Finance. Hourly data may be delayed._"
)

send_telegram(report)

print("Hourly market report sent successfully.")

# Send each new price-action signal separately
all_alerts = btc_alerts + gold_alerts

for alert in all_alerts:
    send_telegram(
        "🚨 *NEW HOURLY PRICE ACTION SIGNAL*\n\n"
        + alert
    )
    print("Price-action alert sent:", alert.splitlines()[1])

if not all_alerts:
    print("No new price-action signals.")

