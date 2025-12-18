import os
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.constants import POLYGON
from dotenv import load_dotenv

load_dotenv()

class PolyClient:
    def __init__(self):
        self.host = os.getenv("HOST", "https://clob.polymarket.com")
        self.key = os.getenv("KEY")
        self.secret = os.getenv("SECRET")
        self.passphrase = os.getenv("PASSPHRASE")
        self.private_key = os.getenv("PRIVATE_KEY")
        self.chain_id = 137 # Polygon
        
        # Determine Auth Mode
        # Magic Link / Email Login Mode
        # Uses Polymarket Proxy wallet created through web interface
        print("Using Magic Link Proxy Wallet Authentication")
        self.client = ClobClient(
            self.host,
            key=self.private_key,
            chain_id=self.chain_id,
            signature_type=1,  # Magic Link / Email mode
            funder=os.getenv("FUNDER_ADDRESS")  # Load from .env
        )
        
        # Derive API credentials from Private Key (per Polymarket documentation)
        try:
            self.client.set_api_creds(self.client.create_or_derive_api_creds())
            print("API credentials derived successfully")
        except Exception as e:
            print(f"Warning: Could not derive API credentials: {e}")
            print("Continuing with Private Key authentication only")

        print("Polymarket Client Initialized")

    def get_market(self, condition_id):
        return self.client.get_market(condition_id)

    def place_limit_order(self, token_id: str, side: str, price: float, size: float):
        # side: BUY or SELL. For this bot we always BUY YES shares? 
        # Actually the prompt says "buy YES shares on the Up contract" or "buy YES shares on the Down contract".
        # In Polymarket, Up and Down are separate token_ids. So we always BUY with side=BUY.
        
        print(f"Placing LIMIT order: BUY {size} shares of {token_id} at {price}")
        try:
            order_args = OrderArgs(
                price=price,
                size=size,
                side="BUY",
                token_id=token_id,
            )
            # Create and post
            resp = self.client.create_and_post_order(order_args)
            return resp
        except Exception as e:
            print(f"Order placement failed: {e}")
            print(f"Exception type: {type(e).__name__}")
            print(f"Exception details: {str(e)}")
            import traceback
            traceback.print_exc()
            return None

    def cancel_order(self, order_id):
        try:
            self.client.cancel(order_id)
            print(f"Cancelled order {order_id}")
        except Exception as e:
            print(f"Failed to cancel order {order_id}: {e}")

    def get_order_status(self, order_id):
        try:
            return self.client.get_order(order_id)
        except Exception as e:
            print(f"Failed to get order status: {e}")
            return None

    def get_markets(self, next_cursor=""):
        # Helper to scan markets
        return self.client.get_markets(next_cursor=next_cursor)

    def get_usdc_balance(self):
        """
        Fetches USDC balance of the FUNDER_ADDRESS (or derived address) on Polygon via RPC.
        Returns float.
        """
        try:
            import requests
            # Clean address
            target_address = os.getenv("FUNDER_ADDRESS")
            if not target_address:
                 # Fallback to key derivation or client address if possible
                 # But we assume FUNDER_ADDRESS is set for Proxy usage
                 try:
                     target_address = self.client.funder # If exposed?
                 except: pass

            if not target_address:
                 # Fallback to private key derivation
                 # This is complex without web3, so let's try to trust env or fail gracefully
                 print("WARNING: No FUNDER_ADDRESS for balance check.")
                 return 0.0

            # RPC
            rpc_url = "https://polygon-rpc.com"
            usdc_contract = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
            
            # Check if address already has 0x
            if target_address.startswith("0x"):
                clean_addr = target_address[2:]
            else:
                clean_addr = target_address
            
            data = "0x70a08231" + clean_addr.zfill(64)
            
            payload = {
                "jsonrpc": "2.0",
                "method": "eth_call",
                "params": [{"to": usdc_contract, "data": data}, "latest"],
                "id": 1
            }
            resp = requests.post(rpc_url, json=payload, timeout=5)
            res = resp.json()
            if 'result' in res:
                hex_bal = res['result']
                if hex_bal == "0x": hex_bal = "0x0"
                return int(hex_bal, 16) / 1e6
        except Exception as e:
            print(f"Error fetching balance: {e}")
            return 0.0
