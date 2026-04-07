import sqlite3

def migrate():
    conn = sqlite3.connect("c:\\Users\\sande\\Downloads\\Supermarket-Dashboard-main\\Supermarket-Dashboard\\Supermarket_Dashboard\\supermarket.db")
    conn.row_factory = sqlite3.Row
    
    # Check if we already migrated
    sales_count = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    if sales_count > 0:
        print("Data already exists in sales table. Aborting to prevent duplicates.")
        return

    old_data = conn.execute("SELECT * FROM supermarket").fetchall()
    
    for row in old_data:
        date = row['date']
        product = row['product_name']
        category = row['category']
        price = row['price']
        qty = row['quantity_sold']
        stock = row['stock_left']
        
        # Check if product exists
        p = conn.execute("SELECT id, remaining_stock FROM products WHERE product_name = ?", (product,)).fetchone()
        
        if not p:
            # Create product
            # Total quantity added is roughly stock + qty (this is an approximation for legacy data)
            total = stock + qty
            conn.execute("""
                INSERT INTO products (product_name, category, price, total_quantity_added, remaining_stock, threshold)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (product, category, price, total, stock, 20))
            conn.commit()
            p = conn.execute("SELECT id FROM products WHERE product_name = ?", (product,)).fetchone()
        
        product_id = p['id']
        
        # We don't want to continually subtract stock for legacy data since we set remaining_stock = stock_left initially
        # But wait, if multiple rows exist for the same product, the last stock_left should probably be the real one.
        # Let's just update the remaining stock to the latest row's stock_left
        conn.execute("UPDATE products SET remaining_stock = ? WHERE id = ?", (stock, product_id))
        
        # Insert sale
        conn.execute("INSERT INTO sales (date, product_id, quantity_sold, stock_at_time_of_sale) VALUES (?, ?, ?, ?)", (date, product_id, qty, stock))
        
    conn.commit()
    conn.close()
    print(f"Successfully migrated {len(old_data)} records to the new inventory system!")

if __name__ == "__main__":
    migrate()
