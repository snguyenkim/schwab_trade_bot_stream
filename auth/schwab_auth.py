import sys
from pathlib import Path
from datetime import datetime
from loguru import logger

# Allow importing credential_manager from the cresential/ directory
sys.path.insert(0, str(Path(__file__).parent.parent / "cresential"))
from credential_manager import CredentialManager

from schwab import SchwabClient, SchwabAuth

DB_PATH = Path(__file__).parent.parent / "schwab_trader.db"


def get_client(db_path: Path = DB_PATH) -> tuple:
    """
    Return an authenticated (SchwabClient, CredentialManager) tuple.
    Loads credentials + tokens from SQLite via CredentialManager.

    If tokens are expired but a refresh token exists, attempts a silent refresh.
    Raises RuntimeError if no usable auth is found.
    """
    cm = CredentialManager(db_path=db_path)
    creds = cm.get_credentials(api_type="trading")
    if not creds:
        raise RuntimeError(
            "No trading credentials found in schwab_trader.db. "
            "Run: python scripts/setup_credentials.py"
        )

    client = SchwabClient(
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
        redirect_uri=creds.get("redirect_uri", "https://127.0.0.1"),
    )

    tokens = cm.get_tokens(api_type="trading")
    if tokens and tokens.get("refresh_token"):
        client.auth.access_token = tokens["access_token"]
        client.auth.refresh_token = tokens["refresh_token"]
        if tokens.get("expiry"):
            client.auth.token_expiry = tokens["expiry"]

        if not tokens["is_valid"]:
            logger.info("[AUTH] Access token expired — attempting silent refresh...")
            try:
                client.auth.refresh_access_token()
                cm.save_tokens(
                    access_token=client.auth.access_token,
                    refresh_token=client.auth.refresh_token,
                    expires_in=1800,
                    api_type="trading",
                )
                logger.info("[AUTH] Token refreshed and persisted to DB")
            except Exception as exc:
                raise RuntimeError(
                    f"Token refresh failed: {exc}. "
                    "Re-authenticate by running: python scripts/setup_credentials.py --reauth"
                ) from exc
        else:
            remaining = int(tokens["expires_in"])
            logger.info(
                "[AUTH] Loaded valid token | expires_in={}s", remaining
            )

        client.session.headers.update(client.auth.authorization_header)
    else:
        raise RuntimeError(
            "No tokens found in schwab_trader.db. "
            "Run initial OAuth flow: python scripts/setup_credentials.py --reauth"
        )

    return client, cm


def refresh_and_save(
    cm: CredentialManager,
    new_access: str,
    new_refresh: str,
    expires_in: int = 1800,
) -> None:
    """Persist refreshed tokens back to SQLite. Call after every OAuth refresh."""
    cm.save_tokens(
        access_token=new_access,
        refresh_token=new_refresh,
        expires_in=expires_in,
        api_type="trading",
    )
    logger.info("[AUTH] Token refreshed and saved to DB | expires_in={}s", expires_in)
