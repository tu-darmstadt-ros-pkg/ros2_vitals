"""Launch file for vitals collector node."""

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
            description='Namespace for the collector node'
        ),

        Node(
            package='ros2_vitals',
            executable='collector',
            name='vitals_collector',
            namespace=LaunchConfiguration('namespace'),
            parameters=[LaunchConfiguration('config_file')],
            output='screen',
        ),
    ])
