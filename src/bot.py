import time
import re
import os
import sys
from datetime import datetime, timedelta, timezone
import requests
from dotenv import load_dotenv
from dateutil import parser
from py_clob_client.client import ClobClient
from src.client import PolyClient
from src.state import BotState
from src.data_sources import PolymarketFetcher, BinancePriceSource
from .strategy import Strategy
import json

# Load env in case provided at runtime
load_dotenv()

# --- User Provided Helpers ---
SERIES_SLUG = "btc-up-or-down-15m"

def get_current_btc_15m_event():
    now = datetime.now(timezone.utc)
    
    # Calculate the start of the current 15-minute window
    # Floor minutes to nearest 15
    floor_min = (now.minute // 15) * 15
    start_dt = now.replace(minute=floor_min, second=0, microsecond=0)
    
    # Timestamp is int seconds
    start_ts = int(start_dt.timestamp())
    
    # Construct Slug
    # Pattern: btc-updown-15m-{timestamp}
    # Example: btc-updown-15m-1765565100
    slug = f"btc-updown-15m-{start_ts}"
    
    print(f"DEBUG: derived slug {slug} for time {start_dt}")

    # Fetch directly
    try:
        resp = requests.get(
            "https://gamma-api.polymarket.com/events",
            params={"slug": slug},
            timeout=10,
        )
        resp.raise_for_status()
        events = resp.json()
    except Exception as e:
        print(f"Error fetching exact gamma event: {e}")
        return None

    if events:
        print(f"DEBUG: Found precise match for {slug}")
        return events[0]
        
    print(f"DEBUG: Precise slug {slug} not found. Searching next window...")
    return None

def get_live_quotes_from_gamma(event):
    m = event["markets"][0]
    # Handle clobTokenIds parsing safely
    try:
        tokens = json.loads(m["clobTokenIds"]) if isinstance(m["clobTokenIds"], str) else m["clobTokenIds"]
        outcomes = json.loads(m["outcomes"]) if isinstance(m["outcomes"], str) else m["outcomes"]
    except:
        tokens = []
        outcomes = []
        
    return {
        "event_id": event["id"],
        "market_id": m["id"],
        "slug": event["slug"],
        "start": event.get("eventStartTime") or event.get("startTime"),
        "end": event["endDate"],
        "bestBid": float(m.get("bestBid", 0) or 0),
        "bestAsk": float(m.get("bestAsk", 0) or 0),
        "spread": float(m.get("spread", 0) or 0),
        "tokens": tokens,   # [UpToken, DownToken]
        "outcomes": outcomes,     # ["Up","Down"]
        "groupItemTitle": m.get("groupItemTitle")
    }
# -----------------------------

class Bot:
    def __init__(self):
        self.state = BotState()
        self.state.load()
        
        self.btc_source = BinancePriceSource()
        self.poly_fetcher = PolymarketFetcher()
        self.poly_fetcher = PolymarketFetcher()
        self.strategy = Strategy()
        self.time_offset = None
        self.last_check_time = 0

        
        try:
            self.client = PolyClient()
        except Exception as e:
            print(f"Failed to init PolyClient: {e}")
            sys.exit(1)

        self.last_run_time = 0
        self.active_event_id = None
        self.active_strike = None
        
        # Multi-layer duplicate prevention
        import threading
        import time
        
        # Layer 1: Cycle-level mutex (prevents overlapping run_cycle_full calls)
        self._cycle_lock = threading.Lock()
        
        # Layer 2: Event-level reservation (atomic in-flight tracking with expiry)
        self._trade_lock = threading.Lock()  # Protects _in_flight and _attempted_keys
        self._in_flight = {}  # Dict[event_id] = timestamp for expiry tracking
        
        # Layer 3: Idempotency tracking (permanent record of attempts)
        self._attempted_keys = set()  # Keys: "event_id:direction"
        
        # Strike price cache: event_id -> strike_price
        # Ensures consistent strike price for each event across all cycles
        self._strike_cache = {}

    def sync_clock(self):
        try:
            print("Syncing clock with Coinbase API...")
            resp = requests.get("https://api.coinbase.com/v2/time", timeout=5)
            data = resp.json()['data']
            # strptime handling Z
            # Python < 3.11 fromisoformat might not handle Z easily? 
            # replace Z with +00:00 is safer
            iso = data['iso'].replace('Z', '+00:00')
            server_time = datetime.fromisoformat(iso)
            
            system_time = datetime.now(timezone.utc)
            self.time_offset = server_time - system_time
            print(f"Time Sync: Offset is {self.time_offset}")
        except Exception as e:
            print(f"Time Sync Failed: {e}. Defaulting to 0 offset.")
            self.time_offset = timedelta(0)

    def cleanup_expired_reservations(self):
        """Remove in-flight reservations older than 300 seconds."""
        import time
        cutoff = time.time() - 300
        with self._trade_lock:
            expired = [eid for eid, ts in self._in_flight.items() if ts < cutoff]
            for eid in expired:
                del self._in_flight[eid]
            if expired:
                print(f"Cleaned up {len(expired)} expired reservations")
    
    def try_reserve_event(self, event_id: str) -> bool:
        """
        Atomically check and reserve event for processing.
        Returns False if event is already in-flight.
        """
        import time
        with self._trade_lock:
            if event_id in self._in_flight:
                return False
            self._in_flight[event_id] = time.time()
            return True
    
    def release_event(self, event_id: str):
        """Release event reservation (e.g., on order failure before submission)."""
        with self._trade_lock:
            self._in_flight.pop(event_id, None)
    
    def mark_attempt(self, event_id: str, direction: str):
        """
        Mark (event_id:direction) as attempted after successful order placement.
        This provides idempotency - prevents retry of same direction.
        """
        key = f"{event_id}:{direction}"
        with self._trade_lock:
            self._attempted_keys.add(key)


    def parse_strike_price(self, title: str) -> float:
        """
        Parse strike price from various title formats:
        - "Bitcoin Up or Down - December 16, 2:30AM-2:45AM ET"
        - "BTC > 98500.55 on Dec..."
        - Extract from market description if needed
        """
        import re
        
        # Try pattern 1: "BTC > NUMBER" or "Bitcoin > NUMBER"
        match = re.search(r'(?:BTC|Bitcoin)\s*>\s*\$?([\d,]+\.?\d*)', title, re.IGNORECASE)
        if match:
            s = match.group(1).replace(',', '')
            return float(s)
        
        # Try pattern 2: Extract from quotes/event data instead
        # The strike price might be in the event description, not title
        # For "Bitcoin Up or Down" markets, we need to get it from the market data
        print(f"DEBUG: Could not parse strike from title: {title}")
        return 0.0

    def get_market_prices(self, clob_id: str) -> dict:
        # Fetch orderbook or ticker
        # We need "Yes" and "No" prices.
        # Use get_market from client?
        m = self.client.get_market(clob_id)
        # m structure depends on library. 
        # Usually has 'rewards'? Or we call get_ticker?
        # Let's try to get mid price or best ask.
        # For simplicity, we want Best Ask (Buy Price).
        # We place LIMIT order at best ask.
        # But we need "Price Up" and "Price Down".
        
        # Actually we can get the ticker via request to CLOB API if library is tricky
        # But `get_market` returns market info.
        # We need the Orderbook to know the Ask.
        return m


    def check_pending_orders(self):
        """Poll status of pending orders and finalize them asynchronously"""
        pending = self.state.get_pending_orders()
        
        if not pending:
            return
        
        for order in pending:
            try:
                status = self.client.get_order_status(order['order_id'])
                order_status = status.get('status') if status else None
                
                if order_status == 'MATCHED':
                    print(f"✓ Order {order['order_id']} FILLED for event {order['event_id']}")
                    self.state.finalize_order(order['order_id'], 'FILLED')
                    # Record as trade
                    trade_record = {
                        'event_id': order['event_id'],
                        'token_id': order['token_id'],
                        'direction': order['direction'],
                        'entry_price': order['entry_price'],
                        'size': order['size'],
                        'timestamp': order['timestamp'],
                        'status': 'OPEN'
                    }
                    self.state.record_trade(trade_record)
                    
                elif order_status in ['CANCELLED', 'EXPIRED']:
                    print(f"✗ Order {order['order_id']} {order_status} for event {order['event_id']}")
                    self.state.finalize_order(order['order_id'], order_status)
                    
            except Exception as e:
                print(f"Error checking order {order['order_id']}: {e}")

    def run(self):
        print("Bot Started (Minute-by-Minute Mode).")
        import traceback
        while True:
            try:
                self.run_cycle_full()
            except Exception as e:
                print(f"Error in cycle: {e}")
                traceback.print_exc()
            
            # Faster Scan: Sleep 15s
            sleep_sec = 15
            
            print(f"Sleeping {sleep_sec}s...")
            time.sleep(sleep_sec)

    def log_activity(self, row_data):
        file_exists = os.path.isfile("activity_log.csv")
        with open("activity_log.csv", "a") as f:
            headers = ["Timestamp", "EventID", "MinutesLeft", "BTC_Price", "Strike", "Diff", "Momentum", "Action", "Details", "Balance"]
            if not file_exists:
                f.write(",".join(headers) + "\n")
            
            # map row_data to headers
            # default to empty string if missing
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            line = [
                timestamp,
                str(row_data.get("EventID", "")),
                f"{row_data.get('MinutesLeft', 0):.2f}",
                f"{row_data.get('BTC_Price', 0):.2f}",
                f"{row_data.get('Strike', 0):.2f}",
                f"{row_data.get('Diff', 0):.2f}",
                f"{row_data.get('Momentum', 0):.2f}",
                row_data.get("Action", ""),
                row_data.get("Details", "").replace(",", ";"), # Escape commas
                str(row_data.get("Balance", ""))
            ]
            f.write(",".join(line) + "\n")




    def run_cycle_full(self):
        # Generate unique cycle ID for debugging
        import uuid
        cycle_id = str(uuid.uuid4())[:8]
        
        # Layer 1: Cycle-level mutex (non-blocking)
        # Prevents overlapping run_cycle_full() calls
        if not self._cycle_lock.acquire(blocking=False):
            print(f"[{cycle_id}] SKIP: Cycle overlap detected, another cycle is running")
            return
        
        try:
            # Check pending orders asynchronously
            self.check_pending_orders()
            
            # Cleanup expired in-flight reservations (>300s old)
            self.cleanup_expired_reservations()
            
            # 1. BTC Data
            btc_price = self.btc_source.fetch_price()
            if not btc_price: return
        
            # 2. State Check
            if self.state.check_safety_locks(): 
                return

            if self.time_offset is None:
                 self.sync_clock()
            
            # Adjust 'now'
            now = datetime.now(timezone.utc) + self.time_offset
        
            # 3. Market Discovery
            event = get_current_btc_15m_event()
            if not event:
                return

            # 4. Get Reliable Quotes (Gamma)
            quotes = get_live_quotes_from_gamma(event)
            
            event_id = quotes['event_id']
            title = event['title']
            minutes_remaining = (datetime.fromisoformat(quotes['end'].replace("Z", "+00:00")) - now).total_seconds() / 60
            
            print(f"[{cycle_id}] DEBUG: Processing {event_id} | {minutes_remaining:.2f}m left")
            
            # Check strike price cache first
            strike = self._strike_cache.get(event_id, 0.0)
            if strike > 0:
                print(f"[{cycle_id}] DEBUG: Using cached strike price: ${strike:.2f}")
            else:
                # Parse Strike from quotes or event
                # For "Bitcoin Up or Down" markets, the strike price is the BTC price at market START
                # We need to fetch it from Binance historical data at the start time
                
                # Try to extract from groupItemTitle (if available)
                group_title = quotes.get('groupItemTitle', '')
                if group_title:
                    match = re.search(r'\$([\d,]+\.?\d*)', group_title)
                    if match:
                        strike = float(match.group(1).replace(',', ''))
                        print(f"[{cycle_id}] DEBUG: Extracted strike from groupItemTitle: ${strike:.2f}")
                
                if strike == 0.0:
                    # Try parsing from title
                    strike = self.parse_strike_price(title)
                
                if strike == 0.0:
                    # Try event description
                    desc = event.get('description', '')
                    if desc:
                        match = re.search(r'\$([\d,]+\.?\d*)', desc)
                        if match:
                            strike = float(match.group(1).replace(',', ''))
                            print(f"[{cycle_id}] DEBUG: Extracted strike from description: ${strike:.2f}")
                
                if strike == 0.0:
                    # For Up/Down markets, get BTC price at market start time from Binance
                    # Parse start time from quotes
                    start_time_str = quotes.get('start', '')
                    if start_time_str:
                        try:
                            # Fetch BTC price at that time using existing method
                            strike = self.btc_source.get_historical_price(start_time_str)
                            if strike and strike > 0:
                                print(f"[{cycle_id}] DEBUG: Fetched strike from Binance at market start: ${strike:.2f}")
                            else:
                                strike = 0.0
                        except Exception as e:
                            print(f"[{cycle_id}] ERROR: Could not fetch strike at start time: {e}")
                            strike = 0.0
                
                if strike == 0.0:
                    print(f"[{cycle_id}] WARNING: Cannot find strike price, using current BTC: {btc_price}")
                    strike = btc_price
                
                # Cache the strike price for this event
                self._strike_cache[event_id] = strike
                print(f"[{cycle_id}] DEBUG: Cached strike price for event {event_id}: ${strike:.2f}")
            
            print(f"[{cycle_id}] DEBUG: Final strike price: ${strike:.2f}")
            
            # Check if we have an open position
            has_pos = any(t['event_id'] == event_id and t['status'] == 'OPEN' for t in self.state.trades)
            
            # Get clob_token_ids
            clob_ids = quotes.get('tokens', [])
            print(f"[{cycle_id}] DEBUG: clob_ids from quotes.get('tokens'): {clob_ids}")
            if len(clob_ids) < 2:
                print(f"[{cycle_id}] DEBUG: Not enough clob_ids ({len(clob_ids)}), returning")
                return
            
            # Get 90s-ago price
            btc_90s = self.btc_source.get_price_90s_ago()
            
            # Get price context by querying CLOB for both tokens
            try:
                up_token_id = clob_ids[0] if len(clob_ids) >= 2 else None
                down_token_id = clob_ids[1] if len(clob_ids) >= 2 else None
                
                up_bid = self.client.client.get_price(up_token_id, side="BUY") if up_token_id else 0
                up_ask = self.client.client.get_price(up_token_id, side="SELL") if up_token_id else 0
                down_bid = self.client.client.get_price(down_token_id, side="BUY") if down_token_id else 0
                down_ask = self.client.client.get_price(down_token_id, side="SELL") if down_token_id else 0
                price_context = f" [UP bid/ask: {up_bid}/{up_ask} | DOWN bid/ask: {down_bid}/{down_ask}]"
            except Exception as e:
                print(f"[{cycle_id}] Warning: Could not fetch price context: {e}")
                price_context = ""
            
            # Fetch Volatility
            volatility = self.btc_source.get_btc_volatility()
            print(f"[{cycle_id}] DEBUG: BTC Volatility (Median 1h): ${volatility:.2f}")

            should_trade, direction, reason = self.strategy.check_entry(
                btc_price=btc_price,
                btc_price_90s_ago=btc_90s,
                strike_price=strike,
                minutes_remaining=minutes_remaining,
                event_id=event_id,
                has_open_position=has_pos,
                volatility=volatility
            )
            
            # Calculate metrics for logging
            diff = btc_price - strike
            momentum = (btc_price - btc_90s) if btc_90s else 0.0
            
            # Get Balance
            bal = self.client.get_usdc_balance()
            
            if not should_trade:
                # Log all evaluations for complete 15-minute data tracking
                self.log_activity({
                    "EventID": event_id,
                    "MinutesLeft": minutes_remaining,
                    "BTC_Price": btc_price,
                    "Strike": strike,
                    "Diff": diff,
                    "Momentum": momentum,
                    "Action": "SKIP",
                    "Details": reason + price_context,
                    "Balance": bal
                })
                print(f"[{cycle_id}] Skipping: {reason} {price_context}")
                return

            # Valid Signal!
            
            # Check if we already traded THIS event (processed_events guard)
            if event_id in self.state.processed_events:
                 bal = self.client.get_usdc_balance()
                 self.log_activity({
                    "EventID": event_id,
                    "MinutesLeft": minutes_remaining,
                    "BTC_Price": btc_price,
                    "Strike": strike,
                    "Diff": diff,
                    "Momentum": momentum,
                    "Action": "WATCH",
                    "Details": "Already Traded - Monitoring" + price_context,
                    "Balance": bal
                 })
                 print(f"[{cycle_id}] Watching (Already Traded): {price_context}")
                 return
            
            # Layer 2: Event Reservation Gate
            # Atomically check if event is already being processed
            if not self.try_reserve_event(event_id):
                print(f"[{cycle_id}] SKIP: Event {event_id} already in-flight")
                return
            
            # Event is now reserved - must release on all early returns before ORDER_SENT
            print(f"[{cycle_id}] Event {event_id} reserved, proceeding with {direction} trade")
            
            # Handle Streak Skipping
            if not self.state.can_trade(event_id):
                 msg = f"Risk limit hit (Daily: {self.state.daily_pnl}, Streak: {self.state.consecutive_losses})"
                 self.log_activity({
                    "EventID": event_id,
                    "MinutesLeft": minutes_remaining,
                    "BTC_Price": btc_price,
                    "Strike": strike,
                    "Diff": diff,
                    "Momentum": momentum,
                    "Action": "PROTECT",
                    "Details": msg + price_context,
                    "Balance": bal
                 })
                 self.state.consume_skip()
                 self.state.processed_events.append(event_id)
                 self.state.save()
                 
                 # Release reservation
                 self.release_event(event_id)
                 return

            # Execute Trade
            import os
            import threading
            pid = os.getpid()
            tid = threading.get_ident()
            print(f"[{cycle_id}] [PID:{pid}|TID:{tid}] EXECUTING {direction} Trade on {title}")
            self.log_activity({
                    "EventID": event_id,
                    "MinutesLeft": minutes_remaining,
                    "BTC_Price": btc_price,
                    "Strike": strike,
                    "Diff": diff,
                    "Momentum": momentum,
                    "Action": "TRADE_EXEC",
                    "Details": f"[{cycle_id}] [PID:{pid}|TID:{tid}] Placing {direction} Order" + price_context
                })
            
            # Identify Tokens
            if not clob_ids:
                 print(f"[{cycle_id}] Error: No Token IDs found")
                 self.release_event(event_id)
                 return

            token_id = clob_ids[0] if direction == "UP" else clob_ids[1]
            
            # Get Best Ask
            try:
                ob = self.client.client.get_order_book(token_id)
                if not ob.asks:
                    print(f"[{cycle_id}] No asks available")
                    self.release_event(event_id)
                    return
                best_ask = float(ob.asks[0].price)
                best_bid = float(ob.bids[0].price) if ob.bids else 0.0
            except Exception as e:
                print(f"[{cycle_id}] Error fetching orderbook: {e}")
                self.release_event(event_id)
                return

            limit_price = best_ask 
            spread = best_ask - best_bid
            
            # Fixed 25 shares per trade
            if limit_price <= 0: 
                self.release_event(event_id)
                return
                
            size = 25.0  # Fixed share count
            size = round(size, 1) 
            
            # Calculate potential outcome
            potential_profit_per_share = 1.0 - limit_price
            max_profit = potential_profit_per_share * size
            roi_pct = (potential_profit_per_share / limit_price) * 100 if limit_price > 0 else 0
            
            print(f"[{cycle_id}] Placing Order: {direction} | Price: ${limit_price} | Size: {size}")
            print(f"[{cycle_id}] Stats: Return +${potential_profit_per_share:.2f}/share | Total Max Profit: +${max_profit:.2f} | ROI: {roi_pct:.1f}%")

            self.log_activity({
                    "EventID": event_id,
                    "MinutesLeft": minutes_remaining,
                    "BTC_Price": btc_price,
                    "Strike": strike,
                    "Diff": diff,
                    "Momentum": momentum,
                    "Action": "ORDER_PREP",
                    "Details": f"[{cycle_id}] Price: ${limit_price} | ROI: {roi_pct:.0f}% | MaxProfit: ${max_profit:.2f}" + price_context,
                    "Balance": bal
                })
            
            # DRY RUN Handling
            if os.getenv("DRY_RUN", "True").lower() in ("true", "1", "yes"):
                print(f"[{cycle_id}] [DRY RUN] Order NOT placed")
                self.release_event(event_id)
                return

            try:
                print(f"[{cycle_id}] DEBUG: sending order {token_id} {limit_price} {size}")
                resp = self.client.place_limit_order(token_id, "BUY", limit_price, size)
                print(f"[{cycle_id}] DEBUG: Order Response: {resp}")
            except Exception as e:
                print(f"[{cycle_id}] CRITICAL: Order Placement Failed: {e}")
                self.log_activity({
                    "EventID": event_id,
                    "MinutesLeft": minutes_remaining,
                    "BTC_Price": btc_price,
                    "Strike": strike,
                    "Diff": diff,
                    "Momentum": momentum,
                    "Action": "ORDER_ERROR",
                    "Details": str(e)
                })
                self.release_event(event_id)
                return
            
            if resp and resp.get('orderID'):
                print(f"[{cycle_id}] [PID:{pid}|TID:{tid}] Order Placed: {resp['orderID']}")
                self.log_activity({
                    "EventID": event_id,
                    "MinutesLeft": minutes_remaining,
                    "BTC_Price": btc_price,
                    "Strike": strike,
                    "Diff": diff,
                    "Momentum": momentum,
                    "Action": "ORDER_SENT",
                    "Details": f"[PID:{pid}|TID:{tid}] ID: {resp['orderID']}",
                    "Balance": bal
                })
                
                # IMMEDIATELY mark event as processed and store pending order
                # This prevents duplicate trades in subsequent cycles
                pending_order = {
                    'order_id': resp['orderID'],
                    'event_id': event_id,
                    'token_id': token_id,
                    'direction': direction,
                    'entry_price': limit_price,
                    'size': size,
                    'timestamp': datetime.utcnow().isoformat(),
                    'status': 'PENDING'  # Will be updated asynchronously
                }
                self.state.add_pending_order(pending_order)
                
                # Mark idempotency immediately
                self.mark_attempt(event_id, direction)
                
                # Keep event reserved permanently (never release)
                # Event stays in _in_flight until cleanup expires it (300s)
                
                print(f"[{cycle_id}] Event {event_id} marked as processed. Order {resp['orderID']} pending confirmation.")
                
            else:
                print(f"[{cycle_id}] CRITICAL: Order Placement Failed. Response: {resp}")
                self.log_activity({
                    "EventID": event_id,
                    "MinutesLeft": minutes_remaining,
                    "BTC_Price": btc_price,
                    "Strike": strike,
                    "Diff": diff,
                    "Momentum": momentum,
                    "Action": "ORDER_FAIL",
                    "Details": f"Resp: {str(resp)}",
                    "Balance": bal
                })
                self.release_event(event_id)
                return
        finally:
            # Release cycle lock
            self._cycle_lock.release()

