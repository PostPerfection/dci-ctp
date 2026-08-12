# Planned

The per-error-code corpus (`scripts/build_corpus.sh` + `run_corpus.py`) proves
all 81 dcpdoctor codes fire non-vacuously (each code is asserted absent on the
fixture's valid baseline): 74 via isolated synthetic + subcommand fixtures and 7
more via the ClairMeta ECL reference packages. `ALL_CODES` is the full
`Code::as_str` enum, so the headline count and the uncovered list share one
denominator (81 + 0 = 81). Most fixtures run through `dcpdoctor validate`; four
codes reachable only through other subcommands use a `subcommand_fixtures`
manifest section (kdm and auto-qc). Baselines are real builds, all clean under
`--strict --check-mxf`: dcpwizard labeled 5.1 (`valid/dcp_ov`), stereoscopic 3D
429-10 (`valid/dcp_3d`) and Atmos AuxData 429-18 (`valid/dcp_atmos`), plus
DCP-o-matic SMPTE (`valid/dcp_dom_ov`) and Interop (`valid/dcp_dom_interop`).
164 harness checks pass over 122 fixtures.

## Two vendors, and what that covers (2026-08-12)

Every fixture built from the shared base and checked against the shared baseline
is generated a second time from a DCP-o-matic base, so 54 of the 68 fixtures
assert their code on two mastering tools' output. Both DoM baselines validate
clean and are in the manifest: `valid/dcp_dom_ov` (SMPTE) and
`valid/dcp_dom_interop`. 164 harness checks pass over 122 fixtures.

Fourteen stay single-vendor, on two grounds. Six need a source DoM cannot author
or the base does not carry (`aux_data_atmos`, `stereo_framerate`,
`dcp_not_signed`, `j2k_bitrate_exceeded`, `sound_no_mca`,
`picture_invalid_frame_rate`). Seven are checked against a baseline with no DoM
twin: the six certificate fixtures share the signed `valid/dcp_certificate_chain`
and `manifest_size_mismatch` compares against another fixture. Porting those
would have meant asserting the code is absent from `dcp_dom_ov`, a package that
carries no certificates at all and so could never emit it, which passes without
proving anything. `markers_bad` is the one flag-level opt-out
(`vendor_portable=False`): DoM writes no FFMC/LFMC, so its clean package already
reports marker_missing and the same mutation proves nothing there.

Two fixture bugs fell out the moment the mutations ran on a package dcpwizard did
not write, which is what the second vendor was for:

- `duplicate_asset_id` picked the sound asset by matching "sound" in its path, a
  dcpwizard filename. It resolves through the CPL asset id now.
- `cpl_invalid_edit_rate` replaced the first EditRate in the CPL on the
  assumption it was the picture's. DoM writes a MainMarkers asset carrying its
  own EditRate ahead of MainPicture, so the mutation landed on the marker asset
  and the code did not fire. It is scoped to MainPicture now. dcpdoctor reported
  nothing at all about a marker asset at 13 1 against a 24 1 picture, recorded as
  a gap in its DESIGN_TODO.

A third vendor is still worth having. Two tools agreeing is not conformance, and
the 28 ECL reference packages remain the only essence here that neither tool
produced, fixed inputs nobody can inject a defect into.

## Docs here have gone stale repeatedly

Four separate claims in this repo's own docs were wrong when last checked: the
README's covered-code count, DESIGN.md's differential snapshot, DESIGN.md calling
XSD validation unwired when `validate` emits `xml_schema_violation`, and its
certificate-rule gap list, which had inverted after dcpdoctor gained the six
codes ClairMeta lacks. A fixture annotation also claimed a code that never fired.
Re-run `run_corpus.py` and `diff/differential.py` before quoting any number here.

## The five codes dcpdoctor added 2026-08-12

`ALL_CODES` is 86. Four of the five got an isolated fixture on both vendors:

- `assetmap_invalid_name`: SMPTE asset map named `ASSETMAP`, not `ASSETMAP.xml`.
- `assetmap_size_mismatch`: a declared chunk `Length` that is not the file's
  size. ST 429-9 §7.4 lets Length be absent and dcpwizard writes none, so the
  fixture adds a disagreeing one rather than corrupting an existing one.
- `reel_edit_rate_mismatch`: `MainMarkers` at 13 1 against a 24 1 picture. The
  marker list is complete, so the marker codes stay quiet and only the rate is
  wrong.
- `composition_metadata_asset_mismatch`: the CompositionMetadataAsset's
  `IntrinsicDuration` one frame off the reel picture's `Duration`.

`unencrypted_dcp_not_signed` has no fixture and cannot have one yet. It fires on
every unsigned package, the baselines included, and `run_corpus.py` fails any
expected code that also fires on the fixture's baseline. A signed baseline would
fix it, and dcpwizard can now produce one (`create --signer-cert/--signer-key`).

`also_emits` is no longer declared per fixture for the asset map size. The DoM
variants reuse the same mutation functions, and only DoM declares a chunk
`Length`, so `build_fixture` reads it back off the built package instead:
whatever resizes an XML file records `assetmap_size_mismatch` on the vendor that
declares a Length and not on the one that does not. That found the same four
fixtures a hand scan did (`dom_invalid_uuid`, `dom_pkl_missing_asset_reference`,
`dom_pkl_size_mismatch`, `dom_xml_schema_violation`) and cannot drift from them.

`interop_namespace_wrong` was failing on both vendors and is fixed. It used to
make a package "Interop" by renaming `ASSETMAP.xml` to `ASSETMAP`, which stopped
working when dcpdoctor began taking the standard from the asset map's namespace
rather than its filename. It now builds on the real Interop package,
`valid/dcp_dom_interop`.

`MainMarkers` is injected ahead of `MainPicture` now, by one `add_markers`
helper. Both fixtures that inject it put it before `MainSound`, where the 429-16
schema rejects it, so each carried an `xml_schema_violation` nobody asked for.
`markers_bad` still violates the schema, unavoidably, since a `Marker` without
`Offset` is exactly what it exists to test, so that one is recorded in its
`also_emits`.

## Fixtures now have to declare everything they emit

`run_corpus.py` used to check only that each expected code fired and was absent
from the baseline. A fixture could carry any number of unintended extra defects
and still report PASS, which is how the `MainMarkers` schema violation survived.
It now also asserts that a fixture emits nothing beyond its expected codes, its
`also_emits`, and whatever its own baseline emits. That turns `also_emits` from a
note into an assertion.

Switching it on failed 52 fixtures. What that found:

- `subtitle_glyph_missing` was the second fixture faking Interop by renaming the
  asset map, the same stale trick `interop_namespace_wrong` used. It was quietly
  emitting `assetmap_invalid_name` and `smpte_namespace_wrong`. It builds on
  `valid/dcp_dom_interop` now. Nothing else in the corpus still renames the file.
- Three side effects belong to the corpus machinery rather than to any one
  mutation, so `side_effects` reads them off the built package instead of asking
  every fixture to declare them: a stale chunk `Length` or PKL Hash/Size wherever
  a fixture skips the reseal, and `subtitle_first_event_early`, which every
  package trips because the Bv2.1 rule wants the first cue at 4s and these
  packages are 48 frames long.
- The remaining 17 are real consequences of their own mutation and are declared
  with a reason each, for instance `mxf_unreadable` also being
  `mxf_invalid_structure` because a truncated file is both, and the fixtures that
  change a duration or edit rate also being
  `composition_metadata_asset_mismatch` because the metadata asset was written
  against the old value.

A fixture's `also` is the union across both vendors, since the DoM variants reuse
the same mutation functions and leave different things behind: DoM packages carry
markers, so changing a duration invalidates a marker Offset there and not on the
dcpwizard side.

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

Re-run 2026-08-12 over a corpus regenerated against the dcpwizard that writes a
CompositionMetadataAsset, carrying the five codes dcpdoctor added and the
declared-side-effect cleanup. `asdcp-info`, `asdcp-unwrap` and `sox` on PATH so
ClairMeta's MXF-essence checks run. Buckets over 161 packages (5 baselines, 128
fixtures, 28 ECL references): BOTH_PASS 36, BOTH_FAIL 77, DCPDOCTOR_ONLY_FAIL 18,
CLAIRMETA_ONLY_FAIL 25, TOOL_ERROR 5.

dcpdoctor catches 128 of 128 injected defects, ClairMeta 100.

`subtitle_glyph_missing` and `reel_edit_rate_mismatch` sit in a bucket that reads
worse than it is. Both are WARNING in dcpdoctor, so their packages pass, and the
differential splits on pass/fail and never sees a warning. Neither is a missed
defect: `run_corpus.py` asserts both fire.

- `check_assets_am_size` was the one gap only the second vendor could show, and
  dcpdoctor's `assetmap_size_mismatch` closes it. ClairMeta checks each ASSETMAP
  chunk Length against the file on disk, DoM writes that element and dcpwizard
  writes none. All 6 packages where ClairMeta reports it are now BOTH_FAIL, where
  the 4 DoM ones used to be CLAIRMETA_ONLY_FAIL, which is the whole of that
  bucket's drop from 28 to 24.
- `reel_edit_rate_mismatch` is WARNING, so its fixture is BOTH_PASS on the
  dcpwizard side: the differential splits on pass/fail and never sees a warning.
  The DoM twin is CLAIRMETA_ONLY_FAIL, ClairMeta erroring where dcpdoctor
  deliberately only warns.
- `reseal` rewrites those Lengths now, not just the PKL Hash+Size. Without that
  every size-changing DoM fixture carried a second unintended defect, and
  `check_assets_am_size` fired on 29 packages rather than 4.
- TOOL_ERROR is 5: `cpl_missing_reel` and its DoM twin (ClairMeta TypeError on
  the emptied ReelList), `picture_invalid_frame_rate` (KeyError 'ReelList' on the
  IMP, a Photon job), and `dom_signature_invalid` / `dom_certificate_chain_broken`,
  where ClairMeta raises "list indices must be integers" on a ds:Signature
  injected into a DoM CPL. The dcpwizard twins of those last two parse fine, so
  it is ClairMeta's reader disagreeing with DoM's CPL shape, not our mutation.

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
- DCPDOCTOR_ONLY_FAIL 17, where dcpdoctor is the only tool catching the defect:
  duplicate_asset_id, missing_cpl, cpl_invalid_content_kind, the six certificate
  fixtures, empty_file_in_package, mxf_unreadable, manifest_size_mismatch,
  j2k_invalid_component_count, main_sound_config_invalid, j2k_guard_bits, and the
  DoM twins of duplicate_asset_id and empty_file_in_package.
- Reference packages run through dcpdoctor with no flags, so it does not read
  their essence while ClairMeta does. Adding `--check-mxf` changes no verdict on
  ECL25, ECL39 or ECL42, so the gaps above are real and not a flags artifact.
- `mediainfo` is still absent. Only `probe_mediainfo` uses it and no DCP check
  calls that, so it changes nothing.

## Remaining coverage gaps: one

85 of the 86 codes in `ALL_CODES` have a fixture that fires them and a baseline
that does not. The exception is `unencrypted_dcp_not_signed`, which needs a
signed baseline before any fixture for it can be non-vacuous. `UNCOVERED_REASONS`
carries it plus three entries for codes that only the ECL reference packages or
the `--manifest` compare reach: those three are what a run without
`CLAIRMETA_DATA` prints as uncovered even though a full run resolves them.

`sound_invalid_block_align` was the last gap, recorded as unreachable because
ffprobe derives block_align from channels x bit-depth. The real reason it never
fired is that ffprobe does not report `block_align` for an MXF at all, so the
value arrived as 0 and the check skipped itself. That was a dcpdoctor gap, not an
unreachable code: every cleartext DCP went unchecked on the field. `read_mxf_info`
now falls back to the WaveAudioDescriptor through asdcplib when ffprobe omits it,
leaving encrypted essence to `check_sound_essence_mxf` so neither reports twice,
and the fixture is a plain byte-patch of the 5.1 baseline's sound MXF.

## Toolchain note

`corpus_gen.py` needs the python `cryptography` package for the certificate
chains. The essence fixtures need `grk_compress` (grok 20.3.9, at `~/bin/grok/bin`)
on PATH and the vendored `asdcp-wrap`, which build_corpus.sh builds once from
`dcpwizard/extern/asdcplib` via cmake into the source dir and caches. If either is
absent the picture/J2K and IMF fixtures are skipped (recorded in the run output),
not failed.

Regenerating baselines also needs the dcpwizard release binary at
`dcpwizard/rust/target/release/dcpwizard`, built and run with
`PKG_CONFIG_PATH=$HOME/bin/grok/lib64/pkgconfig LD_LIBRARY_PATH=$HOME/bin/grok/lib64`.
That is **lib64**, not lib, and the runtime path is needed as well as the build one.

The differential (`diff/differential.py`) needs ClairMeta 1.6.2 in the uv venv at
`diff/.venv`, plus `asdcp-info`, `asdcp-unwrap` and `sox` (14.4.2, `pixi global
install sox`, no sudo) on PATH, or ClairMeta silently skips its MXF-essence checks
and the buckets shift without saying why. The asdcp tools are built out-of-tree
from dcpwizard's vendored asdcplib into `/tmp/ctp-corpus-src/asdcplib-build/src`,
which does not survive a reboot. Photon jars come from
`imfwizard/scripts/fetch_photon.sh` into `~/.cache/imfwizard/photon`.

## Second-vendor readiness (2026-08-12)

`corpus_gen.py` no longer identifies anything by filename. Documents resolve by
root element (`CompositionPlaylist`, `PackingList`), the ASSETMAP resolves under
either ST 429-9 name, and track files resolve from the CPL asset id through the
ASSETMAP. The byte patches read the current local-tag value instead of asserting
a literal, so a fixture holds against any channel count or bit depth.

Verified against three packages: a dcpwizard SMPTE build, a DCP-o-matic SMPTE
build (`cpl_`/`pkl_`/`j2c_`/`pcm_` lowercase names) and a DCP-o-matic Interop
build (extensionless `ASSETMAP`). All five resolvers return the right file for
each. The DoM packages are corpus baselines now and 54 fixtures derive from them,
so see the two-vendor section above for where that landed.

One dcpdoctor false positive already fell out of pointing it at DCP-o-matic
output: ISDCF Doc 1 allows a version number after the content type, so `TST-1`
is legal and dcpdoctor flagged it. Fixed to accept a digits-only suffix, which
keeps `TST-3D-48` and `TST-48-600` flagged. `TST-50` now passes, because a
digits-only suffix is indistinguishable from a version, and ECL43's recorded
codes were refreshed to match.
