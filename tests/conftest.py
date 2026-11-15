"""Shared test helpers.

Each service lives in its own folder with its own app.py and store.py, the
same way it is packaged into its own image. load_service() imports one of
them without the three getting mixed up.
"""
import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Tests never touch Google Cloud.
os.environ["STORE_BACKEND"] = "memory"


def load_service(name):
    folder = str(ROOT / "services" / name)
    for module in ("app", "store"):
        sys.modules.pop(module, None)
    sys.path.insert(0, folder)
    try:
        module = importlib.import_module("app")
    finally:
        sys.path.remove(folder)
        for cached in ("app", "store"):
            sys.modules.pop(cached, None)
    return module
