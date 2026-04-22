"""
eBay Listing Sync
Runs every 30 min via GitHub Actions.
Fetches all offers from every connected eBay account.
Inserts new listings, updates existing ones.
Deduplicates ASINs into products table.
"""

import asyncio
import httpx
from datetime import datetime
from utils import get_db, get_ebay_token, get_all_active_accounts, extract_asin

MARKETPLACE = "EBAY_GB"


async def fetch_offers(token: str) -> list:
    """Fetch all offers (paginated) from one eBay account."""
    offers = []
    offset = 0

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            res = await client.get(
                "https://api.ebay.com/sell/inventory/v1/offer",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE
                },
                params={"limit": 200, "offset": offset}
            )
            data = res.json()
            page = data.get("offers", [])
            if not page:
                break
            offers.extend(page)
            total = data.get("total", 0)
            offset += 200
            if offset >= total:
                break

    return offers


async def fetch_inventory(token: str, sku: str) -> dict:
    """Fetch inventory item for a single SKU to get title + quantity."""
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.get(
            f"https://api.ebay.com/sell/inventory/v1/inventory_item/{sku}",
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE
            }
        )
        if res.status_code == 200:
            return res.json()
    return {}


async def sync_account(db, account_id: str, refresh_token: str) -> dict:
    """Sync one eBay account — returns stats."""
    token = await get_ebay_token(refresh_token)
    if not token:
        print(f"  ✗ {account_id}: could not get token")
        await db.execute(
            "UPDATE ebay_accounts SET status='token_error' WHERE account_id=?",
            [account_id]
        )
        return {"new": 0, "updated": 0, "error": True}

    offers = await fetch_offers(token)
    if not offers:
        print(f"  {account_id}: no offers found")
        return {"new": 0, "updated": 0}

    # Get existing offer IDs for fast dedup check
    existing = await db.execute(
        "SELECT offer_id FROM listings WHERE account_id=? AND offer_id!=''",
        [account_id]
    )
    existing_ids = {row[0] for row in existing.rows}

    new_count = updated_count = 0
    now = datetime.utcnow().isoformat()

    for offer in offers:
        sku          = offer.get("sku", "")
        offer_id     = offer.get("offerId", "")
        ebay_item_id = offer.get("listingId", "")
        price_obj    = offer.get("pricingSummary", {}).get("price", {})
        ebay_price   = float(price_obj.get("value", 0)) if price_obj else 0.0
        quantity     = int(offer.get("availableQuantity", 0))
        asin         = extract_asin(sku)

        # Get title from inventory item (only for new listings to save API calls)
        title = ""
        if offer_id not in existing_ids:
            inv = await fetch_inventory(token, sku)
            title = inv.get("product", {}).get("title", "")

        # Ensure ASIN exists in products (deduplication)
        if asin:
            await db.execute(
                "INSERT OR IGNORE INTO products (asin) VALUES (?)",
                [asin]
            )

        if offer_id not in existing_ids:
            await db.execute("""
                INSERT OR IGNORE INTO listings
                    (account_id, sku, asin, ebay_item_id, offer_id,
                     title, ebay_price, quantity, last_synced)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [account_id, sku, asin, ebay_item_id, offer_id,
                  title, ebay_price, quantity, now])
            new_count += 1
        else:
            await db.execute("""
                UPDATE listings SET
                    ebay_price=?, quantity=?, last_synced=?
                WHERE account_id=? AND offer_id=?
            """, [ebay_price, quantity, now, account_id, offer_id])
            updated_count += 1

    # Update account last_synced
    await db.execute(
        "UPDATE ebay_accounts SET last_synced=? WHERE account_id=?",
        [now, account_id]
    )

    return {"new": new_count, "updated": updated_count}


async def main():
    print("=" * 55)
    print(f"eBay Listing Sync — {datetime.utcnow().strftime('%H:%M UTC')}")
    print("=" * 55)

    db = get_db()
    try:
        accounts = await get_all_active_accounts(db)
        if not accounts:
            print("No active accounts. Connect via dashboard first.")
            return

        total_new = total_updated = 0
        for account_id, refresh_token in accounts:
            print(f"\n  Syncing: {account_id}")
            stats = await sync_account(db, account_id, refresh_token)
            if not stats.get("error"):
                print(f"  ✓ +{stats['new']} new  ~{stats['updated']} updated")
                total_new     += stats["new"]
                total_updated += stats["updated"]

        print(f"\n✓ Done — {total_new} new listings, {total_updated} updated")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())