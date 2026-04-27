"""
eBay Account Connector — run locally to connect a new eBay account.

Usage:
    python src/connect_ebay.py

This opens your browser, you log into eBay, paste the redirect URL back,
and the refresh token is saved directly to your Turso database.

Run this once per eBay account you want to connect.
"""

import os
import asyncio
import webbrowser
import httpx
import base64
from urllib.parse import urlencode, parse_qs, urlparse
from utils import get_db

EBAY_CLIENT_ID     = os.getenv("EBAY_CLIENT_ID")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")
EBAY_REDIRECT_URI  = os.getenv("EBAY_REDIRECT_URI")

SCOPES = " ".join([
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
    "https://api.ebay.com/oauth/api_scope/sell.analytics.readonly"
])


async def connect():
    print("=" * 55)
    print("eBay Account Connector")
    print("=" * 55)

    label = input("\nEnter a label for this account (e.g. 'Store UK 1'): ").strip()
    if not label:
        print("Label cannot be empty.")
        return

    # Build eBay OAuth URL
    auth_url = "https://auth.ebay.com/oauth2/authorize?" + urlencode({
        "client_id":     EBAY_CLIENT_ID,
        "redirect_uri":  EBAY_REDIRECT_URI,
        "response_type": "code",
        "scope":         SCOPES,
        "prompt":        "login"
    })

    print(f"\nOpening browser for: {label}")
    print("Log in with your eBay seller account...")
    webbrowser.open(auth_url)

    print("\nAfter logging in, eBay will redirect to a URL.")
    print("Paste the FULL redirect URL here:")
    redirect_url = input("> ").strip()

    # Extract auth code from redirect URL
    parsed = urlparse(redirect_url)
    params = parse_qs(parsed.query)
    code   = params.get("code", [None])[0]

    if not code:
        print("✗ No code found in URL. Make sure you pasted the full redirect URL.")
        return

    print("\nExchanging code for tokens...")

    credentials = base64.b64encode(
        f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}".encode()
    ).decode()

    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded"
            },
            data={
                "grant_type":   "authorization_code",
                "code":         code,
                "redirect_uri": EBAY_REDIRECT_URI
            }
        )
        tokens = token_res.json()

    if "refresh_token" not in tokens:
        print(f"✗ Token exchange failed: {tokens.get('error_description', tokens)}")
        return

    # Get eBay username
    ebay_username = label
    try:
        async with httpx.AsyncClient() as client:
            user_res = await client.get(
                "https://apiz.ebay.com/commerce/identity/v1/user/",
                headers={"Authorization": f"Bearer {tokens['access_token']}"}
            )
            user_data = user_res.json()
            ebay_username = user_data.get("username", label)
    except Exception:
        pass

    # Save to Turso
    db = get_db()
    try:
        await db.execute("""
            INSERT INTO ebay_accounts
                (account_id, ebay_username, refresh_token, status)
            VALUES (?, ?, ?, 'active')
            ON CONFLICT(account_id) DO UPDATE SET
                ebay_username = excluded.ebay_username,
                refresh_token = excluded.refresh_token,
                status        = 'active'
        """, [label, ebay_username, tokens["refresh_token"]])

        print(f"\n✓ Connected!")
        print(f"  Label    : {label}")
        print(f"  eBay user: {ebay_username}")
        print(f"  Token saved to Turso database.")
        print(f"\nThe sync job will pick up your listings on its next run.")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(connect())