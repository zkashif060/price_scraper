"""
Amazon Price Scraper — Direct scraping with advanced anti-detection measures
Runs every 4 hours via GitHub Actions.

Features:
- Rotates user agents and headers to mimic human browsers
- Random delays between requests (2-8 seconds)
- Handles rate limiting and blocks gracefully
- Smart priority system based on eBay performance
- Updates product prices in database
- Professional error handling and logging

Priority System:
- Hot: Sold in last 7 days → check every 2 hours
- Warm: Views but no sales → check every 6 hours
- Cold: No activity → skip entirely
"""

import os
import asyncio
import random
import time
import httpx
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
from utils import get_db

# Enhanced anti-detection measures
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
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
    'Referer': 'https://www.google.com/',
}

class AmazonScraper:
    def __init__(self):
        self.session = None
        self.base_url = "https://www.amazon.co.uk"
        self.max_retries = 3
        self.request_delay = (2, 8)  # Random delay between 2-8 seconds
        self.batch_delay = (10, 20)  # Delay between batches
        self.block_detected = False

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
        # Add some randomization to other headers
        headers['Accept-Language'] = random.choice([
            'en-US,en;q=0.9',
            'en-GB,en;q=0.9',
            'en-US,en;q=0.9,es;q=0.8'
        ])
        return headers

    async def _random_delay(self):
        """Add random delay to mimic human behavior"""
        delay = random.uniform(*self.request_delay)
        await asyncio.sleep(delay)

    async def _batch_delay(self):
        """Longer delay between batches"""
        delay = random.uniform(*self.batch_delay)
        print(f"⏳ Batch delay: {delay:.1f}s")
        await asyncio.sleep(delay)

    async def _make_request(self, url, retry_count=0):
        """Make HTTP request with retry logic and anti-detection measures"""
        try:
            # Rotate headers for each request
            self.session.headers.update(self._get_random_headers())

            # Add random delay before request
            await self._random_delay()

            response = await self.session.get(url)

            # Check for blocking indicators
            if response.status_code == 503:
                if "Sorry, we just need to make sure you're not a robot" in response.text:
                    self.block_detected = True
                    print(f"⚠ Detected bot protection on attempt {retry_count + 1}")
                    if retry_count < self.max_retries:
                        wait_time = 60 * (retry_count + 1)  # Progressive backoff
                        print(f"⏳ Waiting {wait_time}s before retry...")
                        await asyncio.sleep(wait_time)
                        return await self._make_request(url, retry_count + 1)
                    else:
                        raise Exception("Bot protection detected - manual intervention required")

            # Check for other blocking patterns
            if response.status_code == 429:
                print(f"⚠ Rate limited (429) on attempt {retry_count + 1}")
                if retry_count < self.max_retries:
                    wait_time = 30 * (retry_count + 1)
                    print(f"⏳ Waiting {wait_time}s before retry...")
                    await asyncio.sleep(wait_time)
                    return await self._make_request(url, retry_count + 1)
                else:
                    raise Exception("Rate limited - too many requests")

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
        """Extract price from Amazon HTML with multiple patterns"""
        import re

        # Enhanced price patterns
        price_patterns = [
            r'"priceAmount":"([\d.]+)"',
            r'"displayPrice":"£([\d.]+)"',
            r'<span[^>]*class="[^"]*price[^"]*"[^>]*>£([\d.]+)</span>',
            r'<span[^>]*id="priceblock_ourprice"[^>]*>£([\d.]+)</span>',
            r'<span[^>]*id="priceblock_dealprice"[^>]*>£([\d.]+)</span>',
            r'<span[^>]*class="a-price-whole"[^>]*>(\d+)</span>\s*<span[^>]*class="a-price-fraction"[^>]*>(\d+)</span>',
            r'<span[^>]*class="a-color-price"[^>]*>£([\d.]+)</span>',
        ]

        for pattern in price_patterns:
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if match:
                try:
                    if len(match.groups()) == 2:  # For whole.fraction pattern
                        whole = int(match.group(1))
                        fraction = int(match.group(2))
                        return float(f"{whole}.{fraction}")
                    else:
                        return float(match.group(1))
                except ValueError:
                    continue

        return None

    def _check_availability(self, html):
        """Check if product is in stock with enhanced patterns"""
        out_of_stock_indicators = [
            "currently unavailable",
            "out of stock",
            "temporarily unavailable",
            "not currently available",
            "unavailable",
            "currently out of stock"
        ]

        in_stock_indicators = [
            "in stock",
            "only \\d+ left in stock",
            "left in stock"
        ]

        html_lower = html.lower()

        # Check for out of stock first
        for indicator in out_of_stock_indicators:
            if indicator in html_lower:
                return False

        # Check for in stock indicators
        for indicator in in_stock_indicators:
            if indicator in html_lower:
                return True

        # Default to in stock if no clear indicators
        return True

    async def scrape_asin(self, asin):
        """Scrape single ASIN with enhanced error handling"""
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
                'url': url,
                'success': price is not None
            }

        except Exception as e:
            print(f"❌ Failed to scrape {asin}: {e}")
            return {
                'asin': asin,
                'price': None,
                'in_stock': False,
                'scraped_at': datetime.utcnow().isoformat(),
                'url': url,
                'success': False,
                'error': str(e)
            }

    async def scrape_batch(self, asins, batch_size=3):  # Reduced batch size for safety
        """Scrape multiple ASINs with enhanced batching and anti-detection"""
        results = []
        total_asins = len(asins)

        print(f"🕷️ Starting Amazon scraping: {total_asins} ASINs in batches of {batch_size}")

        for i in range(0, total_asins, batch_size):
            batch = asins[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (total_asins + batch_size - 1) // batch_size

            print(f"📊 Processing batch {batch_num}/{total_batches} ({len(batch)} ASINs)")

            # Check if we should stop due to blocking
            if self.block_detected:
                print("🛑 Stopping due to detected blocking. Will retry later.")
                break

            batch_tasks = []
            for asin in batch:
                batch_tasks.append(self.scrape_asin(asin))

            # Process batch concurrently but with individual delays
            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

            batch_success = 0
            for result in batch_results:
                if isinstance(result, Exception):
                    print(f"❌ Batch error: {result}")
                elif result:
                    results.append(result)
                    if result.get('success'):
                        batch_success += 1

            print(f"✅ Batch {batch_num} complete: {batch_success}/{len(batch)} successful")

            # Longer delay between batches (unless it's the last batch)
            if i + batch_size < total_asins and not self.block_detected:
                await self._batch_delay()

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
        # Get ASINs due for scraping based on priority system
        # Hot: sold in last 7 days → check every 2 hours
        # Warm: views but no sales → check every 6 hours
        # Test: manually added for testing → check every time
        # Cold: no activity → skip

        due = await db.execute("""
            SELECT p.asin, p.priority,
                   CASE
                       WHEN p.priority = 'hot' THEN 2  -- Check every 2 hours
                       WHEN p.priority = 'warm' THEN 6 -- Check every 6 hours
                       WHEN p.priority = 'test' THEN 0 -- Check every time (always due)
                       ELSE 24  -- Cold items rarely checked
                   END as check_interval_hours
            FROM products p
            WHERE
                p.priority IN ('hot', 'warm', 'test')
                AND (
                    p.priority = 'test'  -- Always check test ASINs
                    OR p.last_scraped IS NULL
                    OR p.last_scraped <= datetime(CURRENT_TIMESTAMP, '-' ||
                        (CASE
                            WHEN p.priority = 'hot' THEN '2'
                            WHEN p.priority = 'warm' THEN '6'
                            ELSE '24'
                        END) || ' hours')
                )
            ORDER BY
                CASE p.priority
                    WHEN 'test' THEN 1  -- Test ASINs first
                    WHEN 'hot'  THEN 2
                    WHEN 'warm' THEN 3
                END,
                p.last_scraped ASC
            LIMIT 150  -- Conservative limit to avoid overwhelming Amazon
        """)

        asins = [row[0] for row in due.rows]

        if not asins:
            print("✅ No ASINs due for scraping right now.")
            return

        hot_count = sum(1 for row in due.rows if row[1] == 'hot')
        warm_count = sum(1 for row in due.rows if row[1] == 'warm')
        test_count = sum(1 for row in due.rows if row[1] == 'test')
        print(f"🎯 ASINs to scrape: {len(asins)} (hot:{hot_count} warm:{warm_count} test:{test_count})")

        # Scrape prices
        async with AmazonScraper() as scraper:
            print("🕷️ Starting Amazon scraping...")
            scrape_results = await scraper.scrape_batch(asins)

        successful = len([r for r in scrape_results if r.get('success')])
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