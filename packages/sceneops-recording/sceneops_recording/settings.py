"""Settings of a database-free recording client: the ArtifactStore it publishes to and
reads from, configured by ``SCENEOPS_PUBLISHER_ARTIFACT__*``. The Publisher process
(``apps/publisher``) reads them, and so do the tools that open the same store with the
publisher's credentials."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from sceneops_core.config import ArtifactSettings


class RecordingPublisherSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SCENEOPS_PUBLISHER_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    artifact: ArtifactSettings = Field(default_factory=ArtifactSettings)
