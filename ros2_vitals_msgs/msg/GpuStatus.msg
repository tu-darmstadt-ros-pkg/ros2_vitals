# GPU status information (NVIDIA GPUs via NVML)

uint8 index                         # GPU index
string name                         # GPU name (e.g., "NVIDIA GeForce RTX 3070")

# Utilization
float32 utilization_percent         # GPU compute utilization (0-100)

# Memory (VRAM)
uint64 memory_total_bytes           # Total VRAM
uint64 memory_used_bytes            # Used VRAM

# Thermal and power
float32 temperature_celsius         # GPU temperature
int16 power_watts                   # Power consumption (-1 if unavailable)
int8 fan_speed_percent              # Fan speed percentage (-1 if unavailable)
