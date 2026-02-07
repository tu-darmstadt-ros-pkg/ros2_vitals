# Child process status (simplified version for nodes within launch groups)

# Identity
string node_name                    # ROS node name
string node_namespace               # ROS namespace
int32 pid                           # Process ID
string cmdline                      # Full command line

# CPU and memory
float32 cpu_percent                 # CPU usage percentage
uint64 ram_bytes                    # RAM usage in bytes

# GPU (NVIDIA)
int8 gpu_index                      # GPU index being used (-1 if not using GPU)
uint64 gpu_memory_bytes             # GPU memory used by this process

# Disk I/O
float32 disk_read_bytes_per_sec     # Current read rate
float32 disk_write_bytes_per_sec    # Current write rate

# Status
string status                       # Process status
