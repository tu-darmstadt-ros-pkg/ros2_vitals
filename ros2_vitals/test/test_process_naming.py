"""Tests for ROS process detection and display-name extraction from command lines."""

import pytest

from ros2_vitals.collectors.process_collector import (
    ProcessCollector,
    _is_python_interpreter,
)


@pytest.fixture
def collector():
    return ProcessCollector()


def name_of(collector, cmdline):
    return collector._extract_node_name_from_cmdline(cmdline, " ".join(cmdline))


@pytest.mark.parametrize(
    "name",
    [
        "python",
        "python3",
        "python3.10",
        "python3.12",
        "python3.13",
        "python3.14",
        "python3.20",
    ],
)
def test_is_python_interpreter(name):
    assert _is_python_interpreter(name)


@pytest.mark.parametrize(
    "name",
    [
        "pythonista",
        "python3-config",
        "my_python",
        "ipython3",
        "python3.",
        "bash",
        "",
    ],
)
def test_is_not_python_interpreter(name):
    assert not _is_python_interpreter(name)


@pytest.mark.parametrize(
    "interpreter",
    [
        "python3",
        "/usr/bin/python3.12",
        "/usr/bin/python3.14",
    ],
)
def test_python_inline_name_any_python_version(collector, interpreter):
    cmdline = [interpreter, "-c", "from ros2cli.daemon.daemonize import main; main()"]
    assert collector._extract_python_inline_name(cmdline) == "ros2cli/daemon"


def test_python_inline_name_ignores_non_python(collector):
    cmdline = ["/bin/bash", "-c", "from ros2cli.daemon.daemonize import main; main()"]
    assert collector._extract_python_inline_name(cmdline) == ""


def test_node_name_from_remap(collector):
    cmdline = [
        "/opt/ros/lyrical/lib/demo_nodes_cpp/talker",
        "--ros-args",
        "-r",
        "__node:=my_talker",
        "-r",
        "__ns:=/robot",
    ]
    assert name_of(collector, cmdline) == "my_talker"
    assert collector._extract_namespace_from_cmdline(" ".join(cmdline)) == "/robot"


def test_ros2_run(collector):
    cmdline = [
        "/usr/bin/python3.14",
        "/opt/ros/lyrical/bin/ros2",
        "run",
        "demo_nodes_py",
        "talker",
    ]
    assert name_of(collector, cmdline) == "demo_nodes_py/talker"


def test_ros2_launch_with_namespace(collector):
    cmdline = [
        "/usr/bin/python3",
        "/opt/ros/jazzy/bin/ros2",
        "launch",
        "--namespace",
        "athena",
        "athena_bringup",
        "robot.launch.py",
    ]
    assert name_of(collector, cmdline) == "[launch] athena_bringup/robot"
    assert collector._get_launch_name(" ".join(cmdline)) == "athena_bringup/robot"


@pytest.mark.parametrize("distro", ["jazzy", "lyrical"])
def test_ros2_cli(collector, distro):
    cmdline = [
        "/usr/bin/python3",
        f"/opt/ros/{distro}/bin/ros2",
        "topic",
        "echo",
        "/joint_states",
    ]
    assert name_of(collector, cmdline) == "ros2 topic echo /joint_states [cli]"


def test_ros2_cli_truncates_long_target(collector):
    target = "/a/very/long/namespace/with/many/levels/topic"
    name = collector._extract_ros2_cli_name(["ros2", "topic", "hz", target])
    assert name == f"ros2 topic hz ...{target[-22:]} [cli]"


def test_ros2_cli_ignores_run(collector):
    assert collector._extract_ros2_cli_name(["ros2", "run", "pkg", "exe"]) == ""


@pytest.mark.parametrize("distro", ["jazzy", "lyrical"])
def test_installed_executable_path(collector, distro):
    cmdline = [f"/opt/ros/{distro}/lib/rviz2/rviz2", "--ros-args"]
    assert name_of(collector, cmdline) == "rviz2/rviz2"


def test_workspace_install_path(collector):
    cmdline = [
        "/usr/bin/python3.14",
        "/ws/install/hector_recorder/lib/hector_recorder/record",
    ]
    assert name_of(collector, cmdline) == "hector_recorder/record"


def test_gazebo_server_with_world(collector):
    cmdline = ["ruby", "/usr/bin/gz", "sim", "-s", "/worlds/arena.sdf"]
    assert name_of(collector, cmdline) == "gz-sim-server/arena"


def test_gazebo_gui(collector):
    assert collector._extract_gazebo_name("gz sim -g") == "gz-sim-gui"


def test_shell_wrapper_skips_versioned_python(collector):
    cmdline = ["/bin/sh", "-c", "python3.14 my_script.py --flag"]
    assert collector._is_shell_wrapper(cmdline)
    assert collector._extract_shell_command_name(cmdline) == "my_script.py"


def test_python_module(collector):
    cmdline = ["/usr/bin/python3.14", "-m", "ros2_vitals.daemon"]
    assert name_of(collector, cmdline) == "ros2_vitals.daemon"


@pytest.mark.parametrize("interpreter", ["python3", "python3.12", "python3.14"])
def test_fallback_skips_versioned_python(collector, interpreter):
    cmdline = [f"/usr/bin/{interpreter}", "/home/user/tools/my_tool.py"]
    assert name_of(collector, cmdline) == "my_tool"


def test_fallback_never_returns_interpreter(collector):
    assert name_of(collector, ["/usr/bin/python3.14"]) == ""


@pytest.mark.parametrize(
    "cmdline_str, expected",
    [
        ("ros2 run demo_nodes_cpp talker", True),
        ("ros2 launch pkg file.launch.py", True),
        ("/opt/ros/lyrical/lib/rclcpp_components/component_container", True),
        ("/ws/install/pkg/lib/pkg/node --ros-args -r __node:=x", True),
        ("/usr/bin/python3.14 /opt/ros/lyrical/bin/ros2 daemon", True),
        ("/usr/bin/firefox", False),
        ("/usr/bin/python3.14 -m http.server", False),
    ],
)
def test_is_ros_process(collector, cmdline_str, expected):
    assert collector._is_ros_process(cmdline_str) is expected
