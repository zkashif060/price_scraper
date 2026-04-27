# price_scraper

This repository hosts a static GitHub Pages dashboard and a Cloudflare Worker backend.

## Secure secret handling
- `src/dashboard/index.html` no longer stores Turso or eBay secrets in the client-side page.
- The dashboard now calls worker endpoints under `WORKER_URL`.
- Your Turso credentials and eBay client secret are kept in the worker environment only.

## What changed
- `src/dashboard/index.html` uses `/api/*` endpoints instead of direct Turso HTTP requests.
- `src/worker.js` contains the secure backend logic for database queries and eBay token exchange.
- `.github/workflows/deploy.yml` no longer injects secrets into the published page.

## Deployment notes
1. Deploy the worker with `TURSO_URL`/`LIBSQL_URL`, `TURSO_TOKEN`/`LIBSQL_AUTH_TOKEN`, `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, and `EBAY_REDIRECT_URI` set as worker environment variables.
2. Set `WORKER_URL`, `EBAY_CLIENT_ID`, and `EBAY_REDIRECT_URI` in `src/dashboard/index.html` before publishing.
3. Deploy the dashboard folder with GitHub Pages.

## Cloudflare worker secrets
You can add secrets in Cloudflare in two ways:

- Cloudflare dashboard:
  1. Open your Worker in the Cloudflare dashboard.
  2. Go to `Variables > Environment variables`.
  3. Add `TURSO_URL`, `TURSO_TOKEN`, `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, and `EBAY_REDIRECT_URI`.
  4. Save the worker.

- Wrangler CLI:
  ```bash
  wrangler secret put TURSO_URL
  wrangler secret put TURSO_TOKEN
  wrangler secret put EBAY_CLIENT_ID
  wrangler secret put EBAY_CLIENT_SECRET
  wrangler secret put EBAY_REDIRECT_URI
  ```

In the worker, these are accessed as `env.TURSO_URL`, `env.TURSO_TOKEN`, `env.EBAY_CLIENT_ID`, `env.EBAY_CLIENT_SECRET`, and `env.EBAY_REDIRECT_URI`.

## Why this is safe
- `EBAY_CLIENT_ID` and `EBAY_REDIRECT_URI` are not secret: they are public config values used for OAuth.
- `EBAY_CLIENT_SECRET` is sensitive and must stay only in the worker environment.
- `TURSO_URL` and `TURSO_TOKEN` are also sensitive and must not be exposed in `index.html`.

## Amazon Price Scraping

The project includes a professional Amazon price scraper that runs every 4 hours via GitHub Actions.

## Amazon Price Scraping

The project includes a professional Amazon price scraper that runs every 4 hours via GitHub Actions.

### Features
- **Anti-detection measures**: Rotates user agents, realistic headers, random delays (2-8 seconds)
- **Smart batching**: Processes 3 ASINs at a time with 10-20 second delays between batches
- **Rate limiting protection**: Handles Amazon's bot detection with progressive backoff
- **Smart priority system**: Based on eBay sales/views performance
- **Order-triggered updates**: Checks Amazon prices immediately when orders are placed
- **Database integration**: Updates product prices and logs changes for dashboard stats

### Priority System
- **Hot**: Sold in last 7 days → check every 2 hours
- **Warm**: Views but no sales → check every 6 hours  
- **Test**: Manually added for testing → check every run
- **Cold**: No activity → skip entirely

### Capacity Calculations
With current anti-blocking measures (3 ASINs/batch, 10-20s delays):

- **Per hour**: ~45-60 ASINs (3 batches × 15-20 ASINs, with delays)
- **Per day**: ~1,080-1,440 ASINs (24 hours × 45-60)
- **Monthly**: ~32,400-43,200 ASINs (30 days × 1,080-1,440)

**Scaling options:**
- Reduce to 2 ASINs/batch → ~30-40/hour, safer but slower
- Increase to 5 ASINs/batch → ~75-100/hour, faster but riskier
- Adjust delays: 5-10s → faster, 15-25s → safer

### Anti-blocking strategies
If you encounter Amazon blocking:

1. **Reduce batch size**: Change `batch_size=3` to `batch_size=2` in `amazon_scraper.py`
2. **Increase delays**: Change `request_delay = (2, 8)` to `(5, 15)` seconds
3. **Longer batch delays**: Change `batch_delay = (10, 20)` to `(20, 30)`
4. **Reduce frequency**: Change cron from every 4 hours to every 6-8 hours
5. **IP rotation**: Add proxy support (advanced implementation needed)
6. **Geographic targeting**: Consider using different Amazon domains

### Manual testing
To test the scraper manually:
```bash
cd src
python amazon_scraper.py
```

### Adding test ASINs manually
Run these SQL queries in your Turso database to add test ASINs:

```sql
-- Replace with real ASINs you want to test
INSERT INTO products (asin, priority)
VALUES ("B08N5WRWNW", "test")
ON CONFLICT(asin) DO UPDATE SET priority='test';

INSERT INTO products (asin, priority)
VALUES ("B07ZPKN6YR", "test")
ON CONFLICT(asin) DO UPDATE SET priority='test';

INSERT INTO products (asin, priority)
VALUES ("B08FC6MR62", "test")
ON CONFLICT(asin) DO UPDATE SET priority='test';
```

### Monitoring
- Check GitHub Actions logs for scraping results
- Monitor dashboard "Test ASINs" section for manual tests
- Watch for "bot protection" messages in logs
- Dashboard shows priority breakdown and token usage estimates
