#!/usr/bin/env python3
"""
One-time setup: creates the AWS SNS topic, subscribes cigohpenn@gmail.com,
saves the topic ARN to .env, and installs the cron job.
Run once: python setup_monitor.py
"""

import os
import sys
import subprocess
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR   = Path(__file__).resolve().parents[3]  # backend/deploy/monitor/ -> project root
ENV_FILE   = BASE_DIR / "backend" / ".env"
ALERT_EMAIL = "cigohpenn@gmail.com"
TOPIC_NAME  = "evistream-alerts"

load_dotenv(ENV_FILE)

AWS_REGION         = os.getenv("AWS_REGION", "us-east-1")
AWS_ACCESS_KEY_ID  = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
    print("[ERROR] AWS credentials not found in backend/.env")
    sys.exit(1)

import boto3

client = boto3.client(
    "sns",
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
)

# 1. Create SNS topic (idempotent — safe to run multiple times)
print(f"Creating SNS topic '{TOPIC_NAME}' in {AWS_REGION}...")
resp = client.create_topic(Name=TOPIC_NAME)
topic_arn = resp["TopicArn"]
print(f"Topic ARN: {topic_arn}")

# 2. Subscribe email
print(f"Subscribing {ALERT_EMAIL}...")
sub = client.subscribe(
    TopicArn=topic_arn,
    Protocol="email",
    Endpoint=ALERT_EMAIL,
)
print(f"Subscription ARN: {sub.get('SubscriptionArn', 'pending confirmation')}")
print()
print("=" * 60)
print(f"ACTION REQUIRED: Check {ALERT_EMAIL} for a confirmation")
print("email from AWS and click 'Confirm subscription'.")
print("Alerts won't send until you confirm.")
print("=" * 60)
print()

# 3. Save topic ARN to .env
env_content = ENV_FILE.read_text()
arn_line = f"MONITOR_SNS_TOPIC_ARN={topic_arn}"
if "MONITOR_SNS_TOPIC_ARN=" in env_content:
    import re
    env_content = re.sub(r"MONITOR_SNS_TOPIC_ARN=.*", arn_line, env_content)
else:
    env_content += f"\n# === Uptime Monitoring ===\n{arn_line}\n"
ENV_FILE.write_text(env_content)
print(f"Saved MONITOR_SNS_TOPIC_ARN to backend/.env")

# 4. Install cron job
python_bin  = "/home/ubuntu/miniconda3/envs/topics/bin/python"
monitor_script = str(Path(__file__).resolve().parent / "monitor.py")
log_file    = str(BASE_DIR / "logs" / "monitor.log")
cron_line   = f"*/5 * * * * {python_bin} {monitor_script} >> {log_file} 2>&1"

# Read existing crontab
try:
    existing = subprocess.check_output(["crontab", "-l"], stderr=subprocess.DEVNULL).decode()
except subprocess.CalledProcessError:
    existing = ""

if monitor_script in existing:
    print("Cron job already installed — skipping.")
else:
    new_crontab = existing.rstrip() + f"\n{cron_line}\n"
    proc = subprocess.run(["crontab", "-"], input=new_crontab.encode(), capture_output=True)
    if proc.returncode == 0:
        print(f"Cron job installed: checks every 5 minutes")
    else:
        print(f"[ERROR] Failed to install cron: {proc.stderr.decode()}")

print()
print("Setup complete. Monitor will check every 5 minutes.")
print(f"Logs: {log_file}")
print(f"State: {BASE_DIR}/logs/monitor_state.json")
