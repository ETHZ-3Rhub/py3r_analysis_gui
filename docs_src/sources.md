# Pipeline sources

A "pipeline source" is a public GitHub repo that packages one or more pipelines for the app to install directly — this is the recommended way to distribute a custom pipeline to a lab, since the app then handles installing it and picking up new versions.

## Adding a source

Click the **Manage pipelines** button next to the pipeline dropdown, then **+ Add from URL**. Paste a GitHub URL (or just `owner/name`) and optionally pin a version — leave it blank to install the latest stable release.

Installed pipelines appear in the main pipeline dropdown grouped under their own heading naming the repo, below the built-ins. Like any pipeline that isn't built into the app, you'll be asked to confirm you trust the author before running one for the first time — the prompt names the repo.

## Updating

The app checks every installed source for a newer release automatically, once per launch — there's no manual "check for updates" button. If one is found, the **Manage pipelines** button itself gains an up-arrow and lists the repos with updates in its tooltip; open it and each source with a pending update shows an inline **⬆ Update to vX** link.

To roll back to an older version (or pin a specific one), use **+ Add from URL** again with the same repo and the version you want in the **Version** field — it reinstalls in place, overwriting the current install.

## Hiding and removing

Each pipeline row has a **Hide**/**Show** toggle — this only affects whether it shows up in the picker, nothing is deleted. To remove a source entirely, use the **✕** on its header row; this deletes everything installed from it and can't be undone (re-add the URL to reinstall).

## Publishing your own source repo

A source repo has the same three top-level folders as the manual `/user/` fallback — `configs/`, `scripts/`, `models/` — see [Configs](configs.md), [Models](models.md), and [Scripts](scripts.md) for their contents. The only difference is that paths inside a config (`entry`, `weights`) resolve relative to the repo's own root, not `/user/`.

```
configs/my_pipeline.toml
scripts/my_pipeline.py
models/my_model/
  best.pt
  output_mapping.csv
```

Tag a GitHub release (e.g. `v1.0.0`) each time you want lab users to be able to install or update to that version. **Only a published, non-prerelease release is picked up as the "latest" version on first install** — mark a release as pre-release while you're still testing it, or install/pin it explicitly by tag in the meantime.

### Large model weights

Don't check large weight files straight into the repo — every retrain would permanently bloat the repo's git history, since git can't diff binaries. Instead, publish weights as a release asset and point at them with a `source.toml` pointer file in place of the real folder contents:

```
models/my_model/
  source.toml
```

```toml
# models/my_model/source.toml
repo = "owner/repo"
ref = "v1.0.0"
asset = "my_model.zip"
```

(`url = "https://..."` also works in place of `repo`/`ref`/`asset`, for a weights file hosted somewhere other than this repo's own releases.)

Then attach `my_model.zip` to that GitHub release — a zip of the model folder's contents at its root (`best.pt` and `output_mapping.csv` directly, not nested inside another folder). On install, the app downloads the asset over plain HTTPS and unpacks it in place of the pointer — this sidesteps Git LFS entirely, since GitHub's repo zip download does not resolve LFS pointers. If you check LFS-tracked weights straight into the repo without a pointer, install fails with a clear error rather than silently installing a corrupt model.

### Trust and access

The repo must be **public** — there's no private-repo or access-token support. Anything installed this way is untrusted the same as a hand-copied `/user` file; it's the author's job to publish something lab members will actually trust, and the confirm dialog names your repo so they know where it came from.
