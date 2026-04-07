from flask import Flask, render_template, request, redirect, session, Response
import sqlite3
import csv
import io
from datetime import datetime, timedelta

def parse_date(date_str):
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y', '%Y/%m/%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(date_str, fmt).strftime('%Y-%m-%d')
        except ValueError:
            pass
    return date_str

app = Flask(__name__)
app.secret_key = "secret123"

# ---------- DATABASE ----------
def get_db_connection():
    conn = sqlite3.connect("supermarket.db")
    conn.row_factory = sqlite3.Row
    return conn

def create_tables():
    conn = get_db_connection()

    # Users table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            password TEXT
        )
    """)

    # Supermarket data table (Legacy)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS supermarket (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            product_name TEXT,
            category TEXT,
            price INTEGER,
            quantity_sold INTEGER,
            stock_left INTEGER
        )
    """)

    # Products table (Inventory)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT UNIQUE,
            category TEXT,
            price INTEGER,
            total_quantity_added INTEGER DEFAULT 0,
            remaining_stock INTEGER DEFAULT 0,
            threshold INTEGER DEFAULT 20
        )
    """)

    # Sales table (Transactions)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            product_id INTEGER,
            quantity_sold INTEGER,
            stock_at_time_of_sale INTEGER DEFAULT 0,
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
    """)

    # Attempt to upgrade existing tables without rebuilding
    try:
        conn.execute("ALTER TABLE sales ADD COLUMN stock_at_time_of_sale INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # Insert default user
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
            return redirect("/home")
        else:
            return "Invalid Credentials"

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

# ---------- DATA ENTRY ----------
@app.route("/home")
def home():
    if "user" not in session:
        return redirect("/")

    conn = get_db_connection()
    products = conn.execute("SELECT * FROM products").fetchall()
    conn.close()
    return render_template("index.html", products=products)

@app.route("/add_product", methods=["POST"])
def add_product():
    if "user" not in session: return redirect("/")
    name = request.form["product_name"]
    category = request.form["category"]
    price = request.form["price"]
    initial_stock = request.form["initial_stock"]
    threshold = request.form.get("threshold", 20)
    
    conn = get_db_connection()
    try:
        conn.execute("""
            INSERT INTO products (product_name, category, price, total_quantity_added, remaining_stock, threshold)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, category, price, initial_stock, initial_stock, threshold))
        conn.commit()
    except sqlite3.IntegrityError:
        pass # Product exists
    finally:
        conn.close()
    return redirect("/home")

@app.route("/log_sale", methods=["POST"])
def log_sale():
    if "user" not in session: return redirect("/")
    date = request.form["date"]
    date = parse_date(date)
    product_id = request.form["product_id"]
    qty = int(request.form["quantity_sold"])
    
    conn = get_db_connection()
    conn.execute("UPDATE products SET remaining_stock = remaining_stock - ? WHERE id = ?", (qty, product_id))
    new_stock = conn.execute("SELECT remaining_stock FROM products WHERE id = ?", (product_id,)).fetchone()[0]
    conn.execute("INSERT INTO sales (date, product_id, quantity_sold, stock_at_time_of_sale) VALUES (?, ?, ?, ?)", (date, product_id, qty, new_stock))
    conn.commit()
    conn.close()
    return redirect("/dashboard")

@app.route("/restock", methods=["POST"])
def restock():
    if "user" not in session: return redirect("/")
    product_id = request.form["product_id"]
    qty_added = int(request.form["quantity_added"])
    
    conn = get_db_connection()
    conn.execute("UPDATE products SET remaining_stock = remaining_stock + ?, total_quantity_added = total_quantity_added + ? WHERE id = ?", (qty_added, qty_added, product_id))
    conn.commit()
    conn.close()
    return redirect("/dashboard")

# ---------- CSV IMPORT / EXPORT ----------
@app.route("/export")
def export_csv():
    if "user" not in session:
        return redirect("/")

    conn = get_db_connection()
    data = conn.execute("""
        SELECT s.date, p.product_name, p.category, p.price, s.quantity_sold, s.stock_at_time_of_sale as stock_left
        FROM sales s
        JOIN products p ON s.product_id = p.id
        ORDER BY s.date ASC
    """).fetchall()
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
                    qty = int(qty)
                    stock = int(stock)
                    
                    # Resolve product
                    p = conn.execute("SELECT id FROM products WHERE product_name = ?", (product,)).fetchone()
                    if not p:
                        conn.execute("""
                            INSERT INTO products (product_name, category, price, total_quantity_added, remaining_stock, threshold)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (product, category, price, stock + qty, stock, 20))
                        conn.commit()
                        p = conn.execute("SELECT id FROM products WHERE product_name = ?", (product,)).fetchone()
                    
                    product_id = p['id']
                    
                    # Force update the live stock tracking
                    conn.execute("UPDATE products SET remaining_stock = ? WHERE id = ?", (stock, product_id))
                    
                    # Insert sale into snapshot history
                    conn.execute("INSERT INTO sales (date, product_id, quantity_sold, stock_at_time_of_sale) VALUES (?, ?, ?, ?)", (date, product_id, qty, stock))
                    
                except Exception as e:
                    print(f"Skipping row {row} due to error: {e}")
                    continue
        
        conn.commit()
        conn.close()
    
    return redirect("/dashboard")

# ---------- DASHBOARD ----------
@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/")

    conn = get_db_connection()
    timeframe = request.args.get("timeframe", "all")
    product_filter = request.args.get("product_id", "all")
    day_filter = request.args.get("day", "all")
    custom_start = request.args.get("start_date", "")
    custom_end = request.args.get("end_date", "")

    # Fetch products (Inventory) - filtered if a specific product is selected
    if product_filter != "all":
        products = conn.execute("SELECT * FROM products WHERE id = ?", (product_filter,)).fetchall()
    else:
        products = conn.execute("SELECT * FROM products").fetchall()

    query = """
        SELECT s.id, s.date, s.quantity_sold, s.stock_at_time_of_sale as remaining_stock, p.product_name, p.category, p.price, p.threshold
        FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE 1=1
    """
    params = []

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

    return render_template(
        "dashboard.html",
        products=products,
        sales=sales,
        total_sales=total_sales,
        total_items=total_items,
        low_stock=low_stock,
        category_sales=category_sales,
        daily_sales=daily_sales,
        current_timeframe=timeframe,
        current_product=product_filter,
        current_day=day_filter,
        current_start=custom_start,
        current_end=custom_end
    )

# ---------- LOGOUT ----------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)