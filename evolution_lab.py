# evolution_lab.py
import os
import json
import math
import random
import pandas as pd
import mysql.connector
from Connector import get_connection

try:
    from engine import match_market_orders
except ImportError:
    def match_market_orders(ticker): pass

JOURNAL_DIR = "simulation_journals"
MASTER_CSV_REPORT = "market_evolution_summary.csv"
COMPUTE_COST = 0.25  # Infrastructure tax per step

# === EXPERIMENT PARAMETERS ===
STARTING_CASH = 5000.00  # Low liquidity environment to test income utility

class TradingBrainNN:
    def __init__(self, input_dim=5, learning_rate=0.005):
        self.lr = learning_rate
        # Inputs: [Micro Trend, Macro Trend, Velocity, Stock Inventory Ratio, Wallet Cash Ratio]
        self.weights = [random.uniform(-0.05, 0.05) for _ in range(input_dim)]
        self.bias = 1.0  
        self.output = 1.0
        
    def forward(self, inputs):
        self.inputs = inputs
        z = sum(w * x for w, x in zip(self.weights, inputs)) + self.bias
        self.output = max(0.01, min(5.0, z))
        return self.output

    def backpropagate(self, target_multiplier):
        error = self.output - target_multiplier
        for i in range(len(self.weights)):
            self.weights[i] -= self.lr * error * self.inputs[i]
            self.weights[i] = max(-3.0, min(3.0, self.weights[i]))
        self.bias -= self.lr * error
        self.bias = max(0.1, min(4.0, self.bias))

# =====================================================================
# AUTONOMOUS MARKET INITIALIZER
# =====================================================================
def initialize_random_market(population, cursor, conn):
    print(" Bootstrapping random company asset environment...")
    
    company_registry = {
        "ALPHA": "Alpha Cybernetics", "BETA": "Beta Bio-Systems",
        "GAMMA": "Gamma Quantum Computing", "DELTA": "Delta Aerospace Engine",
        "OMEGA": "Omega Energy Solutions", "SIGMA": "Sigma Heavy Materials",
        "NEXUS": "Nexus Neural Fabrics", "QUANT": "Quant Financial Tech"
    }
    
    chosen_tickers = random.sample(list(company_registry.keys()), k=random.randint(4, 6))
    initial_prices = {ticker: round(random.uniform(25.0, 150.0), 4) for ticker in chosen_tickers}
    
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
    cursor.execute("TRUNCATE TABLE buyer")
    cursor.execute("TRUNCATE TABLE seller")
    cursor.execute("TRUNCATE TABLE balances")
    cursor.execute("TRUNCATE TABLE stocks")
    try:
        cursor.execute("TRUNCATE TABLE ownerships")
    except mysql.connector.Error: pass
    cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
    conn.commit()
    
    for ticker, price in initial_prices.items():
        cursor.execute("INSERT INTO stocks (stock_name, company_name, last_price_sold) VALUES (%s, %s, %s)", (ticker, company_registry[ticker], price))
    conn.commit()
    
    initializer_reference = {"initial_prices": initial_prices, "bot_allocations": {}, "baseline_net_worths": {}}
    bot_initial_portfolios = {}
    
    for bot_name in population.keys():
        bot_initial_portfolios[bot_name] = {}
        initializer_reference["bot_allocations"][bot_name] = {}
        
        stock_value_accumulator = 0.0
        for ticker in chosen_tickers:
            starting_shares = random.randint(5, 25)
            bot_initial_portfolios[bot_name][ticker] = starting_shares
            initializer_reference["bot_allocations"][bot_name][ticker] = starting_shares
            stock_value_accumulator += (starting_shares * initial_prices[ticker])
            
            try:
                cursor.execute("INSERT INTO ownerships (user_id, stock_name, quantity) VALUES (%s, %s, %s)", (bot_name, ticker, starting_shares))
            except mysql.connector.Error: pass
        
        initializer_reference["baseline_net_worths"][bot_name] = round(stock_value_accumulator + STARTING_CASH, 4)
                
    conn.commit()
    with open("market_initializer_reference.json", "w") as f:
        json.dump(initializer_reference, f, indent=2)
        
    print(" Market initialized. Net worth baselines calculated successfully.")
    return initial_prices, bot_initial_portfolios, initializer_reference["baseline_net_worths"]

# =====================================================================
# RUNTIME EVOLUTIONARY ENVIRONMENT LOOP WITH DYNAMIC DIVIDENDS
# =====================================================================
def start_macro_evolution_experiment():
    print("=====================================================================")
    print("    NEURAL EVOLUTION LAB: VARIABLE DIVIDEND EDITION                  ")
    print("=====================================================================")
    try:
        num_agents = int(input("Enter number of parallel neural bots to spawn (N): "))
        total_steps = int(input("Enter total simulation timeline steps to execute: "))
    except ValueError:
        print(" Entry Error: Configuration variables must be integers.")
        return

    if os.path.exists(JOURNAL_DIR):
        for f in os.listdir(JOURNAL_DIR): os.remove(os.path.join(JOURNAL_DIR, f))
    else:
        os.makedirs(JOURNAL_DIR)

    conn = get_connection()
    cursor = conn.cursor(buffered=True)
    
    population = {}
    for i in range(1, num_agents + 1):
        name = f"NeuralBot_{i:03d}"
        population[name] = {"status": "Alive", "experience_ticks": 0, "brain": TradingBrainNN(5, 0.002), "history_ledger": []}

    initial_prices, bot_initial_portfolios, baseline_net_worths = initialize_random_market(population, cursor, conn)
    
    portfolio_trackers = {}
    asset_history_matrices = {ticker: [price] * 10 for ticker, price in initial_prices.items()}
    
    for name in population.keys():
        portfolio_trackers[name] = {}
        for ticker in initial_prices.keys():
            shares = bot_initial_portfolios[name].get(ticker, 0)
            portfolio_trackers[name][ticker] = {"total_shares_held": shares, "avg_buy_price": initial_prices[ticker]}
        
        cursor.execute("INSERT INTO balances (user_id, current_balance) VALUES (%s, %s)", (name, STARTING_CASH))
    conn.commit()

    print(f"\n Engine loaded. Trading actively on {list(initial_prices.keys())}.")

    for step in range(1, total_steps + 1):
        alive_bots = [n for n, m in population.items() if m["status"] == "Alive"]
        if not alive_bots:
            print(f"☠️ Step {step}: Complete market insolvency. All cash exhausted.")
            break

        target_ticker = random.choice(list(initial_prices.keys()))

        cursor.execute("SELECT last_price_sold FROM stocks WHERE stock_name = %s", (target_ticker,))
        live_price_res = cursor.fetchone()
        if not live_price_res: continue
        
        live_price = max(0.01, float(live_price_res[0]))

        history = asset_history_matrices[target_ticker]
        market_trend_signal = live_price / float(history[-1]) if history[-1] > 0 else 1.0
        macro_trend_signal = live_price / float(history[0]) if history[0] > 0 else 1.0
        velocity_signal = float(history[-1]) / float(history[-5]) if history[-5] > 0 else 1.0

        history.append(live_price)
        history.pop(0)

        breakeven_multiplier = (live_price + COMPUTE_COST) / live_price

        # --- SUBMIT ORDERS STAGE ---
        for name in alive_bots:
            meta = population[name]
            
            cursor.execute("UPDATE balances SET current_balance = current_balance - %s WHERE user_id = %s", (COMPUTE_COST, name))
            cursor.execute("SELECT current_balance FROM balances WHERE user_id = %s", (name,))
            wallet_balance = float(cursor.fetchone()[0])
            conn.commit()

            if wallet_balance <= 50.0: 
                meta["status"] = "Bankrupt"
                print(f" {name} went Bankrupt at Step {step} (Cash dried up)!")
                continue

            meta["experience_ticks"] += 1
            current_shares_held = portfolio_trackers[name][target_ticker]["total_shares_held"]
            
            input_vector = [market_trend_signal, macro_trend_signal, velocity_signal, float(current_shares_held) / 50.0, wallet_balance / STARTING_CASH]
            pred_multiplier = meta["brain"].forward(input_vector)
            calculated_bid_price = max(0.01, round(live_price * pred_multiplier, 4))

            shares = random.randint(1, 2)
            buy_probability = 1.0 / (1.0 + math.exp(-pred_multiplier + 1.0))
            action = "BUY" if buy_probability > 0.50 else "SELL"

            can_execute = True
            if action == "SELL" and portfolio_trackers[name][target_ticker]["total_shares_held"] < shares:
                can_execute = False  

            if can_execute:
                if action == "BUY" and wallet_balance >= (shares * calculated_bid_price):
                    cursor.execute("INSERT INTO buyer (buyer_id, stock_name, quantity, expected_price) VALUES (%s, %s, %s, %s)", (name, target_ticker, shares, calculated_bid_price))
                elif action == "SELL":
                    cursor.execute("INSERT INTO seller (seller_id, stock_name, quantity, expected_min_price) VALUES (%s, %s, %s, %s)", (name, target_ticker, shares, calculated_bid_price))
                else: can_execute = False
                conn.commit()

            if can_execute:
                match_market_orders(target_ticker)

        # --- PROCESSING MARKET OUTCOMES & VARIABLE DIVIDENDS ---
        cursor.execute("SELECT last_price_sold FROM stocks WHERE stock_name = %s", (target_ticker,))
        cleared_price_res = cursor.fetchone()
        cleared_price = max(0.01, float(cleared_price_res[0])) if cleared_price_res else live_price
        conn.commit()

        # Dynamic Dividend Calculation based on Company Performance
        price_performance_ratio = cleared_price / live_price
        if price_performance_ratio > 1.0:
            # High profits: base $0.05 yield + 2% bonus of capital growth per share
            dividend_per_share = 0.05 + round((cleared_price - live_price) * 0.02, 4)
        else:
            # Squeezed profits: Dividend slashed down to bare-minimum $0.01 (changeable) utility yield
            dividend_per_share = 0.10

        for name in alive_bots:
            if population[name]["status"] != "Alive": continue
            meta = population[name]
            
            # Retrieve accurate wallet balance following matching engine trades
            cursor.execute("SELECT current_balance FROM balances WHERE user_id = %s", (name,))
            fresh_wallet_balance = float(cursor.fetchone()[0])

            # Trade settlement matrix alignment
            if not can_execute:
                true_multiplier_target = breakeven_multiplier
                net_trade_profit = -COMPUTE_COST
            elif action == "BUY" and cleared_price <= calculated_bid_price:
                current_holdings = portfolio_trackers[name][target_ticker]["total_shares_held"]
                current_avg = portfolio_trackers[name][target_ticker]["avg_buy_price"]
                new_holdings = current_holdings + shares
                portfolio_trackers[name][target_ticker]["total_shares_held"] = new_holdings
                portfolio_trackers[name][target_ticker]["avg_buy_price"] = ((current_avg * current_holdings) + (cleared_price * shares)) / new_holdings
                net_trade_profit = -COMPUTE_COST
                true_multiplier_target = cleared_price / live_price if cleared_price > live_price else breakeven_multiplier
                try:
                    cursor.execute("UPDATE ownerships SET quantity = %s WHERE user_id = %s AND stock_name = %s", (new_holdings, name, target_ticker))
                except mysql.connector.Error: pass
            elif action == "SELL" and cleared_price >= calculated_bid_price:
                original_cost = portfolio_trackers[name][target_ticker]["avg_buy_price"]
                new_holdings = max(0, portfolio_trackers[name][target_ticker]["total_shares_held"] - shares)
                portfolio_trackers[name][target_ticker]["total_shares_held"] = new_holdings
                net_trade_profit = (cleared_price - COMPUTE_COST) - original_cost
                true_multiplier_target = cleared_price / live_price if net_trade_profit > 0 else 0.50
                try:
                    cursor.execute("UPDATE ownerships SET quantity = %s WHERE user_id = %s AND stock_name = %s", (new_holdings, name, target_ticker))
                except mysql.connector.Error: pass
            else:
                net_trade_profit = -COMPUTE_COST
                true_multiplier_target = breakeven_multiplier  

            # Variable Dividend Distribution Engine
            held_shares = portfolio_trackers[name][target_ticker]["total_shares_held"]
            dividend_earned = 0.0
            if held_shares > 0:
                dividend_earned = round(held_shares * dividend_per_share, 4)
                cursor.execute("UPDATE balances SET current_balance = current_balance + %s WHERE user_id = %s", (dividend_earned, name))
                fresh_wallet_balance += dividend_earned
            conn.commit()

            # Net Worth calculation (using true cash levels + live inventory values)
            current_portfolio_value = 0.0
            for tk in initial_prices.keys():
                cursor.execute("SELECT last_price_sold FROM stocks WHERE stock_name = %s", (tk,))
                p_res = cursor.fetchone()
                tk_live_price = float(p_res[0]) if p_res else initial_prices[tk]
                tk_shares = portfolio_trackers[name][tk]["total_shares_held"]
                current_portfolio_value += (tk_shares * tk_live_price)
            
            current_net_worth = fresh_wallet_balance + current_portfolio_value
            net_worth_delta = current_net_worth - baseline_net_worths[name]
            scaling_factor = 0.0001
            
            if net_worth_delta < 0:
                punishment_intensity = abs(net_worth_delta) * scaling_factor
                if net_trade_profit <= 0:
                    true_multiplier_target -= punishment_intensity
                else:
                    true_multiplier_target -= (punishment_intensity * 0.2)
            else:
                reward_intensity = net_worth_delta * scaling_factor
                if net_trade_profit > 0:
                    true_multiplier_target += reward_intensity

            true_multiplier_target = max(0.05, min(3.0, true_multiplier_target))
            meta["brain"].backpropagate(true_multiplier_target)

            meta["history_ledger"].append({
                "step": step, "ticker_traded": target_ticker, "action": action if can_execute else "HOLD_DENIED",
                "predicted_multiplier": round(pred_multiplier, 4), "actual_clearing_price": round(cleared_price, 4), 
                "net_profit_margin": round(net_trade_profit, 4), "dividend_earned": round(dividend_earned, 4)
            })

    # =====================================================================
    # COMPILING REPORT DATA GENERATION
    # =====================================================================
    summary_records = []
    for name, meta in population.items():
        cursor.execute("SELECT current_balance FROM balances WHERE user_id = %s", (name,))
        final_balance = float(cursor.fetchone()[0])
        
        final_portfolio_value = 0.0
        for tk in initial_prices.keys():
            cursor.execute("SELECT last_price_sold FROM stocks WHERE stock_name = %s", (tk,))
            p_res = cursor.fetchone()
            tk_price = float(p_res[0]) if p_res else initial_prices[tk]
            final_portfolio_value += (portfolio_trackers[name][tk]["total_shares_held"] * tk_price)
            
        final_net_worth = final_balance + final_portfolio_value
        status_tag = meta["status"] if final_balance > 50.0 else "Bankrupt"
        
        with open(os.path.join(JOURNAL_DIR, f"{name}_journal.json"), "w") as bot_file:
            json.dump({"agent_id": name, "status": status_tag, "journal": meta["history_ledger"]}, bot_file, indent=2)

        summary_records.append({
            "Agent_ID": name, "Status": status_tag, 
            "Final_Wallet_Cash": round(final_balance, 2),
            "Final_Total_NetWorth": round(final_net_worth, 2), 
            "Initial_Baseline": round(baseline_net_worths[name], 2),
            "Total_Trades": len(meta["history_ledger"]),
            "Model_Bias": round(meta["brain"].bias, 4)
        })

    df = pd.DataFrame(summary_records)
    df.to_csv(MASTER_CSV_REPORT, index=False)
    print("\n=====================================================================")
    print("            VARIABLE DIVIDENDS: FINAL LAB RESULTS                    ")
    print("=====================================================================")
    print(df.to_string(index=False))
    cursor.close()
    conn.close()

if __name__ == "__main__":
    start_macro_evolution_experiment()
