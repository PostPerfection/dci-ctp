# Planned

Re-run `run_corpus.py` and `diff/differential.py` before quoting any number
here. Four separate counts in this repo's docs have been wrong when checked.

## Coverage gaps

`ALL_CODES` is the full `Code::as_str` enum (120) and `UNCOVERED_REASONS` in
`run_corpus.py` carries a reason for every code without a fixture. The open
ones fall into two groups.

Need essence or inputs the corpus has no builder for: `projector_4k_stereo_support`
(4K stereoscopic essence) and the three KDM rules (`kdm_thumbprint_invalid`,
`kdm_content_authenticator_invalid`, `kdm_assume_trust_conflict`).

Cannot be isolated: `cpl_invalid_language` (the CPL language elements are
`xs:language`, so a bogus tag draws `xml_schema_violation` with it) and
`schema_validation_skipped` (fires only when no schema directory is found, and
dcpdoctor ships `schemas/`).

## Standing decisions

A third mastering tool was considered and declined. Every candidate that still
runs headless is abandoned, and the 28 ECL reference packages already provide
essence neither dcpwizard nor DCP-o-matic produced. Revisit only if a maintained
independent mastering tool appears.

Three codes stay WARNING in dcpdoctor where ClairMeta rejects the package, so
their fixtures sit in CLAIRMETA_ONLY_FAIL by policy (dcpdoctor's DESIGN_TODO
points here for the justification):

- `reel_discontinuity`: ST 429-2/-7 define no cross-reel EntryPoint continuity
  requirement. Each reel references an independent asset at an arbitrary entry
  point, so a non-contiguous chain is unusual but conformant.
- `pkl_missing_asset_reference`: no normative "shall" locates a PKL asset via the
  AssetMap (ST 429-9 maps UUIDs but does not reject a PKL-only asset), and a
  physically present, correctly hashed asset is still deliverable.
- `bv21_pkl_no_xml_ext`: the `.xml` extension is an ISDCF Bv2.1 naming
  convention, not a SMPTE normative requirement. The AssetMap references assets
  by path, so the extension does not affect resolution or playback.

The differential splits on pass/fail and never sees a warning, so a fixture
whose code is WARNING in dcpdoctor lands in BOTH_PASS or CLAIRMETA_ONLY_FAIL
without being a missed defect. `run_corpus.py` asserts every one of them fires.
