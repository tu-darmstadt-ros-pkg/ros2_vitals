# Network interface statistics

string name                         # Interface name (e.g., "eth0", "wlan0")
bool is_up                          # Whether the interface is up

# Cumulative counters (since boot)
uint64 bytes_sent_total             # Total bytes sent
uint64 bytes_recv_total             # Total bytes received
uint64 packets_sent_total           # Total packets sent
uint64 packets_recv_total           # Total packets received
uint64 errors_in                    # Total input errors
uint64 errors_out                   # Total output errors
uint64 drops_in                     # Total input drops
uint64 drops_out                    # Total output drops

# Calculated rates (bytes per second)
float32 bytes_sent_per_sec          # Current send rate
float32 bytes_recv_per_sec          # Current receive rate
