"""Plugin registry: discovers and loads ToolPlugins, first-party and
third-party, under one mechanism (SPEC.md 6.1).

**Third-party discovery format, resolved (SPEC.md section 5's open
item)**: a simple `plugin.json` manifest per plugin directory under
`/plugins`, not Python `entry_points`. SPEC.md section 1 locks this
project's distribution model as "small team, local installs, no
server component" — `entry_points` requires a plugin to be a properly
pip-installed package (its own `pyproject.toml`, a real install step)
just to register one tool, real friction for a teammate authoring one
operation. A folder dropped into `/plugins` with a manifest, scanned
at startup, is the lower-ceremony mechanism that actually fits. See
`plugins/README.md` for the manifest schema and a walkthrough.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from core.errors import PluginCompatibilityError, PluginLoadError
from core.logging_config import get_logger
from core.registry.plugin_base import PLUGIN_MANIFEST_SCHEMA_VERSION, ToolPlugin

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
    from core.ops.annotate import (
        AddAnnotationPlugin,
        DeleteAnnotationPlugin,
        EditAnnotationPlugin,
    )
    from core.ops.convert_from import (
        PdfToDocxPlugin,
        PdfToHtmlPlugin,
        PdfToJpgPlugin,
        PdfToPptxPlugin,
        PdfToXlsxPlugin,
    )
    from core.ops.convert_to import (
        DocxToPdfPlugin,
        HtmlToPdfPlugin,
        JpgToPdfPlugin,
        PptxToPdfPlugin,
        XlsxToPdfPlugin,
    )
    from core.ops.forms import (
        CreateFormFieldPlugin,
        FillFormPlugin,
        FlattenPlugin,
        RemoveAnnotationsPlugin,
        SignPlugin,
    )
    from core.ops.layout import CropPlugin, FlipPlugin, GrayscalePlugin, NUpPlugin, ResizePlugin
    from core.ops.merge_split import ExtractPagesPlugin, MergePlugin
    from core.ops.metadata import RenamePlugin, SetMetadataPlugin
    from core.ops.numbering import BatesNumberingPlugin, HeaderFooterPlugin
    from core.ops.ocr_scan import DeskewPlugin, OCRPlugin
    from core.ops.organize import (
        CompressPlugin,
        DeletePagesPlugin,
        ReorderPagesPlugin,
        RotatePagesPlugin,
    )
    from core.ops.repair import RepairPlugin
    from core.ops.security import ProtectPlugin, UnlockPlugin
    from core.ops.watermark import WatermarkPlugin

    return [
        AddAnnotationPlugin(),
        EditAnnotationPlugin(),
        DeleteAnnotationPlugin(),
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
        FlipPlugin(),
        HeaderFooterPlugin(),
        BatesNumberingPlugin(),
        FlattenPlugin(),
        RemoveAnnotationsPlugin(),
        FillFormPlugin(),
        SignPlugin(),
        CreateFormFieldPlugin(),
        PdfToDocxPlugin(),
        PdfToPptxPlugin(),
        PdfToXlsxPlugin(),
        PdfToHtmlPlugin(),
        PdfToJpgPlugin(),
        DocxToPdfPlugin(),
        PptxToPdfPlugin(),
        XlsxToPdfPlugin(),
        HtmlToPdfPlugin(),
        JpgToPdfPlugin(),
        OCRPlugin(),
        DeskewPlugin(),
        RepairPlugin(),
    ]


def _default_plugins_dir() -> Path:
    # core/registry/registry.py -> core/registry -> core -> repo root
    return Path(__file__).resolve().parents[2] / "plugins"


def _load_plugin_class(module_path: Path, class_name: str) -> type[ToolPlugin]:
    # A unique synthetic module name, not just the file stem (which
    # could collide across plugins - e.g. two plugins each naming
    # their own module "operation.py", as this project's own example
    # plugin does). Registering it in sys.modules *before*
    # exec_module() matters, not just for style: confirmed by hand
    # that omitting this step raises `AttributeError: 'NoneType'
    # object has no attribute '__dict__'` for any dataclass-based
    # Operation - Python's dataclasses machinery looks itself up via
    # `sys.modules[cls.__module__]` while processing field annotations
    # (this project's `from __future__ import annotations` style makes
    # every Operation hit this path), so the module must already be
    # registered under that exact name for a dynamically-loaded module
    # to work at all, not a hypothetical edge case.
    module_name = f"pdfeditor_plugin_{module_path.parent.name}_{module_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise PluginLoadError(f"Could not load plugin module '{module_path}'.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    try:
        plugin_class = getattr(module, class_name)
    except AttributeError as exc:
        raise PluginLoadError(f"'{module_path}' has no class '{class_name}'.") from exc
    if not (isinstance(plugin_class, type) and issubclass(plugin_class, ToolPlugin)):
        raise PluginLoadError(f"'{class_name}' in '{module_path}' is not a ToolPlugin subclass.")
    return plugin_class


def _third_party_plugins(plugins_dir: Path) -> list[ToolPlugin]:
    """Scan `plugins_dir/*/plugin.json` manifests and load each
    declared ToolPlugin (see `plugins/README.md` for the schema).

    A malformed manifest, or a module/class that fails to load, logs a
    warning and is skipped rather than raising - one broken
    third-party plugin must never prevent the app from starting.
    """
    plugins: list[ToolPlugin] = []
    if not plugins_dir.is_dir():
        return plugins

    for manifest_path in sorted(plugins_dir.glob("*/plugin.json")):
        plugin_dir = manifest_path.parent
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read plugin manifest '%s': %s", manifest_path, exc)
            continue

        if manifest.get("schema_version") != PLUGIN_MANIFEST_SCHEMA_VERSION:
            log.warning(
                "Skipping plugin manifest '%s': unsupported schema_version %r",
                manifest_path,
                manifest.get("schema_version"),
            )
            continue

        try:
            module_name = manifest["module"]
            class_name = manifest["plugin_class"]
        except KeyError as exc:
            log.warning("Skipping plugin manifest '%s': missing field %s", manifest_path, exc)
            continue

        try:
            plugin_class = _load_plugin_class(plugin_dir / module_name, class_name)
            plugins.append(plugin_class())
        except Exception as exc:  # noqa: BLE001 - a broken third-party plugin must not crash the app
            log.warning("Skipping plugin '%s': %s", plugin_dir.name, exc)
            continue

    return plugins


def discover_and_load(registry: Registry, plugins_dir: Path | None = None) -> None:
    """Discover first-party plugins (core/ops/) and third-party
    plugins (plugin.json manifests under `plugins_dir`, defaulting to
    the repo's `/plugins` - `plugins_dir` is mainly for tests) and
    register them with `registry`.

    A third-party plugin that fails to register (e.g. a duplicate
    tool_id, or an incompatible `compatible_core_version`) is skipped
    with a logged warning, same "never crash app startup" policy as
    `_third_party_plugins`'s own load failures.
    """
    for plugin in _first_party_plugins():
        registry.register(plugin)
    for plugin in _third_party_plugins(plugins_dir or _default_plugins_dir()):
        try:
            registry.register(plugin)
        except (PluginLoadError, PluginCompatibilityError) as exc:
            log.warning("Skipping third-party plugin '%s': %s", plugin.tool_id, exc)
