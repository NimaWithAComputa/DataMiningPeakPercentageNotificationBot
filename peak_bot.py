"""
MyPower Peak Percentage Monitor Bot
Scrapes the system peak percentage from mypower.coop and sends
Telegram notifications based on configurable color-zone thresholds.

Runs as a single check (designed for GitHub Actions cron scheduling).
State is persisted to state.json so zone-change detection works across runs.
"""

import os
import re
import json
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ─────────────────────────────────────────────
#  CONFIGURATION  — edit these values
# ─────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "YOUR_CHAT_ID_HERE")

URL = "https://www.mypower.coop/peakv2.aspx"

# File that stores the last known zone between GitHub Actions runs
STATE_FILE = "state.json"

# Color zone thresholds — customize these percentages
# Each zone: (min%, max%, label, emoji)
ZONES = [
    (0,   75,  "Low",      "🟢"),
    (75,  90,  "Moderate", "🟡"),
    (90,  100, "High",     "🟠"),
    (100, 999, "Critical", "🔴"),
]

# Only notify when the zone CHANGES (prevents spam on every hourly run).
# Set to False to get a message on every single run.
ALERT_ON_ZONE_CHANGE_ONLY = True

# Also alert whenever the value crosses these thresholds (resets each time
# it dips back below, so you'll be alerted again next time it climbs).
EXTRA_ALERT_THRESHOLDS = [85, 95, 100, 105]

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  STATE  (persisted to repo via git commit in workflow)
# ─────────────────────────────────────────────

def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_zone_min": None, "crossed_thresholds": []}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ─────────────────────────────────────────────
#  SCRAPER
# ─────────────────────────────────────────────

def fetch_peak_percentage() -> float | None:
    """
    Fetch the System Peak Percentage from the mypower.coop page.
    Returns a float (e.g. 100.60) or None if parsing fails.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    try:
        resp = requests.get(URL, headers=headers, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"HTTP error fetching page: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Strategy 1: find percentage near "System Peak" label
    text = soup.get_text(" ", strip=True)
    idx = text.lower().find("system peak")
    if idx != -1:
        nearby = text[idx : idx + 200]
        local = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", nearby)
        if local:
            return float(local.group(1))

    # Strategy 2: any percentage found anywhere on the page
    matches = re.findall(r"(\d{1,3}(?:\.\d+)?)\s*%", text)
    if matches:
        return float(matches[0])

    # Strategy 3: look for standalone percentage in a tag
    for tag in soup.find_all(["span", "div", "td", "p"]):
        m = re.match(r"^\s*(\d{1,3}(?:\.\d+)?)\s*%\s*$", tag.get_text())
        if m:
            val = float(m.group(1))
            if 0 < val < 200:
                return val

    log.warning("Could not parse peak percentage from page.")
    return None


# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        log.info("Telegram message sent.")
        return True
    except requests.RequestException as e:
        log.error(f"Failed to send Telegram message: {e}")
        return False


# ─────────────────────────────────────────────
#  ZONE LOGIC
# ─────────────────────────────────────────────

def get_zone(pct: float) -> tuple:
    for zone in ZONES:
        if zone[0] <= pct < zone[1]:
            return zone
    return ZONES[-1]


def build_bar(pct: float) -> str:
    filled = min(int(pct / 5), 20)
    empty  = 20 - filled
    return f"[{'█' * filled}{'░' * empty}] {pct:.1f}%"


def build_message(pct: float, zone: tuple, reason: str) -> str:
    _, _, label, emoji = zone
    now = datetime.now().strftime("%b %d, %Y  %I:%M %p UTC")
    return (
        f"{emoji} <b>Peak Alert — {label} Zone</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>{pct:.2f}%</b> of monthly system peak\n"
        f"{build_bar(pct)}\n"
        f"🕐 {now}\n"
        f"📌 {reason}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 <a href=\"{URL}\">View live dashboard</a>"
    )


# ─────────────────────────────────────────────
#  MAIN  (single-run mode for GitHub Actions)
# ─────────────────────────────────────────────

def main():
    state = load_state()

    pct = fetch_peak_percentage()
    if pct is None:
        log.error("Could not fetch percentage. Exiting.")
        return

    log.info(f"Current peak: {pct}%")

    zone        = get_zone(pct)
    last_zone   = next((z for z in ZONES if z[0] == state.get("last_zone_min")), None)
    crossed     = set(state.get("crossed_thresholds", []))
    alerts      = []

    # Zone-change alert
    if ALERT_ON_ZONE_CHANGE_ONLY:
        if zone != last_zone:
            if last_zone is None:
                alerts.append(f"First check — currently in {zone[2]} zone")
            else:
                direction = "↑" if zone[0] > last_zone[0] else "↓"
                alerts.append(f"Zone changed {direction} {last_zone[2]} → {zone[2]}")
    else:
        alerts.append("Scheduled hourly check")

    # Extra threshold alerts
    for threshold in EXTRA_ALERT_THRESHOLDS:
        if pct >= threshold and threshold not in crossed:
            alerts.append(f"Crossed {threshold}% threshold ⚠️")
            crossed.add(threshold)
        elif pct < threshold:
            crossed.discard(threshold)   # reset for next time it climbs

    # Send all queued alerts
    for reason in alerts:
        msg = build_message(pct, zone, reason)
        send_telegram(msg)

    # Persist updated state back to file (committed by the workflow)
    state["last_zone_min"]        = zone[0]
    state["crossed_thresholds"]   = list(crossed)
    save_state(state)
    log.info(f"State saved. Zone={zone[2]}, crossed={list(crossed)}")


if __name__ == "__main__":
    main()
