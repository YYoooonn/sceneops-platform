from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper


def create_dummy_onnx_model(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    input_tensor = helper.make_tensor_value_info(
        "input",
        TensorProto.FLOAT,
        [1, 4],
    )
    output_tensor = helper.make_tensor_value_info(
        "output",
        TensorProto.FLOAT,
        [1, 4],
    )

    weight = helper.make_tensor(
        "weight",
        TensorProto.FLOAT,
        [4, 4],
        np.eye(4, dtype=np.float32).flatten().tolist(),
    )

    node = helper.make_node(
        "MatMul",
        inputs=["input", "weight"],
        outputs=["output"],
    )

    graph = helper.make_graph(
        [node],
        "dummy_detector",
        [input_tensor],
        [output_tensor],
        [weight],
    )

    model = helper.make_model(
        graph,
        producer_name="sceneops",
    )

    onnx.checker.check_model(model)
    onnx.save(model, output_path)

    print(f"Created dummy ONNX model: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="/data/models/dummy-detector/versions/v0/model.onnx",
    )
    args = parser.parse_args()

    create_dummy_onnx_model(Path(args.output))


if __name__ == "__main__":
    main()
