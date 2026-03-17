import os
import time
import json
import requests
from dotenv import load_dotenv
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

ENV_PATH = os.path.join(BASE_DIR, ".env")
SEEN_FILE = os.path.join(DATA_DIR, "seen_trades.json")
ROLLING_FILE = os.path.join(DATA_DIR, "rolling_totals.json")

load_dotenv(dotenv_path=ENV_PATH)

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

if not DISCORD_WEBHOOK_URL:
    raise ValueError("Missing DISCORD_WEBHOOK_URL environment variable")

# --- CONFIG ---
WALLET = "0x6ac5bb06a9eb05641fd5e82640268b92f3ab4b6e"
THRESHOLD = 2000
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


def load_rolling_totals():
    if os.path.exists(ROLLING_FILE):
        try:
            with open(ROLLING_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data
        except Exception as e:
            print(f"Error loading rolling totals: {e}")
    return {}


def save_rolling_totals(rolling_totals):
    try:
        with open(ROLLING_FILE, "w", encoding="utf-8") as f:
            json.dump(rolling_totals, f, indent=2)
    except Exception as e:
        print(f"Error saving rolling totals: {e}")


def get_trade_id(trade):
    for key in ["id", "transactionHash", "txHash"]:
        value = trade.get(key)
        if value:
            return str(value)

    return f"{trade.get('slug','unknown')}|{trade.get('timestamp','')}|{trade.get('usdcSize','')}|{trade.get('title','')}|{trade.get('outcome','')}"


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


def check_markets(seen_trades, rolling_totals):
    trades = get_trades()
    if not trades:
        print("No trades found.")
        return seen_trades, rolling_totals

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
        return seen_trades, rolling_totals

    print(f"New trades found: {len(new_trades)}")

    batch_totals = defaultdict(float)
    batch_info = {}

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

        key = f"{slug}|||{outcome}"

        batch_totals[key] += usdc

        if key not in batch_info:
            batch_info[key] = {
                "title": title,
                "outcome": outcome,
                "prices": [],
            }

        batch_info[key]["prices"].append(price)

    print("New activity this cycle:")
    for key, total in sorted(batch_totals.items(), key=lambda x: x[1], reverse=True):
        info = batch_info[key]
        print(f"- {info['title']} | {info['outcome']}: ${total:,.2f}")

    for key, batch_total in batch_totals.items():
        info = batch_info[key]

        old_total = rolling_totals.get(key, {}).get("total", 0)
        already_alerted = rolling_totals.get(key, {}).get("alerted", False)

        new_total = old_total + batch_total

        rolling_totals[key] = {
            "title": info["title"],
            "outcome": info["outcome"],
            "total": new_total,
            "alerted": already_alerted,
        }

        avg_price = sum(info["prices"]) / len(info["prices"]) if info["prices"] else 0

        print(
            f"Running total -> {info['title']} | {info['outcome']}: "
            f"${new_total:,.2f}"
        )

        if new_total >= THRESHOLD and not already_alerted:
            msg = (
                f"🚨 NEW POLYMARKET ACTIVITY\n"
                f"Market: {info['title']}\n"
                f"Bet on: {info['outcome']}\n"
                f"New this cycle: ${batch_total:,.0f}\n"
                f"Running total: ${new_total:,.0f}\n"
                f"Avg price paid this cycle: {avg_price:.3f}"
            )
            send_discord(msg)
            rolling_totals[key]["alerted"] = True

    save_seen_trades(seen_trades)
    save_rolling_totals(rolling_totals)

    return seen_trades, rolling_totals


def main():
    if not os.path.exists(SEEN_FILE):
        print("First run detected. Warming up from current activity...")
        seen_trades = warmup_seen_trades()
    else:
        seen_trades = load_seen_trades()
        print(f"Loaded {len(seen_trades)} previously seen trades.")

    rolling_totals = load_rolling_totals()
    print(f"Loaded {len(rolling_totals)} rolling market/outcome totals.")
    print("Monitoring started...")

    while True:
        print(f"Checking... ({time.strftime('%H:%M:%S')})")
        seen_trades, rolling_totals = check_markets(seen_trades, rolling_totals)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()