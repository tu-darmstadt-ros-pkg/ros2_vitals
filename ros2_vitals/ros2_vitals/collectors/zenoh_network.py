import zenoh
import time
import sys
import json

# 1. Dependency Check
try:
    import cbor2
except ImportError:
    print("[!] CRITICAL: 'cbor2' library missing.")
    print("    Run: pip install cbor2")
    sys.exit(1)

# --- GLOBAL STORAGE ---
stats_cache = {} # { uuid: {'rx': 0, 'tx': 0, 'ts': time.time()} }

def decode_cbor_metrics(payload):
    """
    Decodes the binary CBOR payload from Zenoh metrics.
    Navigates the dictionary to find 'rx_bytes' / 'tx_bytes'.
    """
    try:
        data = cbor2.loads(payload)
        rx = 0
        tx = 0
        
        # Helper to recursively find 'bytes' in the nested map
        # Zenoh metrics structure: {'peer': {'transport': {'unicast': {'rx': {'bytes': 123}}}}}
        def find_counters(obj):
            r, t = 0, 0
            if isinstance(obj, dict):
                # Check for explicit keys (stats plugin v0.10+)
                if 'rx_bytes' in obj: r = obj['rx_bytes']
                if 'tx_bytes' in obj: t = obj['tx_bytes']
                
                # Check for nested 'rx': {'bytes': ...} (stats plugin v1.0+)
                if 'rx' in obj and isinstance(obj['rx'], dict) and 'bytes' in obj['rx']:
                    r = obj['rx']['bytes']
                if 'tx' in obj and isinstance(obj['tx'], dict) and 'bytes' in obj['tx']:
                    t = obj['tx']['bytes']
                
                # If found, return immediately
                if r > 0 or t > 0:
                    return r, t
                
                # Otherwise, dig deeper
                for v in obj.values():
                    child_r, child_t = find_counters(v)
                    r = max(r, child_r) # Aggregate or find first match
                    t = max(t, child_t)
            return r, t

        rx, tx = find_counters(data)
        return rx, tx
    except Exception as e:
        return 0, 0

def main():
    print("[-] Opening Zenoh Session...")
    conf = zenoh.Config()
    
    with zenoh.open(conf) as session:
        print("[-] Monitor Active. Polling metrics every 1s...")
        
        try:
            while True:
                # 1. Poll (GET) specifically for metrics keys
                # Matches keys like: @/uuid/peer/metrics
                replies = session.get("@/**/metrics")
                
                count = 0
                current_time = time.time()
                
                print("\033[H\033[J", end="") # Clear Screen
                print(f"--- Zenoh Polling Monitor [{time.strftime('%H:%M:%S')}] ---")
                print(f"{'Session / Process UUID':<40} | {'RX (KB/s)':<12} | {'TX (KB/s)':<12}")
                print("-" * 75)

                for reply in replies:
                    try:
                        # Handle v1.0 wrapper
                        sample = reply.ok if hasattr(reply, 'ok') else reply
                        
                        key = str(sample.key_expr)
                        payload = sample.payload
                        
                        # Extract UUID from key (@/UUID/peer/metrics)
                        parts = key.split('/')
                        if len(parts) > 1:
                            uuid = parts[1] # The segment after @
                            
                            # Decode
                            rx_total, tx_total = decode_cbor_metrics(payload)
                            
                            if rx_total > 0 or tx_total > 0:
                                count += 1
                                # Calculate Rate
                                rate_rx = 0.0
                                rate_tx = 0.0
                                
                                if uuid in stats_cache:
                                    prev = stats_cache[uuid]
                                    dt = current_time - prev['ts']
                                    if dt > 0:
                                        rate_rx = (rx_total - prev['rx']) / dt
                                        rate_tx = (tx_total - prev['tx']) / dt
                                        
                                        # Handle reset/restart
                                        if rate_rx < 0: rate_rx = 0
                                        if rate_tx < 0: rate_tx = 0
                                
                                # Update Cache
                                stats_cache[uuid] = {'rx': rx_total, 'tx': tx_total, 'ts': current_time}
                                
                                # Print
                                print(f"{uuid[:38]:<40} | {rate_rx/1024.0:<12.2f} | {rate_tx/1024.0:<12.2f}")
                                
                    except Exception as e:
                        pass
                
                if count == 0:
                    print("[No metrics found yet. Ensure router has --cfg plugins/stats:{}]")
                else:
                    print("-" * 75)
                    print(f"Tracking {count} sessions.")

                time.sleep(1.0)
                
        except KeyboardInterrupt:
            print("\nStopping...")

if __name__ == "__main__":
    main()