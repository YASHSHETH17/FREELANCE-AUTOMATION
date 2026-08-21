import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME")
LLM_BASE_URL = os.getenv("LLM_BASE_URL")