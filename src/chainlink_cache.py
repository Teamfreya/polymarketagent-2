import json
import time
from datetime import datetime
from pathlib import Path
from chainlink_price import get_chainlink_feed

class ChainlinkPriceCache:
    """
    Caches Chainlink BTC/USD prices every minute.
    Allows historical lookups for strike price determination.
    """
    
    def __init__(self, cache_file="chainlink_price_cache.json"):
        self.cache_file = Path(__file__).parent.parent / cache_file
        self.feed = get_chainlink_feed()
        self.cache = self._load_cache()
        
    def _load_cache(self):
        """Load existing cache from disk"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def _save_cache(self):
        """Save cache to disk"""
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(self.cache, f)
        except Exception as e:
            print(f"Error saving price cache: {e}")
    
    def update(self):
        """
        Fetch current Chainlink price and cache it.
        Should be called every ~60 seconds.
        """
        price = self.feed.get_btc_price()
        if price:
            # Use minute-level timestamp as key
            timestamp = int(time.time())
            minute_key = str(timestamp - (timestamp % 60))
            
            self.cache[minute_key] = {
                'price': price,
                'timestamp': timestamp,
                'datetime': datetime.utcnow().isoformat()
            }
            
            # Keep only last 24 hours of data
            cutoff = timestamp - (24 * 3600)
            self.cache = {k: v for k, v in self.cache.items() if int(k) > cutoff}
            
            self._save_cache()
            return price
        return None
    
    def get_price_at_time(self, target_timestamp):
        """
        Get cached price closest to target timestamp.
        Returns: (price, actual_timestamp) or (None, None)
        """
        if not self.cache:
            return None, None
        
        # Find closest minute
        target_minute = target_timestamp - (target_timestamp % 60)
        
        # Try exact match first
        minute_key = str(target_minute)
        if minute_key in self.cache:
            entry = self.cache[minute_key]
            return entry['price'], entry['timestamp']
        
        # Find closest within ±2 minutes
        for offset in range(-120, 121, 60):
            check_key = str(target_minute + offset)
            if check_key in self.cache:
                entry = self.cache[check_key]
                return entry['price'], entry['timestamp']
        
        return None, None


# Singleton instance
_price_cache = None

def get_price_cache():
    """Get or create price cache instance"""
    global _price_cache
    if _price_cache is None:
        _price_cache = ChainlinkPriceCache()
    return _price_cache


if __name__ == "__main__":
    # Test the cache
    cache = ChainlinkPriceCache()
    
    # Update cache
    price = cache.update()
    print(f"Cached price: ${price:,.2f}")
    
    # Test retrieval
    import time
    now = int(time.time())
    retrieved, ts = cache.get_price_at_time(now)
    print(f"Retrieved: ${retrieved:,.2f} from {datetime.fromtimestamp(ts)}")
