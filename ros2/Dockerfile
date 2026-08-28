FROM ros:jazzy-ros-base
# ros-base (not ros-core) is required: it includes rclpy and the other client
# libraries the CanReplayNode / RosbagAdapter work (roadmap Phase 4) needs.
# Jazzy targets Ubuntu 24.04 Noble, matching docs/robot-data-model.md's
# stated environment.

# rosbag2's default storage plugin is sqlite3; the MCAP plugin ships as a
# separate package because docs/robot-data-model.md standardizes on MCAP
# (not sqlite3) for rosbag2 storage, to match RosbagAdapter's MCAP reader.
# demo-nodes-py is installed purely as an environment smoke test (talker/
# listener) since no real publishing node exists in this repo yet.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-jazzy-rosbag2-storage-mcap \
    ros-jazzy-demo-nodes-py \
    && rm -rf /var/lib/apt/lists/*

# docker compose exec bypasses the base image's ros_entrypoint.sh (which
# sources setup.bash), so an interactive `exec ... bash` session would
# otherwise not have `ros2`/rclpy on PATH. `docker compose run` still goes
# through the entrypoint and does not need this.
RUN echo "source /opt/ros/jazzy/setup.bash" >> /root/.bashrc

WORKDIR /workspace
CMD ["sleep", "infinity"]
