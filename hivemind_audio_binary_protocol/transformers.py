from typing import List, Optional

from ovos_config import Configuration
from ovos_plugin_manager.transformer_services import (
    DialogTransformersService as _DialogTransformersService,
    MetadataTransformersService as _MetadataTransformersService,
    UtteranceTransformersService as _UtteranceTransformersService)


def _section_config(section: str, enabled_plugins: Optional[List[str]]) -> dict:
    """Build a transformer config section gated by an explicit plugin list.

    When ``enabled_plugins`` is given, only those plugins are enabled,
    keeping any per-plugin config from the deployer configuration.
    Reserved (non-plugin) keys are preserved.
    """
    config = dict(Configuration().get(section) or {})
    if enabled_plugins:
        reserved = {k: v for k, v in config.items()
                    if k in ("order", "blacklisted_skills")}
        config = {k: config.get(k) or {} for k in enabled_plugins}
        config.update(reserved)
    return config


class DialogTransformersService(_DialogTransformersService):
    """Transforms dialogs before being sent to TTS."""

    def __init__(self, bus, enabled_plugins: Optional[List[str]] = None):
        super().__init__(bus=bus,
                         config=_section_config("dialog_transformers",
                                                enabled_plugins))


class UtteranceTransformersService(_UtteranceTransformersService):
    """Transforms utterances after STT."""

    def __init__(self, bus, enabled_plugins: Optional[List[str]] = None):
        super().__init__(bus=bus,
                         config=_section_config("utterance_transformers",
                                                enabled_plugins))


class MetadataTransformersService(_MetadataTransformersService):
    """Transforms message context after utterance transformers."""

    def __init__(self, bus, enabled_plugins: Optional[List[str]] = None):
        super().__init__(bus=bus,
                         config=_section_config("metadata_transformers",
                                                enabled_plugins))
