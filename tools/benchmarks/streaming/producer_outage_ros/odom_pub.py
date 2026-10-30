"""Publishes synthetic /vehicle/odom messages at a fixed rate once the bridge subscribes.
Run inside the streaming-bridge image by experiment_producer_outage.py.
"""

import argparse
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy
from nav_msgs.msg import Odometry

ap = argparse.ArgumentParser()
ap.add_argument("--rate", type=float, default=1000)
ap.add_argument("--count", type=int, default=10000)
ap.add_argument("--wait-subscriber", type=float, default=60)
a = ap.parse_args()
rclpy.init()
n = Node("odom_pub")
pub = n.create_publisher(
    Odometry,
    "/vehicle/odom",
    QoSProfile(
        reliability=QoSReliabilityPolicy.RELIABLE, history=QoSHistoryPolicy.KEEP_ALL
    ),
)
t0 = time.time()
while pub.get_subscription_count() == 0 and time.time() - t0 < a.wait_subscriber:
    time.sleep(0.1)
print("subscribers", pub.get_subscription_count(), flush=True)
start = time.monotonic()
for i in range(a.count):
    m = Odometry()
    m.header.stamp.sec = 1_700_000_000
    m.header.stamp.nanosec = i + 1
    m.header.frame_id = "odom"
    pub.publish(m)
    d = start + (i + 1) / a.rate - time.monotonic()
    if d > 0:
        time.sleep(d)
print(f"published {a.count} in {time.monotonic() - start:.2f}s", flush=True)
time.sleep(2)
