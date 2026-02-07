# ROS 2 Vitals

Lightweight system monitoring for ROS 2 that collects CPU, RAM, GPU, disk, and network statistics across multiple machines and discovers ROS processes with their resource usage.

## Overview

ROS 2 Vitals consists of three packages:

- **ros2_vitals_msgs**: Message and service definitions
- **ros2_vitals**: Collector node and terminal UI
- **rqml_vitals**: RQML visualization plugin

## Architecture

```
┌─────────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│ Robot PC            │     │ Compute PC          │     │ Operator PC         │
│ ┌─────────────────┐ │     │ ┌─────────────────┐ │     │ ┌─────────────────┐ │
│ │ vitals_         │ │     │ │ vitals_         │ │     │ │ Terminal UI /   │ │
│ │ collector       │ │     │ │ collector       │ │     │ │ RQML Plugin     │ │
│ └────────┬────────┘ │     │ └────────┬────────┘ │     │ └────────▲────────┘ │
└──────────┼──────────┘     └──────────┼──────────┘     └──────────┼──────────┘
           │                           │                           │
           └───────────── /vitals/status ─────────────────────────┘
```

Each machine runs a `vitals_collector` node that publishes to `/vitals/status`. All collectors publish to the same topic, and subscribers (terminal UI or RQML plugin) aggregate data by hostname.

## Features

### System Metrics
- **CPU**: Overall and per-core usage, temperature
- **Memory**: RAM and swap usage
- **Load average**: 1, 5, 15 minute averages
- **Uptime**: System uptime

### GPU Metrics (NVIDIA)
- GPU utilization percentage
- VRAM usage (used/total)
- Temperature
- Power consumption
- Fan speed

### Disk Metrics
- Partition usage (used/total/free)
- Read/write I/O rates

### Network Metrics
- Per-interface send/receive rates
- Packet and error counters

### Process Discovery
- Automatic discovery of ROS 2 processes (no code changes required)
- Per-process CPU, RAM, disk I/O
- Per-process GPU memory usage
- Child process aggregation (for component containers)
- Docker container detection

### Kill Service
- Remote process termination via ROS service
- Supports both SIGTERM and SIGKILL

## Installation

### Dependencies

```bash
# Required
pip install psutil

# Optional (for GPU monitoring)
pip install nvidia-ml-py

# Optional (for Docker container name detection)
pip install docker
```

### Build

```bash
cd your_ros2_ws
colcon build --packages-select ros2_vitals_msgs ros2_vitals rqml_vitals
```

## Usage

### Collector Node

Run on each machine you want to monitor:

```bash
# Basic usage
ros2 run ros2_vitals collector

# With launch file
ros2 launch ros2_vitals collector.launch.py

# With custom namespace
ros2 launch ros2_vitals collector.launch.py namespace:=/robot1
```

### Terminal UI

```bash
ros2 run ros2_vitals monitor
```

Controls:
- `Q` - Quit
- `P` - Pause/resume updates
- `←/→` - Select host
- `↑/↓` - Select process

### RQML Plugin

The plugin is available in the RQML Plugins menu under "Introspection" → "System Monitor".

## Configuration

Parameters can be set in the config file or via launch arguments:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `publish_rate` | 1.0 | Publishing rate in Hz |
| `topic` | `/vitals/status` | Status topic |
| `monitor_gpu` | true | Enable GPU monitoring |
| `monitor_processes` | true | Enable process discovery |
| `monitor_network` | true | Enable network monitoring |
| `monitor_disk` | true | Enable disk monitoring |
| `include_children` | true | Aggregate child process stats |
| `enable_kill_service` | true | Enable kill service |

## Message Types

### SystemStatus.msg
Complete status for one host including all metrics.

### ProcessStatus.msg
Per-process statistics including CPU, RAM, GPU memory, and disk I/O.

### KillProcess.srv
Service to terminate a process by PID.

## Future Improvements

- [ ] **Per-process network I/O**: Currently not implemented due to complexity (would require packet capture). System-wide network stats are available instead.
- [ ] AMD GPU support via rocm-smi
- [ ] Process filtering options
- [ ] Historical data storage

## License

MIT
