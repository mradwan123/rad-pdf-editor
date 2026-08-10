# Third-party plugins

This directory is scanned at startup (`core/registry/registry.py`'s
`discover_and_load`) for team-authored tools, registered under the
exact same `ToolPlugin` contract every first-party tool in `core/ops/`
uses — there is only ever one extensibility system (`docs/SPEC.md`
sections 2 and 6.1).

## Format: `plugin.json`, not Python `entry_points`

Each plugin is a folder under `plugins/` containing a `plugin.json`
manifest plus its own Python module(s). This is a deliberate choice,
not the only option considered: `docs/SPEC.md` section 1 locks this
project's distribution model as "small team, local installs — no
server component, no shared license/auth system needed." Python's
`entry_points` mechanism would require a plugin to be a properly
pip-installed package (its own `pyproject.toml`, a real install step)
just to register one tool — real friction for a teammate who wants to
add one operation. Dropping a folder in and having it picked up at the
next launch is the lower-ceremony mechanism that actually fits.

## Directory layout

```
plugins/
  your_plugin_name/
    plugin.json
    your_module.py       # whatever name you like, referenced by plugin.json
```

## `plugin.json` schema

| Field | Type | Required | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Must be `1` (matches `core.registry.plugin_base.PLUGIN_MANIFEST_SCHEMA_VERSION`). A manifest with any other value is skipped. |
| `tool_id` | string | yes | Documentation only — see note below. Should match the loaded class's own `tool_id` attribute, which is the actual value used at runtime. |
| `display_name` | string | yes | Documentation only, same note. |
| `module` | string | yes | Filename of the Python module to load, relative to this plugin's own directory (e.g. `"operation.py"`). |
| `plugin_class` | string | yes | Name of the `ToolPlugin` subclass inside that module to instantiate. |
| `compatible_core_version` | string | yes | Documentation only, same note — the loaded class's own `compatible_core_version` attribute is what the registry actually checks (`Registry._check_compatibility`). |

**Note on the "documentation only" fields**: `tool_id`/`display_name`/
`compatible_core_version` are *not* read out of the manifest by the
loader — the instantiated `ToolPlugin` class is the actual source of
truth for all three, exactly as it is for a first-party plugin. They're
in the manifest so a human (or a future manifest-only listing feature)
can see what a plugin declares without importing its code. Keep them
in sync with the class by hand for now; there's no automatic
cross-check.

## Writing a plugin

Your `ToolPlugin`/`Operation` subclasses follow the exact same contract
as every built-in tool — read `core/registry/plugin_base.py` and
`core/model/operation.py` in full before starting; they're the source
of truth, not this file. In short: `Operation` implements `apply()`,
`invert()`, `serialize()`, `describe()`; `ToolPlugin` implements
`build_operation(**kwargs)` and `operation_class()`. Reuse
`core/ops/common.py`'s helpers (`allocate_working_path`, `next_session`,
`open_pdf`, `read_working_bytes`, `snapshot_restore_invert`,
`resolve_page_targets`) exactly like first-party ops do — they're
usable by external code, not `core/ops`-private, and they're what
gives your plugin undo/redo, autosave, and Workflow save/replay for
free without writing any of that yourself.

`plugins/example_plugin/` is a complete, real, working example — not
just illustrative markdown — a "Reverse Page Order" tool. Read
`plugins/example_plugin/operation.py` and `plugins/example_plugin/plugin.json`
side by side as the walkthrough:

1. `plugin.json` declares `module: "operation.py"`,
   `plugin_class: "ReversePagesPlugin"`.
2. `operation.py` defines `ReversePagesOperation` (the `Operation`) and
   `ReversePagesPlugin` (the `ToolPlugin`), following the identical
   dataclass-with-a-`_pre_snapshot`-field shape every `core/ops/*.py`
   module uses.
3. Drop the folder in `plugins/`, and the next `discover_and_load()`
   call (every CLI invocation, every GUI launch) picks it up
   automatically — no registration step, no restart-with-a-flag.

## Failure handling

A malformed manifest (bad JSON, missing required field, unsupported
`schema_version`), or a module/class that fails to import or
instantiate, is skipped with a logged warning — **never** a startup
crash. One broken third-party plugin must not take down the app for
everyone else. Check the app log (`core.logging_config`) if a plugin
you expect to see isn't registered.

## Known limitation: not auto-exposed as a CLI subcommand or Tools-menu entry

This is a real, current scope boundary, not specific to third-party
plugins — **every** tool, first-party included, needs its own
hand-written `argparse` subparser in `cli/main.py` and its own entry in
`gui/dialogs/tool_dialog_registry.py`'s `TOOL_DIALOGS` to get a
dedicated CLI subcommand or Tools-menu dialog; there's no generic
"any registered tool_id automatically gets a CLI/GUI surface"
mechanism (yet). A third-party plugin *is* fully usable today via:

- **Programmatically**: `registry.get(tool_id).build_operation(**kwargs)`,
  same as every first-party plugin.
- **Workflows**: the GUI's Workflow builder ("Build Workflow..." menu)
  lists every registered plugin (first- and third-party) in its
  "Add Step..." picker. If your plugin has no matching entry in
  `TOOL_DIALOGS`, it's treated as taking no configuration and built
  directly with no dialog shown — true for `reverse_pages`, and a
  reasonable default for any plugin whose `build_operation()` doesn't
  require kwargs. The CLI's `run-workflow` command replays a saved
  Workflow the same way, so a third-party step works there too.

Shipping your own dialog and wiring a CLI subcommand for your plugin
is possible (follow any existing `core/ops/*.py` + its dialog + its
`cli/main.py` subparser as a template) but is your plugin's own code
to add, not something this discovery mechanism does for you.
