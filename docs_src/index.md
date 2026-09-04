# Analys3R

Select a pipeline, load your files, pick an output folder, run. Most things are labelled.

## Custom pipelines

The main way to get a custom pipeline is to **add it from a GitHub repo**: click the **Manage pipelines** button next to the pipeline dropdown, then **+ Add from URL** and paste the repo's URL (or just `owner/name`). The app installs it, keeps it grouped under that repo's own heading in the pipeline list, and checks for new releases automatically each time it starts. See [Pipeline sources](sources.md) for the full add/update/remove workflow, and for how to publish your own repo.

If you just have a single `.toml` config someone handed you directly (no repo), you can still drop it straight into `/user/configs/` next to the app as a manual fallback — useful for a one-off script or a quick local experiment that isn't worth publishing. It appears in the pipeline list the same way. Either way, anything not built into the app is untrusted: you'll be asked to confirm you trust the author before it runs.

## Going deeper

Whether a pipeline arrives via a git source or the manual `/user/` fallback, it's built from the same three pieces:

- [Pipeline sources](sources.md) — adding/updating/removing a GitHub-hosted pipeline, and the repo layout for publishing one
- [Configs](configs.md) — the `.toml` files that define a pipeline
- [Models](models.md) — bring your own YOLO weights
- [Scripts](scripts.md) — write a custom analysis script
