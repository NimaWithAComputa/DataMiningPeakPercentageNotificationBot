"""
MyPower Peak Percentage Monitor Bot
- Alerts when peak exceeds 95%
- Monitors only during active windows (Central Time):
    Oct-May: 6-11am, 5-9pm
    Jun-Sep: 11am-9pm
"""

import os
import re
import json
import logging
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import pytz

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
MYPOWER_USERNAME   = os.getenv("MYPOWER_USERNAME")
MYPOWER_PASSWORD   = os.getenv("MYPOWER_PASSWORD")

LOGIN_URL = "https://www.mypower.coop/peakv2.aspx"
PEAK_URL  = "https://www.mypower.coop/peakv2.aspx"

STATE_FILE = "state.json"

ALERT_THRESHOLD = 95.0

# America/Chicago handles both CST (UTC-6) and CDT (UTC-5) automatically
CENTRAL = pytz.timezone("America/Chicago")

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  MONITORING WINDOW CHECK
# ─────────────────────────────────────────────

def is_active_window() -> bool:
    """
    Returns True if current Central Time falls within a monitoring window.
    America/Chicago automatically handles CST/CDT switching.

    Oct-May:  6:00 AM - 11:00 AM  and  5:00 PM - 9:00 PM
    Jun-Sep: 11:00 AM - 9:00 PM
    """
    utc_now     = datetime.now(pytz.utc)
    central_now = utc_now.astimezone(CENTRAL)
    month       = central_now.month
    hour        = central_now.hour + central_now.minute / 60

    if 6 <= month <= 9:
        active = 10.5 <= hour < 22.0
    else:
        active = (5.75 <= hour < 11.0) or (16.5 <= hour < 22.0)

    log.info(
        f"Time check: {central_now.strftime('%b %d %I:%M %p %Z')} "
        f"(UTC {utc_now.strftime('%H:%M')}) — "
        f"{'ACTIVE' if active else 'outside window, skipping'}"
    )
    return active


# ─────────────────────────────────────────────
#  STATE
# ─────────────────────────────────────────────

def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"alerted": False}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ─────────────────────────────────────────────
#  LOGIN
# ─────────────────────────────────────────────

def create_session() -> requests.Session | None:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    })

    try:
        resp = session.get(LOGIN_URL, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"Failed to load login page: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    def hidden(name):
        tag = soup.find("input", {"name": name})
        return tag["value"] if tag and tag.get("value") else ""

    payload = {
        "__LASTFOCUS":           "",
        "__EVENTTARGET":         "",
        "__EVENTARGUMENT":       "",
        "__VIEWSTATE":           hidden("__VIEWSTATE"),
        "__VIEWSTATEGENERATOR":  hidden("__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION":     hidden("__EVENTVALIDATION"),
        "loginid":               MYPOWER_USERNAME,
        "password":              MYPOWER_PASSWORD,
        "btnSubmit":             "Submit",
    }

    try:
        login_resp = session.post(LOGIN_URL, data=payload, timeout=15)
        login_resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"Login POST failed: {e}")
        return None

    if "loginid" in login_resp.text.lower() and "btnsubmit" in login_resp.text.lower():
        log.error("Login failed — still seeing login form.")
        return None

    log.info("Login successful.")
    return session


# ─────────────────────────────────────────────
#  SCRAPER
# ─────────────────────────────────────────────

def fetch_peak_percentage(session: requests.Session) -> float | None:
    try:
        resp = session.get(PEAK_URL, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"Failed to fetch peak page: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    tag = soup.find("p", class_="percentage") or soup.find(class_="percentage")
    if tag:
        m = re.search(r"(\d{1,3}(?:\.\d+)?)", tag.get_text())
        if m:
            return float(m.group(1))

    log.warning("Could not parse peak percentage from page.")
    return None


# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        log.info("Telegram message sent.")
        return True
    except requests.RequestException as e:
        log.error(f"Failed to send Telegram message: {e}")
        return False


def build_bar(pct: float) -> str:
    filled = min(int(pct / 5), 20)
    empty  = 20 - filled
    return f"[{'█' * filled}{'░' * empty}] {pct:.1f}%"


def build_alert(pct: float, now: datetime) -> str:
    ts = now.strftime("%b %d, %Y  %I:%M %p %Z")
    return (
        f"⚡ <b>CURTAILMENT ALERT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>{pct:.2f}%</b> of monthly system peak\n"
        f"{build_bar(pct)}\n"
        f"🕐 {ts}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <b>Peak exceeds {ALERT_THRESHOLD:.0f}% — curtailment may be needed.</b>\n"
        f"🌐 <a href=\"{PEAK_URL}\">View live dashboard</a>"
    )


def build_all_clear(pct: float, now: datetime) -> str:
    ts = now.strftime("%b %d, %Y  %I:%M %p %Z")
    return (
        f"✅ <b>All Clear</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Peak back to <b>{pct:.2f}%</b>\n"
        f"🕐 {ts}\n"
        f"Curtailment risk has passed."
    )


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    log.info("Bot started — looping every 15 minutes while window is active.")

    while True:
        central_now = datetime.now(pytz.utc).astimezone(CENTRAL)
        state       = load_state()

        if not is_active_window():
            log.info("Window closed — exiting.")
            if state.get("alerted"):
                state["alerted"] = False
                save_state(state)
            break

        session = create_session()
        if session is None:
            log.error("Could not log in. Retrying next cycle.")
        else:
            pct = fetch_peak_percentage(session)
            if pct is None:
                log.error("Could not fetch percentage. Retrying next cycle.")
            else:
                log.info(f"Current peak: {pct}%")
                previously_alerted = state.get("alerted", False)

                if pct > ALERT_THRESHOLD:
                    send_telegram(build_alert(pct, central_now))
                    state["alerted"] = True
                else:
                    if previously_alerted:
                        send_telegram(build_all_clear(pct, central_now))
                    state["alerted"] = False

                save_state(state)
                log.info(f"Done. alerted={state['alerted']}")

        log.info("Sleeping 15 minutes...")
        time.sleep(900)   # 15 minutes


if __name__ == "__main__":
    main()
