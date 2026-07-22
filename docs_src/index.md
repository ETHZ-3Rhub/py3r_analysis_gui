# Analys3R

Select a pipeline, load your files, pick an output folder, run. Most things are labelled.

## Custom pipelines

If someone sent you a `.toml` pipeline file, drop it in `/user/configs/` next to the app. It will appear in the pipeline list. You'll be asked to confirm you trust it before it runs.

## Going deeper

The `/user/` folder next to the app has three subfolders — each has its own page:

- [Configs](configs.md) — the `.toml` files that define a pipeline
- [Models](models.md) — bring your own YOLO weights
- [Scripts](scripts.md) — write a custom analysis script
