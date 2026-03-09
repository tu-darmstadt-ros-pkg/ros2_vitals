"""Launch file for vitals bridge node.

The bridge connects to the vitals-daemon (running as root) via Unix socket
and publishes system metrics to ROS. Start the daemon first:

    sudo python3 -m ros2_vitals.daemon

Or use the standalone collector (no daemon needed, limited features):

    ros2 run ros2_vitals collector
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():
    pkg_share = get_package_share_directory('ros2_vitals')
    default_config = os.path.join(pkg_share, 'config', 'defaults.yaml')

    return LaunchDescription([
        DeclareLaunchArgument(
            'config_file',
            default_value=default_config,
            description='Path to configuration file'
        ),

        DeclareLaunchArgument(
            'namespace',
            default_value='',
            description='Namespace for the bridge node'
        ),

        DeclareLaunchArgument(
            'socket_path',
            default_value='/run/vitals/collector.sock',
            description='Path to vitals-daemon Unix socket'
        ),

        Node(
            package='ros2_vitals',
            executable='bridge',
            name='vitals_collector',
            namespace=LaunchConfiguration('namespace'),
            parameters=[
                LaunchConfiguration('config_file'),
                {'socket_path': LaunchConfiguration('socket_path')},
            ],
            output='screen',
        ),
    ])
