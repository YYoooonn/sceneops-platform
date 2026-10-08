FROM ros:jazzy-ros-base
# ros-base (not ros-core) is required: it includes rclpy and the other client
# libraries the streaming bridge node needs.

# rosbag2's default storage plugin is sqlite3; the MCAP plugin ships as a separate package.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-jazzy-rosbag2-storage-mcap \
    ros-jazzy-demo-nodes-py \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Installed with the system pip (--break-system-packages), NOT uv: rclpy lives in
# this image's apt-managed system site-packages; a uv-managed venv would be isolated
# from it and could not `import rclpy`. Every local distribution goes into one
# invocation so pip resolves the workspace dependencies from the local paths instead
# of looking them up on PyPI (none of them is published there).
COPY packages/sceneops-core /workspace/packages/sceneops-core
COPY packages/sceneops-streaming /workspace/packages/sceneops-streaming
COPY apps/streaming-bridge /workspace/apps/streaming-bridge
RUN pip3 install --break-system-packages --no-cache-dir \
    /workspace/packages/sceneops-core \
    /workspace/packages/sceneops-streaming \
    /workspace/apps/streaming-bridge

WORKDIR /workspace
# The ROS entrypoint sources the ROS 2 environment before the app starts.
ENTRYPOINT ["/ros_entrypoint.sh", "python3", "-m", "sceneops_streaming_bridge"]
CMD ["--help"]
