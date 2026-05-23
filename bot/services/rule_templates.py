from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TemplateSpec:
    name: str
    newcomer_restrict_enabled: bool
    keyword_filter_enabled: bool
    link_filter_enabled: bool
    flood_enabled: bool
    enforcement_mode: str
    flood_window_seconds: int
    flood_max_messages: int
    allow_auto_ban: bool


TEMPLATES: dict[str, TemplateSpec] = {
    "lenient": TemplateSpec(
        name="lenient",
        newcomer_restrict_enabled=False,
        keyword_filter_enabled=True,
        link_filter_enabled=False,
        flood_enabled=True,
        enforcement_mode="observe",
        flood_window_seconds=15,
        flood_max_messages=8,
        allow_auto_ban=False,
    ),
    "standard": TemplateSpec(
        name="standard",
        newcomer_restrict_enabled=True,
        keyword_filter_enabled=True,
        link_filter_enabled=True,
        flood_enabled=True,
        enforcement_mode="enforce",
        flood_window_seconds=10,
        flood_max_messages=5,
        allow_auto_ban=False,
    ),
    "strict_ads": TemplateSpec(
        name="strict_ads",
        newcomer_restrict_enabled=True,
        keyword_filter_enabled=True,
        link_filter_enabled=True,
        flood_enabled=True,
        enforcement_mode="enforce",
        flood_window_seconds=8,
        flood_max_messages=4,
        allow_auto_ban=True,
    ),
    "newcomer_guard": TemplateSpec(
        name="newcomer_guard",
        newcomer_restrict_enabled=True,
        keyword_filter_enabled=True,
        link_filter_enabled=True,
        flood_enabled=True,
        enforcement_mode="enforce",
        flood_window_seconds=12,
        flood_max_messages=5,
        allow_auto_ban=False,
    ),
    "night_anti_flood": TemplateSpec(
        name="night_anti_flood",
        newcomer_restrict_enabled=True,
        keyword_filter_enabled=True,
        link_filter_enabled=True,
        flood_enabled=True,
        enforcement_mode="enforce",
        flood_window_seconds=6,
        flood_max_messages=3,
        allow_auto_ban=False,
    ),
}


def template_names() -> list[str]:
    return sorted(TEMPLATES.keys())


def get_template(template_name: str) -> TemplateSpec | None:
    return TEMPLATES.get(template_name)
