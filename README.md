# PDF Editor

A local, fully offline, cross-platform PDF editing suite: merge/split,
edit & sign, security (encrypt/watermark/flatten), compression,
conversion (Word/Excel/PowerPoint/HTML/JPG in both directions), OCR
& scan cleanup, and automation via saved Workflows.

Built for handling confidential/regulated documents — no network calls
anywhere in the codebase.

## Status

**Phase 0 — Foundation.** Core interfaces (`Operation`, `DocumentSession`,
`Pipeline`, plugin `Registry`) are scaffolded. No tools are implemented
yet — that's Phase 1.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

## Development

```bash
ruff check .      # lint
mypy core         # strict type-check on core/
pytest            # tests
```

## Documentation

- [`docs/SPEC.md`](docs/SPEC.md) — full technical specification,
  architecture, agent/workstream breakdown, and roadmap.
- [`CLAUDE.md`](CLAUDE.md) — quick-reference conventions for Claude
  Code sessions working in this repo.
