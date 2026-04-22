"""
Keepa Price Scraper — €49 plan optimised
Runs every 4 hours via GitHub Actions.

€49 plan = 20 tokens/min = 864,000 tokens/month
With smart priority system:
  Hot   listings (~5%)  → checked every 2 hrs
  Warm  listings (~20%) → checked every 6 hrs
  Cold  listings (~75%) → skipped entirely

Typical run: 300–800 ASINs checked per run
Token usage: ~300–800 per run × 6 runs/day = ~3,600–4,800/day
Monthly:     ~108,000–144,000 / 864,000 available = 13–17% used
"""

import os
import asyncio
import keepa
from datetime import datetime
from utils import get_db

KEEPA_API_KEY = os.getenv("KEEPA_API_KEY")


async def main():
    print("=" * 55)
    print(f"Keepa Scraper — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 55)

    db = get_db()
    try:
        # Only check ASINs that are due based on priority
        due = await db.execute("""
            SELECT asin, priority
            FROM products
            WHERE
                priority IN ('hot', 'warm')
                AND (
                    next_check_at IS NULL
                    OR next_check_at <= datetime(CURRENT_TIMESTAMP)
                )
            ORDER BY
                CASE priority
                    WHEN 'hot'  THEN 1
                    WHEN 'warm' THEN 2
                END,
                next_check_at ASC
            LIMIT 1000
        """)

        all_asins = [row[0] for row in due.rows]

        if not all_asins:
            print("No ASINs due for checking right now.")
            return

        hot_count  = sum(1 for row in due.rows if row[1] == 'hot')
        warm_count = sum(1 for row in due.rows if row[1] == 'warm')
        print(f"ASINs to check: {len(all_asins)} (hot:{hot_count} warm:{warm_count})")

        # Connect to Keepa
        api = keepa.Keepa(KEEPA_API_KEY)
        print(f"Keepa tokens available: {api.tokens_left}")

        # Warn if tokens are low
        if api.tokens_left < len(all_asins):
            print(f"⚠ Low tokens ({api.tokens_left}) — will process {api.tokens_left} ASINs max")
            # Prioritise hot over warm when tokens are limited
            all_asins = all_asins[:api.tokens_left]

        checked = updated = oos = errors = 0

        # Process in batches of 100 (Keepa max per request on paid plan)
        # wait=True = library auto-pauses when bucket empty, resumes when refilled
        for i in range(0, len(all_asins), 100):
            batch = all_asins[i:i + 100]

            try:
                products = api.query(
                    batch,
                    domain="GB",      # amazon.co.uk
                    history=False,    # skip full history — faster + smaller response
                    update=1,         # refresh only if data older than 1 hour
                    stats=30,         # last 30 days stats — gives current price fast
                    wait=True         # auto-manage token bucket
                )

                for product in (products or []):
                    asin = product.get("asin", "")
                    if not asin:
                        continue

                    amazon_price = None

                    # Method 1: stats current price (fastest)
                    stats = product.get("stats", {})
                    current = stats.get("current", [])
                    if current and len(current) > 0 and current[0] and current[0] > 0:
                        amazon_price = current[0] / 100.0

                    # Method 2: csv price history last value
                    if amazon_price is None:
                        csv = product.get("csv", [])
                        if csv and csv[0]:
                            last_val = csv[0][-1]
                            if last_val == -1:
                                amazon_price = -1   # Out of stock
                            elif last_val > 0:
                                amazon_price = last_val / 100.0

                    if amazon_price is not None:
                        stock_status = "out_of_stock" if amazon_price == -1 else "in_stock"

                        # Set next check time based on priority
                        priority = next(
                            (row[1] for row in due.rows if row[0] == asin), "warm"
                        )
                        interval = "+2 hours" if priority == "hot" else "+6 hours"

                        await db.execute("""
                            UPDATE products
                            SET amazon_price  = ?,
                                stock_status  = ?,
                                last_checked  = CURRENT_TIMESTAMP,
                                next_check_at = datetime(CURRENT_TIMESTAMP, ?)
                            WHERE asin = ?
                        """, [amazon_price, stock_status, interval, asin])

                        if amazon_price == -1:
                            oos += 1
                        else:
                            updated += 1

                    checked += 1

                progress = min(i + 100, len(all_asins))
                print(f"  [{progress}/{len(all_asins)}] "
                      f"updated:{updated} oos:{oos} "
                      f"tokens_left:{api.tokens_left}")

            except Exception as e:
                print(f"  Batch error at {i}: {e}")
                errors += 1
                await asyncio.sleep(5)

        print(f"\n✓ Done — checked:{checked} updated:{updated} oos:{oos} errors:{errors}")
        print(f"  Tokens remaining: {api.tokens_left}")

    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())