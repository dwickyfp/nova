"""Measured accuracy of the business ontology.

The ontology claims to make agents answer correctly. This package measures it:

- ``gold``: a gold set built from published Semantic Views. Answerable cases are
  plans compiled by the governed compiler, phrased by a model and confirmed by
  judges; unanswerable and ambiguous cases test abstention and clarification.
- ``runner``: the path Studio takes (grounding, planning, compiling, executing
  as the caller), run with and without the ontology on the same cases.
- ``claims``: a stratified audit of what the ontology claims, judged against
  the published evidence, with the calibration of its stored confidence.
- ``metrics``: what the Overview shows, every number with n and an interval.

Runs are stored append-only in ``CONFIG_ONTOLOGY_EVAL_*`` and advanced by the
caller in small resumable steps, so no run depends on one long request.
"""
