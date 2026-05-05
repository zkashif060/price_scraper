import os
import sqlite3
from flask import Flask, request, jsonify

app = Flask(__name__)

# Path to local Turso/SQLite DB file. Set TURSO_DB_PATH in env to point to your DB file.
DB_PATH = os.environ.get("TURSO_DB_PATH") or os.environ.get("DATABASE_PATH") or os.path.join(os.path.dirname(__file__), "..", "turso.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/", methods=["GET"])
def index():
    return jsonify({"ok": True, "db_path": DB_PATH})


@app.route("/api/stats", methods=["POST"])
def api_stats():
    payload = request.get_json(silent=True) or {}
    today = payload.get("today")
    conn = get_conn()
    cur = conn.cursor()

    def scalar(q, params=()):
        r = cur.execute(q, params).fetchone()
        return r[0] if r else 0

    totalAccounts = scalar("SELECT COUNT(*) FROM ebay_accounts")
    totalListings = scalar("SELECT COUNT(*) FROM listings")
    totalAsins = scalar("SELECT COUNT(*) FROM products")
    hotAsins = scalar("SELECT COUNT(*) FROM products WHERE priority='hot'")
    warmAsins = scalar("SELECT COUNT(*) FROM products WHERE priority='warm'")
    coldAsins = scalar("SELECT COUNT(*) FROM products WHERE priority='cold'")

    priceUpdates = 0
    if today:
        priceUpdates = scalar("SELECT COUNT(*) FROM price_changes WHERE date(changed_at)=?", (today,))

    oosCount = scalar("SELECT COUNT(*) FROM price_changes WHERE change_type='out_of_stock'")
    restoredCount = scalar("SELECT COUNT(*) FROM price_changes WHERE change_type='restored'")

    return jsonify({
        "totalAccounts": totalAccounts,
        "totalListings": totalListings,
        "totalAsins": totalAsins,
        "hotAsins": hotAsins,
        "warmAsins": warmAsins,
        "coldAsins": coldAsins,
        "priceUpdates": priceUpdates,
        "oosCount": oosCount,
        "restoredCount": restoredCount,
    })


@app.route("/api/accounts", methods=["POST"])
def api_accounts():
    conn = get_conn()
    cur = conn.cursor()
    q = """
    SELECT a.account_id, a.ebay_username, a.status, a.connected_at, a.last_synced,
      (SELECT COUNT(*) FROM listings l WHERE l.account_id = a.account_id) as listing_count
    FROM ebay_accounts a
    ORDER BY a.account_id
    """
    rows = [
        [r["account_id"], r["ebay_username"], r["status"], r["connected_at"], r["last_synced"], r["listing_count"]]
        for r in cur.execute(q).fetchall()
    ]
    return jsonify({"rows": rows})


@app.route("/api/changes", methods=["POST"])
def api_changes():
    payload = request.get_json(silent=True) or {}
    today = payload.get("today")
    conn = get_conn()
    cur = conn.cursor()
    q = """
    SELECT pc.asin, pc.sku, pc.old_price, pc.new_price, pc.change_type, pc.changed_at,
           COALESCE(l.title, '') as title
    FROM price_changes pc
    LEFT JOIN listings l ON pc.sku = l.sku
    """
    params = ()
    if today:
        q += " WHERE date(pc.changed_at)=?"
        params = (today,)
    q += " ORDER BY pc.changed_at DESC LIMIT 200"

    rows = []
    for r in cur.execute(q, params).fetchall():
        rows.append([r["asin"], r["sku"], r["old_price"], r["new_price"], r["change_type"], r["changed_at"], r["title"]])
    return jsonify({"rows": rows})


@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    payload = request.get_json(silent=True) or {}
    account_id = payload.get("account_id")
    if not account_id:
        return jsonify({"error": "missing account_id"}), 400
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE ebay_accounts SET status='disconnected' WHERE account_id=?", (account_id,))
    conn.commit()
    return jsonify({"ok": True})


@app.route("/api/exchange-token", methods=["POST"])
def api_exchange_token():
    # Token exchange is intentionally not implemented here. In production this
    # should be handled by your secure Cloudflare Worker which has Turso and
    # eBay secrets. This local endpoint is a placeholder for development.
    return jsonify({"error": "exchange-token not implemented in local server"}), 501


if __name__ == "__main__":
    print("Starting local API server. DB_PATH=", DB_PATH)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=True)
