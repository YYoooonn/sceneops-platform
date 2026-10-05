"""MCAP ``ros2msg`` schema text from the installed ROS 2 interfaces.

An MCAP ROS 2 recording embeds each message type's definition: the type's
own ``.msg`` text followed by every type it depends on, each introduced by a
separator line and ``MSG: <package>/<Name>`` -- the format rosbag2's MCAP
plugin writes and every MCAP ROS 2 reader parses. The text comes from the
``.msg`` files of the ROS 2 distribution installed in this image, so the
definition always matches the interfaces the bridge's publishers use; there
is no hand-written schema text.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

from ament_index_python.packages import get_package_share_directory

SEPARATOR = "=" * 80

_PRIMITIVES = frozenset(
    {
        "bool",
        "byte",
        "char",
        "float32",
        "float64",
        "int8",
        "uint8",
        "int16",
        "uint16",
        "int32",
        "uint32",
        "int64",
        "uint64",
        "string",
        "wstring",
    }
)
_ARRAY_SUFFIX = re.compile(r"\[[^\]]*\]$")
_BOUND_SUFFIX = re.compile(r"<=\d+$")


def _split(type_name: str) -> tuple[str, str]:
    package, kind, name = type_name.split("/")
    if kind != "msg":
        raise ValueError(f"only message interfaces are supported, got {type_name!r}")
    return package, name


@cache
def _msg_text(package: str, name: str) -> str:
    path = Path(get_package_share_directory(package)) / "msg" / f"{name}.msg"
    return path.read_text()


def _dependencies(package: str, text: str) -> list[tuple[str, str]]:
    deps: list[tuple[str, str]] = []
    for line in text.splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) < 2:
            continue
        base = _BOUND_SUFFIX.sub("", _ARRAY_SUFFIX.sub("", fields[0]))
        if base in _PRIMITIVES:
            continue
        if base == "Header":
            deps.append(("std_msgs", "Header"))
        elif "/" in base:
            dep_package, dep_name = base.split("/")[0], base.split("/")[-1]
            deps.append((dep_package, dep_name))
        else:
            deps.append((package, base))
    return deps


@cache
def message_definition(type_name: str) -> str:
    """The ``ros2msg`` schema text of ``type_name`` (``pkg/msg/Name``)."""
    package, name = _split(type_name)
    top = _msg_text(package, name)
    ordered: list[tuple[str, str]] = []
    seen = {(package, name)}

    def visit(owner: str, text: str) -> None:
        for dep in _dependencies(owner, text):
            if dep in seen:
                continue
            seen.add(dep)
            ordered.append(dep)
            visit(dep[0], _msg_text(*dep))

    visit(package, top)
    parts = [top.rstrip("\n") + "\n"]
    for dep_package, dep_name in ordered:
        parts.append(
            f"{SEPARATOR}\nMSG: {dep_package}/{dep_name}\n"
            f"{_msg_text(dep_package, dep_name).rstrip(chr(10))}\n"
        )
    return "".join(parts)
