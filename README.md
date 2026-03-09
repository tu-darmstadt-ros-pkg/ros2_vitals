# ROS 2 Vitals

Lightweight system monitoring for ROS 2 that collects CPU, RAM, GPU, disk, and network statistics across multiple machines and discovers ROS processes with their resource usage.

## Overview

ROS 2 Vitals consists of three packages:

- **ros2_vitals_msgs**: Message and service definitions
- **ros2_vitals**: Daemon, bridge node, standalone collector, and terminal UI
- **rqml_vitals**: RQML visualization plugin

## Architecture

The recommended setup splits collection from publishing:

```
┌─────────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│ Robot PC            │     │ Compute PC          │     │ Operator PC         │
│ ┌─────────────────┐ │     │ ┌─────────────────┐ │     │ ┌─────────────────┐ │
│ │ vitals-daemon   │ │     │ │ vitals-daemon   │ │     │ │ Terminal UI /   │ │
│ │ (root, no ROS)  │ │     │ │ (root, no ROS)  │ │     │ │ RQML Plugin     │ │
│ │       │         │ │     │ │       │         │ │     │ └────────▲────────┘ │
│ │  Unix socket    │ │     │ │  Unix socket    │ │     └──────────┼──────────┘
│ │       │         │ │     │ │       │         │ │                │
│ │ vitals_bridge   │ │     │ │ vitals_bridge   │ │                │
│ │ (ROS node)      │ │     │ │ (ROS node)      │ │                │
│ └────────┬────────┘ │     │ └────────┬────────┘ │                │
└──────────┼──────────┘     └──────────┼──────────┘                │
           │                           │                           │
           └───────────── /vitals/status ─────────────────────────┘
```

**Daemon** (runs as root): Pure Python, no ROS dependencies. Collects all system metrics including eBPF network stats and full process visibility. Serves data via Unix socket.

**Bridge** (normal ROS node): Connects to daemon, converts data to ROS messages, publishes to `/vitals/status`. Can run in any namespace.

A **standalone collector** (`ros2 run ros2_vitals collector`) is also available for simple setups where root is not needed (uses `ss` fallback for network stats, limited process visibility).

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
- Per-process network I/O (TCP + UDP via eBPF, or TCP-only via `ss` fallback)
- Per-process GPU memory usage
- Child process aggregation (for component containers)
- Docker container detection

### Kill Service
- Remote process termination via ROS service
- Supports both SIGTERM and SIGKILL
- Daemon handles kills with root privileges

## Installation

### Dependencies

```bash
# Required
pip install psutil

# Optional (for eBPF network monitoring — TCP + UDP, ~1ms instead of ~100ms)
sudo apt install bpfcc-tools python3-bpfcc

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

### Daemon + Bridge (recommended)

This is the recommended setup for full functionality including eBPF network
monitoring and complete process visibility.

**1. Start the daemon (as root):**

```bash
# Direct (for development)
sudo python3 -m ros2_vitals.daemon

# Or with custom options
sudo python3 -m ros2_vitals.daemon --rate 2.0 --socket-path /run/vitals/collector.sock
```

**2. Start the bridge (as normal user):**

```bash
# Basic
ros2 run ros2_vitals bridge

# With launch file and namespace
ros2 launch ros2_vitals collector.launch.py namespace:=/robot1

# Custom socket path
ros2 launch ros2_vitals collector.launch.py socket_path:=/run/vitals/collector.sock
```

#### Production Setup (systemd)

Install the daemon as a systemd service:

```bash
sudo cp install/ros2_vitals/share/ros2_vitals/config/vitals-daemon.service \
     /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vitals-daemon
```

The bridge can be launched from your robot's existing launch files or via another systemd service.

Check daemon status:

```bash
sudo systemctl status vitals-daemon
sudo journalctl -u vitals-daemon -f
```

### Standalone Collector (simple setup)

For machines where root access is not available or not needed:

```bash
ros2 run ros2_vitals collector
```

This runs everything in a single process with `ss` fallback for network stats
(TCP only, ~100ms) and limited process visibility.

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

### Bridge Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `publish_rate` | 1.0 | Publishing rate in Hz |
| `topic` | `/vitals/status` | Status topic |
| `enable_kill_service` | true | Enable kill service |
| `socket_path` | `/run/vitals/collector.sock` | Daemon socket path |

### Standalone Collector Parameters

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

### Daemon Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `--rate` | 1.0 | Collection rate in Hz |
| `--socket-path` | `/run/vitals/collector.sock` | Unix socket path |

## Message Types

### SystemStatus.msg
Complete status for one host including all metrics.

### ProcessStatus.msg
Per-process statistics including CPU, RAM, GPU memory, disk I/O, and network I/O (TCP + UDP with eBPF, TCP only with ss fallback).

### KillProcess.srv
Service to terminate a process by PID.

## License

MIT
