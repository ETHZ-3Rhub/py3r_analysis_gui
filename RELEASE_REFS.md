# Release reference pins

Hand-edit these before tagging a release. CI reads this file to build a
"clean" release artifact from explicit, pinned versions of the two
out-of-repo dependencies — it never guesses "latest". Values can be tags
or full commit hashes.

py3r_behaviour: v0.1.0
pose_models: v0.1.0
