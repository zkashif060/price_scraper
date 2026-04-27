export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return this.corsResponse(null, 204);
    }

    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+/g, "/").replace(/\/+$/g, "");

    if (request.method !== "POST") {
      return this.corsResponse({ error: "Only POST requests are allowed." }, 405);
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return this.corsResponse({ error: "Invalid JSON body." }, 400);
    }

    try {
      switch (path) {
        case "/api/stats":
          return this.corsResponse(await this.handleStats(env, body));
        case "/api/accounts":
          return this.corsResponse(await this.handleAccounts(env));
        case "/api/changes":
          return this.corsResponse(await this.handleChanges(env, body));
        case "/api/disconnect":
          return this.corsResponse(await this.handleDisconnect(env, body));
        case "/api/exchange-token":
          return this.corsResponse(await this.handleExchangeToken(env, body));
        default:
          return this.corsResponse({ error: "Unknown API endpoint." }, 404);
      }
    } catch (err) {
      return this.corsResponse({ error: err.message || "Internal error" }, 500);
    }
  },

  corsHeaders() {
    return {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Content-Type": "application/json;charset=utf-8"
    };
  },

  corsResponse(body, status = 200) {
    return new Response(body !== null ? JSON.stringify(body) : null, {
      status,
      headers: this.corsHeaders()
    });
  },

  async executeSQL(env, sql, args = []) {
    if (!env.TURSO_URL || !env.TURSO_TOKEN) {
      throw new Error("Server is missing Turso credentials.");
    }

    const res = await fetch(`${env.TURSO_URL}/v2/pipeline`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.TURSO_TOKEN}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        requests: [{
          type: "execute",
          stmt: {
            sql,
            args: args.map(v => ({ type: "text", value: String(v ?? "") }))
          }
        }]
      })
    });

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Turso HTTP ${res.status}: ${text.slice(0, 200)}`);
    }

    const data = await res.json();
    const err = data.results?.[0]?.error?.message;
    if (err) {
      throw new Error(err);
    }

    return data.results?.[0]?.response?.result || { cols: [], rows: [] };
  },

  async handleStats(env, body) {
    const today = body.today || new Date().toISOString().split("T")[0];

    const accounts = await this.executeSQL(env, "SELECT COUNT(*) FROM ebay_accounts WHERE status='active'");
    const listings = await this.executeSQL(env, "SELECT COUNT(*) FROM listings WHERE status='active'");
    const priorities = await this.executeSQL(env, "SELECT priority, COUNT(*) FROM products GROUP BY priority");
    const changes = await this.executeSQL(env, "SELECT change_type, COUNT(*) FROM price_changes WHERE changed_at >= ? GROUP BY change_type", [today]);

    const totalAccounts = Number(accounts.rows?.[0]?.[0] || 0);
    const totalListings = Number(listings.rows?.[0]?.[0] || 0);

    let hotAsins = 0, warmAsins = 0, coldAsins = 0;
    for (const [priority, count] of priorities.rows || []) {
      const n = Number(count);
      if (priority === "hot") hotAsins = n;
      if (priority === "warm") warmAsins = n;
      if (priority === "cold") coldAsins = n;
    }

    let priceUpdates = 0, oosCount = 0, restoredCount = 0;
    for (const [changeType, count] of changes.rows || []) {
      const n = Number(count);
      if (changeType === "up" || changeType === "down") priceUpdates += n;
      if (changeType === "out_of_stock") oosCount = n;
      if (changeType === "restored") restoredCount = n;
    }

    return { totalAccounts, totalListings, hotAsins, warmAsins, coldAsins, priceUpdates, oosCount, restoredCount };
  },

  async handleAccounts(env) {
    const result = await this.executeSQL(env, `
      SELECT a.account_id, a.ebay_username, a.status, a.connected_at, a.last_synced,
             COUNT(l.id) AS cnt
      FROM ebay_accounts a
      LEFT JOIN listings l ON l.account_id = a.account_id AND l.status='active'
      GROUP BY a.account_id
      ORDER BY a.connected_at DESC
    `);

    return { rows: result.rows || [] };
  },

  async handleChanges(env, body) {
    const today = body.today || new Date().toISOString().split("T")[0];
    const result = await this.executeSQL(env, `
      SELECT pc.asin, pc.sku, pc.old_price, pc.new_price, pc.change_type, pc.changed_at, l.title
      FROM price_changes pc
      LEFT JOIN listings l ON l.sku = pc.sku AND l.account_id = pc.account_id
      WHERE pc.changed_at >= ?
      ORDER BY pc.changed_at DESC
      LIMIT 50
    `, [today]);

    return { rows: result.rows || [] };
  },

  async handleDisconnect(env, body) {
    if (!body?.account_id) {
      throw new Error("Missing account_id.");
    }
    await this.executeSQL(env, "UPDATE ebay_accounts SET status='disconnected' WHERE account_id=?", [body.account_id]);
    return { ok: true };
  },

  async handleExchangeToken(env, body) {
    const code = body?.code;
    const label = body?.label?.trim();
    if (!code || !label) {
      throw new Error("Missing label or code.");
    }
    if (!env.EBAY_CLIENT_ID || !env.EBAY_CLIENT_SECRET || !env.EBAY_RUNAME) {
      throw new Error("Server is missing eBay OAuth settings.");
    }

    const credentials = btoa(`${env.EBAY_CLIENT_ID}:${env.EBAY_CLIENT_SECRET}`);
    const tokenResp = await fetch("https://api.ebay.com/identity/v1/oauth2/token", {
      method: "POST",
      headers: {
        "Authorization": `Basic ${credentials}`,
        "Content-Type": "application/x-www-form-urlencoded"
      },
      body: new URLSearchParams({
        grant_type: "authorization_code",
        code,
        redirect_uri: env.EBAY_RUNAME
      })
    });

    const tokenData = await tokenResp.json();
    if (!tokenResp.ok || !tokenData.refresh_token) {
      throw new Error(tokenData.error_description || tokenData.error || "Failed to exchange eBay code.");
    }

    let ebayUsername = label;
    try {
      const userResp = await fetch("https://apiz.ebay.com/commerce/identity/v1/user/", {
        headers: { "Authorization": `Bearer ${tokenData.access_token}` }
      });
      if (userResp.ok) {
        const userData = await userResp.json();
        ebayUsername = userData.username || ebayUsername;
      }
    } catch (err) {
      // ignore username fetch failures
    }

    await this.executeSQL(env, `
      INSERT INTO ebay_accounts
        (account_id, ebay_username, refresh_token, status)
      VALUES (?, ?, ?, 'active')
      ON CONFLICT(account_id) DO UPDATE SET
        ebay_username = excluded.ebay_username,
        refresh_token = excluded.refresh_token,
        status        = 'active'
    `, [label, ebayUsername, tokenData.refresh_token]);

    return { ebay_username: ebayUsername };
  }
};
