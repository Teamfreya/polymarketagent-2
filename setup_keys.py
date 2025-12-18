import os
import sys
from py_clob_client.client import ClobClient
from dotenv import load_dotenv, set_key

def setup():
    load_dotenv()
    pk = os.getenv("PRIVATE_KEY")
    if not pk:
        print("No PRIVATE_KEY found in .env")
        return

    print("Generating API Keys from Private Key...")
    
    # Initialize client with PK to sign the create_api_key request
    # Note: chain_id 137 for Polygon
    try:
        host = "https://clob.polymarket.com"
        # We assume the PK corresponds to an EOA that is already onboarded/proxy deployed?
        # If not, we might need create_api_key checks.
        
        client = ClobClient(host, key=pk, chain_id=137) 
        
        # Create API Key
        # This returns { 'apiKey': ..., 'secret': ..., 'passphrase': ... }
        resp = client.create_api_key()
        
        print("API Keys Generated Successfully!")
        print(f"Key: {resp['apiKey']}")
        
        # Save to .env
        dotenv_path = ".env"
        set_key(dotenv_path, "KEY", resp['apiKey'])
        set_key(dotenv_path, "SECRET", resp['secret'])
        set_key(dotenv_path, "PASSPHRASE", resp['passphrase'])
        
        print("Updated .env with new credentials.")
        
    except Exception as e:
        print(f"Error generating keys: {e}")
        print("Ensure your wallet has some MATIC/POL for gas if this is an on-chain transaction (it's usually just a signature though).")
        print("Also ensure the Private Key is correct and includes '0x' prefix if needed.")

if __name__ == "__main__":
    setup()
