# --------------------
# ROS2 (Jazzy) dev sandbox — robot runtime environment (roadmap Phase 4)
# --------------------

.PHONY: ros2-up
ros2-up:
	docker compose -f $(COMPOSE_FILE) --profile ros2 up -d --build

.PHONY: ros2-down
ros2-down:
	docker compose -f $(COMPOSE_FILE) --profile ros2 down

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
