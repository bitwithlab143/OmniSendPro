"""Entrypoint (ARCHITECTURE.md §36: backend/main.py). Run with: uvicorn main:app"""

from app.main import app

__all__ = ["app"]
