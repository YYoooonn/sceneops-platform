"""Dataset Integration source preservation (ADR-015 §5).

Reads a local source tree as a declared selection of files, identifies it
(``sceneops_core.sources``), publishes the preserved bytes to the object store
without a database, and gives format readers verified access to those bytes.
Nothing here interprets a file: format readers and canonical builders sit on
top of ``ContentFiles``.
"""

from .content_files import (
    ContentFileError,
    ContentFileIntegrityError,
    ContentFileMissingError,
    ContentFiles,
    LocalContentFiles,
    StoredContentFiles,
    load_stored_inventory,
)
from .layout import (
    SourceLayoutError,
    content_file_uri,
    content_files_prefix,
    content_id_of_inventory_uri,
    descriptor_uri,
    inventory_uri,
    parse_descriptor_uri,
)
from .publication import (
    ContentPublicationConflictError,
    PublishedSnapshot,
    SnapshotPublicationConflictError,
    publish_snapshot,
)
from .tree import (
    SourceFileChangedError,
    SourceTreeError,
    build_inventory,
    hash_file,
    select_files,
)

__all__ = [
    "ContentFileError",
    "ContentFileIntegrityError",
    "ContentFileMissingError",
    "ContentFiles",
    "ContentPublicationConflictError",
    "LocalContentFiles",
    "PublishedSnapshot",
    "SnapshotPublicationConflictError",
    "SourceFileChangedError",
    "SourceLayoutError",
    "SourceTreeError",
    "StoredContentFiles",
    "build_inventory",
    "content_file_uri",
    "content_files_prefix",
    "content_id_of_inventory_uri",
    "descriptor_uri",
    "hash_file",
    "inventory_uri",
    "load_stored_inventory",
    "parse_descriptor_uri",
    "publish_snapshot",
    "select_files",
]
