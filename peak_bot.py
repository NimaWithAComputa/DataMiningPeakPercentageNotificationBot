"""
MyPower Peak Percentage Monitor Bot
Logs into mypower.coop, scrapes the system peak percentage, and sends
Telegram notifications based on configurable color-zone thresholds.
"""
 
import os
import re
import json
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime
 
# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
 
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "YOUR_CHAT_ID_HERE")
MYPOWER_USERNAME   = os.getenv("MYPOWER_USERNAME",    "YOUR_USERNAME_HERE")
MYPOWER_PASSWORD   = os.getenv("MYPOWER_PASSWORD",    "YOUR_PASSWORD_HERE")
 
LOGIN_URL = "https://www.mypower.coop/peakv2.aspx"
PEAK_URL  = "https://www.mypower.coop/peakv2.aspx"
 
STATE_FILE = "state.json"
 
# Color zone thresholds
ZONES = [
    (0,   75,  "Low",      "🟢"),
    (75,  90,  "Moderate", "🟡"),
    (90,  100, "High",     "🟠"),
    (100, 999, "Critical", "🔴"),
]
 
ALERT_ON_ZONE_CHANGE_ONLY  = True
EXTRA_ALERT_THRESHOLDS     = [85, 95, 100, 105]
 
# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)
 
 
# ─────────────────────────────────────────────
#  STATE
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
#  LOGIN + SCRAPER
# ─────────────────────────────────────────────
 
def create_session() -> requests.Session | None:
    """
    Log into mypower.coop using a session.
    ASP.NET requires grabbing hidden fields (__VIEWSTATE etc.) from
    the login page before posting credentials.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    })
 
    # Step 1: GET the login page to collect hidden ASP.NET fields
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
 
    # Step 2: POST credentials along with all required hidden fields
    payload = {
        "__LASTFOCUS":        "",
        "__EVENTTARGET":      "",
        "__EVENTARGUMENT":    "",
        "__VIEWSTATE":        hidden("__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": hidden("__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION":  hidden("__EVENTVALIDATION"),
        "loginid":            MYPOWER_USERNAME,
        "password":           MYPOWER_PASSWORD,
        "btnSubmit":          "Submit",
    }
 
    try:
        login_resp = session.post(LOGIN_URL, data=payload, timeout=15)
        login_resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"Login POST failed: {e}")
        return None
 
    # Check we actually got in (look for a sign of failed login)
    if "loginid" in login_resp.text.lower() and "password" in login_resp.text.lower():
        log.error("Login appears to have failed — still seeing login form.")
        return None
 
    log.info("Login successful.")
    return session
 
 
def fetch_peak_percentage(session: requests.Session) -> float | None:
    """Fetch the peak page and extract the percentage."""
    try:
        resp = session.get(PEAK_URL, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"Failed to fetch peak page: {e}")
        return None
 
    soup = BeautifulSoup(resp.text, "html.parser")
 
    # Primary: <p class="percentage">80.90%</p>
    tag = soup.find("p", class_="percentage")
    if tag:
        m = re.search(r"(\d{1,3}(?:\.\d+)?)", tag.get_text())
        if m:
            return float(m.group(1))
 
    # Fallback: any element with class="percentage"
    tag = soup.find(class_="percentage")
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
        f"🌐 <a href=\"{PEAK_URL}\">View live dashboard</a>"
    )
 
 
# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
 
def main():
    state = load_state()
 
    session = create_session()
    if session is None:
        log.error("Could not log in. Exiting.")
        return
 
    pct = fetch_peak_percentage(session)
    if pct is None:
        log.error("Could not fetch percentage. Exiting.")
        return
 
    log.info(f"Current peak: {pct}%")
 
    zone      = get_zone(pct)
    last_zone = next((z for z in ZONES if z[0] == state.get("last_zone_min")), None)
    crossed   = set(state.get("crossed_thresholds", []))
    alerts    = []
 
    # Zone-change alert
    if ALERT_ON_ZONE_CHANGE_ONLY:
        if zone != last_zone:
            if last_zone is None:
                alerts.append(f"First check — currently in {zone[2]} zone")
            else:
                direction = "↑" if zone[0] > last_zone[0] else "↓"
                alerts.append(f"Zone changed {direction} {last_zone[2]} → {zone[2]}")
    else:
        alerts.append("Scheduled check")
 
    # Extra threshold alerts
    for threshold in EXTRA_ALERT_THRESHOLDS:
        if pct >= threshold and threshold not in crossed:
            alerts.append(f"Crossed {threshold}% threshold ⚠️")
            crossed.add(threshold)
        elif pct < threshold:
            crossed.discard(threshold)
 
    for reason in alerts:
        msg = build_message(pct, zone, reason)
        send_telegram(msg)
 
    # Save state
    state["last_zone_min"]      = zone[0]
    state["crossed_thresholds"] = list(crossed)
    save_state(state)
    log.info(f"State saved. Zone={zone[2]}, crossed={list(crossed)}")
 
 
if __name__ == "__main__":
    main()
 
