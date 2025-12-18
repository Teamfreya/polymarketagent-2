import requests
import time
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone
from dateutil import parser

class BinancePriceSource:
    def __init__(self):
        self.base_url = "https://api.binance.com"

    def fetch_price(self) -> Optional[float]:
        # Binance API
        url = f"{self.base_url}/api/v3/ticker/price?symbol=BTCUSDT"
        try:
            resp = requests.get(url, timeout=5)
            data = resp.json()
            price = float(data['price'])
            return price
        except Exception as e:
            print(f"Error fetching BTC price from Binance: {e}")
            return None

    def get_price_90s_ago(self) -> Optional[float]:
        # Fetch historical candle from API
        # Target time: 90 seconds ago
        now = time.time()
        target_ts = now - 90
        
        # Floor to minute to find the relevant candle
        candle_start_ts = int(target_ts) - (int(target_ts) % 60)
        
        # We need the candle that *contains* this time, or closest?
        # Actually, using the Close of the minute that covers 90s ago is reasonable.
        
        try:
            # Kline params: startTime in ms
            ts_ms = candle_start_ts * 1000
            
            url = f"{self.base_url}/api/v3/klines"
            params = {
                "symbol": "BTCUSDT",
                "interval": "1m",
                "startTime": ts_ms,
                "limit": 1
            }
            resp = requests.get(url, params=params, timeout=5)
            data = resp.json()
            # [ [ openTime, open, high, low, close, volume, closeTime ... ], ... ]
            
            if data and isinstance(data, list) and len(data) > 0:
                # Return Close price of that candle
                # Check if candle time matches expected
                candle_open_time = data[0][0] # ms
                if abs(candle_open_time - ts_ms) < 60000:
                   return float(data[0][4]) # Close
                
        except Exception as e:
             print(f"Error fetching 90s ago price: {e}")
             
        return None

    def get_historical_price(self, time_iso: str) -> Optional[float]:
        # Parse time to timestamp (ms)
        try:
            dt = parser.isoparse(time_iso)
            ts = int(dt.timestamp() * 1000)
            
            url = f"{self.base_url}/api/v3/klines"
            params = {
                "symbol": "BTCUSDT",
                "interval": "1m",
                "startTime": ts,
                "limit": 1
            }
            resp = requests.get(url, params=params, timeout=5)
            data = resp.json()
            # [ [ openTime, open, high, low, close, volume, closeTime, ... ] ]
            if data and isinstance(data, list) and len(data) > 0:
                # Use Open price for Strike determination (start of event)
                return float(data[0][1])
        except Exception as e:
             print(f"Error fetching historical price from Binance: {e}")
        return None

    def get_btc_volatility(self) -> float:
        # Median True Range over last ~5 hours (20 x 15m), excluding current candle
        try:
            url = f"{self.base_url}/api/v3/klines"
            params = {"symbol": "BTCUSDT", "interval": "15m", "limit": 21}  # 20 completed + 1 current
            resp = requests.get(url, params=params, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list) or len(data) < 3:
                return 0.0

            completed = data[:-1]  # exclude current forming candle
            trs = []
            prev_close = None

            for c in completed:
                high = float(c[2])
                low = float(c[3])
                close = float(c[4])

                if prev_close is None:
                    tr = high - low
                else:
                    tr = max(
                        high - low,
                        abs(high - prev_close),
                        abs(low - prev_close),
                    )

                trs.append(tr)
                prev_close = close

            if not trs:
                return 0.0

            import statistics
            return statistics.median(trs)

        except Exception as e:
            print(f"Error fetching volatility: {e}")
            return 0.0

class PolymarketFetcher:
    def __init__(self):
        self.gamma_url = "https://gamma-api.polymarket.com/events"

    def get_target_resolution_time(self) -> datetime:
        # Returns the next quarter-hour timestamp (UTC)
        # e.g. if 14:07, returns 14:15
        now = datetime.now(timezone.utc)
        # Round up to next 15 min
        minutes = now.minute
        remainder = 15 - (minutes % 15)
        # If exactly on step, maybe look ahead? But usually we are mid-window.
        if remainder == 0:
            remainder = 15 # Look at next one
        
        target = now + timedelta(minutes=remainder)
        target = target.replace(second=0, microsecond=0)
        return target

    def find_current_market(self, now_time: datetime = None):
        # Calculate target resolution time based on provided now_time or system utc
        if now_time is None:
            now_time = datetime.now(timezone.utc)
            
        # Target: Next 15 minute interval
        # e.g. 10:04 -> 10:15
        # e.g. 10:16 -> 10:30
        
        # Round up to next 15m
        delta = 15 - (now_time.minute % 15)
        # If delta is small (e.g. 0), we might want the *next* one? 
        # But usually we trade the one expiring soon.
        # But if it's too close (<3 mins), strategy filters it.
        # So we target the immediate next 15m mark.
        
        # Careful: if seconds > 0, we are already past the minute.
        target_time = (now_time + timedelta(minutes=delta)).replace(second=0, microsecond=0)
        
        # If target is same as now (unlikely due to seconds), add 15m?
        if target_time <= now_time:
             target_time += timedelta(minutes=15)
             
        # print(f"DEBUG: Calculated Target Resolution: {target_time}")
        target_iso = target_time.isoformat().replace("+00:00", "Z") # Gamma matches this format roughly?
        
        # Gamma API query
        # Filter is tricky, let's just get nearby expiring events and filter locally
        # Sort by endDate ascending
        params = {
            "limit": 100,
            "active": "true",
            "closed": "false",
            "order": "endDate",
            "ascending": "true",
        }
        # Debug time
        # print(f"DEBUG: System Time (UTC): {datetime.utcnow()}")
        # print(f"DEBUG: Target Time (UTC): {target_time}")

        # Optimizing query:
        params['limit'] = 100
        
        # Proper ISO format for Gamma: YYYY-MM-DDTHH:MM:SSZ
        now_iso = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        params['start_date_min'] = now_iso
        
        # print(f"DEBUG: Querying Gamma with start_date_min={now_iso}")
        
        try:
            # Slug format: btc-updown-15m-{ts}
            # IMPT: The slug uses the START time, but target_time is the END time (Resolution).
            # So subtract 15 minutes (900 seconds)
            start_ts = int(target_time.timestamp()) - 900
            target_slug = f"btc-updown-15m-{start_ts}"
            print(f"DEBUG: Searching by Slug: {target_slug}")
            
            slug_params = {"slug": target_slug}
            resp_slug = requests.get(self.gamma_url, params=slug_params, timeout=5)
            if resp_slug.status_code == 200:
                slug_events = resp_slug.json()
                if slug_events:
                    print(f"FOUND MATCH via Slug: {slug_events[0].get('title')}")
                    return slug_events[0]
            
            # 2. Fallback: Scan active markets (Broadest)
            # Remove strict date filters that depend on system time which might be skewed.
            params['limit'] = 100
            if 'start_date_min' in params: del params['start_date_min'] 
            
            resp = requests.get(self.gamma_url, params=params, timeout=5)
            events = resp.json()
            # print(f"DEBUG: Gamma returned {len(events)} events (Scan).") 
            
            # Filter client-side for BTC
            btc_events = []
            for e in events:
                title = e.get('title', '')
                if "BTC" in title or "Bitcoin" in title:
                     btc_events.append(e)
            
            # Sort by endDate
            btc_events.sort(key=lambda x: x.get('endDate', ''))
            
            # Return the first one that ends in the future (relative to SOME reference)
            # If system time is wrong, we might just have to trust the top of the list 
            # is the current active one?
            if btc_events:
                # print(f"DEBUG: Best Candidate: {btc_events[0].get('title')} | End: {btc_events[0].get('endDate')}")
                return btc_events[0]
            
            # If still nothing, iterate original loop (legacy)
            for event in events:
                # Check title for "Bitcoin" or "BTC"
                title = event.get('title', '')
                # Strict check for the 15m pattern? 
                # User URL: btc-updown-15m... Title usually "BTC Price > X at H:M?"
                
                # Check end date
                end_date_str = event.get('endDate')
                if not end_date_str: continue

                if "BTC" not in title and "Bitcoin" not in title:
                    continue
                
                try:
                    end_date = parser.isoparse(end_date_str)
                    
                    if end_date.tzinfo is None:
                        end_date = end_date.replace(tzinfo=timezone.utc)
                    if target_time.tzinfo is None:
                        target_time = target_time.replace(tzinfo=timezone.utc)

                    diff = (end_date - target_time).total_seconds()
                    
                    # We need to match the specific 15m window.
                    # Tolerance: within 2 mins
                    if abs(diff) < 120:
                         print(f"FOUND MATCH: {title} | ID: {event.get('id')}")
                         return event
                except Exception as e:
                    continue

                if "BTC" not in title and "Bitcoin" not in title:
                    continue
                
                # print(f"DEBUG: Candidate: {title} | {end_date_str}")
                
                try:
                    end_date = parser.isoparse(end_date_str)
                    
                    if end_date.tzinfo is None:
                        end_date = end_date.replace(tzinfo=timezone.utc)
                    if target_time.tzinfo is None:
                        target_time = target_time.replace(tzinfo=timezone.utc)

                    diff = (end_date - target_time).total_seconds()
                    
                    # We need to match the specific 15m window.
                    # Tolerance: within 2 mins
                    if abs(diff) < 120:
                         print(f"FOUND MATCH: {title} | ID: {event.get('id')}")
                         return event
                except Exception as e:
                    continue
            
            return None


        except Exception as e:
            print(f"Error fetching markets: {e}")
            return None
