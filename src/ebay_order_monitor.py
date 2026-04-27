"""
eBay Order Monitor — Triggers price checks when orders are placed
Runs every 15 minutes via GitHub Actions.

When an order appears for a product:
1. Immediately check Amazon price
2. If price changed significantly, update eBay listing
3. Log the price change for dashboard stats
"""

import asyncio
import httpx
from datetime import datetime, timedelta
from utils import get_db, get_ebay_token, get_all_active_accounts

MARKETPLACE = "EBAY_GB"


async def check_recent_orders(db, account_id: str, token: str):
    """Check for new orders in the last 15 minutes and trigger price checks"""
    now = datetime.utcnow()
    fifteen_min_ago = (now - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {token}"}

        # Get orders from last 15 minutes
        orders_res = await client.get(
            "https://api.ebay.com/sell/fulfillment/v1/order",
            headers=headers,
            params={
                "filter": f"creationdate:[{fifteen_min_ago}..{now_iso}]",
                "limit": 50  # Should be plenty for 15 minutes
            }
        )

        if orders_res.status_code != 200:
            print(f"  {account_id}: Failed to get orders ({orders_res.status_code})")
            return []

        orders = orders_res.json().get("orders", [])

        order_asins = []
        for order in orders:
            for item in order.get("lineItems", []):
                sku = item.get("sku", "")
                if sku:
                    # Extract ASIN from SKU (format: ASIN or contains ASIN)
                    from utils import extract_asin
                    asin = extract_asin(sku)
                    if asin:
                        order_asins.append(asin)
                        print(f"  {account_id}: New order for ASIN {asin}")

        return list(set(order_asins))  # Remove duplicates


async def trigger_price_check(db, asins):
    """Mark ASINs for immediate price checking"""
    if not asins:
        return

    # Update next_check_at to now for immediate checking
    placeholders = ','.join('?' for _ in asins)
    await db.execute(f"""
        UPDATE products
        SET next_check_at = CURRENT_TIMESTAMP,
            priority = CASE
                WHEN priority != 'hot' THEN 'hot'  -- Temporarily promote to hot
                ELSE priority
            END
        WHERE asin IN ({placeholders})
    """, asins)

    print(f"🚨 Triggered immediate price check for {len(asins)} ASINs from new orders")


async def main():
    print("=" * 55)
    print(f"eBay Order Monitor — {datetime.utcnow().strftime('%H:%M UTC')}")
    print("=" * 55)

    db = get_db()

    try:
        accounts = await get_all_active_accounts(db)
        if not accounts:
            print("No active accounts.")
            return

        all_order_asins = []

        for account_id, refresh_token in accounts:
            print(f"\n  Checking orders: {account_id}")
            token = await get_ebay_token(refresh_token)
            if not token:
                print(f"  ✗ No token for {account_id}")
                continue

            account_asins = await check_recent_orders(db, account_id, token)
            all_order_asins.extend(account_asins)

        # Remove duplicates across accounts
        unique_asins = list(set(all_order_asins))

        if unique_asins:
            print(f"\n📦 Found {len(unique_asins)} unique ASINs from new orders")
            await trigger_price_check(db, unique_asins)
        else:
            print("\n✅ No new orders in the last 15 minutes")

    except Exception as e:
        print(f"❌ Fatal error: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())