# Planned

The per-error-code corpus (`scripts/build_corpus.sh` + `run_corpus.py`) proves
61 of 80 dcpdoctor codes fire non-vacuously (each code is asserted absent on the
fixture's valid baseline): 53 via isolated synthetic + subcommand fixtures and 8
more via the ClairMeta ECL reference packages. `ALL_CODES` is the full
`Code::as_str` enum, so the headline count and the uncovered list share one
denominator (61 + 19 = 80). Most fixtures run through `dcpdoctor validate`; four
codes reachable only through other subcommands use a `subcommand_fixtures`
manifest section (kdm and auto-qc). Baselines are real dcpwizard builds: labeled
5.1 (`valid/dcp_ov`), stereoscopic 3D 429-10 (`valid/dcp_3d`), Atmos AuxData
429-18 (`valid/dcp_atmos`), all clean under `--strict --check-mxf`. 85 harness
checks pass.

## Coverage added 2026-07-23

- `dcp_not_signed` (new dcpdoctor code): the real encrypted DCP dcpwizard builds
  carries KeyIds but no CPL/PKL signature, so it fires directly. The baseline is
  the same package with synthetic `ds:Signature` blocks injected (dcpwizard emits
  no signature, so it is synthesised).
- `mxf_invalid_structure`: picture MXF footer partition pack key corrupted
  (`--check-mxf`); the header still parses, so no mxf_unreadable. Reachable via
  `validate`, not just mxf_advanced/studio as previously recorded.
- `interop_namespace_wrong`: Interop-detected DCP (ASSETMAP has no `.xml`) whose
  subtitle uses the SMPTE DCST namespace; validate_subtitle runs with
  Standard::Interop and flags it.
- `sound_clipping`, `sound_silent` (auto-qc): full-scale and near-silent WAVs.
  auto-qc prints these as finding strings, not Code notes, so the subcommand
  fixture matches the finding substring.
- `kdm_expired`, `kdm_not_yet_valid` (kdm subcommand): KDMs dcpwizard generates
  against the encrypted source with fixed past/future validity windows; the
  in-window KDM is the silent baseline.
- `picture_invalid_resolution` + `j2k_invalid_profile`: 1920x1080 non-DCI J2K
  (grok `grk_compress`, no cinema profile) wrapped by the vendored C++ asdcp-wrap
  (built once from `dcpwizard/extern/asdcplib` into the source dir, cached) and
  swapped in for the picture MXF. dcpwizard/postkit enforce DCI on their wrap
  paths, so asdcp-wrap is the only way to get non-DCI essence into an AS-DCP MXF.
- `j2k_invalid_component_count`: asdcp-wrap refuses a 4-component essence, so a
  valid 3-component wrap has its first-frame SIZ Csiz byte-patched 3 -> 4
  (`--check-mxf --deep-j2k`).
- `picture_invalid_frame_rate`: real IMF IMP (imfwizard `create` from grok J2K,
  8-bit frames so App 2E accepts the wrap) whose CPL EditRate is edited to 25 1
  against the 24 fps essence; the IMF validate path (imf.rs) flags the mismatch.
  `valid/imf_ov` is the clean IMP baseline.

## Differential vs ClairMeta (diff/differential.py): current state

Buckets over 78 packages: BOTH_PASS 35, BOTH_FAIL 29, DCPDOCTOR_ONLY_FAIL 8,
CLAIRMETA_ONLY_FAIL 3, TOOL_ERROR 3. The three new essence fixtures:
picture_invalid_resolution is BOTH_PASS (dcpdoctor warns, so the package passes);
j2k_invalid_component_count is DCPDOCTOR_ONLY_FAIL (ClairMeta without asdcp-info
does not read the codestream); picture_invalid_frame_rate is TOOL_ERROR (ClairMeta
is a DCP validator and errors on the IMP, which is a Photon job).

- CLAIRMETA_ONLY_FAIL 3: dcpdoctor flags the same defect at WARNING so the
  package "passes". Kept at WARNING deliberately; no SMPTE "shall" demands
  rejection (per-code justification below):
  - `reel_discontinuity`: ST 429-2/-7 define no cross-reel EntryPoint continuity
    requirement. Each reel references an independent asset at an arbitrary entry
    point, so a non-contiguous chain is unusual but conformant.
  - `pkl_missing_asset_reference`: no normative "shall" locates a PKL asset via
    the AssetMap (ST 429-9 maps UUIDs but does not reject a PKL-only asset); a
    physically-present, correctly-hashed asset is still deliverable. ClairMeta
    strictness, not a spec rejection.
  - `bv21_pkl_no_xml_ext`: the `.xml` extension is an ISDCF SMPTE-Bv2.1 file
    naming convention, not a SMPTE normative requirement; the AssetMap references
    assets by path, so the extension does not affect resolution or playback.
- Severity escalated to ERROR 2026-07-23 (moved from CLAIRMETA_ONLY_FAIL to
  BOTH_FAIL), each demanded by spec text cited in the dcpdoctor code comment:
  - `cpl_mismatched_durations`: ST 429-2 §9.4 ("all Duration elements in a reel,
    except timed text, shall be equal"). Both the MainSound/picture path
    (validate.rs) and the ST 429-18 aux-data path (validators.rs) escalated.
  - `subtitle_font_missing`: ST 428-7:2014 (when Text elements are present, at
    least one LoadFont shall be present). Escalated only when the subtitle has
    Text; image-only subtitles still warn.
  - `subtitle_wrong_namespace` (smpte_namespace_wrong) and `interop_namespace_wrong`
    on the subtitle path: ST 428-7 fixes the DCST namespace string, so a wrong
    namespace is non-conformant and unparseable by a compliant player.
- DCPDOCTOR_ONLY_FAIL 8: dcpdoctor is stricter (duplicate_asset_id, missing_cpl,
  cpl_invalid_content_kind, empty_file_in_package, sound_invalid_quantization,
  mxf_unreadable, mxf_hash_mismatch, j2k_invalid_component_count); all defensible.
- TOOL_ERROR 3: ClairMeta crashes on cpl_missing_reel and ECL08, and errors on
  the IMF fixture (an IMP is a Photon job, not a ClairMeta DCP).
- ClairMeta was importable but without asdcp-info / asdcp-unwrap / sox, so its
  MXF-essence-level checks did not run; the XML/structure comparison is complete.

## Remaining coverage gaps (19, from run_corpus.py UNCOVERED_REASONS)

- Deep certificate-rule codes (6): fire together on real malformed cert chains
  (the reference packages); no minimal single-code fixture.
- `sound_invalid_block_align`: unreachable via validate (ffprobe derives
  block_align from channels x bit-depth, so it is always consistent); covered by
  an mxf.rs unit test.
- Advisory quality checks (12): `j2k_legacy_ffff`, `j2k_guard_bits`,
  `main_sound_config_invalid`, `subtitle_frame_rate_mismatch`,
  `subtitle_glyph_missing`, `subtitle_line_count`, `subtitle_line_length`,
  `closed_caption_line_count`, `closed_caption_line_length`,
  `closed_caption_charset`, `reel_too_short`, `non_ascii_filename`. Real Code
  variants (studio/deep/subtitle-render paths) with unit-test coverage but no
  isolated corpus fixture yet.

## Toolchain note

The essence fixtures need `grk_compress` (grok, at `~/bin/grok/bin`) on PATH and
the vendored `asdcp-wrap`, which build_corpus.sh builds once from
`dcpwizard/extern/asdcplib` via cmake into the source dir and caches. If either is
absent the picture/J2K and IMF fixtures are skipped (recorded in the run output),
not failed.
