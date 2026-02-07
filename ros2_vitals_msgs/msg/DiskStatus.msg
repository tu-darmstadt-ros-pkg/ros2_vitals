# Disk partition status

string device                       # Device name (e.g., "/dev/sda1")
string mount_point                  # Mount point (e.g., "/", "/home")
string filesystem                   # Filesystem type (e.g., "ext4", "btrfs")

# Capacity
uint64 total_bytes                  # Total space
uint64 used_bytes                   # Used space
uint64 free_bytes                   # Free space
float32 usage_percent               # Usage percentage (0-100)

# I/O rates (bytes per second)
float32 read_bytes_per_sec          # Current read rate
float32 write_bytes_per_sec         # Current write rate
