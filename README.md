# MyPower Peak Bot — GitHub Actions Setup

Monitors the System Peak Percentage on mypower.coop and sends Telegram
alerts when it crosses color-zone thresholds. Runs free on GitHub Actions.

---

## Quick Setup (10 minutes)

### Step 1 — Create a Telegram Bot
1. Open Telegram → search **@BotFather** → send `/newbot`
2. Follow the prompts and copy your **Bot Token** (`123456:ABCdef...`)
3. Send any message to your new bot (so it can message you back)
4. Get your **Chat ID**: visit this URL in your browser:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   Look for `"id":` inside the `"chat"` object.

---

### Step 2 — Put the bot on GitHub
1. Go to [github.com](https://github.com) → **New repository**
2. Name it `peak-bot`, set it to **Private**, click Create
3. Upload these files to the repo:
   ```
   peak_bot.py
   requirements.txt
   .github/
   └── workflows/
       └── peak-monitor.yml
   ```
   (You can drag-and-drop them on the GitHub website)

---

### Step 3 — Add your Telegram secrets
1. In your repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret** and add:
   - Name: `TELEGRAM_BOT_TOKEN` → Value: your bot token
   - Name: `TELEGRAM_CHAT_ID`   → Value: your chat ID

---

### Step 4 — Enable Actions & do a test run
1. Go to the **Actions** tab in your repo
2. Click **Peak Monitor** in the left sidebar
3. Click **Run workflow** → **Run workflow** (green button)
4. Watch it run — you should get a Telegram message within 30 seconds!

After that, it runs automatically every hour forever, for free.

---

## Customize Thresholds

Edit the top of `peak_bot.py`:

```python
ZONES = [
    (0,   75,  "Low",      "🟢"),
    (75,  90,  "Moderate", "🟡"),
    (90,  100, "High",     "🟠"),
    (100, 999, "Critical", "🔴"),
]

EXTRA_ALERT_THRESHOLDS = [85, 95, 100, 105]
```

To change the schedule, edit the `cron:` line in `.github/workflows/peak-monitor.yml`:
```yaml
- cron: '0 * * * *'      # every hour
- cron: '0 */6 * * *'    # every 6 hours
- cron: '0 14 * * *'     # once a day at 9am CDT (14:00 UTC)
```
Use https://crontab.guru to build cron expressions easily.

---

## How It Works

```
GitHub Actions (cron) → peak_bot.py → scrapes mypower.coop
                                     → checks zone vs last run (state.json)
                                     → sends Telegram if zone changed
                                     → saves state.json back to repo
```

State is saved in `state.json` (committed automatically by the workflow),
so the bot remembers which zone it was in even between runs.

---

## Example Notifications

```
🟠 Peak Alert — High Zone
━━━━━━━━━━━━━━━━━━━━
📊 94.30% of monthly system peak
[██████████████████░░] 94.3%
🕐 Jun 01, 2026  02:00 PM UTC
📌 Zone changed ↑ Moderate → High
━━━━━━━━━━━━━━━━━━━━
🌐 View live dashboard

🔴 Peak Alert — Critical Zone
━━━━━━━━━━━━━━━━━━━━
📊 101.20% of monthly system peak
[████████████████████] 101.2%
🕐 Jun 01, 2026  03:00 PM UTC
📌 Crossed 100% threshold ⚠️
━━━━━━━━━━━━━━━━━━━━
🌐 View live dashboard
```
