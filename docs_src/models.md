# Models

Custom model weights go in `/user/models/`. Each model is a named folder.

## Folder layout

```
/user/models/
  mouse_finetuned/
    best.pt
    output_mapping.csv
```

`output_mapping.csv` maps the model's output indices to keypoint names. No header row — columns are `instance_type, keypoint, index`. The `instance_type` value is what you use in `instances` in the config.

```
mouse_top,nose,0
mouse_top,headcentre,1
mouse_top,neck,2
mouse_top,earl,3
mouse_top,earr,4
mouse_top,bodycentre,5
mouse_top,bcl,6
mouse_top,bcr,7
mouse_top,hipl,8
mouse_top,hipr,9
mouse_top,tailbase,10
mouse_top,tailcentre,11
mouse_top,tailtip,12
```

A single model file can define multiple instance types (e.g. `mouse_top` and `mouse_top_white`) — each becomes a valid `type` value in `instances`.

## Referencing in a config

Use the folder name as the `weights` value:

```toml
[models.mouse]
weights = "mouse_finetuned"
instances = [{ type = "mouse_top", max = 1 }]
batch = 32
```

Bundled models are checked first; `/user/models/` is the fallback. A missing folder won't error at pipeline-select time — it will fail when tracking starts.
