"""
Shared utilities — imported by all src/ scripts.
Handles Turso DB connection and eBay OAuth token refresh.
"""

import os
import asyncio
import httpx
import base64
from libsql_client import create_client


def get_db():
    return create_client(
        os.getenv("LIBSQL_URL"),
        auth_token=os.getenv("LIBSQL_AUTH_TOKEN")
    )


def calc_ebay_price(amazon_price: float) -> float:
    """
    Calculate eBay listing price from Amazon source price.
    Formula: (amazon + buffer) / (1 - ebay_fee - margin)
    Edit margin here to adjust profit target.
    """
    if not amazon_price or amazon_price <= 0:
        return 0.0
    ebay_fee = 0.1295
    margin   = 0.25
    buffer   = 1.50
    price = (amazon_price + buffer) / (1.0 - ebay_fee - margin)
    return round(price * 100) / 100


def extract_asin(sku: str) -> str:
    """Extract Amazon ASIN from eBay SKU field."""
    import re
    match = re.search(r'\b(B[0-9A-Z]{9})\b', (sku or "").upper())
    return match.group(1) if match else ""


async def get_ebay_token(refresh_token: str) -> str | None:
    """Exchange eBay refresh token for short-lived access token."""
    client_id     = os.getenv("EBAY_CLIENT_ID")
    client_secret = os.getenv("EBAY_CLIENT_SECRET")
    credentials   = base64.b64encode(
        f"{client_id}:{client_secret}".encode()
    ).decode()

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            res = await client.post(
                "https://api.ebay.com/identity/v1/oauth2/token",
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                data={
                    "grant_type":    "refresh_token",
                    "refresh_token": refresh_token,
                    "scope": " ".join([
                        "https://api.ebay.com/oauth/api_scope/sell.inventory",
                        "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
                        "https://api.ebay.com/oauth/api_scope/sell.analytics.readonly"
                    ])
                }
            )
            data = res.json()
            return data.get("access_token")
    except Exception as e:
        print(f"  Token refresh error: {e}")
        return None


async def get_all_active_accounts(db) -> list:
    """Return all active eBay accounts with their refresh tokens."""
    res = await db.execute(
        "SELECT account_id, refresh_token FROM ebay_accounts WHERE status = 'active'"
    )
    return res.rows