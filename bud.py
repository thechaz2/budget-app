#!/usr/bin/env python3
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
import os
import json
import sqlite3
from urllib.parse import urlparse, parse_qs

PORT = 8000
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(DIRECTORY, "budget.db")

# ---------- DB Helpers ----------

def get_conn():
    conn = sqlite3.connect(DB_FILE)
    # Return rows as dict-like tuples when needed
    conn.row_factory = sqlite3.Row
    # Ensure foreign keys (for ON DELETE CASCADE) actually work
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS months (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ym TEXT UNIQUE NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            amount REAL NOT NULL,
            date TEXT,
            quarterly BOOLEAN DEFAULT 0,
            FOREIGN KEY (month_id) REFERENCES months(id) ON DELETE CASCADE
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS money_ins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            amount REAL NOT NULL,
            date TEXT,
            FOREIGN KEY (month_id) REFERENCES months(id) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()

def ensure_month(conn, ym: str) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id, opening_balance, closing_balance FROM months WHERE ym = ?", (ym,))
    row = cur.fetchone()
    if row:
        return row["id"]
    
    # Try to get closing balance from previous month
    prev_balance = 0
    year, month = map(int, ym.split('-'))
    if month == 1:
        prev_ym = f"{year-1}-12"
    else:
        prev_ym = f"{year}-{month-1:02d}"
    
    cur.execute("SELECT closing_balance FROM months WHERE ym = ?", (prev_ym,))
    prev_row = cur.fetchone()
    if prev_row:
        prev_balance = prev_row["closing_balance"] or 0
    
    cur.execute("INSERT INTO months (ym, opening_balance, closing_balance) VALUES (?, ?, ?)", 
                (ym, prev_balance, prev_balance))
    conn.commit()
    return cur.lastrowid

# ---------- HTTP Handler ----------

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    # Small helpers
    def _send_json(self, obj, status=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b"{}"
            return json.loads(body.decode("utf-8") or "{}")
        except Exception:
            return {}

    def do_GET(self):
        # Ignore favicon
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        parsed = urlparse(self.path)
        route = parsed.path
        qs = parse_qs(parsed.query)

        try:
            if route == "/months":
                with get_conn() as conn:
                    rows = conn.execute("SELECT id, ym FROM months ORDER BY ym").fetchall()
                    resp = [dict(id=r["id"], ym=r["ym"]) for r in rows]
                self._send_json(resp, 200)
                return

            # In your /bills endpoint in bud.py
            if route == "/bills":
                ym = (qs.get("ym") or [None])[0]
                if not ym:
                    self._send_json({"error": "Missing ym query param"}, 400)
                    return
                with get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT b.id, b.name, b.amount, b.date, b.quarterly
                        FROM bills b
                        JOIN months m ON m.id = b.month_id
                        WHERE m.ym = ?
                        ORDER BY b.id
                    """, (ym,))
                    rows = cur.fetchall()
                    resp = [dict(id=r["id"], name=r["name"], amount=r["amount"], 
                                date=r["date"], quarterly=bool(r["quarterly"])) for r in rows]
                self._send_json(resp, 200)
                return

            if route == "/money_ins":
                ym = (qs.get("ym") or [None])[0]
                if not ym:
                    self._send_json({"error": "Missing ym query param"}, 400)
                    return
                with get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT mi.id, mi.source, mi.amount, mi.date
                        FROM money_ins mi
                        JOIN months m ON m.id = mi.month_id
                        WHERE m.ym = ?
                        ORDER BY mi.id
                    """, (ym,))
                    rows = cur.fetchall()
                    resp = [dict(id=r["id"], source=r["source"], amount=r["amount"], date=r["date"]) for r in rows]
                self._send_json(resp, 200)
                return

            # Fallback to static file serving
            return super().do_GET()

        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        route = parsed.path
        data = self._read_json()

        try:
            if route == "/add_month":
                ym = data.get("ym")
                if not ym:
                    self._send_json({"error": "Missing 'ym' in body"}, 400)
                    return
                with get_conn() as conn:
                    ensure_month(conn, ym)
                    row = conn.execute("SELECT id, ym FROM months WHERE ym = ?", (ym,)).fetchone()
                    self._send_json({"status": "ok", "month": dict(id=row["id"], ym=row["ym"])}, 201)
                return

            if route == "/delete_month":
                ym = data.get("ym")
                if not ym:
                    self._send_json({"error": "Missing 'ym' in body"}, 400)
                    return
                with get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT id FROM months WHERE ym = ?", (ym,))
                    row = cur.fetchone()
                    if not row:
                        self._send_json({"status": "not_found", "ym": ym}, 404)
                        return
                    # Deleting the month cascades to bills/money_ins
                    cur.execute("DELETE FROM months WHERE id = ?", (row["id"],))
                    conn.commit()
                    self._send_json({"status": "ok", "deleted": ym}, 200)
                return

            if route == "/add_bill":
                ym = data.get("ym")
                name = data.get("name")
                amount = data.get("amount")
                date = data.get("date", "")
                quarterly = data.get("quarterly", False)
                
                if not (ym and name and amount is not None):
                    self._send_json({"error": "Body must include ym, name, amount"}, 400)
                    return
                try:
                    amount = float(amount)
                except ValueError:
                    self._send_json({"error": "amount must be a number"}, 400)
                    return

                with get_conn() as conn:
                    month_id = ensure_month(conn, ym)
                    cur = conn.cursor()
                    cur.execute("INSERT INTO bills (month_id, name, amount, date, quarterly) VALUES (?, ?, ?, ?, ?)",
                                (month_id, name, amount, date, 1 if quarterly else 0))
                    conn.commit()
                    bill_id = cur.lastrowid
                    self._send_json({"status": "ok", "bill": {"id": bill_id, "name": name, "amount": amount, "date": date, "quarterly": quarterly}}, 201)
                return
            if route == "/update_bill":
                bill_id = data.get("id")
                name = data.get("name")
                amount = data.get("amount")
                date = data.get("date", "")
                quarterly = data.get("quarterly", False)
                
                if not (bill_id and name and amount is not None):
                    self._send_json({"error": "Body must include id, name, amount"}, 400)
                    return
                try:
                    amount = float(amount)
                except ValueError:
                    self._send_json({"error": "amount must be a number"}, 400)
                    return

                with get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("UPDATE bills SET name = ?, amount = ?, date = ?, quarterly = ? WHERE id = ?",
                                (name, amount, date, 1 if quarterly else 0, bill_id))
                    conn.commit()
                    self._send_json({"status": "ok", "bill": {"id": bill_id, "name": name, "amount": amount, "date": date, "quarterly": quarterly}}, 200)
                return

            if route == "/add_money_in":
                ym = data.get("ym")
                source = data.get("source")
                amount = data.get("amount")
                date = data.get("date", "")
                
                if not (ym and source and amount is not None):
                    self._send_json({"error": "Body must include ym, source, amount"}, 400)
                    return
                try:
                    amount = float(amount)
                except ValueError:
                    self._send_json({"error": "amount must be a number"}, 400)
                    return

                with get_conn() as conn:
                    month_id = ensure_month(conn, ym)
                    cur = conn.cursor()
                    cur.execute("INSERT INTO money_ins (month_id, source, amount, date) VALUES (?, ?, ?, ?)",
                                (month_id, source, amount, date))
                    conn.commit()
                    mi_id = cur.lastrowid
                    self._send_json({"status": "ok", "money_in": {"id": mi_id, "source": source, "amount": amount, "date": date}}, 201)
                return

            if route == "/update_money_in":
                mi_id = data.get("id")
                source = data.get("source")
                amount = data.get("amount")
                date = data.get("date", "")
                
                if not (mi_id and source and amount is not None):
                    self._send_json({"error": "Body must include id, source, amount"}, 400)
                    return
                try:
                    amount = float(amount)
                except ValueError:
                    self._send_json({"error": "amount must be a number"}, 400)
                    return

                with get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("UPDATE money_ins SET source = ?, amount = ?, date = ? WHERE id = ?",
                                (source, amount, date, mi_id))
                    conn.commit()
                    self._send_json({"status": "ok", "money_in": {"id": mi_id, "source": source, "amount": amount, "date": date}}, 200)
                return
            
            # update balace
            if route == "/update_balance":
                ym = data.get("ym")
                closing_balance = data.get("closing_balance")
                
                if not ym or closing_balance is None:
                    self._send_json({"error": "Body must include ym and closing_balance"}, 400)
                    return
                
                try:
                    closing_balance = float(closing_balance)
                except ValueError:
                    self._send_json({"error": "closing_balance must be a number"}, 400)
                    return

                with get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("UPDATE months SET closing_balance = ? WHERE ym = ?", 
                                (closing_balance, ym))
                    conn.commit()
                    self._send_json({"status": "ok", "ym": ym, "closing_balance": closing_balance}, 200)
                return

            # Unknown POST route
            self._send_json({"error": "Not found"}, 404)

        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        route = parsed.path

        try:
            if route.startswith("/delete_bill/"):
                bill_id = int(route.split("/")[-1])
                with get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("DELETE FROM bills WHERE id = ?", (bill_id,))
                    conn.commit()
                    self._send_json({"status": "ok", "deleted_id": bill_id}, 200)
                return
            
            if route.startswith("/delete_money_in/"):
                mi_id = int(route.split("/")[-1])
                with get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("DELETE FROM money_ins WHERE id = ?", (mi_id,))
                    conn.commit()
                    self._send_json({"status": "ok", "deleted_id": mi_id}, 200)
                return
            
            # Unknown DELETE route
            self._send_json({"error": "Not found"}, 404)
            
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

# ---------- Main ----------

def main():
    os.chdir(DIRECTORY)
    init_db()
    print(f"Serving from: {DIRECTORY}")
    print(f"Available files: {os.listdir(DIRECTORY)}")

    server = HTTPServer(("", PORT), Handler)
    webbrowser.open(f"http://localhost:{PORT}")

    try:
        print(f"\nServer running at http://localhost:{PORT}")
        print("Press Ctrl+C to stop\n")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped")

if __name__ == "__main__":
    main()


    


