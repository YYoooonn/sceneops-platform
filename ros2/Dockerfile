FROM ros:jazzy-ros-base
# ros-base (not ros-core) is required: it includes rclpy and the other client
# libraries the streaming bridge node needs.

# rosbag2's default storage plugin is sqlite3; the MCAP plugin ships as a separate package
RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-jazzy-rosbag2-storage-mcap \
    ros-jazzy-demo-nodes-py \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# The durable MCAP capture consumer (ros2/capture/) must validate a
# finalized bag by reading it back with the same official MCAP reader
# stack used elsewhere in SceneOps (apps/worker's RosbagAdapter), not by
# trusting the writer -- pure-Python, no ROS2/rclpy dependency of its own.
RUN pip3 install --break-system-packages --no-cache-dir \
    "mcap>=1.4" \
    "mcap-ros2-support>=0.5"

# StreamingBridgeNode (ros2/nodes/streaming_bridge_node.py) publishes
# TelemetryEnvelopes through KafkaTelemetryProducer -- installed via plain
# system pip (--break-system-packages), NOT uv,
# because rclpy itself lives in this image's apt-managed system
# site-packages; a uv-managed venv would be isolated from it and could not
# `import rclpy`. sceneops-core first, then sceneops-streaming, in one
# invocation so pip's resolver satisfies sceneops-streaming's
# "sceneops-core" dependency from the local path just installed rather
# than looking on PyPI (sceneops-core is not published there).
COPY packages/sceneops-core /workspace/packages/sceneops-core
COPY packages/sceneops-streaming /workspace/packages/sceneops-streaming
RUN pip3 install --break-system-packages --no-cache-dir \
    /workspace/packages/sceneops-core \
    /workspace/packages/sceneops-streaming

# docker compose exec bypasses the base image's ros_entrypoint.sh (which
# sources setup.bash), so an interactive `exec ... bash` session would
# otherwise not have `ros2`/rclpy on PATH. `docker compose run` still goes
# through the entrypoint and does not need this.
RUN echo "source /opt/ros/jazzy/setup.bash" >> /root/.bashrc

# Captured recordings are written to the shared acquisition-recordings
# volume mounted at /recordings. Sticky + world-writable so the volume is
# usable by the non-root acquisition containers whichever service creates
# it first.
RUN mkdir -p /recordings && chmod 1777 /recordings

WORKDIR /workspace
CMD ["sleep", "infinity"]
