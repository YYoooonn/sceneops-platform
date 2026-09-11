# --------------------
# ROS2 (Jazzy) dev sandbox — robot runtime environment (roadmap Phase 4)
# --------------------

.PHONY: ros2-up
ros2-up:
	docker compose -f $(COMPOSE_FILE) --profile ros2 up -d --build

.PHONY: ros2-down
# Named service, not `--profile ros2 down` bare — the latter also tears down
# every default-profile service (postgres/redis/api), not just this one,
# since profile flags only ADD services to the "down" set, they don't scope
# it. Naming `ros2` here keeps this from taking the rest of local-up with it.
ros2-down:
	docker compose -f $(COMPOSE_FILE) --profile ros2 down ros2

.PHONY: ros2-shell
ros2-shell:
	docker compose -f $(COMPOSE_FILE) --profile ros2 exec ros2 bash

.PHONY: ros2-run
ros2-run:
	@if [ -z "$(ROS2_CMD)" ]; then \
		echo "ROS2_CMD is required. Usage: make ros2-run ROS2_CMD='ros2 topic list'"; \
		exit 1; \
	fi
	docker compose -f $(COMPOSE_FILE) --profile ros2 run --rm ros2 sh -c "$(ROS2_CMD)"

.PHONY: ros2-logs
ros2-logs:
	docker compose -f $(COMPOSE_FILE) --profile ros2 logs -f ros2

.PHONY: ros2-check
ros2-check:
	docker compose -f $(COMPOSE_FILE) --profile ros2 run --rm ros2 sh -c \
		"python3 -c 'import rclpy; print(\"rclpy ok\")' && \
		 ros2 pkg list | grep -q rosbag2_storage_mcap && echo 'mcap storage plugin ok'"

.PHONY: ros2-can-replay
ros2-can-replay:
	docker compose -f $(COMPOSE_FILE) --profile ros2 run --rm ros2 \
		python3 /workspace/nodes/can_replay_node.py --scene $(or $(SCENE),scene-0061) --rate $(or $(RATE),1.0)

.PHONY: ros2-can-replay-record
# DURATION bounds the recorder with `timeout` instead of signalling it to stop
# when the replay finishes — sending SIGINT to a backgrounded `ros2 bag
# record` from this shell doesn't reliably reach the actual recorder process,
# and letting it hang means `wait` never returns. Default covers a full
# realtime (RATE=1.0) nuScenes scene (~20s) plus margin; raise it for slower
# rates.
ros2-can-replay-record:
	@scene=$(or $(SCENE),scene-0061); \
	rate=$(or $(RATE),5.0); \
	duration=$(or $(DURATION),30); \
	out=/data/raw/rosbag/$$scene; \
	docker compose -f $(COMPOSE_FILE) --profile ros2 run --rm ros2 sh -c " \
		rm -rf $$out && \
		timeout $$duration ros2 bag record -o $$out --storage mcap \
			/vehicle/odom /vehicle/imu /vehicle/status /vehicle/control /mission/status & \
		sleep 2; \
		python3 /workspace/nodes/can_replay_node.py --scene $$scene --rate $$rate; \
		wait \
	"
