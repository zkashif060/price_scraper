"""
eBay Analytics Sync
Runs every hour via GitHub Actions.
Pulls views (last 24h) and sales (last 7 days) from eBay.
Updates views_24h and sales_7d on listings.
Assigns priority tiers to products for Keepa scraper.
"""

import asyncio
import httpx
from datetime import datetime, timedelta
from utils import get_db, get_ebay_token, get_all_active_accounts

MARKETPLACE = "EBAY_GB"


async def sync_analytics(db, account_id: str, token: str):
    """Pull views + sales for one account."""
    now       = datetime.utcnow()
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    today     = now.strftime("%Y-%m-%d")
    week_ago  = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    now_iso   = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {token}"}

        # ── Views ──
        traffic_res = await client.get(
            "https://api.ebay.com/sell/analytics/v1/traffic_report",
            headers=headers,
            params={
                "dimension": "LISTING",
                "metric": "LISTING_IMPRESSION_STORE",
                "filter": f"date_range:[{yesterday}..{today}]"
            }
        )

        if traffic_res.status_code == 200:
            traffic = traffic_res.json()
            for record in traffic.get("records", []):
                listing_id = record.get("dimensionValues", [{}])[0].get("value")
                views      = int(record.get("metricValues", [{}])[0].get("value", 0))
                if listing_id:
                    await db.execute(
                        "UPDATE listings SET views_24h=? WHERE ebay_item_id=? AND account_id=?",
                        [views, listing_id, account_id]
                    )
            print(f"  {account_id}: views updated")

        # ── Sales — reset then recount ──
        await db.execute(
            "UPDATE listings SET sales_7d=0 WHERE account_id=?",
            [account_id]
        )

        orders_res = await client.get(
            "https://api.ebay.com/sell/fulfillment/v1/order",
            headers=headers,
            params={
                "filter": f"creationdate:[{week_ago}..{now_iso}]",
                "limit": 200
            }
        )

        if orders_res.status_code == 200:
            orders = orders_res.json()
            for order in orders.get("orders", []):
                for item in order.get("lineItems", []):
                    listing_id = item.get("legacyItemId")
                    if listing_id:
                        await db.execute(
                            "UPDATE listings SET sales_7d=sales_7d+1 WHERE ebay_item_id=? AND account_id=?",
                            [listing_id, account_id]
                        )
            print(f"  {account_id}: sales updated")


async def update_priorities(db):
    """
    Assign priority tiers to products based on aggregated views/sales.
    Hot   → sold in last 7 days across any account → check every 2 hrs
    Warm  → views but no sales                     → check every 6 hrs
    Cold  → nothing happening                      → skip
    """

    # Hot: any listing with recent sales
    await db.execute("""
        UPDATE products
        SET priority = 'hot',
            next_check_at = datetime(CURRENT_TIMESTAMP, '+2 hours')
        WHERE asin IN (
            SELECT DISTINCT asin FROM listings
            WHERE sales_7d > 0 AND asin != ''
        )
    """)

    # Warm: views but no sales (don't downgrade hot)
    await db.execute("""
        UPDATE products
        SET priority = 'warm',
            next_check_at = datetime(CURRENT_TIMESTAMP, '+6 hours')
        WHERE asin IN (
            SELECT DISTINCT asin FROM listings
            WHERE views_24h > 0 AND sales_7d = 0 AND asin != ''
        )
        AND priority != 'hot'
    """)

    # Cold: no activity at all
    await db.execute("""
        UPDATE products
        SET priority = 'cold',
            next_check_at = NULL
        WHERE asin NOT IN (
            SELECT DISTINCT asin FROM listings
            WHERE (views_24h > 0 OR sales_7d > 0) AND asin != ''
        )
    """)

    # Count each tier for logging
    counts = await db.execute("""
        SELECT priority, COUNT(*) FROM products GROUP BY priority
    """)
    for row in counts.rows:
        print(f"  Priority {row[0]}: {row[1]} ASINs")


async def main():
    print("=" * 55)
    print(f"eBay Analytics Sync — {datetime.utcnow().strftime('%H:%M UTC')}")
    print("=" * 55)

    db = get_db()
    try:
        accounts = await get_all_active_accounts(db)
        if not accounts:
            print("No active accounts.")
            return

        for account_id, refresh_token in accounts:
            print(f"\n  Account: {account_id}")
            token = await get_ebay_token(refresh_token)
            if not token:
                print(f"  ✗ No token for {account_id}")
                continue
            await sync_analytics(db, account_id, token)

        print("\nUpdating priority tiers...")
        await update_priorities(db)
        print("\n✓ Analytics sync complete")

    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())