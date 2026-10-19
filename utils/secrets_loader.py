"""Load configuration from this checkout's .env or the process environment."""
from pathlib import Path
from dotenv import load_dotenv

def load_secrets() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
