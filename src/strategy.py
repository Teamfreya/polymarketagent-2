from typing import Dict, Optional, Tuple
from datetime import datetime, timezone
import pytz
from .ema_filter import get_btc_2h_ema13

class Strategy:
    def __init__(self):
        self.min_time = 4  # 4-10 minute window
        self.max_time = 10
        
        # Adaptive Strategy: Weekday vs Weekend
        # Weekend (Sat/Sun): Low volatility, use Drift mode
        # Weekday (Mon-Fri): Higher volatility, use Strict mode
        current_day = datetime.now(timezone.utc).weekday()  # Monday=0, Sunday=6
        is_weekend = current_day >= 5  # Saturday=5, Sunday=6
        
        if is_weekend:
            # Weekend: Drift Strategy (Capture slow moves)
            self.min_spread = 10  # ≥$10 difference
            self.min_momentum = 0  # ≥+$0 (Any positive/negative)
            self.strategy_mode = "Weekend Drift"
        else:
            # Weekday: Strict Strategy (Filter noise)
            self.min_spread = 40  # ≥$40 difference
            self.min_momentum = 25  # ≥+$25 (UP) or ≤-$25 (DOWN)
            self.strategy_mode = "Weekday Strict"
        
        self.trade_amount = 25.0  # Fixed 25 shares per trade
        # Entry price cap is handled in bot.py (Removed)
        
        print(f"Strategy Mode: {self.strategy_mode} (Spread ${self.min_spread}, Momentum ${self.min_momentum})")
        
        # EMA13 2H trend filter cache
        self.ema13_2h = None
        self.ema13_last_update = None
        
        # Simplified Trading Hours (Copenhagen/CET timezone)
        self.allowed_hours_cet = [
            (2, 9),    # 02:00 - 09:00 CET
            (10, 17),  # 10:00 - 17:00 CET
        ]

    def check_entry(self, 
                    btc_price: float, 
                    btc_price_90s_ago: Optional[float],
                    strike_price: float,
                    minutes_remaining: int,
                    event_id: str,
                    has_open_position: bool,
                    volatility: float = 0.0) -> Tuple[bool, Optional[str], Optional[str]]:
        
        # Returns (should_trade, direction, reason_if_any)
        
        # 1. Time-of-Day Filter (DISABLED - Trading 24/7)
        # cet_tz = pytz.timezone('Europe/Copenhagen')
        # current_time_cet = datetime.now(cet_tz)
        # current_hour_cet = current_time_cet.hour
        
        # Check if current hour is in allowed trading windows
        # is_allowed = False
        # for start_hour, end_hour in self.allowed_hours_cet:
        #     if start_hour <= current_hour_cet < end_hour:
        #         is_allowed = True
        #         break
        
        # if not is_allowed:
        #     return False, None, f"Outside trading hours: {current_hour_cet}:00 CET (allowed: 02-09, 10-17)"
        
        # 2. Position Check
        if has_open_position:
            return False, None, "Already active in this event"

        # 3. Time Filter
        if not (self.min_time <= minutes_remaining <= self.max_time):
            return False, None, f"Time {minutes_remaining} not in [{self.min_time}, {self.max_time}]"

        # 4. Spread Filter & Direction Logic
        # Calculate dynamic spread threshold based on volatility
        # Formula: max(40, min(180, 0.5 * vol))
        self.min_spread = max(40, min(180, 0.5 * volatility))

        # Direction determined by relationship to Strike Price (BTC > Strike = UP)
        diff = btc_price - strike_price
        abs_diff = abs(diff)
        
        if abs_diff < self.min_spread:
            return False, None, f"Spread {abs_diff:.2f} < {self.min_spread:.2f} (Vol: ${volatility:.2f})"

        if diff == 0:
            return False, None, "Price at strike (diff=0)"

        direction = "UP" if diff > 0 else "DOWN"

        # 5. EMA Trend Confirmation
        # Update EMA13 every 30 minutes
        now = datetime.now(timezone.utc)
        if self.ema13_last_update is None or (now - self.ema13_last_update).total_seconds() > 1800:
            self.ema13_2h = get_btc_2h_ema13()
            self.ema13_last_update = now
            
        if self.ema13_2h is None:
            return False, None, "EMA13 data unavailable"

        # EMA must agree with direction
        if direction == "UP":
            if btc_price <= self.ema13_2h:
                return False, None, f"Trend Mismatch: Direction UP but BTC ${btc_price:,.0f} <= EMA ${self.ema13_2h:,.0f}"
        else: # DOWN
            if btc_price >= self.ema13_2h:
                return False, None, f"Trend Mismatch: Direction DOWN but BTC ${btc_price:,.0f} >= EMA ${self.ema13_2h:,.0f}"

        # 6. Momentum Filter (Must align with Strike Direction)
        # 6. Momentum Filter (DISABLED)
        # if btc_price_90s_ago is None:
        #     return False, None, "No 90s history"
            
        # delta_90s = btc_price - btc_price_90s_ago

        # if direction == "UP":
        #     # Must have positive momentum
        #     if delta_90s < self.min_momentum:
        #         return False, None, f"UP Momentum {delta_90s:.2f} < {self.min_momentum}"
        # else: # DOWN
        #     # Must have negative momentum
        #     if delta_90s > -self.min_momentum:
        #         return False, None, f"DOWN Momentum {delta_90s:.2f} > -{self.min_momentum}"

        # All checks passed
        return True, direction, "Signal Valid"
