#!/bin/sh
# Source the ROS 2 environment (rclpy, message packages), then run the tool.
set -e
. /opt/ros/jazzy/setup.sh
exec dataset-acquisition "$@"
