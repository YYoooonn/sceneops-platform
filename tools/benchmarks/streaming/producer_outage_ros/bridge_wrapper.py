"""The real bridge main() with librdkafka's queue cut to QUEUE_MESSAGES.

OLD_PRODUCE=1 restores the earlier produce-then-poll(0); OLD_EXIT=1 the earlier exit
decision that ignored an unconfirmed delivery. Run inside the streaming-bridge image
by experiment_producer_outage.py.
"""

import os
import sys

from sceneops_streaming import producer as pm

real = pm.ConfluentProducer
limit = int(os.environ["QUEUE_MESSAGES"])
pm.ConfluentProducer = lambda config: real(
    {**config, "queue.buffering.max.messages": limit}
)
if os.environ.get("OLD_PRODUCE") == "1":

    def _old(self, record):
        self._producer.produce(
            topic=self._topic,
            key=record.key,
            value=record.value,
            headers=record.headers,
            on_delivery=self._on_delivery,
        )
        self._producer.poll(0)

    pm.KafkaTelemetryProducer._produce_sync = _old
sys.argv[0] = "sceneops_streaming_bridge"
# Imported after the patches above so the bridge builds its producer from them.
from sceneops_streaming_bridge.node import StreamingBridgeNode, main  # noqa: E402

if os.environ.get("OLD_EXIT") == "1":
    # The exit decision before delivery_unconfirmed existed: failed_count only.
    StreamingBridgeNode.delivery_unconfirmed = property(
        lambda self: False, lambda self, v: None
    )
main()
