# Release reference pins

Hand-edit these before tagging a release. CI reads this file to build a
"clean" release artifact from explicit, pinned versions of the two
out-of-repo dependencies — it never guesses "latest". Values can be tags
or full commit hashes.

py3r_behaviour: v3.4.0
pose_models: 92942ee5c71f4b2661cd38a94fcab8437525e431
