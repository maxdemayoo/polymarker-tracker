import os
import time
import json
import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

ENV_PATH = os.path.join(BASE_DIR, ".env")
SEEN_FILE = os.path.join(DATA_DIR, "seen_trades.json")

load_dotenv(dotenv_path=ENV_PATH)

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

if not DISCORD_WEBHOOK_URL:
    raise ValueError("Missing DISCORD_WEBHOOK_URL environment variable")

# --- CONFIG ---
WALLET = "0x6ac5bb06a9eb05641fd5e82640268b92f3ab4b6e"
THRESHOLD = 7500
CHECK_INTERVAL = 30  # seconds


def load_seen_trades():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"Error loading seen trades: {e}")
    return set()


def save_seen_trades(seen_trades):
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen_trades), f)
    except Exception as e:
        print(f"Error saving seen trades: {e}")


def get_trade_id(trade):
    for key in ["id", "transactionHash", "txHash"]:
        value = trade.get(key)
        if value:
            return str(value)

    return (
        f"{trade.get('slug', 'unknown')}|"
        f"{trade.get('timestamp', '')}|"
        f"{trade.get('usdcSize', '')}|"
        f"{trade.get('title', '')}|"
        f"{trade.get('outcome', '')}"
    )


def get_trades():
    url = f"https://data-api.polymarket.com/activity?user={WALLET}&limit=500"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching trades: {e}")
        return []


def send_discord(message):
    try:
        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": message},
            timeout=10
        )
        response.raise_for_status()
        print(f"Discord alert sent:\n{message}")
    except Exception as e:
        print(f"Error sending Discord alert: {e}")


def warmup_seen_trades():
    trades = get_trades()
    if not trades:
        print("Warmup failed: no trades found.")
        return set()

    seen_trades = set()

    for trade in trades:
        if trade.get("type") != "TRADE":
            continue
        seen_trades.add(get_trade_id(trade))

    save_seen_trades(seen_trades)
    print(f"Warmup complete. Saved {len(seen_trades)} existing trades. No alerts sent.")
    return seen_trades


def check_trades(seen_trades):
    trades = get_trades()
    if not trades:
        print("No trades found.")
        return seen_trades

    new_trades = []
    for trade in trades:
        if trade.get("type") != "TRADE":
            continue

        trade_id = get_trade_id(trade)
        if trade_id not in seen_trades:
            new_trades.append(trade)
            seen_trades.add(trade_id)

    if not new_trades:
        print("No new trade activity.")
        return seen_trades

    print(f"New trades found: {len(new_trades)}")

    for trade in new_trades:
        slug = trade.get("slug", "unknown")
        title = trade.get("title", slug)

        outcome = (
            trade.get("outcome")
            or trade.get("side")
            or trade.get("outcomeName")
            or trade.get("tokenName")
            or "Unknown Outcome"
        )

        usdc = float(trade.get("usdcSize", 0) or 0)

        price = (
            trade.get("price")
            or trade.get("pricePaid")
            or trade.get("avgPrice")
            or trade.get("outcomePrice")
            or 0
        )
        price = float(price or 0)

        print(f"- {title} | {outcome}: ${usdc:,.2f} at {price:.3f}")

        if usdc >= THRESHOLD:
            msg = (
                f"🚨 HUGE SINGLE POLYMARKET BET\n"
                f"Market: {title}\n"
                f"Bet on: {outcome}\n"
                f"Trade size: ${usdc:,.0f}\n"
                f"Price paid: {price:.3f}"
            )
            send_discord(msg)

    save_seen_trades(seen_trades)
    return seen_trades


def main():
    if not os.path.exists(SEEN_FILE):
        print("First run detected. Warming up from current activity...")
        seen_trades = warmup_seen_trades()
    else:
        seen_trades = load_seen_trades()
        print(f"Loaded {len(seen_trades)} previously seen trades.")

    print("Monitoring started...")

    while True:
        print(f"Checking... ({time.strftime('%H:%M:%S')})")
        seen_trades = check_trades(seen_trades)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()