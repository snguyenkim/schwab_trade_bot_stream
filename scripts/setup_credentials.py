#!/usr/bin/env python3
"""
One-time credential setup + OAuth flow for the Schwab trading bot.

Usage:
    python scripts/setup_credentials.py           # save credentials only
    python scripts/setup_credentials.py --reauth  # save credentials + run OAuth
"""

import sys
import argparse
import webbrowser
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).parent.parent / "examples"))
from credential_manager import CredentialManager

sys.path.insert(0, str(Path(__file__).parent.parent))

DB_PATH = Path(__file__).parent.parent / "schwab_trader.db"


def save_credentials(cm: CredentialManager) -> dict:
    print("\n=== Schwab Trader — Credential Setup ===")
    client_id = input("Trading Client ID     : ").strip()
    client_secret = input("Trading Client Secret : ").strip()
    redirect_uri = (
        input("Redirect URI [https://127.0.0.1]: ").strip() or "https://127.0.0.1"
    )

    ok = cm.save_all_credentials(
        trading_client_id=client_id,
        trading_client_secret=client_secret,
        redirect_uri=redirect_uri,
    )
    if ok:
        print(f"\nCredentials saved to {cm.db_path}")
    else:
        print("\nFailed to save credentials")
        sys.exit(1)

    return {"client_id": client_id, "client_secret": client_secret,
            "redirect_uri": redirect_uri}


def run_oauth(creds: dict, cm: CredentialManager) -> None:
    from schwab import SchwabClient

    client = SchwabClient(
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
        redirect_uri=creds["redirect_uri"],
    )

    auth_url = client.auth.get_authorization_url()
    print(f"\nAuthorization URL:\n{auth_url}\n")
    print("Opening browser...")
    webbrowser.open(auth_url)

    print("\nAfter authorizing, paste the full callback URL here:")
    callback_url = input().strip()

    parsed = urlparse(callback_url)
    params = parse_qs(parsed.query)
    if "code" not in params:
        print("ERROR: No 'code' found in callback URL")
        sys.exit(1)

    auth_code = params["code"][0]
    print("\nExchanging authorization code for tokens...")
    client.auth.exchange_code_for_tokens(auth_code)

    cm.save_tokens(
        access_token=client.auth.access_token,
        refresh_token=getattr(client.auth, "refresh_token", None),
        expires_in=1800,
        api_type="trading",
    )
    print("Tokens saved. Bot is ready to run: python main.py")


def main():
    parser = argparse.ArgumentParser(description="Schwab credential setup")
    parser.add_argument(
        "--reauth",
        action="store_true",
        help="Run full OAuth flow after saving credentials",
    )
    args = parser.parse_args()

    cm = CredentialManager(db_path=DB_PATH)
    creds = save_credentials(cm)

    if args.reauth:
        run_oauth(creds, cm)
    else:
        print("\nCredentials saved. Run with --reauth to complete OAuth flow.")


if __name__ == "__main__":
    main()
