"""Tests for the collector node running in-process with rclpy."""

import os
import subprocess
import time

import pytest
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

from ros2_vitals.collector_node import VitalsCollectorNode
from ros2_vitals_msgs.msg import SystemStatus
from ros2_vitals_msgs.srv import KillProcess

TOPIC = f"/vitals_test_{os.getpid()}/status"


@pytest.fixture
def collector_node():
    rclpy.init(
        args=[
            "--ros-args",
            "-p",
            f"topic:={TOPIC}",
            "-p",
            "publish_rate:=5.0",
            "-p",
            "monitor_gpu:=false",
            "-p",
            "enable_kill_service:=false",
        ]
    )
    node = VitalsCollectorNode()
    yield node
    node.destroy_node()
    rclpy.shutdown()


def test_publishes_system_status(collector_node):
    listener = rclpy.create_node("vitals_test_listener")
    received = []
    qos = QoSProfile(
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
        depth=1,
    )
    listener.create_subscription(SystemStatus, TOPIC, received.append, qos)

    executor = SingleThreadedExecutor()
    executor.add_node(collector_node)
    executor.add_node(listener)
    deadline = time.monotonic() + 10.0
    while not received and time.monotonic() < deadline:
        executor.spin_once(timeout_sec=0.1)
    executor.shutdown()
    listener.destroy_node()

    assert received, f"no SystemStatus received on {TOPIC}"
    msg = received[0]
    assert msg.hostname
    assert msg.cpu_count >= 1
    assert len(msg.cpu_per_core) == msg.cpu_count
    assert msg.ram_total_bytes > 0
    assert msg.stamp.sec > 0


def test_kill_request_terminates_process(collector_node):
    proc = subprocess.Popen(["sleep", "60"])
    try:
        response = collector_node._handle_kill_request(
            KillProcess.Request(pid=proc.pid, force=False), KillProcess.Response()
        )
        assert response.success, response.message
        assert proc.wait(timeout=5) != 0
    finally:
        proc.kill()
        proc.wait()


def test_kill_request_unknown_pid(collector_node):
    # PIDs are bounded by /proc/sys/kernel/pid_max (at most 2^22), so this one cannot exist
    response = collector_node._handle_kill_request(
        KillProcess.Request(pid=2**30, force=True), KillProcess.Response()
    )
    assert not response.success
    assert "not found" in response.message
