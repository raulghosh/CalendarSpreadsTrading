#!/usr/bin/env python3
"""One-time interactive Schwab OAuth login: opens a browser and saves the token file that the
snapshot job uses. Re-run when Home says the token is about to expire (~every 7 days).

Schwab redirects to SCHWAB_CALLBACK_URL, where schwab-py listens with a self-signed certificate:
make sure nothing else is using that port, and click through the browser's certificate warning.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from calscan.adapters.schwab import login  # noqa: E402
from calscan.settings import Secrets  # noqa: E402

if __name__ == "__main__":
    secrets = Secrets()
    login(secrets)
    print(f"Token saved to {secrets.schwab_token_file}")
