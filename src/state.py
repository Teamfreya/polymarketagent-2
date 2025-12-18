import json
import os
from datetime import datetime
from typing import List, Dict

STATE_FILE = "bot_state.json"

class BotState:
    def __init__(self):
        self.daily_pnl: float = 0.0
        self.consecutive_losses: int = 0
        self.skipped_signals_remaining: int = 0
        self.last_trade_date: str = datetime.utcnow().strftime("%Y-%m-%d")
        self.weekly_pnl: float = 0.0
        self.week_start_date: str = self.get_week_start()
        self.pause_until: str = "" # ISO format timestamp
        self.trades: List[Dict] = []
        self.active_position_event_id: str = None
        self.processed_events: List[str] = [] # List of event_ids we've already processed/traded/decided on
        self.pending_orders: List[Dict] = []  # Track orders awaiting confirmation

    def get_week_start(self) -> str:
        # Simple ISO week format for grouping
        return datetime.utcnow().strftime("%Y-W%W")

    def load(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
                    self.__dict__.update(data)
                
                # Check for day rollover
                current_date = datetime.utcnow().strftime("%Y-%m-%d")
                if current_date != self.last_trade_date:
                    print(f"New day detected. Resetting daily PnL (Prev: {self.daily_pnl})")
                    self.daily_pnl = 0.0
                    self.last_trade_date = current_date
                
                # Check for week rollover (simple check)
                current_week = self.get_week_start()
                if current_week != self.week_start_date:
                    print(f"New week detected. Resetting weekly PnL (Prev: {self.weekly_pnl})")
                    self.weekly_pnl = 0.0
                    self.week_start_date = current_week

            except Exception as e:
                print(f"Error loading state: {e}")

    def save(self):
        with open(STATE_FILE, 'w') as f:
            json.dump(self.__dict__, f, indent=4)

    def add_pending_order(self, order: Dict):
        """Store order immediately after ORDER_SENT to prevent duplicate trades"""
        self.pending_orders.append(order)
        # Mark event as processed immediately - prevents retry in subsequent cycles
        if order['event_id'] not in self.processed_events:
            self.processed_events.append(order['event_id'])
        self.save()
    
    def finalize_order(self, order_id: str, status: str, pnl: float = 0.0):
        """Update order status after async polling"""
        for order in self.pending_orders:
            if order['order_id'] == order_id:
                order['status'] = status
                order['finalized_at'] = datetime.utcnow().isoformat()
                if status == 'FILLED':
                    order['pnl'] = pnl
                break
        self.save()
    
    def get_pending_orders(self) -> List[Dict]:
        """Get all pending orders for async status checking"""
        return [o for o in self.pending_orders if o.get('status') == 'PENDING']
    
    def record_trade(self, trade: Dict):
        self.trades.append(trade)
        self.active_position_event_id = trade['event_id']
        if trade['event_id'] not in self.processed_events:
            self.processed_events.append(trade['event_id'])
        self.save()

    def update_pnl(self, pnl: float, won: bool):
        self.daily_pnl += pnl
        self.weekly_pnl += pnl
        self.active_position_event_id = None
        
        if won:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            if self.consecutive_losses >= 3:
                print("WARNING: 3 consecutive losses. Activating streak protection (skip next 5 signals).")
                self.skipped_signals_remaining = 5
        
        self.save()

    def consume_skip(self):
        if self.skipped_signals_remaining > 0:
            self.skipped_signals_remaining -= 1
            # If we just finished skipping, reset consecutive losses? 
            # The prompt says: "After 5 valid signals have been skipped, reset the loss streak counter"
            if self.skipped_signals_remaining == 0:
                self.consecutive_losses = 0
            self.save()

    def is_paused(self) -> bool:
        if not self.pause_until:
            return False
        pause_end = datetime.fromisoformat(self.pause_until)
        if datetime.utcnow() < pause_end:
            return True
        # Pause expired
        self.pause_until = ""
        self.save()
        return False

    def check_safety_locks(self) -> bool:
        # Returns True if trading is BLOCKED
        if self.daily_pnl <= -80:  # Updated for $20 trades (4 losses)
            print(f"Daily Loss Limit Hit: {self.daily_pnl}")
            return True
        
        if self.weekly_pnl <= -120:  # Updated for $20 trades (6 losses)
            if not self.is_paused():
                print(f"Weekly Loss Limit Hit: {self.weekly_pnl}. Pausing 48h.")
                # Set pause
                from datetime import timedelta
                self.pause_until = (datetime.utcnow() + timedelta(hours=48)).isoformat()
                self.save()
            return True

        if self.is_paused():
            print(f"Trading paused until {self.pause_until}")
            return True

        return False

    def can_trade(self, event_id: str) -> bool:
        # Check Global Locks
        if self.check_safety_locks():
            return False
            
        # Check if already handled
        if event_id in self.processed_events:
            print(f"Event {event_id} already processed/traded.")
            return False
            
        # Check Streak Protection
        if self.skipped_signals_remaining > 0:
            print(f"Streak Protection: Skipping signal ({self.skipped_signals_remaining} remaining).")
            self.consume_skip()
            # We treat this signal as 'used' (skipped)
            self.processed_events.append(event_id)
            self.save()
            return False
            
        return True
