"""Sunroof camera catalog. Importing loads `.env` into the environment (nearest one upward
from cwd, then `camera/.env`) without overriding variables that are already set."""

from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True), override=False)
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
