# Complete system status published by each vitals collector node

builtin_interfaces/Time stamp       # Timestamp of this status

# Host identification
string hostname                     # Machine hostname
string[] ip_addresses               # IP addresses of this host

# CPU
float32 cpu_percent                 # Overall CPU usage (0-100, normalized)
uint8 cpu_count                     # Number of CPU cores
float32[] cpu_per_core              # Per-core CPU usage

# Load average (1, 5, 15 minute)
float32 load_avg_1min
float32 load_avg_5min
float32 load_avg_15min

# Memory
uint64 ram_total_bytes              # Total RAM
uint64 ram_used_bytes               # Used RAM
uint64 ram_available_bytes          # Available RAM (includes buffers/cache)
uint64 swap_total_bytes             # Total swap
uint64 swap_used_bytes              # Used swap

# Temperature
float32 cpu_temperature_celsius     # CPU temperature (-1 if unavailable)

# Uptime
float64 uptime_seconds              # System uptime in seconds

# GPU array (for multi-GPU systems)
ros2_vitals_msgs/GpuStatus[] gpus

# Network interfaces
ros2_vitals_msgs/NetworkInterface[] network_interfaces

# Disk partitions
ros2_vitals_msgs/DiskStatus[] disks

# Discovered ROS processes on this host
ros2_vitals_msgs/ProcessStatus[] processes
