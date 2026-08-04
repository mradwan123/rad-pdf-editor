"""Plugin registry: discovers and loads ToolPlugins, first-party and
third-party, under one mechanism (SPEC.md 6.1).

The exact discovery method (Python entry_points vs. a plugin.json
manifest scan) is an open item — see SPEC.md section 5 — but the
interface below (register / get / all_plugins / discover_and_load) is
frozen regardless of which discovery mechanism ends up backing it, so
other agents can build against this now.
"""

from __future__ import annotations

from core.errors import PluginCompatibilityError, PluginLoadError
from core.logging_config import get_logger
from core.registry.plugin_base import ToolPlugin

log = get_logger(__name__)

CORE_VERSION = "1.0.0"


class Registry:
    """Holds every loaded ToolPlugin, keyed by tool_id."""

    def __init__(self) -> None:
        self._plugins: dict[str, ToolPlugin] = {}

    def register(self, plugin: ToolPlugin) -> None:
        if plugin.tool_id in self._plugins:
            existing = type(self._plugins[plugin.tool_id]).__name__
            raise PluginLoadError(
                f"tool_id '{plugin.tool_id}' is already registered "
                f"(conflict with {existing})"
            )
        self._check_compatibility(plugin)
        self._plugins[plugin.tool_id] = plugin
        log.info("Registered plugin: %s", plugin.tool_id)

    def get(self, tool_id: str) -> ToolPlugin:
        try:
            return self._plugins[tool_id]
        except KeyError as exc:
            raise PluginLoadError(
                f"No plugin registered for tool_id '{tool_id}'"
            ) from exc

    def all_plugins(self) -> list[ToolPlugin]:
        return list(self._plugins.values())

    def _check_compatibility(self, plugin: ToolPlugin) -> None:
        # Placeholder for real semver range checking (e.g. via the
        # `packaging` library's SpecifierSet) — deferred, see SPEC.md
        # section 5 open items.
        if not plugin.compatible_core_version:
            raise PluginCompatibilityError(
                f"Plugin '{plugin.tool_id}' declares no compatible_core_version."
            )


def _first_party_plugins() -> list[ToolPlugin]:
    """Every first-party (core/ops/) plugin, in no particular order.

    Imports are local to this function rather than module-level so
    that importing `core.registry.registry` doesn't transitively
    import every op module (and their heavier dependencies like
    reportlab/pikepdf) just to build a `Registry` instance.
    """
    from core.ops.forms import (
        CreateFormFieldPlugin,
        FillFormPlugin,
        FlattenPlugin,
        RemoveAnnotationsPlugin,
        SignPlugin,
    )
    from core.ops.layout import CropPlugin, GrayscalePlugin, NUpPlugin, ResizePlugin
    from core.ops.merge_split import ExtractPagesPlugin, MergePlugin
    from core.ops.metadata import RenamePlugin, SetMetadataPlugin
    from core.ops.numbering import BatesNumberingPlugin, HeaderFooterPlugin
    from core.ops.organize import (
        CompressPlugin,
        DeletePagesPlugin,
        ReorderPagesPlugin,
        RotatePagesPlugin,
    )
    from core.ops.security import ProtectPlugin, UnlockPlugin
    from core.ops.watermark import WatermarkPlugin

    return [
        MergePlugin(),
        ExtractPagesPlugin(),
        ReorderPagesPlugin(),
        RotatePagesPlugin(),
        DeletePagesPlugin(),
        CompressPlugin(),
        SetMetadataPlugin(),
        RenamePlugin(),
        ProtectPlugin(),
        UnlockPlugin(),
        WatermarkPlugin(),
        CropPlugin(),
        ResizePlugin(),
        NUpPlugin(),
        GrayscalePlugin(),
        HeaderFooterPlugin(),
        BatesNumberingPlugin(),
        FlattenPlugin(),
        RemoveAnnotationsPlugin(),
        FillFormPlugin(),
        SignPlugin(),
        CreateFormFieldPlugin(),
    ]


def discover_and_load(registry: Registry) -> None:
    """Discover first-party plugins (core/ops/) and third-party plugins
    (/plugins/) and register them with `registry`.

    Third-party discovery mechanism TBD — see SPEC.md section 5. Only
    first-party registration is wired up so far.
    """
    for plugin in _first_party_plugins():
        registry.register(plugin)
