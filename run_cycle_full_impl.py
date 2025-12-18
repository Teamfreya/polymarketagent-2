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
            
            # Parse Strike from quotes or event
            strike = self.parse_strike_price(title)
            if strike == 0.0:
                return
            
            # Check if we have an open position
            has_pos = any(t['event_id'] == event_id and t['status'] == 'OPEN' for t in self.state.trades)
            
            # Get clob_token_ids
            clob_ids = quotes.get('clob_token_ids', [])
            if len(clob_ids) < 2:
                return
            
            # Get price context for logging
            up_bid = quotes.get('up_bid', 0)
            up_ask = quotes.get('up_ask', 0)
            down_bid = quotes.get('down_bid', 0)
            down_ask = quotes.get('down_ask', 0)
            price_context = f" [UP bid/ask: {up_bid}/{up_ask} | DOWN bid/ask: {down_bid}/{down_ask}]"
            
            # Get 90s-ago price
            btc_90s = self.btc_source.fetch_price_90s_ago()
            
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
                self.check_open_positions() # Check resolutions while waiting
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
            print(f"[{cycle_id}] EXECUTING {direction} Trade on {title}")
            self.log_activity({
                    "EventID": event_id,
                    "MinutesLeft": minutes_remaining,
                    "BTC_Price": btc_price,
                    "Strike": strike,
                    "Diff": diff,
                    "Momentum": momentum,
                    "Action": "TRADE_EXEC",
                    "Details": f"[{cycle_id}] Placing {direction} Order" + price_context
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
                print(f"[{cycle_id}] Order Placed: {resp['orderID']}")
                self.log_activity({
                    "EventID": event_id,
                    "MinutesLeft": minutes_remaining,
                    "BTC_Price": btc_price,
                    "Strike": strike,
                    "Diff": diff,
                    "Momentum": momentum,
                    "Action": "ORDER_SENT",
                    "Details": f"ID: {resp['orderID']}",
                    "Balance": bal
                })
                
                # Mark as PROCESSED PERMANENTLY
                self.state.processed_events.append(event_id)
                self.state.save()
                
                # Layer 3: Mark idempotency AFTER successful order
                self.mark_attempt(event_id, direction)
                
                # Keep event reserved (don't release) - prevents duplicate attempts
                # Event stays in _in_flight until cleanup expires it (300s) or market resolves
                
                # Check confirmation
                try:
                    status = self.client.get_order_status(resp['orderID'])
                    print(f"[{cycle_id}] DEBUG: Order Status: {status}")
                except Exception as e:
                    print(f"[{cycle_id}] Error fetching order status: {e}")
                    status = None

                # Check if order was matched/filled
                order_status = status.get('status') if status else None
                
                if order_status != 'MATCHED': 
                     print(f"[{cycle_id}] Order status {order_status}. Cancelling...")
                     try:
                        self.client.cancel_order(resp['orderID'])
                     except: pass
                     return
                
                # Filled
                trade_record = {
                    'event_id': event_id,
                    'token_id': token_id, 
                    'direction': direction,
                    'entry_price': limit_price,
                    'size': size,
                    'timestamp': datetime.utcnow().isoformat(),
                    'status': 'OPEN'
                }
                self.state.record_trade(trade_record)
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
