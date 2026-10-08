FROM ros:jazzy-ros-base
# ros-base ships the ROS 2 interface definitions (.msg files) that Capture writes
# MCAP schemas from (sceneops_recording.capture.message_definition). Capture is
# plain Python: it runs no ROS 2 node.

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Installed with the system pip (--break-system-packages), NOT uv: the ROS 2 Python
# packages (ament_index_python) live in this image's apt-managed system
# site-packages, which a uv-managed venv would not see. Every local distribution
# goes into one invocation so pip resolves the workspace dependencies from the
# local paths instead of looking them up on PyPI (none of them is published there).
COPY packages/sceneops-core /workspace/packages/sceneops-core
COPY packages/sceneops-storage /workspace/packages/sceneops-storage
COPY packages/sceneops-streaming /workspace/packages/sceneops-streaming
COPY packages/sceneops-recording /workspace/packages/sceneops-recording
COPY apps/capture /workspace/apps/capture
RUN pip3 install --break-system-packages --no-cache-dir \
    /workspace/packages/sceneops-core \
    /workspace/packages/sceneops-storage \
    /workspace/packages/sceneops-streaming \
    "/workspace/packages/sceneops-recording[capture]" \
    /workspace/apps/capture

# Captured recordings are written to the shared acquisition-recordings volume
# mounted at /recordings. Sticky + world-writable so the volume is usable by the
# non-root acquisition containers whichever service creates it first.
RUN mkdir -p /recordings && chmod 1777 /recordings

WORKDIR /workspace
# The ROS entrypoint sources the ROS 2 environment before the app starts.
ENTRYPOINT ["/ros_entrypoint.sh", "python3", "-m", "sceneops_capture"]
CMD ["--help"]
