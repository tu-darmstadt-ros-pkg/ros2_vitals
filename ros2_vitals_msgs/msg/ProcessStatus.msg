# ROS process status (includes aggregated stats from child processes)

# Identity
string node_name                    # ROS node name (if detected, empty otherwise)
string node_namespace               # ROS namespace (if detected)
int32 pid                           # Process ID
int32[] child_pids                  # Direct child process PIDs
string cmdline                      # Full command line
string container_name               # Docker container name (empty if not containerized)

# Process type and hierarchy
bool is_launch_process              # True if this is a ros2 launch process
string launch_name                  # Name of the launch file (only set for launch processes)
ros2_vitals_msgs/ProcessStatus[] child_nodes  # Child node processes (only for launch processes)

# CPU and memory (includes children)
float32 cpu_percent                 # CPU usage percentage (includes children)
uint64 ram_bytes                    # RAM usage in bytes (includes children)
uint64 ram_bytes_self               # RAM usage of this process only

# GPU (NVIDIA)
int8 gpu_index                      # GPU index being used (-1 if not using GPU)
uint64 gpu_memory_bytes             # GPU memory used by this process

# Disk I/O (cumulative, includes children)
uint64 disk_read_bytes_total        # Total bytes read from disk
uint64 disk_write_bytes_total       # Total bytes written to disk
float32 disk_read_bytes_per_sec     # Current read rate
float32 disk_write_bytes_per_sec    # Current write rate

# Resources
uint32 open_files_count             # Number of open file descriptors
uint32 network_connections_count    # Number of active network connections
uint32 num_threads                  # Number of threads

# Status
string status                       # Process status: "running", "sleeping", "zombie", "stopped", "disk-sleep"
float64 create_time                 # Process creation time (Unix timestamp)
