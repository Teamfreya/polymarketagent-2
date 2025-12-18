import sys
import fcntl
from src.bot import Bot

LOCK_FILE = "/tmp/polymarket_btc_15m_bot.lock"

def acquire_process_lock():
    """
    Acquire an OS-level exclusive lock to prevent multiple bot instances.
    Returns the lock file handle if successful, exits if lock is already held.
    """
    try:
        lock_file = open(LOCK_FILE, 'w')
        # Try to acquire exclusive lock (non-blocking)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Write PID to lock file for debugging
        lock_file.write(str(os.getpid()) + '\n')
        lock_file.flush()
        print(f"✓ Process lock acquired: {LOCK_FILE}")
        return lock_file
    except BlockingIOError:
        print(f"✗ ERROR: Another bot instance is already running!")
        print(f"✗ Lock file: {LOCK_FILE}")
        print(f"✗ Cannot start multiple instances. Please stop the existing bot first.")
        sys.exit(0)
    except Exception as e:
        print(f"✗ ERROR: Failed to acquire process lock: {e}")
        sys.exit(1)

if __name__ == "__main__":
    import os
    
    # Acquire process lock before starting bot
    lock_handle = acquire_process_lock()
    
    try:
        bot = Bot()
        bot.run()
    finally:
        # Lock is automatically released when process exits
        # But we can explicitly close the file handle
        try:
            lock_handle.close()
        except:
            pass
