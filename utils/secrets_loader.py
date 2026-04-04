"""
Loads secrets from AWS Secrets Manager (production) or .env file (development).
Must be called before any app.config / core.config imports.

Trigger: set AWS_SECRETS_NAME=evistream/production in the process environment
         (e.g. via /etc/evistream/bootstrap.env referenced in systemd units).
         If the variable is absent, falls back to loading backend/.env as before.
"""
import json
import os
import sys
from pathlib import Path


def load_secrets() -> None:
    secret_name = os.environ.get("AWS_SECRETS_NAME")

    if not secret_name:
        # Development / local: fall back to .env file
        from dotenv import load_dotenv
        dotenv_path = Path(__file__).parent.parent / ".env"
        load_dotenv(dotenv_path=dotenv_path, override=False)
        return

    # Production: fetch from AWS Secrets Manager using the EC2 instance role
    try:
        import boto3
        region = os.environ.get("AWS_REGION", "us-east-1")
        client = boto3.client("secretsmanager", region_name=region)
        response = client.get_secret_value(SecretId=secret_name)
        secret_dict = json.loads(response["SecretString"])
    except Exception as e:
        print(
            f"[secrets_loader] FATAL: Could not load secrets from '{secret_name}': {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    injected = 0
    for key, value in secret_dict.items():
        if key not in os.environ:  # allow operator overrides via env
            os.environ[key] = str(value)
            injected += 1

    print(
        f"[secrets_loader] Loaded {injected} secrets from '{secret_name}'",
        file=sys.stderr,
    )
