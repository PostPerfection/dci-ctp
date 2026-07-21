# Planned

The per-error-code corpus (`scripts/build_corpus.sh` + `run_corpus.py`) proves
46 of 63 dcpdoctor codes fire through `dcpdoctor validate`, each non-vacuously
(the code is asserted absent on the valid baseline): 42 via isolated synthetic
fixtures, 4 more via the ClairMeta ECL reference packages. Baselines are real
dcpwizard builds: labeled 5.1 (`valid/dcp_ov`), stereoscopic 3D 429-10
(`valid/dcp_3d`), Atmos AuxData 429-18 (`valid/dcp_atmos`), all clean under
`--strict --check-mxf`. 74 harness checks pass.

## Differential vs ClairMeta (diff/differential.py): current state

All previously recorded findings were fixed in dcpdoctor (sha1 signature
dispatch, default-on XSD schema validation via vendored `schemas/`,
kdm_not_yet_valid/kdm_expired split, invalid_uuid wired, OV-aware VF semantics,
Interop scoping, deep cert rules, sound quantization/blockalign, CPL metadata,
foreign/empty-file hygiene, reel coherence, aux-vs-picture duration,
PKL Size check). Remaining divergences, all deliberate or upstream:

- CLAIRMETA_ONLY_FAIL 6: dcpdoctor flags the same defect but at WARNING, so
  the package "passes" (cpl_mismatched_durations, reel_discontinuity,
  pkl_missing_asset_reference, subtitle_font_missing, subtitle_wrong_namespace,
  bv21_pkl_no_xml_ext). Severity policy, not blindness; revisit per code if a
  DCI CTP reading demands ERROR.
- DCPDOCTOR_ONLY_FAIL 7: dcpdoctor is stricter, each listed in the report's
  false-positive table with the code (e.g. duplicate_asset_id, missing_cpl,
  mxf_unreadable); all defensible.
- TOOL_ERROR 2: ClairMeta itself crashes on cpl_missing_reel and ECL08.
- Coverage gap 1: `check_dcp_signed` (encrypted DCP must be signed). dcpdoctor
  fails those packages via kdm_required/encryption_detected anyway; a signed
  KDM-present-but-unsigned package would slip through.

## Remaining coverage gaps (from run_corpus.py UNCOVERED_REASONS)

- Deep certificate-rule codes (7): fire on real malformed chains only; no
  minimal single-code fixture. Exercised via the reference packages.
- `picture_invalid_resolution`, `j2k_invalid_profile`,
  `j2k_invalid_component_count`: need non-DCI J2K essence; dcpwizard emits
  DCI-conformant codestreams and no re-wrap tool is available.
- `picture_invalid_frame_rate`: IMF-only; needs an imfwizard IMP with a
  pic/edit-rate mismatch.
- `sound_invalid_block_align`: unreachable via validate (ffprobe derives
  block_align, always consistent); covered by an mxf.rs unit test.
- `mxf_invalid_structure`, `sound_clipping`, `sound_silent`, `kdm_expired`,
  `kdm_not_yet_valid`, `interop_namespace_wrong`: only reachable via
  non-validate subcommands (studio, auto-qc/loudness, kdm) or need an Interop
  subtitle fixture; run_corpus runs `validate` only.
