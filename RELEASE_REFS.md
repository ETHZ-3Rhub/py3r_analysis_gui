# Release reference pins

Hand-edit these before tagging a release. CI reads this file to build a
"clean" release artifact from explicit, pinned versions of the two
out-of-repo dependencies — it never guesses "latest". Values can be tags
or full commit hashes.

py3r_behaviour: v3.4.2
pose_models: ac241117611f28fa842aff46dcb63defd2b459e4
