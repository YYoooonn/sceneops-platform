"""Independent, source-grounded verification of the reference contract.

Rules: docs/development/reference-verification.md. Standard library plus
``zstandard`` / ``lz4`` (MCAP chunk decompression) and, for collecting from a
running platform only, ``boto3``. No SceneOps package is imported.
"""
