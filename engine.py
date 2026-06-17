# engine.py
import sys
import mysql.connector
from Connector import get_connection  # Keeps your existing DB credentials

# =====================================================================
# 1. CORE MATCHING ENGINE
# =====================================================================
def match_market_orders(stock_name):
    """
    Checks the SQL tables for the matching stock name, executes settlements,
    and handles partial or full fills sequentially.
    """
    while True:
        conn = get_connection()
        cursor = conn.cursor(buffered=True)
        
        try:
            # Get the highest bidding buyer
            cursor.execute("""
                SELECT id, buyer_id, quantity, expected_price 
                FROM buyer WHERE stock_name = %s 
                ORDER BY expected_price DESC, id ASC LIMIT 1
            """, (stock_name,))
            top_buy = cursor.fetchone()
            
            # Get the lowest asking seller
            cursor.execute("""
                SELECT id, seller_id, quantity, expected_min_price 
                FROM seller WHERE stock_name = %s 
                ORDER BY expected_min_price ASC, id ASC LIMIT 1
            """, (stock_name,))
            top_sell = cursor.fetchone()
            
            # Force close read locks before processing writes
            conn.commit()
            
            if not top_buy or not top_sell:
                break
                
            buy_row_id, b_id, b_qty, b_price = top_buy
            sell_row_id, s_id, s_qty, s_price = top_sell
            
            if b_price < s_price:
                break
                
            execution_price = s_price
            filled_qty = min(b_qty, s_qty)
            total_cost = filled_qty * execution_price
            
            if b_id != s_id:
                # 1. Exchange cash balances
                cursor.execute("UPDATE balances SET current_balance = current_balance - %s WHERE user_id = %s", (total_cost, b_id))
                cursor.execute("UPDATE balances SET current_balance = current_balance + %s WHERE user_id = %s", (total_cost, s_id))
                
                # 2. Update buyer share inventory
                cursor.execute("SELECT quantity FROM ownerships WHERE user_id = %s AND stock_name = %s", (b_id, stock_name))
                buyer_row = cursor.fetchone()
                conn.commit() # Clear select read state
                
                if buyer_row is None:
                    cursor.execute("INSERT INTO ownerships (user_id, stock_name, quantity) VALUES (%s, %s, %s)", (b_id, stock_name, filled_qty))
                else:
                    cursor.execute("UPDATE ownerships SET quantity = quantity + %s WHERE user_id = %s AND stock_name = %s", (filled_qty, b_id, stock_name))
                
                # 3. Update seller share inventory
                cursor.execute("UPDATE ownerships SET quantity = quantity - %s WHERE user_id = %s AND stock_name = %s", (filled_qty, s_id, stock_name))
            
            # 4. Update the core global stock price metrics
            cursor.execute("UPDATE stocks SET last_price_sold = %s WHERE stock_name = %s", (execution_price, stock_name))
            
            # 5. Write clear ledger history notifications
            query_hist = "INSERT INTO history (user_id, message, is_shown) VALUES (%s, %s, 'no')"
            if b_id == s_id:
                cursor.execute(query_hist, (b_id, f"Self-Trade Executed: {filled_qty:,} shares of {stock_name} at ${execution_price}."))
            else:
                cursor.execute(query_hist, (b_id, f"Executed: Bought {filled_qty:,} shares of {stock_name} at ${execution_price}."))
                cursor.execute(query_hist, (s_id, f"Executed: Sold {filled_qty:,} shares of {stock_name} at ${execution_price}."))
            
            # 6. Deduct quantities from books or delete rows completely if filled
            new_b_qty = b_qty - filled_qty
            new_s_qty = s_qty - filled_qty
            
            if new_b_qty <= 0:
                cursor.execute("DELETE FROM buyer WHERE id = %s", (buy_row_id,))
            else:
                cursor.execute("UPDATE buyer SET quantity = %s WHERE id = %s", (new_b_qty, buy_row_id))
                
            if new_s_qty <= 0:
                cursor.execute("DELETE FROM seller WHERE id = %s", (sell_row_id,))
            else:
                cursor.execute("UPDATE seller SET quantity = %s WHERE id = %s", (new_s_qty, sell_row_id))
            
            conn.commit()
            print(f"\n SUCCESS: {filled_qty:,} shares of {stock_name} successfully matched and traded at ${execution_price}!")
            
        except mysql.connector.Error as err:
            print(f" Engine Error: Settlement skipped: {err}")
            break
        finally:
            cursor.close()
            conn.close()

# =====================================================================
# 2. USER ORDER INTERACTIONS & SYSTEM FEATURES
# =====================================================================
def place_buy_order(user_id):
    print("\n--- Buy Shares Panel ---")
    stock_name = input("Enter valid stock ticker symbol (e.g. AIR): ").strip().upper()
    
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT stock_name FROM stocks WHERE stock_name = %s", (stock_name,))
        if not cursor.fetchone():
            print(f" Rejected: Ticker '{stock_name}' does not exist on this exchange.")
            return

        qty = int(input("Enter quantity: "))
        price = int(input("Enter maximum buy price ($): "))
        if qty <= 0 or price <= 0:
            print(" Quantity and price must be greater than zero.")
            return

        cursor.execute("SELECT current_balance FROM balances WHERE user_id = %s", (user_id,))
        balance_row = cursor.fetchone()
        conn.commit() # Clear transaction state immediately after reading
        
        if balance_row is None:
            print(" New user profile detected! Initializing account with a standard $10,000 starter balance...")
            cursor.execute("INSERT INTO balances (user_id, current_balance) VALUES (%s, 10000)", (user_id,))
            conn.commit()
            balance = 10000
        else:
            balance = balance_row[0]

        if balance < (qty * price):
            print(f" Rejected: Total cost (${qty*price:,}) exceeds account balance (${balance:,}).")
            return

        cursor.execute(
            "INSERT INTO buyer (buyer_id, stock_name, quantity, expected_price) VALUES (%s, %s, %s, %s)",
            (user_id, stock_name, qty, price)
        )
        conn.commit()
        print("Buy order registered in database! Processing matching loops...")
        
        # Safe callback termination
        cursor.close()
        conn.close()
        
        match_market_orders(stock_name)

    except ValueError:
        print("Entry error: Inputs must be valid numbers.")
        cursor.close()
        conn.close()

def place_sell_order(user_id):
    print("\n--- Sell Shares Panel ---")
    stock_name = input("Enter valid stock ticker symbol (e.g. AIR): ").strip().upper()
    
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT quantity FROM ownerships WHERE user_id = %s AND stock_name = %s", (user_id, stock_name))
        res = cursor.fetchone()
        conn.commit() # Clear transaction state immediately after reading
        owned_qty = res[0] if res else 0
        
        if owned_qty <= 0:
            print(f" Rejected: You own 0 shares of {stock_name}.")
            return

        qty = int(input(f"You own {owned_qty:,} shares. Enter qty to sell: "))
        price = int(input("Enter minimum expected price ($): "))
        
        if qty <= 0 or price <= 0:
            print(" Quantity and price must be greater than zero.")
            return
        if qty > owned_qty:
            print(" Rejected: Insufficient shares available in your portfolio.")
            return

        cursor.execute(
            "INSERT INTO seller (seller_id, stock_name, quantity, expected_min_price) VALUES (%s, %s, %s, %s)",
            (user_id, stock_name, qty, price)
        )
        conn.commit()
        print(" Sell offer registered in database! Processing matching loops...")
        
        # Safe callback termination
        cursor.close()
        conn.close()
        
        match_market_orders(stock_name)

    except ValueError:
        print("⚠️ Entry error: Inputs must be valid numbers.")
        cursor.close()
        conn.close()

def manage_pending_swaps(user_id):
    print("\n==================== PENDING ORDER MANAGEMENT ====================")
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, stock_name, quantity, expected_price FROM buyer WHERE buyer_id = %s", (user_id,))
    buys = cursor.fetchall()
    cursor.execute("SELECT id, stock_name, quantity, expected_min_price FROM seller WHERE seller_id = %s", (user_id,))
    sells = cursor.fetchall()
    conn.commit()
    
    if not buys and not sells:
        print("You do not have any pending orders waiting in the market right now.")
        cursor.close()
        conn.close()
        return

    all_orders = []
    idx = 1
    print(f"\n{'#':<4} | {'TYPE':<6} | {'TICKER':<8} | {'QTY':<8} | {'PRICE':<10}")
    print("-" * 45)
    for r in buys:
        all_orders.append(('BUY', r[0], r[1]))
        print(f"{idx:<4} | {'BUY':<6} | {r[1]:<8} | {r[2]:<8,} | ${r[3]:,}")
        idx += 1
    for r in sells:
        all_orders.append(('SELL', r[0], r[1]))
        print(f"{idx:<4} | {'SELL':<6} | {r[1]:<8} | {r[2]:<8,} | ${r[3]:,}")
        idx += 1

    try:
        choice = int(input("\nEnter order number to DELETE completely (or 0 to exit): "))
        if choice == 0 or choice >= idx:
            return
        
        o_type, db_row_id, ticker = all_orders[choice - 1]
        table = "buyer" if o_type == "BUY" else "seller"
        
        cursor.execute(f"DELETE FROM {table} WHERE id = %s", (db_row_id,))
        conn.commit()
        print(f" Successfully revoked pending {o_type} order for {ticker}!")
    except ValueError:
        print("Invalid entry. Exiting dashboard panel.")
    finally:
        cursor.close()
        conn.close()

def launch_stock_ipo(user_id):
    """Allows any profile user to instantly issue a brand new stock asset ticker onto the trading floor."""
    print("\n--- Launch Stock Corporate IPO ---")
    stock_name = input("Create 3-4 letter Ticker Symbol (e.g. AAPL): ").strip().upper()
    company_name = input("Enter official Corporate Company Name: ").strip()
    
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Check if it already exists
        cursor.execute("SELECT stock_name FROM stocks WHERE stock_name = %s", (stock_name,))
        if cursor.fetchone():
            print(f" Rejected: Ticker '{stock_name}' has already been listed on this market.")
            return
            
        initial_price = int(input("Enter starting trading price per share ($): "))
        initial_shares = int(input("Enter total initial share supply volume to issue to your inventory: "))
        
        if initial_price <= 0 or initial_shares <= 0:
            print(" Volume metrics must be positive numbers.")
            return
            
        conn.commit() # Pure transaction isolation clear
        
        # 1. Register the new stock asset row
        cursor.execute("INSERT INTO stocks (stock_name, company_name, last_price_sold) VALUES (%s, %s, %s)", (stock_name, company_name, initial_price))
        # 2. Assign the created share volume straight to the launching corporate profile
        cursor.execute("INSERT INTO ownerships (user_id, stock_name, quantity) VALUES (%s, %s, %s)", (user_id, stock_name, initial_shares))
        conn.commit()
        
        print(f" SUCCESS! {company_name} ({stock_name}) is now public! {initial_shares:,} shares dropped into your portfolio.")
        
    except ValueError:
        print(" Entry error: Financial listings must be whole numbers.")
    except mysql.connector.Error as err:
        print(f" Database error during IPO: {err}")
    finally:
        cursor.close()
        conn.close()

# =====================================================================
# 3. INTERACTIVE RUNTIME ENTRY PORTAL
# =====================================================================
def user_dashboard(user_id):
    while True:
        conn = get_connection()
        cursor = conn.cursor()
        
        # --- 1. DYNAMIC BALANCE TRACKING READOUT ---
        cursor.execute("SELECT current_balance FROM balances WHERE user_id = %s", (user_id,))
        balance_row = cursor.fetchone()
        conn.commit() # Clear transaction read state
        
        # Fallback profile setup if user data row hasn't hit the DB ledger yet
        if balance_row is None:
            cursor.execute("INSERT INTO balances (user_id, current_balance) VALUES (%s, 10000)", (user_id,))
            conn.commit()
            user_balance = 10000
        else:
            user_balance = balance_row[0]
            
        print("\n==================================================")
        print(f" PROFILE DASHBOARD: {user_id}")
        print(f" LIQUID BANK WALLET BALANCE: ${user_balance:,}")
        print("==================================================")
        
        # --- 2. DYNAMIC PORTFOLIO SHARE OWNERSHIPS READOUT ---
        print(" YOUR SHARE HOLDINGS:")
        cursor.execute("SELECT stock_name, quantity FROM ownerships WHERE user_id = %s AND quantity > 0", (user_id,))
        holdings = cursor.fetchall()
        conn.commit()
        
        if not holdings:
            print("   (You do not currently own any corporate stock assets)")
        else:
            for stock, qty in holdings:
                print(f"   • {stock:<6} : {qty:,} shares")
        print("--------------------------------------------------")
        
        cursor.close()
        conn.close()
        
        # --- 3. DASHBOARD ACTION SELECTIONS ---
        print("1. View Market Listings")
        print("2. Buy Shares")
        print("3. Sell Shares")
        print("4. View Pending Book Orders (Swaps)")
        print("5. Launch Stock IPO (Create New Asset)")
        print("6. Log Out")
        
        choice = input("Select a dashboard menu option: ").strip()
        if choice == "1":
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT stock_name, company_name, last_price_sold FROM stocks")
            print(f"\n{'TICKER':<8} | {'COMPANY':<25} | {'LAST TRADED COST'}")
            print("-" * 50)
            for row in cursor.fetchall():
                print(f"{row[0]:<8} | {row[1]:<25} | ${row[2]:,}")
            cursor.close()
            conn.close()
        elif choice == "2":
            place_buy_order(user_id)
        elif choice == "3":
            place_sell_order(user_id)
        elif choice == "4":
            manage_pending_swaps(user_id)
        elif choice == "5":
            launch_stock_ipo(user_id)
        elif choice == "6":
            print(f"Logging session user '{user_id}' out...")
            break

def main():
    while True:
        print("\n==================================================")
        print("      CLEAN GROUND-UP EXPLICIT SQL EXCHANGE       ")
        print("==================================================")
        user_input = input("Enter User ID profile to log in (or 'exit' to quit): ").strip()
        
        if user_input.lower() == 'exit':
            print("System down. Goodbye!")
            sys.exit(0)
        elif user_input:
            user_dashboard(user_input)

if __name__ == "__main__":
    main()
