#!/usr/bin/env python3
"""
eviStream uptime monitor.
Checks the site and backend every run (called by cron every 5 min).
Sends email via Gmail SMTP only when state changes (up→down or down→up).
"""

import json
import os
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
import urllib.request
import urllib.error

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parents[3]  # backend/deploy/monitor/ -> project root

CHECKS = [
    {"name": "Frontend",  "url": "https://evistreams.com"},
    {"name": "Backend",   "url": "https://evistreams.com/health"},
]
TIMEOUT    = 15
STATE_FILE = BASE_DIR / "logs" / "monitor_state.json"

GMAIL_SENDER   = os.environ.get("MONITOR_GMAIL_SENDER", "")
GMAIL_PASSWORD = os.environ.get("MONITOR_GMAIL_PASSWORD", "")
ALERT_EMAIL    = os.environ.get("MONITOR_ALERT_EMAIL", GMAIL_SENDER)

# ── Helpers ───────────────────────────────────────────────────────────────────
def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def check_url(url: str) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "evistream-monitor/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return (resp.status == 200), f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, str(e.reason)
    except Exception as e:
        return False, str(e)

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}

def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))

def send_email(subject: str, body: str):
    try:
        msg = MIMEMultipart()
        msg["From"]    = GMAIL_SENDER
        msg["To"]      = ALERT_EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
            server.login(GMAIL_SENDER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_SENDER, ALERT_EMAIL, msg.as_string())
        print(f"[ALERT] Email sent: {subject}")
    except Exception as e:
        print(f"[ERROR] Email failed: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    timestamp = now()
    state = load_state()
    any_alert = False

    for check in CHECKS:
        name   = check["name"]
        url    = check["url"]
        is_up, reason = check_url(url)
        was_up = state.get(name, {}).get("up", True)

        status = "UP" if is_up else "DOWN"
        print(f"[{timestamp}] {name}: {status} — {reason}")

        if not is_up and was_up:
            send_email(
                subject=f"🔴 eviStreams DOWN — {name}",
                body=(
                    f"eviStreams is DOWN.\n\n"
                    f"Check : {name}\n"
                    f"URL   : {url}\n"
                    f"Reason: {reason}\n"
                    f"Time  : {timestamp}\n\n"
                    f"SSH in: ssh ubuntu@3.213.243.178\n"
                    f"Quick fix:\n"
                    f"  sudo systemctl status evistream-*.service\n"
                    f"  sudo systemctl restart evistream.target\n"
                ),
            )
            any_alert = True

        elif is_up and not was_up:
            send_email(
                subject=f"✅ eviStreams RECOVERED — {name}",
                body=(
                    f"eviStreams is back UP.\n\n"
                    f"Check : {name}\n"
                    f"URL   : {url}\n"
                    f"Time  : {timestamp}\n"
                ),
            )
            any_alert = True

        state[name] = {"up": is_up, "reason": reason, "last_checked": timestamp}

    save_state(state)
    if not any_alert:
        print(f"[{timestamp}] All checks passed. No state change.")

if __name__ == "__main__":
    main()
