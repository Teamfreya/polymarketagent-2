from web3 import Web3
import os

class ChainlinkPriceFeed:
    """
    Fetches BTC/USD price from Chainlink oracle on Ethereum mainnet.
    This matches Polymarket's settlement source exactly.
    """
    
    def __init__(self):
        # Use public RPC endpoint (can be replaced with Infura/Alchemy)
        self.rpc_url = os.getenv('ETH_RPC_URL', 'https://eth.llamarpc.com')
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        
        # BTC/USD Price Feed Address (Ethereum Mainnet)
        # Source: https://data.chain.link/feeds/ethereum/mainnet/btc-usd
        self.feed_address = "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c"
        
        # AggregatorV3Interface ABI (minimal - only what we need)
        self.abi = [
            {
                "inputs": [],
                "name": "latestRoundData",
                "outputs": [
                    {"name": "roundId", "type": "uint80"},
                    {"name": "answer", "type": "int256"},
                    {"name": "startedAt", "type": "uint256"},
                    {"name": "updatedAt", "type": "uint256"},
                    {"name": "answeredInRound", "type": "uint80"}
                ],
                "stateMutability": "view",
                "type": "function"
            },
            {
                "inputs": [],
                "name": "decimals",
                "outputs": [{"name": "", "type": "uint8"}],
                "stateMutability": "view",
                "type": "function"
            }
        ]
        
        self.contract = self.w3.eth.contract(
            address=self.feed_address,
            abi=self.abi
        )
        
        # Cache decimals (constant)
        self._decimals = None
    
    def get_btc_price(self):
        """
        Get current BTC/USD price from Chainlink oracle.
        Returns: float (BTC price in USD)
        """
        try:
            # Call latestRoundData
            round_data = self.contract.functions.latestRoundData().call()
            
            # Extract price (index 1 = answer)
            price_raw = round_data[1]
            
            # Get decimals (cache it)
            if self._decimals is None:
                self._decimals = self.contract.functions.decimals().call()
            
            # Convert to float (BTC/USD uses 8 decimals)
            price = price_raw / (10 ** self._decimals)
            
            return price
            
        except Exception as e:
            print(f"Chainlink price fetch error: {e}")
            return None
    
    def get_price_with_timestamp(self):
        """
        Get BTC price with update timestamp.
        Returns: tuple (price: float, updated_at: int)
        """
        try:
            round_data = self.contract.functions.latestRoundData().call()
            
            price_raw = round_data[1]
            updated_at = round_data[3]
            
            if self._decimals is None:
                self._decimals = self.contract.functions.decimals().call()
            
            price = price_raw / (10 ** self._decimals)
            
            return price, updated_at
            
        except Exception as e:
            print(f"Chainlink price fetch error: {e}")
            return None, None


# Singleton instance
_chainlink_feed = None

def get_chainlink_feed():
    """Get or create Chainlink price feed instance"""
    global _chainlink_feed
    if _chainlink_feed is None:
        _chainlink_feed = ChainlinkPriceFeed()
    return _chainlink_feed


if __name__ == "__main__":
    # Test the price feed
    feed = ChainlinkPriceFeed()
    price, timestamp = feed.get_price_with_timestamp()
    
    if price:
        from datetime import datetime
        dt = datetime.fromtimestamp(timestamp)
        print(f"BTC/USD: ${price:,.2f}")
        print(f"Updated: {dt}")
    else:
        print("Failed to fetch price")
