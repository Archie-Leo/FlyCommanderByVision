from launch import LaunchDescription
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():
    config = os.path.join(
        get_package_share_directory("drone_control_gateway"), "config", "gateway.yaml"
    )
    return LaunchDescription([
        Node(
            package="drone_control_gateway",
            executable="control_gateway_node",
            name="control_gateway",
            output="screen",
            parameters=[config],
        )
    ])
