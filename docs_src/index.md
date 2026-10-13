# Analys3R

Select a pipeline, load your files, pick an output folder, run. Most things are labelled.

## Custom pipelines

The main way to get a custom pipeline is to **add it from a GitHub repo**: click the **Manage pipelines** button next to the pipeline dropdown, then **+ Add from URL** and paste the repo's URL (or just `owner/name`). The app installs it, keeps it grouped under that repo's own heading in the pipeline list, and checks for new releases automatically each time it starts. See [Pipeline sources](sources.md) for the full add/update/remove workflow, and for how to publish your own repo.

If you just have a single `.toml` config someone handed you directly (no repo), you can still drop it straight into `configs/` in your [user folder](#where-your-files-are-kept) as a manual fallback (Manage pipelines → **Open user folder** takes you there) — useful for a one-off script or a quick local experiment that isn't worth publishing. It appears in the pipeline list the same way. Either way, anything not built into the app is untrusted: you'll be asked to confirm you trust the author before it runs.

## Where your files are kept

Pipelines you add and the tracking environment the app installs are kept in a folder of their own, separate from the app. Replacing the app with a new version doesn't touch them.

| System  | Folder |
|---------|--------|
| Windows | `%LOCALAPPDATA%\Analys3R` (usually `C:\Users\<you>\AppData\Local\Analys3R`) |
| macOS   | `~/Library/Application Support/Analys3R` |

Inside it:

- `user/` — your pipelines: the manual fallback's `configs/`, `scripts/` and `models/`, plus everything installed from GitHub. Manage pipelines → **Open user folder** opens it.
- `tracking_env/` — the tracking environment (Python, PyTorch and YOLO, several GB). Settings → **(Re)install tracking environment** recreates it.

On macOS the `Library` folder is hidden in Finder. Use **Open user folder**, or in Finder choose Go → Go to Folder… and paste the path.

**Removing the app completely:** delete the app, then delete this folder.

**Upgrading from an older Windows version:** earlier versions kept `user` next to `Analys3R.exe`. If you extract the new version over the old one, it copies that folder across the first time it starts (the old copy stays where it was). If you put the new version somewhere else, copy the old `user` folder into the folder above yourself. Either way the tracking environment has to be reinstalled once.

## Going deeper

Whether a pipeline arrives via a git source or the manual user-folder fallback, it's built from the same three pieces:

- [Pipeline sources](sources.md) — adding/updating/removing a GitHub-hosted pipeline, and the repo layout for publishing one
- [Configs](configs.md) — the `.toml` files that define a pipeline
- [Models](models.md) — bring your own YOLO weights
- [Scripts](scripts.md) — write a custom analysis script
