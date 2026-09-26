"""BestNAI gateway entry point."""
from __future__ import annotations
import os, sys
from pathlib import Path

from dotenv import load_dotenv
import uvicorn

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))
from app.main import app

if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("BESTNAI_HOST", "127.0.0.1"),
        port=int(os.getenv("BESTNAI_PORT", "8010")),
    )
