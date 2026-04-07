import sqlite3

def run():
    conn = sqlite3.connect("supermarket.db")
    try:
        conn.execute("ALTER TABLE sales ADD COLUMN stock_at_time_of_sale INTEGER DEFAULT 0")
        print("Column added.")
    except sqlite3.OperationalError as e:
        print("Error or already exists:", e)
        
    # Attempt to backfill existing sales rows using products table
    sales = conn.execute("SELECT id, product_id FROM sales WHERE stock_at_time_of_sale = 0").fetchall()
    for s in sales:
        p = conn.execute("SELECT remaining_stock FROM products WHERE id = ?", (s[1],)).fetchone()
        if p:
            conn.execute("UPDATE sales SET stock_at_time_of_sale = ? WHERE id = ?", (p[0], s[0]))
    conn.commit()
    conn.close()
    
if __name__ == "__main__":
    run()
