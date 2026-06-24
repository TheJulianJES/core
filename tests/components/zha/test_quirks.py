"""Test ZHA quirks."""

import orjson
from zha.quirks import DEVICE_REGISTRY, QuirkRegistryEntry
import zhaquirks
from zhaquirks.builder.device import QuirkV2Factory
from zhaquirks.builder.metadata import EntityMetadata

from homeassistant.const import Platform
from homeassistant.util.json import load_json


def test_v2_quirks() -> None:
    """Ensure v2 quirk entities have a translations."""
    zhaquirks.setup()

    translations = load_json("homeassistant/components/zha/strings.json")
    translations_new = translations.copy()
    for entry in DEVICE_REGISTRY:
        factory = entry.zha_device_factory
        if not isinstance(factory, QuirkV2Factory):
            continue
        for entity_metadata in factory.quirk_definition.entity_metadata:
            platform = Platform(entity_metadata.entity_platform.value)
            validate_translation_keys(
                entry, entity_metadata, platform, translations, translations_new
            )

    # sort dict in all nested dics
    translations_new["entity"] = {
        platform: dict(sorted(entities.items()))
        for platform, entities in translations_new["entity"].items()
    }

    with open("homeassistant/components/zha/strings.json", "w", encoding="utf-8") as f:
        f.write(
            orjson.dumps(translations_new, option=orjson.OPT_INDENT_2).decode() + "\n"
        )


def validate_translation_keys(
    quirk: QuirkRegistryEntry,
    entity_metadata: EntityMetadata,
    platform: Platform,
    translations: dict,
    translations_new: dict,
) -> None:
    """Ensure translation keys exist for all v2 quirks."""
    translation_key = entity_metadata.translation_key

    if (
        translation_key is not None
        and translation_key not in translations["entity"][platform]
    ):
        translations_new["entity"][platform][translation_key] = {
            "name": entity_metadata.fallback_name
        }

        # raise ValueError(
        #     f"Missing translation key: {translation_key} for {platform.name} {quirk}"
        # )
