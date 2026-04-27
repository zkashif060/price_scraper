"""
Amazon Price Scraper — Direct scraping with anti-detection measures
Runs every 4 hours via GitHub Actions.

Features:
- Rotates user agents and headers to mimic human browsers
- Random delays between requests (2-8 seconds)
- Handles rate limiting and blocks gracefully
- Updates product prices in database
- Professional error handling and logging
"""

import os
import asyncio
import random
import time
import httpx
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
from utils import get_db

# Anti-detection measures
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]

HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Cache-Control': 'max-age=0',
}

class AmazonScraper:
    def __init__(self):
        self.session = None
        self.base_url = "https://www.amazon.co.uk"
        self.max_retries = 3
        self.request_delay = (2, 8)  # Random delay between 2-8 seconds

    async def __aenter__(self):
        self.session = httpx.AsyncClient(
            headers=self._get_random_headers(),
            timeout=30.0,
            follow_redirects=True
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.aclose()

    def _get_random_headers(self):
        headers = HEADERS.copy()
        headers['User-Agent'] = random.choice(USER_AGENTS)
        return headers

    async def _random_delay(self):
        """Add random delay to mimic human behavior"""
        delay = random.uniform(*self.request_delay)
        await asyncio.sleep(delay)

    async def _make_request(self, url, retry_count=0):
        """Make HTTP request with retry logic and anti-detection measures"""
        try:
            # Rotate headers for each request
            self.session.headers.update(self._get_random_headers())

            response = await self.session.get(url)

            # Check for blocking indicators
            if response.status_code == 503:
                if "Sorry, we just need to make sure you're not a robot" in response.text:
                    print(f"⚠ Detected bot protection on attempt {retry_count + 1}")
                    if retry_count < self.max_retries:
                        wait_time = 60 * (retry_count + 1)  # Progressive backoff
                        print(f"⏳ Waiting {wait_time}s before retry...")
                        await asyncio.sleep(wait_time)
                        return await self._make_request(url, retry_count + 1)
                    else:
                        raise Exception("Bot protection detected - manual intervention required")

            response.raise_for_status()
            return response

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                print(f"⚠ ASIN not found: {url}")
                return None
            raise
        except Exception as e:
            if retry_count < self.max_retries:
                wait_time = 10 * (retry_count + 1)
                print(f"⚠ Request failed (attempt {retry_count + 1}): {e}")
                print(f"⏳ Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
                return await self._make_request(url, retry_count + 1)
            raise

    def _extract_price(self, html):
        """Extract price from Amazon HTML"""
        import re

        # Look for various price patterns
        price_patterns = [
            r'"priceAmount":"([\d.]+)"',
            r'"displayPrice":"£([\d.]+)"',
            r'<span[^>]*class="[^"]*price[^"]*"[^>]*>£([\d.]+)</span>',
            r'<span[^>]*id="priceblock_ourprice"[^>]*>£([\d.]+)</span>',
            r'<span[^>]*id="priceblock_dealprice"[^>]*>£([\d.]+)</span>',
        ]

        for pattern in price_patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    continue

        return None

    def _check_availability(self, html):
        """Check if product is in stock"""
        out_of_stock_indicators = [
            "currently unavailable",
            "out of stock",
            "temporarily unavailable",
            "not currently available"
        ]

        html_lower = html.lower()
        for indicator in out_of_stock_indicators:
            if indicator in html_lower:
                return False
        return True

    async def scrape_asin(self, asin):
        """Scrape single ASIN"""
        url = f"{self.base_url}/dp/{asin}"

        try:
            response = await self._make_request(url)
            if not response:
                return None

            html = response.text
            price = self._extract_price(html)
            in_stock = self._check_availability(html)

            return {
                'asin': asin,
                'price': price,
                'in_stock': in_stock,
                'scraped_at': datetime.utcnow().isoformat(),
                'url': url
            }

        except Exception as e:
            print(f"❌ Failed to scrape {asin}: {e}")
            return None

    async def scrape_batch(self, asins, batch_size=5):
        """Scrape multiple ASINs with delays between requests"""
        results = []

        for i in range(0, len(asins), batch_size):
            batch = asins[i:i + batch_size]
            print(f"📊 Processing batch {i//batch_size + 1}/{(len(asins) + batch_size - 1)//batch_size} ({len(batch)} ASINs)")

            batch_tasks = []
            for asin in batch:
                batch_tasks.append(self.scrape_asin(asin))

            # Process batch concurrently but with individual delays
            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

            for result in batch_results:
                if isinstance(result, Exception):
                    print(f"❌ Batch error: {result}")
                elif result:
                    results.append(result)

            # Longer delay between batches
            if i + batch_size < len(asins):
                batch_delay = random.uniform(10, 20)
                print(f"⏳ Batch delay: {batch_delay:.1f}s")
                await asyncio.sleep(batch_delay)

        return results


async def update_database(db, scrape_results):
    """Update database with scraped prices"""
    updated = oos = errors = 0

    for result in scrape_results:
        try:
            asin = result['asin']
            price = result['price']
            in_stock = result['in_stock']
            scraped_at = result['scraped_at']

            # Get current price from database
            current = await db.execute(
                "SELECT price, in_stock FROM products WHERE asin = ?",
                [asin]
            )

            old_price = old_in_stock = None
            if current.rows:
                old_price = current.rows[0][0]
                old_in_stock = current.rows[0][1]

            # Determine change type
            change_type = None
            if old_price is not None and price is not None:
                if abs(price - old_price) > 0.01:  # Significant price change
                    change_type = "up" if price > old_price else "down"
            elif old_in_stock != in_stock:
                change_type = "out_of_stock" if not in_stock else "restored"

            # Update product
            await db.execute("""
                UPDATE products
                SET price = ?, in_stock = ?, last_scraped = ?, updated_at = CURRENT_TIMESTAMP
                WHERE asin = ?
            """, [price, in_stock, scraped_at, asin])

            # Record price change if significant
            if change_type:
                await db.execute("""
                    INSERT INTO price_changes (asin, old_price, new_price, change_type, changed_at)
                    VALUES (?, ?, ?, ?, ?)
                """, [asin, old_price, price, change_type, scraped_at])

                if change_type in ["up", "down"]:
                    updated += 1
                elif change_type == "out_of_stock":
                    oos += 1
                elif change_type == "restored":
                    # This would be handled separately, but counting here for now
                    pass

        except Exception as e:
            print(f"❌ DB update error for {result['asin']}: {e}")
            errors += 1

    return updated, oos, errors


async def main():
    print("=" * 60)
    print(f"Amazon Price Scraper — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    db = get_db()

    try:
        # Get ASINs due for scraping (hot and warm priority only, like Keepa scraper)
        due = await db.execute("""
            SELECT asin, priority
            FROM products
            WHERE
                priority IN ('hot', 'warm')
                AND (
                    last_scraped IS NULL
                    OR last_scraped <= datetime(CURRENT_TIMESTAMP, '-4 hours')
                )
            ORDER BY
                CASE priority
                    WHEN 'hot'  THEN 1
                    WHEN 'warm' THEN 2
                END,
                last_scraped ASC
            LIMIT 200  -- Conservative limit to avoid overwhelming Amazon
        """)

        asins = [row[0] for row in due.rows]

        if not asins:
            print("✅ No ASINs due for scraping right now.")
            return

        hot_count = sum(1 for row in due.rows if row[1] == 'hot')
        warm_count = sum(1 for row in due.rows if row[1] == 'warm')
        print(f"🎯 ASINs to scrape: {len(asins)} (hot:{hot_count} warm:{warm_count})")

        # Scrape prices
        async with AmazonScraper() as scraper:
            print("🕷️ Starting Amazon scraping...")
            scrape_results = await scraper.scrape_batch(asins)

        successful = len([r for r in scrape_results if r['price'] is not None])
        print(f"📊 Scraping complete: {successful}/{len(scrape_results)} successful")

        # Update database
        if scrape_results:
            print("💾 Updating database...")
            updated, oos, errors = await update_database(db, scrape_results)
            print(f"✅ Database updated: {updated} price changes, {oos} OOS, {errors} errors")

        print("🎉 Amazon scraping cycle complete!")

    except Exception as e:
        print(f"❌ Fatal error: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())