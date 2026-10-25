# Schemas

This directory holds the JSON Schemas for profile files and content artifacts. They are generated from the Pydantic models in `backend/app/domain/manifests/`, so do not edit them by hand.

- `manifest.v1.schema.json` describes a profile (manifest) file in `content/profiles/`.
- `artifact.v1.schema.json` describes an artifact file in `content/scripts/`, `content/instruments/`, `content/policies/`, or `content/coding/`.

To regenerate them after changing the models, run this from `backend/`:

```bash
uv run python -m app.domain.manifests.cli schemas --write
```

A test fails if the files here no longer match the models. Schemas for task worlds and claims were planned for later work packages (WP05 and WP10a) and are not in this directory.
