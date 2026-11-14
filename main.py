
import os
import json
import requests
import yfinance as yf
import pandas as pd

from datetime import datetime
from zoneinfo import ZoneInfo

# --------------------------------------------------
# TELEGRAM SETTINGS
# --------------------------------------------------

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN secret is missing")

if not CHAT_ID:
    raise ValueError("TELEGRAM_CHAT_ID secret is missing")

chat_id = int(CHAT_ID)

BASE = f"https://api.telegram.org/bot{TOKEN}"


# --------------------------------------------------
# ALERT STATE - PREVENT DUPLICATE ALERTS
# --------------------------------------------------

STATE_FILE = "alert_state.json"

try:
    with open(STATE_FILE, "r") as f:
        alert_state = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    alert_state = {}


# --------------------------------------------------
# TELEGRAM MESSAGE FUNCTION
# --------------------------------------------------

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
        raise RuntimeError("Telegram delivery failed")


# --------------------------------------------------
# MARKET DATA AND INDICATORS
# --------------------------------------------------

def get_market_data(symbol, name):

    df = yf.Ticker(symbol).history(
        period="10d",
        interval="1h",
        auto_adjust=False
    )

    if df.empty:
        raise RuntimeError(f"No hourly data for {name}")

    df = df[["Open", "High", "Low", "Close"]].dropna()

    # Yahoo hourly timestamps represent candle start times.
    # Exclude candles that have not completed yet.

    if df.index.tz is None:
        raise RuntimeError(
            f"Hourly data timezone missing for {name}"
        )

    utc_index = df.index.tz_convert("UTC")

    cutoff = (
        pd.Timestamp.now(tz="UTC")
        - pd.Timedelta(seconds=60)
    )

    completed = (
        utc_index + pd.Timedelta(hours=1)
    ) <= cutoff

    df = df.loc[completed].copy()

    if len(df) < 50:
        raise RuntimeError(
            f"Insufficient completed hourly candles for {name}"
        )

    # --------------------------------------------------
    # PRICE SERIES
    # --------------------------------------------------

    open_price = df["Open"]
    high_price = df["High"]
    low_price = df["Low"]
    close = df["Close"]

    # --------------------------------------------------
    # EMA 9 AND EMA 20
    # --------------------------------------------------

    ema9 = close.ewm(
        span=9,
        adjust=False
    ).mean()

    ema20 = close.ewm(
        span=20,
        adjust=False
    ).mean()

    # --------------------------------------------------
    # RSI 14
    # --------------------------------------------------

    delta = close.diff()

    gain = delta.clip(lower=0).ewm(
        alpha=1/14,
        adjust=False
    ).mean()

    loss = (-delta.clip(upper=0)).ewm(
        alpha=1/14,
        adjust=False
    ).mean()

    rs = gain / loss.replace(0, float("nan"))

    rsi = 100 - (100 / (1 + rs))

    rsi = rsi.mask((loss == 0) & (gain > 0), 100)
    rsi = rsi.mask((gain == 0) & (loss > 0), 0)

    # --------------------------------------------------
    # CURRENT CANDLE INFORMATION
    # --------------------------------------------------

    last_open = open_price.iloc[-1]
    last_close = close.iloc[-1]

    candle_change = (
        (last_close - last_open) / last_open
    ) * 100

    if candle_change > 0:
        candle_text = f"🟢 +{candle_change:.2f}%"
    elif candle_change < 0:
        candle_text = f"🔴 {candle_change:.2f}%"
    else:
        candle_text = "⚪ 0.00%"

    last_time = df.index[-1].tz_convert(
        ZoneInfo("Asia/Kolkata")
    )

    # --------------------------------------------------
    # HOURLY REPORT
    # --------------------------------------------------

    report = (
        f"*{name}*\n"
        f"Hourly Close: {last_close:,.2f}\n"
        f"Candle Change: {candle_text}\n"
        f"RSI (14): {rsi.iloc[-1]:.2f}\n"
        f"EMA 20: {ema20.iloc[-1]:,.2f}\n"
        f"Candle Time: {last_time:%H:%M} IST\n"
    )

    # --------------------------------------------------
    # PRICE ACTION CONDITIONS
    # --------------------------------------------------

    conditions = {

        "BULLISH - CLOSE ABOVE PREVIOUS OPEN":
            close > open_price.shift(1),

        "BULLISH - CLOSE ABOVE PREVIOUS HIGH":
            close > high_price.shift(1),

        "BEARISH - CLOSE BELOW PREVIOUS OPEN":
            close < open_price.shift(1),

        "BEARISH - CLOSE BELOW PREVIOUS LOW":
            close < low_price.shift(1)
    }

    # --------------------------------------------------
    # BULLISH AND BEARISH ENGULFING
    # --------------------------------------------------

    previous_red = (
        close.shift(1) < open_price.shift(1)
    )

    previous_green = (
        close.shift(1) > open_price.shift(1)
    )

    current_green = close > open_price
    current_red = close < open_price

    bullish_engulfing = (
        previous_red
        & current_green
        & (open_price <= close.shift(1))
        & (close >= open_price.shift(1))
    )

    bearish_engulfing = (
        previous_green
        & current_red
        & (open_price >= close.shift(1))
        & (close <= open_price.shift(1))
    )

    conditions["BULLISH ENGULFING"] = bullish_engulfing
    conditions["BEARISH ENGULFING"] = bearish_engulfing

    # --------------------------------------------------
    # EMA 9 TOUCH + EMA 20 POSITION
    # --------------------------------------------------

    # Actual candle range must touch or cross EMA 9.
    # EMA 9 value will not be displayed in Telegram.

    touches_ema9 = (
        (low_price <= ema9)
        & (high_price >= ema9)
    )

    bullish_ema_setup = (
        current_green
        & (close < ema20)
        & touches_ema9
    )

    bearish_ema_setup = (
        current_red
        & (close > ema20)
        & touches_ema9
    )

    conditions[
        "BULLISH - GREEN CANDLE BELOW EMA 20 TOUCHING EMA 9"
    ] = bullish_ema_setup

    conditions[
        "BEARISH - RED CANDLE ABOVE EMA 20 TOUCHING EMA 9"
    ] = bearish_ema_setup


    # --------------------------------------------------
    # NEW CANDLE CHECK
    # --------------------------------------------------

    candle_id = df.index[-1].isoformat()

    previous_candle_id = alert_state.get(symbol)

    alerts = []

    if candle_id != previous_candle_id:

        triggered = []

        for title, condition in conditions.items():

            if bool(condition.iloc[-1]):

                emoji = (
                    "🟢" if "BULLISH" in title else "🔴"
                )

                triggered.append(f"{emoji} {title}")

        if triggered:

            alerts.append(
                "🚨 *NEW HOURLY SIGNALS*\n\n"
                f"*{name}*\n\n"
                + "\n".join(triggered)
                + f"\n\nHourly Close: {last_close:,.2f}"
                + f"\nCandle Change: {candle_text}"
                + f"\nRSI (14): {rsi.iloc[-1]:.2f}"
                + f"\nEMA 20: {ema20.iloc[-1]:,.2f}"
                + f"\nCandle Time: {last_time:%H:%M} IST"
            )

        alert_state[symbol] = candle_id

    return report, alerts



# --------------------------------------------------
# FETCH BTC AND GOLD
# --------------------------------------------------

btc, btc_alerts = get_market_data(
    "BTC-USD",
    "BITCOIN (BTC/USD)"
)

gold, gold_alerts = get_market_data(
    "GC=F",
    "COMEX GOLD FUTURES (USD/oz)"
)


# --------------------------------------------------
# SEND REGULAR HOURLY REPORT
# --------------------------------------------------

now = datetime.now(ZoneInfo("Asia/Kolkata"))

report = (
    "📊 *HOURLY MARKET REPORT*\n"
    f"Date: {now:%d-%m-%Y}\n"
    f"Time: {now:%H:%M} IST\n\n"
    f"{btc}\n"
    f"{gold}\n"
    "_Data: Yahoo Finance. Hourly data may be delayed._"
)

send_telegram(report)

print("Hourly market report sent successfully.")


# --------------------------------------------------
# SEND SEPARATE SIGNAL ALERTS
# --------------------------------------------------

all_alerts = btc_alerts + gold_alerts

for alert in all_alerts:

    send_telegram(alert)

    print("New signal sent successfully.")

if not all_alerts:
    print("No new price-action signals.")


# --------------------------------------------------
# SAVE ALERT STATE
# --------------------------------------------------

with open(STATE_FILE, "w") as f:
    json.dump(alert_state, f, indent=4)

print("Alert state saved successfully.")
