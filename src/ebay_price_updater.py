"""
eBay Price Updater
Runs every 15 min via GitHub Actions.
Reads amazon_price from products table.
Calculates new eBay price and pushes to ALL accounts where that ASIN exists.
Logs every change to price_changes table for dashboard stats.
"""

import asyncio
import httpx
from datetime import datetime
from utils import get_db, get_ebay_token, get_all_active_accounts, calc_ebay_price

MARKETPLACE = "EBAY_GB"


async def update_offer(
    client: httpx.AsyncClient,
    token: str,
    offer_id: str,
    price: float,
    quantity: int | None = None
) -> bool:
    """PATCH an eBay offer — update price and/or quantity."""
    body = {
        "pricingSummary": {
            "price": {"currency": "GBP", "value": f"{price:.2f}"}
        }
    }
    if quantity is not None:
        body["availableQuantity"] = quantity

    res = await client.patch(
        f"https://api.ebay.com/sell/inventory/v1/offer/{offer_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE
        },
        json=body
    )
    return res.status_code in (200, 204)


async def main():
    print("=" * 55)
    print(f"eBay Price Updater — {datetime.utcnow().strftime('%H:%M UTC')}")
    print("=" * 55)

    db = get_db()
    try:
        # Find all listings needing update across all accounts
        result = await db.execute("""
            SELECT
                p.asin,
                p.amazon_price,
                l.account_id,
                l.offer_id,
                l.sku,
                l.ebay_price,
                l.status      AS listing_status,
                a.refresh_token
            FROM products p
            JOIN listings l ON l.asin = p.asin
            JOIN ebay_accounts a ON a.account_id = l.account_id
            WHERE
                p.amazon_price IS NOT NULL
                AND l.offer_id != ''
                AND a.status = 'active'
                AND (
                    ABS(l.ebay_price -
                        ((p.amazon_price + 1.50) / (1.0 - 0.1295 - 0.25))
                    ) > 0.10
                    OR (p.amazon_price = -1 AND l.status != 'out_of_stock')
                    OR (p.amazon_price > 0  AND l.status = 'out_of_stock')
                )
            ORDER BY p.asin, l.account_id
            LIMIT 500
        """)

        rows = result.rows
        if not rows:
            print("Nothing to update — all prices current.")
            return

        print(f"Found {len(rows)} listing(s) needing update")

        # Group by account to get one token per account
        by_account = {}
        for row in rows:
            asin, amazon_price, account_id, offer_id, sku, \
                ebay_price, listing_status, refresh_token = row
            if account_id not in by_account:
                by_account[account_id] = {
                    "refresh_token": refresh_token,
                    "listings": []
                }
            by_account[account_id]["listings"].append({
                "asin":          asin,
                "amazon_price":  float(amazon_price) if amazon_price else 0,
                "offer_id":      offer_id,
                "sku":           sku,
                "ebay_price":    float(ebay_price),
                "listing_status": listing_status
            })

        price_updates = oos_paused = restored = errors = 0

        for account_id, data in by_account.items():
            print(f"\n  Account: {account_id}")
            token = await get_ebay_token(data["refresh_token"])
            if not token:
                print(f"  ✗ No token for {account_id}")
                continue

            async with httpx.AsyncClient(timeout=30) as client:
                for l in data["listings"]:
                    amazon_price   = l["amazon_price"]
                    offer_id       = l["offer_id"]
                    sku            = l["sku"]
                    old_price      = l["ebay_price"]
                    listing_status = l["listing_status"]
                    asin           = l["asin"]

                    try:
                        # ── Out of stock ──
                        if amazon_price == -1:
                            ok = await update_offer(client, token, offer_id, old_price, 0)
                            if ok:
                                await db.execute(
                                    "UPDATE listings SET status='out_of_stock', last_synced=CURRENT_TIMESTAMP WHERE account_id=? AND sku=?",
                                    [account_id, sku]
                                )
                                await db.execute(
                                    "INSERT INTO price_changes (asin, account_id, sku, old_price, new_price, change_type) VALUES (?,?,?,?,?,'out_of_stock')",
                                    [asin, account_id, sku, old_price, 0]
                                )
                                oos_paused += 1
                                print(f"    PAUSED   | {sku[:28]}")

                        # ── Back in stock ──
                        elif listing_status == "out_of_stock" and amazon_price > 0:
                            new_price = calc_ebay_price(amazon_price)
                            ok = await update_offer(client, token, offer_id, new_price, 1)
                            if ok:
                                await db.execute(
                                    "UPDATE listings SET ebay_price=?, status='active', last_synced=CURRENT_TIMESTAMP WHERE account_id=? AND sku=?",
                                    [new_price, account_id, sku]
                                )
                                await db.execute(
                                    "INSERT INTO price_changes (asin, account_id, sku, old_price, new_price, change_type) VALUES (?,?,?,?,?,'restored')",
                                    [asin, account_id, sku, 0, new_price]
                                )
                                restored += 1
                                print(f"    RESTORED | {sku[:28]} | £{new_price}")

                        # ── Price change ──
                        else:
                            new_price = calc_ebay_price(amazon_price)
                            ok = await update_offer(client, token, offer_id, new_price)
                            if ok:
                                await db.execute(
                                    "UPDATE listings SET ebay_price=?, last_synced=CURRENT_TIMESTAMP WHERE account_id=? AND sku=?",
                                    [new_price, account_id, sku]
                                )
                                direction = "up" if new_price > old_price else "down"
                                await db.execute(
                                    "INSERT INTO price_changes (asin, account_id, sku, old_price, new_price, change_type) VALUES (?,?,?,?,?,?)",
                                    [asin, account_id, sku, old_price, new_price, direction]
                                )
                                price_updates += 1
                                arrow = "▲" if new_price > old_price else "▼"
                                print(f"    {arrow} PRICE  | {sku[:24]} | £{old_price:.2f}→£{new_price:.2f}")

                        await asyncio.sleep(0.2)

                    except Exception as e:
                        print(f"    ✗ {sku}: {e}")
                        errors += 1

        print(f"\n✓ Done — updated:{price_updates} paused:{oos_paused} restored:{restored} errors:{errors}")

    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())