import websocket
import json
import threading
import time
from typing import Optional

class ChainlinkPriceSource:
    """
    Fetches BTC price from Polymarket's Chainlink oracle feed via RTDS WebSocket
    """
    def __init__(self):
        self.ws_url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        self.current_price = None
        self.last_update = None
        self.ws = None
        self.connected = False
        self.lock = threading.Lock()
        
    def on_message(self, ws, message):
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(message)
            
            # Check if it's a Chainlink BTC price update
            if (data.get('topic') == 'crypto_prices_chainlink' and 
                data.get('type') == 'update'):
                
                payload = data.get('payload', {})
                symbol = payload.get('symbol')
                
                if symbol == 'btc/usd':
                    with self.lock:
                        self.current_price = float(payload.get('value'))
                        self.last_update = payload.get('timestamp')
                    print(f"Chainlink BTC: ${self.current_price:,.2f}")
                    
        except Exception as e:
            print(f"Error processing message: {e}")
    
    def on_error(self, ws, error):
        print(f"WebSocket error: {error}")
    
    def on_close(self, ws, close_status_code, close_msg):
        print("WebSocket closed")
        self.connected = False
    
    def on_open(self, ws):
        """Subscribe to Chainlink BTC/USD feed when connection opens"""
        print("WebSocket connected, subscribing to Chainlink BTC/USD...")
        
        subscribe_msg = {
            "action": "subscribe",
            "subscriptions": [
                {
                    "topic": "crypto_prices_chainlink",
                    "type": "*",
                    "filters": "{\"symbol\":\"btc/usd\"}"
                }
            ]
        }
        
        ws.send(json.dumps(subscribe_msg))
        self.connected = True
        print("Subscribed to Chainlink BTC/USD feed")
    
    def connect(self):
        """Start WebSocket connection in background thread"""
        def run_ws():
            import ssl
            self.ws = websocket.WebSocketApp(
                self.ws_url,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close,
                on_open=self.on_open
            )
            # Disable SSL verification for testing
            self.ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})
        
        thread = threading.Thread(target=run_ws, daemon=True)
        thread.start()
        
        # Wait for connection
        for _ in range(10):
            if self.connected:
                break
            time.sleep(0.5)
    
    def fetch_price(self) -> Optional[float]:
        """Get current BTC price from Chainlink feed"""
        if not self.connected:
            self.connect()
            time.sleep(2)  # Wait for first price update
        
        with self.lock:
            return self.current_price
    
    def close(self):
        """Close WebSocket connection"""
        if self.ws:
            self.ws.close()

# Test
if __name__ == "__main__":
    print("Testing Chainlink price feed...")
    
    source = ChainlinkPriceSource()
    source.connect()
    
    # Wait for prices
    time.sleep(5)
    
    price = source.fetch_price()
    if price:
        print(f"\n✅ Current BTC price (Chainlink): ${price:,.2f}")
    else:
        print("\n❌ No price received")
    
    source.close()
