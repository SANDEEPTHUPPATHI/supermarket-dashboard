from flask import Flask, render_template, request, redirect, session, Response
import sqlite3
import csv
import io
import os
from datetime import datetime, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

def parse_date(date_str):
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y', '%Y/%m/%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(date_str, fmt).strftime('%Y-%m-%d')
        except ValueError:
            pass
    return date_str

def predict_next_days(daily_sales, days=2):
    if len(daily_sales) < 2:
        return {}
    
    sorted_dates = sorted(daily_sales.keys())
    y = [daily_sales[d] for d in sorted_dates]
    n = len(y)
    x = list(range(n))
    
    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(xi*yi for xi, yi in zip(x, y))
    sum_x2 = sum(xi**2 for xi in x)
    
    denominator = (n * sum_x2 - sum_x**2)
    if denominator == 0:
        return {}
    
    m = (n * sum_xy - sum_x * sum_y) / denominator
    c = (sum_y - m * sum_x) / n
    
    predictions = {}
    try:
        last_date = datetime.strptime(sorted_dates[-1], "%Y-%m-%d")
        for i in range(1, days + 1):
            next_date = (last_date + timedelta(days=i)).strftime("%Y-%m-%d")
            pred_value = m * (n - 1 + i) + c
            predictions[next_date] = max(0, round(pred_value, 2))
    except ValueError:
        pass
    return predictions

app = Flask(__name__)
app.secret_key = "secret123"

# ---------- DATABASE ----------
class PostgresWrapper:
    def __init__(self, conn):
        self.conn = conn
        
    def execute(self, query, params=()):
        try:
            cur = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            # Convert SQLite placeholders to Postgres placeholders
            query = query.replace("?", "%s")
            # Replace string literal day of week extract
            query = query.replace("strftime('%w', s.date)", "CAST(EXTRACT(DOW FROM CAST(s.date AS DATE)) AS TEXT)")
            
            cur.execute(query, params)
            return cur
        except Exception as e:
            self.conn.rollback() # Prevent poisoned transactions
            raise e
        
    def commit(self):
        self.conn.commit()
        
    def close(self):
        self.conn.close()

def get_db_connection():
    db_url = os.getenv("DATABASE_URL")
    
    if db_url and db_url.startswith("postgres"):
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        if psycopg2 is None:
            raise RuntimeError("psycopg2 is not installed!")
            
        conn = psycopg2.connect(db_url)
        return PostgresWrapper(conn)

    db_path = db_url if db_url else "supermarket.db"
    if db_path.startswith("sqlite:///"):
        db_path = db_path.replace("sqlite:///", "", 1)
        
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def create_tables():
    conn = get_db_connection()
    is_postgres = type(conn).__name__ == "PostgresWrapper"

    # Users table
    pk_stmt = "SERIAL PRIMARY KEY" if is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS users (
            id {pk_stmt},
            username TEXT,
            password TEXT
        )
    """)

    # Supermarket data table (Legacy)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS supermarket (
            id {pk_stmt},
            date TEXT,
            product_name TEXT,
            category TEXT,
            price INTEGER,
            quantity_sold INTEGER,
            stock_left INTEGER
        )
    """)

    # Products table (Inventory)
    if is_postgres:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS products (
                id {pk_stmt},
                user_id INTEGER DEFAULT 1,
                product_name TEXT,
                category TEXT,
                price INTEGER,
                total_quantity_added INTEGER DEFAULT 0,
                remaining_stock INTEGER DEFAULT 0,
                threshold INTEGER DEFAULT 20,
                UNIQUE(user_id, product_name)
            )
        """)
    else:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS products_new (
                id {pk_stmt},
                user_id INTEGER DEFAULT 1,
                product_name TEXT,
                category TEXT,
                price INTEGER,
                total_quantity_added INTEGER DEFAULT 0,
                remaining_stock INTEGER DEFAULT 0,
                threshold INTEGER DEFAULT 20,
                UNIQUE(user_id, product_name)
            )
        """)
        
        old_products_exist = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='products'").fetchone()
        if old_products_exist:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()]
            if 'user_id' not in cols:
                conn.execute("INSERT INTO products_new (id, product_name, category, price, total_quantity_added, remaining_stock, threshold) SELECT id, product_name, category, price, total_quantity_added, remaining_stock, threshold FROM products")
                conn.execute("DROP TABLE products")
                conn.execute("ALTER TABLE products_new RENAME TO products")
            else:
                conn.execute("DROP TABLE products_new")
        else:
            conn.execute("ALTER TABLE products_new RENAME TO products")

    # Sales table (Transactions)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS sales (
            id {pk_stmt},
            user_id INTEGER DEFAULT 1,
            date TEXT,
            product_id INTEGER,
            quantity_sold INTEGER,
            stock_at_time_of_sale INTEGER DEFAULT 0,
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
    """)

    if not is_postgres:
        # Attempt to upgrade existing SQLite tables without rebuilding
        try:
            conn.execute("ALTER TABLE sales ADD COLUMN stock_at_time_of_sale INTEGER DEFAULT 0")
        except Exception:
            pass
            
        try:
            conn.execute("ALTER TABLE sales ADD COLUMN user_id INTEGER DEFAULT 1")
        except Exception:
            pass

    # Insert default user
    if is_postgres:
        conn.execute("INSERT INTO users (id, username, password) VALUES (1, 'admin', 'admin') ON CONFLICT (id) DO NOTHING")
    else:
        conn.execute("INSERT OR IGNORE INTO users (id, username, password) VALUES (1, 'admin', 'admin')")

    conn.commit()
    conn.close()

create_tables()

# ---------- LOGIN ----------
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db_connection()
        user = conn.execute(
            "SELECT * FROM users WHERE username=? AND password=?",
            (username, password)
        ).fetchone()
        conn.close()

        if user:
            session["user"] = username
            session["user_id"] = user["id"]
            return redirect("/dashboard")
        else:
            return render_template("login.html", error="Invalid Credentials")

    return render_template("login.html")
# ---------------REGISTER-----------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        # ✅ ADD THIS HERE
        if not username or not password:
            return "Invalid input"

        conn = get_db_connection()

        # Check if user already exists
        existing = conn.execute(
            "SELECT * FROM users WHERE username=?",
            (username,)
        ).fetchone()

        if existing:
            conn.close()
            return "User already exists"

        # Insert new user
        conn.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)",
            (username, password)
        )
        conn.commit()
        conn.close()

        return redirect("/")

    return render_template("register.html")

# ---------- DATA ENTRY & VIEWS ----------
@app.route("/inventory")
def inventory():
    if "user" not in session:
        return redirect("/")

    user_id = session.get("user_id")
    conn = get_db_connection()
    products = conn.execute("SELECT * FROM products WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()
    return render_template("inventory.html", products=products)
@app.route("/sales")
def sales_view():
    if "user" not in session:
        return redirect("/")

    user_id = session.get("user_id")
    conn = get_db_connection()
    products = conn.execute("SELECT id, product_name, remaining_stock FROM products WHERE user_id = ?", (user_id,)).fetchall()
    sales = conn.execute("""
        SELECT s.date, s.quantity_sold, s.stock_at_time_of_sale as remaining_stock, p.product_name, p.category, p.price
        FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE p.user_id = ?
        ORDER BY s.id DESC LIMIT 50
    """, (user_id,)).fetchall()
    conn.close()
    return render_template("sales.html", products=products, sales=sales)

@app.route("/reports")
def reports_view():
    if "user" not in session:
        return redirect("/")
    
    user_id = session.get("user_id")
    conn = get_db_connection()
    products = conn.execute("SELECT id, product_name FROM products WHERE user_id = ?", (user_id,)).fetchall()

    timeframe = request.args.get("timeframe", "all")
    product_filter = request.args.get("product_id", "all")
    day_filter = request.args.get("day", "all")
    custom_start = request.args.get("start_date", "")
    custom_end = request.args.get("end_date", "")

    query = """
        SELECT s.id, s.date, s.quantity_sold, s.stock_at_time_of_sale as remaining_stock, p.product_name, p.category, p.price, p.threshold
        FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE p.user_id = ?
    """
    params = [user_id]

    if custom_start and custom_end:
        query += " AND s.date >= ? AND s.date <= ?"
        params.extend([custom_start, custom_end])
    elif timeframe != "all":
        today = datetime.today()
        if timeframe == "1w":
            start_date = today - timedelta(days=7)
        elif timeframe == "1m":
            start_date = today - timedelta(days=30)
        elif timeframe == "3m":
            start_date = today - timedelta(days=90)
        elif timeframe == "6m":
            start_date = today - timedelta(days=180)
        elif timeframe == "1y":
            start_date = today - timedelta(days=365)
        else:
            start_date = today
        
        query += " AND s.date >= ?"
        params.append(start_date.strftime('%Y-%m-%d'))

    if product_filter != "all":
        query += " AND s.product_id = ?"
        params.append(product_filter)

    if day_filter != "all":
        if day_filter == "weekends":
            query += " AND strftime('%w', s.date) IN ('0', '6')"
        elif day_filter == "weekdays":
            query += " AND strftime('%w', s.date) IN ('1', '2', '3', '4', '5')"
        else:
            query += " AND strftime('%w', s.date) = ?"
            params.append(day_filter)

    sales = conn.execute(query, params).fetchall()
    conn.close()

    total_sales = sum(row["price"] * row["quantity_sold"] for row in sales)
    total_items = sum(row["quantity_sold"] for row in sales)

    return render_template(
        "reports.html", 
        products=products,
        sales=sales,
        total_sales=total_sales,
        total_items=total_items,
        current_timeframe=timeframe,
        current_product=product_filter,
        current_day=day_filter,
        current_start=custom_start,
        current_end=custom_end
    )

@app.route("/add_product", methods=["POST"])
def add_product():
    if "user" not in session: return redirect("/")
    user_id = session.get("user_id")
    name = request.form["product_name"]
    category = request.form["category"]
    price = request.form["price"]
    initial_stock = request.form["initial_stock"]
    threshold = request.form.get("threshold", 20)
    
    try:
        if float(price) <= 0 or float(initial_stock) <= 0:
            return redirect("/inventory?msg=invalid_product")
    except ValueError:
        return redirect("/inventory?msg=invalid_product")
    
    conn = get_db_connection()
    try:
        conn.execute("""
            INSERT INTO products (user_id, product_name, category, price, total_quantity_added, remaining_stock, threshold)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, name, category, price, initial_stock, initial_stock, threshold))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return redirect("/inventory?msg=product_exists")
        
    conn.close()
    return redirect("/inventory?msg=product_added")

@app.route("/log_sale", methods=["POST"])
def log_sale():
    if "user" not in session: return redirect("/")
    user_id = session.get("user_id")
    date = request.form["date"]
    date = parse_date(date)
    product_id = request.form["product_id"]
    qty = int(request.form["quantity_sold"])
    
    conn = get_db_connection()
    current_stock_row = conn.execute("SELECT remaining_stock FROM products WHERE id = ? AND user_id = ?", (product_id, user_id)).fetchone()
    
    if not current_stock_row:
        conn.close()
        return redirect("/sales?msg=invalid_product")
        
    current_stock = current_stock_row[0]
    
    if qty > current_stock:
        conn.close()
        return redirect("/sales?msg=invalid_stock")
        
    conn.execute("UPDATE products SET remaining_stock = remaining_stock - ? WHERE id = ? AND user_id = ?", (qty, product_id, user_id))
    new_stock = current_stock - qty
    conn.execute("INSERT INTO sales (user_id, date, product_id, quantity_sold, stock_at_time_of_sale) VALUES (?, ?, ?, ?, ?)", (user_id, date, product_id, qty, new_stock))
    conn.commit()
    conn.close()
    return redirect("/sales?msg=sale_logged")

@app.route("/restock", methods=["POST"])
def restock():
    if "user" not in session: return redirect("/")
    user_id = session.get("user_id")
    product_id = request.form["product_id"]
    qty_added = int(request.form["quantity_added"])
    
    conn = get_db_connection()
    conn.execute("UPDATE products SET remaining_stock = remaining_stock + ?, total_quantity_added = total_quantity_added + ? WHERE id = ? AND user_id = ?", (qty_added, qty_added, product_id, user_id))
    conn.commit()
    conn.close()
    return redirect("/inventory?msg=restocked")

# ---------- CSV IMPORT / EXPORT ----------
@app.route("/export")
def export_csv():
    if "user" not in session:
        return redirect("/")
    user_id = session.get("user_id")

    conn = get_db_connection()
    data = conn.execute("""
        SELECT s.date, p.product_name, p.category, p.price, s.quantity_sold, s.stock_at_time_of_sale as stock_left
        FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE p.user_id = ?
        ORDER BY s.date ASC
    """, (user_id,)).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Product', 'Category', 'Price', 'Quantity Sold', 'Stock Left'])
    
    for row in data:
        writer.writerow([row['date'], row['product_name'], row['category'], row['price'], row['quantity_sold'], row['stock_left']])
    
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=supermarket_data.csv"}
    )

@app.route("/import_csv", methods=["POST"])
def import_csv():
    if "user" not in session:
        return redirect("/")
    user_id = session.get("user_id")

    if 'file' not in request.files:
        return redirect("/home")
        
    file = request.files['file']
    if file.filename == '':
        return redirect("/home")

    if file:
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.reader(stream)
        
        # Skip header
        next(csv_input, None)
        
        conn = get_db_connection()
        for row in csv_input:
            if len(row) >= 6:
                try:
                    date, product, category, price, qty, stock = row[0:6]
                    date = parse_date(date)
                    price = int(float(price))
                    qty = int(float(qty))
                    stock = int(float(stock))
                    
                    # Resolve product
                    p = conn.execute("SELECT id FROM products WHERE product_name = ? AND user_id = ?", (product, user_id)).fetchone()
                    if not p:
                        conn.execute("""
                            INSERT INTO products (user_id, product_name, category, price, total_quantity_added, remaining_stock, threshold)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (user_id, product, category, price, stock + qty, stock, 20))
                        conn.commit()
                        p = conn.execute("SELECT id FROM products WHERE product_name = ? AND user_id = ?", (product, user_id)).fetchone()
                    
                    product_id = p['id']
                    
                    # Force update the live stock tracking
                    conn.execute("UPDATE products SET remaining_stock = ? WHERE id = ? AND user_id = ?", (stock, product_id, user_id))
                    
                    # Insert sale into snapshot history
                    conn.execute("INSERT INTO sales (user_id, date, product_id, quantity_sold, stock_at_time_of_sale) VALUES (?, ?, ?, ?, ?)", (user_id, date, product_id, qty, stock))
                    
                except Exception as e:
                    print(f"Skipping row {row} due to error: {e}")
                    continue
        
        conn.commit()
        conn.close()
    
    return redirect("/inventory?msg=csv_imported")

# ---------- DASHBOARD ----------
@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/")
    user_id = session.get("user_id")

    conn = get_db_connection()
    timeframe = request.args.get("timeframe", "all")
    product_filter = request.args.get("product_id", "all")
    day_filter = request.args.get("day", "all")
    custom_start = request.args.get("start_date", "")
    custom_end = request.args.get("end_date", "")

    # Fetch products (Inventory) - filtered if a specific product is selected
    if product_filter != "all":
        products = conn.execute("SELECT * FROM products WHERE id = ? AND user_id = ?", (product_filter, user_id)).fetchall()
    else:
        products = conn.execute("SELECT * FROM products WHERE user_id = ?", (user_id,)).fetchall()

    all_products = conn.execute("SELECT id, product_name FROM products WHERE user_id = ?", (user_id,)).fetchall()

    query = """
        SELECT s.id, s.date, s.quantity_sold, s.stock_at_time_of_sale as remaining_stock, p.product_name, p.category, p.price, p.threshold
        FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE p.user_id = ?
    """
    params = [user_id]

    if custom_start and custom_end:
        query += " AND s.date >= ? AND s.date <= ?"
        params.extend([custom_start, custom_end])
    elif timeframe != "all":
        today = datetime.today()
        if timeframe == "1w":
            start_date = today - timedelta(days=7)
        elif timeframe == "1m":
            start_date = today - timedelta(days=30)
        elif timeframe == "3m":
            start_date = today - timedelta(days=90)
        elif timeframe == "6m":
            start_date = today - timedelta(days=180)
        elif timeframe == "1y":
            start_date = today - timedelta(days=365)
        else:
            start_date = today
        
        query += " AND s.date >= ?"
        params.append(start_date.strftime('%Y-%m-%d'))

    if product_filter != "all":
        query += " AND s.product_id = ?"
        params.append(product_filter)

    if day_filter != "all":
        if day_filter == "weekends":
            query += " AND strftime('%w', s.date) IN ('0', '6')"
        elif day_filter == "weekdays":
            query += " AND strftime('%w', s.date) IN ('1', '2', '3', '4', '5')"
        else:
            query += " AND strftime('%w', s.date) = ?"
            params.append(day_filter)

    sales = conn.execute(query, params).fetchall()
    conn.close()

    total_sales = sum(row["price"] * row["quantity_sold"] for row in sales)
    total_items = sum(row["quantity_sold"] for row in sales)

    # LOW STOCK
    low_stock = [p for p in products if p["remaining_stock"] < p["threshold"]]

    # CATEGORY-WISE SALES
    category_sales = {}
    for row in sales:
        cat = row["category"]
        category_sales[cat] = category_sales.get(cat, 0) + row["quantity_sold"]

    # DAILY SALES
    daily_sales = {}
    for row in sales:
        d = row["date"]
        daily_sales[d] = daily_sales.get(d, 0) + (row["price"] * row["quantity_sold"])

    # Sort dictionary by date to ensure the chart displays chronologically
    daily_sales = dict(sorted(daily_sales.items()))
    predicted_sales = predict_next_days(daily_sales, days=2)

    # Also grab total products for KPI
    total_products = len(products)
    
    return render_template(
        "dashboard.html",
        total_sales=total_sales,
        total_items=total_items,
        total_products=total_products,
        low_stock_count=len(low_stock),
        low_stock=low_stock[:5],  # Just top 5 for activity feed
        category_sales=category_sales,
        daily_sales=daily_sales,
        predicted_sales=predicted_sales,
        current_timeframe=timeframe,
        all_products=all_products,
        current_product=product_filter,
        current_day=day_filter,
        current_start=custom_start,
        current_end=custom_end
    )

@app.route("/api/dashboard_data")
def api_dashboard_data():
    if "user" not in session:
        return {"error": "unauthorized"}, 401
    user_id = session.get("user_id")
    
    conn = get_db_connection()
    timeframe = request.args.get("timeframe", "all")
    product_filter = request.args.get("product_id", "all")
    day_filter = request.args.get("day", "all")
    custom_start = request.args.get("start_date", "")
    custom_end = request.args.get("end_date", "")

    if product_filter != "all":
        products = conn.execute("SELECT * FROM products WHERE id = ? AND user_id = ?", (product_filter, user_id)).fetchall()
    else:
        products = conn.execute("SELECT * FROM products WHERE user_id = ?", (user_id,)).fetchall()

    query = """
        SELECT s.quantity_sold, p.price, p.category, s.date, p.product_name, s.id
        FROM sales s JOIN products p ON s.product_id = p.id
        WHERE p.user_id = ?
    """
    params = [user_id]

    if custom_start and custom_end:
        query += " AND s.date >= ? AND s.date <= ?"
        params.extend([custom_start, custom_end])
    elif timeframe != "all":
        today = datetime.today()
        if timeframe == "1w":
            start_date = today - timedelta(days=7)
        elif timeframe == "1m":
            start_date = today - timedelta(days=30)
        elif timeframe == "3m":
            start_date = today - timedelta(days=90)
        elif timeframe == "6m":
            start_date = today - timedelta(days=180)
        elif timeframe == "1y":
            start_date = today - timedelta(days=365)
        else:
            start_date = today
        
        query += " AND s.date >= ?"
        params.append(start_date.strftime('%Y-%m-%d'))

    if product_filter != "all":
        query += " AND s.product_id = ?"
        params.append(product_filter)

    if day_filter != "all":
        if day_filter == "weekends":
            query += " AND strftime('%w', s.date) IN ('0', '6')"
        elif day_filter == "weekdays":
            query += " AND strftime('%w', s.date) IN ('1', '2', '3', '4', '5')"
        else:
            query += " AND strftime('%w', s.date) = ?"
            params.append(day_filter)

    sales = conn.execute(query, params).fetchall()
    
    recent_query = query + " ORDER BY s.id DESC LIMIT 5"
    recent_sales = conn.execute(recent_query, params).fetchall()
    
    conn.close()

    total_sales = sum(row["price"] * row["quantity_sold"] for row in sales)
    total_items = sum(row["quantity_sold"] for row in sales)
    
    category_sales = {}
    for row in sales:
        cat = row["category"]
        category_sales[cat] = category_sales.get(cat, 0) + row["quantity_sold"]
        
    daily_sales = {}
    for row in sales:
        d = row["date"]
        daily_sales[d] = daily_sales.get(d, 0) + (row["price"] * row["quantity_sold"])
    
    daily_sales = dict(sorted(daily_sales.items()))
    predicted_sales = predict_next_days(daily_sales, days=2)

    return {
        "kpis": {
            "total_sales": total_sales,
            "total_items": total_items,
            "total_products": len(products),
            "low_stock": len([p for p in products if p["remaining_stock"] < p["threshold"]])
        },
        "charts": {
            "category": category_sales,
            "daily": daily_sales,
            "predicted": predicted_sales
        },
        "recent_sales": [dict(r) for r in recent_sales]
    }

# ---------- LOGOUT ----------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)