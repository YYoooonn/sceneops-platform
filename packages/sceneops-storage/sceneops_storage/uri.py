from __future__ import annotations


def join_uri(root: str, *parts: str) -> str:
    normalized_root = root.rstrip("/")
    normalized_parts = [part.strip("/") for part in parts if part.strip("/")]

    if not normalized_parts:
        return normalized_root

    return "/".join([normalized_root, *normalized_parts])
