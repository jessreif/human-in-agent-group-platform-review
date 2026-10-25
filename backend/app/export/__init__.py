"""Offline export package (WP11: INV-1106..1110, spec §9.2/§9.3/§9.5).

All exports here are offline products over *committed* records. They never run inside the
participant request path and never read ``identity.*`` unless the product is the restricted
payment export (:mod:`app.export.restricted`), which must run as ``investigators_compensation``.

Modules:

- :mod:`app.export.study` — INV-1106 consistent-snapshot study export (participant-wide,
  crate-long, event/message/model/context/claim/evidence/episode/revision/exposure/incident
  tables + fidelity-rate table), one REPEATABLE READ snapshot.
- :mod:`app.export.aggregates` — INV-1108 compatibility-filtered aggregates and descriptive
  technical-incident summaries; grouping by ``compatibility_key`` is mandatory.
- :mod:`app.export.restricted` — INV-1109 restricted payment export and identity-free
  sharing bundles with free-text review boundaries.
- :mod:`app.export.csvsafe` — INV-1109 formula-safe CSV writing + manifest checksums.
- :mod:`app.export.legacy` — INV-1107 INV_FINISH legacy-field reconciliation table.
- :mod:`app.export.historical` — INV-1110 historical Qualtrics import contract (validation
  + ``not_applicable`` stub until raw inputs exist).
"""
