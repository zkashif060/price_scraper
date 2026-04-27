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
1. Deploy the worker with `TURSO_URL`/`LIBSQL_URL`, `TURSO_TOKEN`/`LIBSQL_AUTH_TOKEN`, `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, and `EBAY_RUNAME` set as worker environment variables.
2. Set `WORKER_URL`, `EBAY_CLIENT_ID`, and `EBAY_RUNAME` in `src/dashboard/index.html` before publishing.
3. Deploy the dashboard folder with GitHub Pages.

## Cloudflare worker secrets
You can add secrets in Cloudflare in two ways:

- Cloudflare dashboard:
  1. Open your Worker in the Cloudflare dashboard.
  2. Go to `Variables > Environment variables`.
  3. Add `TURSO_URL`, `TURSO_TOKEN`, `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, and `EBAY_RUNAME`.
  4. Save the worker.

- Wrangler CLI:
  ```bash
  wrangler secret put TURSO_URL
  wrangler secret put TURSO_TOKEN
  wrangler secret put EBAY_CLIENT_ID
  wrangler secret put EBAY_CLIENT_SECRET
  wrangler secret put EBAY_RUNAME
  ```

In the worker, these are accessed as `env.TURSO_URL`, `env.TURSO_TOKEN`, `env.EBAY_CLIENT_ID`, `env.EBAY_CLIENT_SECRET`, and `env.EBAY_RUNAME`.

## Why this is safe
- `EBAY_CLIENT_ID` and `EBAY_RUNAME` are not secret: they are public config values used for OAuth.
- `EBAY_CLIENT_SECRET` is sensitive and must stay only in the worker environment.
- `TURSO_URL` and `TURSO_TOKEN` are also sensitive and must not be exposed in `index.html`.
