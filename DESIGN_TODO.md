# Planned

The per-error-code corpus (`scripts/build_corpus.sh` + `run_corpus.py`) proves
all 81 dcpdoctor codes fire non-vacuously (each code is asserted absent on the
fixture's valid baseline): 74 via isolated synthetic + subcommand fixtures and 7
more via the ClairMeta ECL reference packages. `ALL_CODES` is the full
`Code::as_str` enum, so the headline count and the uncovered list share one
denominator (81 + 0 = 81). Most fixtures run through `dcpdoctor validate`; four
codes reachable only through other subcommands use a `subcommand_fixtures`
manifest section (kdm and auto-qc). Baselines are real dcpwizard builds: labeled
5.1 (`valid/dcp_ov`), stereoscopic 3D 429-10 (`valid/dcp_3d`), Atmos AuxData
429-18 (`valid/dcp_atmos`), all clean under `--strict --check-mxf`. 103 harness
checks pass.

## Coverage added 2026-08-12

The six deep certificate-rule codes now have one isolated fixture each, built
from ST 430-2 profile chains `corpus_gen.py` generates with the python
`cryptography` package (self-signed root, intermediate, signer leaf; all
sha256WithRSA, 2048-bit, e=65537, one Organization, dnQualifier =
Base64(SHA-1(subjectPublicKey payload))). The chain is injected into the base
DCP's CPL as an enveloped `ds:Signature`, and the shared baseline
(`valid/dcp_certificate_chain`) is the same package signed with a chain that
carries no defect at all, so every fixture asserts its code against a chain that
differs only in the deliberate defect. Only the leaf carries the defect, since
cert_rules.rs derives a cert's role from whether it issues another cert in the
chain.

- `certificate_basic_constraints_invalid`: leaf Basic Constraints cA=TRUE.
- `certificate_key_usage_invalid`: leaf Key Usage without digitalSignature.
- `certificate_key_size_invalid`: 3072-bit leaf key.
- `certificate_role_invalid`: leaf CommonName starting with `.`, so its role
  token is empty and matches the CA roles.
- `certificate_thumbprint_invalid`: leaf dnQualifier is a well-formed base64
  value that is not its own public-key thumbprint.
- `certificate_organization_inconsistent`: leaf O differs from the CA certs'.

The SignedInfo is complete enough for the 429-16 schema, but the digest and
SignatureValue are placeholders (a real enveloped signature would need c14n over
the mutated CPL), so `signature_invalid` rides along on all six and on the
baseline; it is recorded in each fixture's `also_emits`.

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

Re-run 2026-08-12 over a fully regenerated corpus, with `asdcp-info`,
`asdcp-unwrap` and `sox` on PATH so ClairMeta's MXF-essence checks run. Buckets
over 99 packages (3 baselines, 68 fixtures, 28 ECL references): BOTH_PASS 31,
BOTH_FAIL 36, DCPDOCTOR_ONLY_FAIL 15, CLAIRMETA_ONLY_FAIL 15, TOOL_ERROR 2.

dcpdoctor catches 68 of 68 injected defects, ClairMeta 46.

- `check_assets_cpl_metadata` fails 6 packages, down from 64. Every one of the 64
  reported "Id metadata mismatch, CPL claims X but MXF Y" because dcpwizard
  minted a CPL asset Id that was not the MXF's own AssetUUID, and dcpwizard
  c1d73a6 fixed the mint. All three valid baselines are BOTH_PASS with no
  ClairMeta error at all, and the 14 fixtures the mismatch had dragged from
  DCPDOCTOR_ONLY_FAIL to BOTH_FAIL are back where their own defect puts them.
  What is left is packages that earn the check: `cpl_invalid_edit_rate`,
  `stereo_framerate`, `picture_invalid_resolution` and `mxf_asset_id_mismatch`
  each inject a CPL-versus-essence disagreement on purpose, `encrypted_no_kdm`
  gives ClairMeta no key to probe the essence with, and ECL40 is a reference
  package that already failed it.
- `mxf_asset_id_mismatch` is BOTH_FAIL. ClairMeta confirms it with
  `check_assets_cpl_metadata` and `check_assets_cpl_uuid`, so the fixture holds
  the defect dcpwizard used to emit and both tools now reject it.
- `check_picture_cpl_max_bitrate` fires on 4 packages: the
  `j2k_bitrate_exceeded` fixture at 264.38 Mb/s over the 250 Mb/s limit, ECL25 at
  358.25, ECL42 at 593.55 over the 4K 500 limit, and ECL40 through a ClairMeta
  internal error. `valid/dcp_3d` no longer fires. It is rebuilt at 100 Mb/s per
  eye and measures 200.01, where the old build measured 250.01.
- dcpdoctor's bitrate measurement agrees with asdcp-info on the fixture, "Peak
  frame bitrate 264.4 Mbps exceeds the DCI limit of 250 Mbps (frame 29 of 48)",
  and stays silent on the 200 Mb/s baseline. `j2k_bitrate_exceeded` coverage is a
  real measurement now, not the static HFR INFO string from `hfr_stereo.rs`.
- ClairMeta reads sound essence, so it catches `sound_invalid_sample_rate`
  (check_sound_cpl_sampling), `sound_invalid_quantization`
  (check_sound_cpl_quantization) and `sound_no_mca` (check_sound_cpl_channels_odd).
- TOOL_ERROR 2 is `cpl_missing_reel` (ClairMeta TypeError on the emptied
  ReelList) and `picture_invalid_frame_rate` (KeyError 'ReelList' on the IMP,
  which is a Photon job).

Three of the CLAIRMETA_ONLY_FAIL packages are unchanged policy divergences, where
dcpdoctor flags the same defect at WARNING so the package "passes". Kept at
WARNING deliberately, no SMPTE "shall" demands rejection:
- `reel_discontinuity`: ST 429-2/-7 define no cross-reel EntryPoint continuity
  requirement. Each reel references an independent asset at an arbitrary entry
  point, so a non-contiguous chain is unusual but conformant.
- `pkl_missing_asset_reference`: no normative "shall" locates a PKL asset via the
  AssetMap (ST 429-9 maps UUIDs but does not reject a PKL-only asset), and a
  physically-present, correctly-hashed asset is still deliverable. ClairMeta
  strictness, not a spec rejection.
- `bv21_pkl_no_xml_ext`: the `.xml` extension is an ISDCF SMPTE-Bv2.1 file naming
  convention, not a SMPTE normative requirement. The AssetMap references assets by
  path, so the extension does not affect resolution or playback.
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
- DCPDOCTOR_ONLY_FAIL 15, where dcpdoctor is the only tool catching the defect:
  duplicate_asset_id, missing_cpl, cpl_invalid_content_kind, the six certificate
  fixtures, empty_file_in_package, mxf_unreadable, manifest_size_mismatch,
  j2k_invalid_component_count, main_sound_config_invalid and j2k_guard_bits.
- Reference packages run through dcpdoctor with no flags, so it does not read
  their essence while ClairMeta does. Adding `--check-mxf` changes no verdict on
  ECL25, ECL39 or ECL42, so the gaps above are real and not a flags artifact.
- `mediainfo` is still absent. Only `probe_mediainfo` uses it and no DCP check
  calls that, so it changes nothing.

## Remaining coverage gaps: none

Every code in `ALL_CODES` has a fixture that fires it and a baseline that does
not, so a full run reports none uncovered. `UNCOVERED_REASONS` still carries
three entries, for the codes that only the ECL reference packages or the
`--manifest` compare reach: they are what a run without `CLAIRMETA_DATA` prints.

`sound_invalid_block_align` was the last gap, recorded as unreachable because
ffprobe derives block_align from channels x bit-depth. The real reason it never
fired is that ffprobe does not report `block_align` for an MXF at all, so the
cleartext path reads 0 and skips the check. Only `check_sound_essence_mxf` reads
the field, out of the WaveAudioDescriptor via asdcplib, and it runs on encrypted
essence with a covering key. The fixture is therefore the encrypted package with
BlockAlign byte-patched, validated with the corpus KDM and recipient key.

That leaves a real dcpdoctor gap: a cleartext DCP's BlockAlign is never checked,
because the only prober used on that path cannot see it. Reading the descriptor
through asdcplib for cleartext PCM too would close it.

## Toolchain note

`corpus_gen.py` needs the python `cryptography` package for the certificate
chains. The essence fixtures need `grk_compress` (grok, at `~/bin/grok/bin`) on
PATH and the vendored `asdcp-wrap`, which build_corpus.sh builds once from
`dcpwizard/extern/asdcplib` via cmake into the source dir and caches. If either is
absent the picture/J2K and IMF fixtures are skipped (recorded in the run output),
not failed.
