FROM ros:jazzy-ros-base
# ros-base (not ros-core) is required: it includes rclpy and the other client
# libraries the CanReplayNode / RosbagAdapter work (roadmap Phase 4) needs.

# rosbag2's default storage plugin is sqlite3; the MCAP plugin ships as a separate package
RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-jazzy-rosbag2-storage-mcap \
    ros-jazzy-demo-nodes-py \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# CanReplayNode (ros2/nodes/can_replay_node.py) reads nuScenes CAN bus data
RUN pip3 install --break-system-packages --no-cache-dir nuscenes-devkit==1.2.0

# docker compose exec bypasses the base image's ros_entrypoint.sh (which
# sources setup.bash), so an interactive `exec ... bash` session would
# otherwise not have `ros2`/rclpy on PATH. `docker compose run` still goes
# through the entrypoint and does not need this.
RUN echo "source /opt/ros/jazzy/setup.bash" >> /root/.bashrc

WORKDIR /workspace
CMD ["sleep", "infinity"]
