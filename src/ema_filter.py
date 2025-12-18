"""
EMA Calculator and 2H Candle Fetcher
"""
import requests
from typing import Optional, List
from datetime import datetime, timedelta

def calculate_ema(prices: List[float], period: int = 13) -> Optional[float]:
    """
    Calculate Exponential Moving Average
    
    Args:
        prices: List of prices (most recent last)
        period: EMA period (default 13)
    
    Returns:
        EMA value or None if insufficient data
    """
    if len(prices) < period:
        return None
    
    # Calculate multiplier
    multiplier = 2 / (period + 1)
    
    # Start with SMA for first EMA value
    sma = sum(prices[:period]) / period
    ema = sma
    
    # Calculate EMA for remaining prices
    for price in prices[period:]:
        ema = (price * multiplier) + (ema * (1 - multiplier))
    
    return ema


def get_btc_2h_ema13() -> Optional[float]:
    """
    Fetch 2H BTC candles from Binance and calculate EMA13
    
    Returns:
        EMA13 value or None if fetch fails
    """
    try:
        # Fetch last 50 2H candles from Binance (enough for EMA calculation)
        url = "https://api.binance.com/api/v3/klines"
        params = {
            'symbol': 'BTCUSDT',
            'interval': '2h',
            'limit': 50
        }
        
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        candles = resp.json()
        
        # Extract close prices
        # Binance kline format: [open_time, open, high, low, close, volume, ...]
        close_prices = [float(candle[4]) for candle in candles]
        
        # Calculate EMA13
        ema13 = calculate_ema(close_prices, period=13)
        
        return ema13
        
    except Exception as e:
        print(f"Error fetching 2H EMA13: {e}")
        return None


if __name__ == "__main__":
    # Test
    ema13 = get_btc_2h_ema13()
    if ema13:
        print(f"BTC 2H EMA13: ${ema13:,.2f}")
    else:
        print("Failed to calculate EMA13")
