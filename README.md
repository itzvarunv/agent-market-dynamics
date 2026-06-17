# SQL Exchange + Neural Evolution Lab

A from-scratch stock exchange matching engine backed directly by SQL (no ORM), plus an evolutionary sandbox that spawns small neural-net trading bots and lets them trade against each other on that exchange to see what kind of behavior emerges.

```
Connector.py        -> raw MySQL/MariaDB connection (env-driven)
engine.py            -> the exchange itself: order books, matching, settlement, CLI dashboard
evolution_lab.py     -> spawns N bots with a tiny perceptron "brain" each, runs them for T steps,
                        logs per-bot journals + a summary CSV
tests/...            -> (2 test folders — see "Tests" below)
```

## How it fits together

1. **`Connector.py`** opens a `mysql.connector` connection using credentials from a `.env` file. Nothing fancy — every other file calls `get_connection()` to get a fresh connection per operation.
2. **`engine.py`** is a self-contained limit order book exchange:
   - `buyer` / `seller` tables act as the order books (price + quantity rows).
   - `match_market_orders(stock_name)` loops, grabbing the best bid and best ask for a ticker and clearing trades at the ask price while `bid >= ask`, updating `balances`, `ownerships`, `stocks.last_price_sold`, and writing notifications to `history`.
   - A CLI dashboard (`user_dashboard`) lets a "user" view holdings, place buy/sell orders, manage pending orders, or IPO a brand-new ticker straight from the terminal.
3. **`evolution_lab.py`** is the interesting part: it spins up `N` `NeuralBot_xxx` agents, each with a 5-input single-neuron "brain" (`TradingBrainNN`), gives them a randomized stock universe and starting cash, then for `T` steps:
   - charges every bot a flat `COMPUTE_COST` per step (an infrastructure tax),
   - has each bot look at short-term/long-term price trend + velocity + its own inventory/cash ratio, and decide BUY vs SELL,
   - submits the order, calls into `engine.py`'s real matching loop to clear it,
   - pays out a variable per-share dividend depending on whether the clearing price rose or fell since the bot's decision,
   - computes a "target multiplier" reward signal from trade P&L and net-worth delta vs. baseline, and nudges the bot's weights toward it with a manual gradient step (not real backprop — more like an adaptive linear controller).
   - At the end it dumps a `{bot}_journal.json` per bot (full step-by-step trade history) into `simulation_journals/`, and a `market_evolution_summary.csv` with final standings.

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file next to the scripts:

```
DB_HOST=localhost
DB_USER=your_user
DB_PASSWORD=your_password
DB_DATABASE=your_database
DB_SOCKET=/path/to/mysqld.sock   # or omit if connecting over TCP
```


```sql
CREATE TABLE stocks (
    stock_name VARCHAR(10) PRIMARY KEY,
    company_name VARCHAR(100) NOT NULL,
    last_price_sold DECIMAL(18,4) NOT NULL
);

CREATE TABLE balances (
    user_id VARCHAR(50) PRIMARY KEY,
    current_balance DECIMAL(18,4) NOT NULL
);

CREATE TABLE ownerships (
    user_id VARCHAR(50) NOT NULL,
    stock_name VARCHAR(10) NOT NULL,
    quantity INT NOT NULL,
    PRIMARY KEY (user_id, stock_name)
);

CREATE TABLE buyer (
    id INT AUTO_INCREMENT PRIMARY KEY,
    buyer_id VARCHAR(50) NOT NULL,
    stock_name VARCHAR(10) NOT NULL,
    quantity INT NOT NULL,
    expected_price DECIMAL(18,4) NOT NULL
);

CREATE TABLE seller (
    id INT AUTO_INCREMENT PRIMARY KEY,
    seller_id VARCHAR(50) NOT NULL,
    stock_name VARCHAR(10) NOT NULL,
    quantity INT NOT NULL,
    expected_min_price DECIMAL(18,4) NOT NULL
);

CREATE TABLE history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(50) NOT NULL,
    message VARCHAR(255) NOT NULL,
    is_shown VARCHAR(5) NOT NULL DEFAULT 'no'
);
```

(`schema.sql` in this repo has the same thing — pipe it straight into your DB.)

## Running it

```bash
# Interactive exchange — log in as any user_id, trade, IPO a ticker
python3 engine.py

# Evolutionary trading lab — prompts for bot count and step count
python3 evolution_lab.py
```

## Tests

This repo has two test folders — `tests/<name1>/` and `tests/<name2>/` *(placeholder — tell me the real names/scope and I'll fill these in properly, including what each one actually covers)*.


| Run | Config | Avg Final Net Worth | Avg Baseline | Avg Δ vs Baseline | Bankrupt |
|---|---|---|---|---|---|
| **1** — baseline | 6 bots, 200 steps | $5,709.86 | $11,247.33 | **−48.5%** | 0/6 |
| **2** — baseline | 10 bots, 500 steps | $6,151.32 | $12,751.22 | **−51.8%** | 0/10 |
| **3** — tweaked (epsilon-greedy) | 6 bots, 200 steps | $5,108.29 | $14,313.87 | **−64.1%** | 0/6 |

**The honest finding from Run 1 & 2:** every bot in every config lost value relative to its starting net worth, regardless of how long it ran. That's not random bad luck — `COMPUTE_COST` (a flat tax charged every single step to every bot, win or lose) is a constant drain, and the dividend yield isn't close to large enough to offset it at these share counts. Right now the evolutionary pressure mostly selects for "loses value slowest," not "actually profitable trading." Worth knowing before reading too much into `Model_Bias` as a measure of trading skill.

### What I tweaked, and what happened

The brain's action selection is pure exploitation — it always trusts whatever the perceptron currently outputs, with zero mechanism to ever try the option it's biased against. That's also exactly the gap between this and the RL agent you've mentioned wanting eventually: there's no exploration term at all right now.

I added decaying **epsilon-greedy exploration** (`evolution_lab_explore.py`) — early steps have up to a 30% chance of taking a random action instead of the brain's pick, decaying down to a 5% floor as the run progresses. Same 6-bot/200-step config as Run 1, only this one variable changed.

Result: it crashed the market. Final prices dropped to as low as **$0.038** (from a $25–150 starting range) on some tickers. The mechanism is straightforward once you see it: this exchange has no market maker and no outside liquidity — the only participants are the bots themselves. Any systematic excess of SELL actions (whether from real policy or injected randomness) pushes price down with nothing pushing back, and price has a floor at $0.01 but no ceiling-side recovery force. Random exploration was enough to tip the sell/buy balance and the market never recovered. That's a more interesting result than "the tweak made it better/worse" — it exposes that the simulation's price discovery is fragile to noise because there's no counterparty depth, which matters a lot if you're heading toward RL agents that will explore a lot more aggressively than this.

## Notes / things worth hardening before this goes further

- `match_market_orders` calls `conn.commit()` right after each `SELECT` specifically to drop read locks before the writes — but that also means there's a window between reading the best bid/ask and writing the fill where a second process touching the same ticker could interleave. Fine for a single-process CLI/sim; would need `SELECT ... FOR UPDATE` (or an app-level lock per ticker) before running this with real concurrency.
- `evolution_lab.py` imports `match_market_orders` in a `try/except ImportError` with a no-op fallback — if `engine.py` isn't importable for any reason, the lab silently runs with no trades ever clearing rather than failing loudly.
- `initialize_random_market` disables `FOREIGN_KEY_CHECKS` during setup, and there's no FK from `buyer.stock_name` / `seller.stock_name` back to `stocks.stock_name` in the inferred schema, so a typo'd ticker would sit in the order book forever with no error.
- `TradingBrainNN.backpropagate` is a single linear unit with clipped weights, not a network with hidden layers/backprop in the conventional sense — closer to an adaptive linear controller nudged toward a hand-computed target. Worth keeping in mind if you're benchmarking this against an eventual real RL policy — the bar it needs to clear is lower than "neural network" suggests.

## Repo structure

```
.
├── Connector.py
├── engine.py
├── evolution_lab.py
├── evolution_lab_explore.py     # epsilon-greedy variant used for Run 3
├── schema.sql
├── requirements.txt
├── sample_results/
│   ├── run1_6bots_200steps/
│   ├── run2_10bots_500steps/
│   └── run3_tweaked_epsilon_greedy_6bots_200steps/
└── tests/
    ├── <test_folder_1>/
    └── <test_folder_2>/
```
