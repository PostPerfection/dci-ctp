# Planned

The per-error-code corpus (`scripts/build_corpus.sh` + `run_corpus.py`) proves
105 of dcpdoctor's 120 codes fire non-vacuously (each code is asserted
absent on the fixture's valid baseline): 94 via isolated synthetic + subcommand
fixtures and 11 more via the ClairMeta ECL reference packages. `ALL_CODES` is the
full `Code::as_str` enum, so the headline count and the uncovered list share one
denominator (105 + 15 = 120), and every uncovered code carries a reason in
`UNCOVERED_REASONS`. The 120th code, `check_skipped`, landed after 0.5.0 with
the no-silent-skips pass. Most fixtures run through `dcpdoctor validate`; four
codes reachable only through other subcommands use a `subcommand_fixtures`
manifest section (kdm and auto-qc). Baselines are all clean under
`--strict --check-mxf`: dcpwizard labeled 5.1 (`valid/dcp_ov`), stereoscopic 3D
429-10 (`valid/dcp_3d`), Atmos AuxData 429-18 (`valid/dcp_atmos`) and the signed
package (`valid/dcp_signed`), plus DCP-o-matic SMPTE (`valid/dcp_dom_ov`) and
Interop (`valid/dcp_dom_interop`), and the synthesised `valid/dcp_all_markers`.
358 harness checks pass over 157 fixtures, 7 baselines, 4 subcommand fixtures and
28 reference packages.

## Two vendors, and what that covers (2026-08-12)

Every fixture built from the shared base and checked against the shared baseline
is generated a second time from a DCP-o-matic base, so 138 of the 157 fixtures
(69 pairs) assert their code on two mastering tools' output. Both DoM baselines
validate clean and are in the manifest: `valid/dcp_dom_ov` (SMPTE) and
`valid/dcp_dom_interop`. 358 harness checks pass over 157 fixtures.

Seventeen stay single-vendor, on three grounds. Six need a source DoM cannot
author or the base does not carry (`aux_data_atmos`, `stereo_framerate`,
`dcp_not_signed`, `j2k_bitrate_exceeded`, `sound_no_mca`,
`picture_invalid_frame_rate`). Two build on the DoM Interop package, which
dcpwizard cannot author (`interop_namespace_wrong`, `subtitle_glyph_missing`).
Nine are checked against a baseline with no DoM twin: the six certificate
fixtures share the signed `valid/dcp_certificate_chain`,
`unencrypted_dcp_not_signed` needs `valid/dcp_signed`,
`manifest_size_mismatch` compares against another fixture, and `markers_bad`
needs `valid/dcp_all_markers`. Porting those
would have meant asserting the code is absent from `dcp_dom_ov`, a package that
carries no certificates at all and so could never emit it, which passes without
proving anything. `markers_bad` is also the one flag-level opt-out
(`vendor_portable=False`): under `--strict` dcpdoctor reports every recommended
marker any CPL leaves out, and both vendors' clean packages leave several out, so
the fixture only means something against the all-markers baseline.

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

A third vendor was considered and declined on 2026-08-12. Every candidate that
still runs headless is abandoned, and the 28 ECL reference packages already
provide essence neither tool produced, fixed inputs nobody can inject a defect
into. Revisit only if a maintained independent mastering tool appears.

## Docs here have gone stale repeatedly

Four separate claims in this repo's own docs were wrong when last checked: the
README's covered-code count, DESIGN.md's differential snapshot, DESIGN.md calling
XSD validation unwired when `validate` emits `xml_schema_violation`, and its
certificate-rule gap list, which had inverted after dcpdoctor gained the six
codes ClairMeta lacks. A fixture annotation also claimed a code that never fired.
Re-run `run_corpus.py` and `diff/differential.py` before quoting any number here.

## The five codes dcpdoctor added 2026-08-12

Four of the five got an isolated fixture on both vendors:

- `assetmap_invalid_name`: SMPTE asset map named `ASSETMAP`, not `ASSETMAP.xml`.
- `assetmap_size_mismatch`: a declared chunk `Length` that is not the file's
  size. ST 429-9 §7.4 lets Length be absent and dcpwizard writes none, so the
  fixture adds a disagreeing one rather than corrupting an existing one.
- `reel_edit_rate_mismatch`: `MainMarkers` at 13 1 against a 24 1 picture. The
  marker list is complete, so the marker codes stay quiet and only the rate is
  wrong.
- `composition_metadata_asset_mismatch`: the CompositionMetadataAsset's
  `IntrinsicDuration` one frame off the reel picture's `Duration`.

`unencrypted_dcp_not_signed` now has one too, via `valid/dcp_signed`. It fires on
every unsigned package, the baselines included, and `run_corpus.py` fails any
expected code that also fires on the fixture's baseline, so it needed a baseline
that does not fire it. `build_corpus.sh` signs one package with the chain it
already generates, and the fixture strips both signatures back off.

Building it found a real defect first. Every leaf certificate postkit generated
carried no Basic Constraints and no Key Usage, which ST 430-2 requires, because
rcgen writes no extensions at all for `IsCa::NoCa`. Root and intermediate were
the only certificates asking for CA constraints, so they were always right and
only the signer was bare. Nothing else here would have caught it: dcpwizard's own
tests filter for hash and signature errors, and the `certificate_*` fixtures use
chains `corpus_gen.py` builds in python. Fixed in postkit 7d12db8.

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

## Coverage added 2026-08-26

The five codes that already fired somewhere in the corpus without an isolated
fixture now have one each, so coverage is 105 of 120 and `check_skipped` is the
only code left that appears nowhere but another fixture's `also_emits`. Each new
fixture declares exactly one code and nothing else on the dcpwizard base, and its
DoM twin adds only the `signature_invalid` every DoM fixture carries, since
editing a signed CPL invalidates its signature.

- `cpl_pkl_hash_mismatch`: the CPL's picture `<Hash>` replaced with a base64 SHA-1
  that is no file's digest. `reseal` takes a `reseal_cpl` keyword now: with it
  off, the CPL's own asset hashes are left alone while the PKL is still rewritten
  from the files, so the PKL records the CPL the package ships and the two only
  disagree about the picture.
- `cpl_missing_hash`: the same `<Hash>` deleted, with the same `reseal_cpl=False`.
  It is on the 5.1 base rather than `valid/dcp_3d`, where dcpwizard's stereoscopic
  CPL omits the element and the code fires on the baseline itself.
- `cpl_active_area_invalid`: `MainPictureActiveArea` Width set to 4096 against the
  2048-wide essence. 4096 is even, so the edge-parity half of the check stays
  quiet, and `check_composition_metadata_asset` reads only EditRate and
  IntrinsicDuration, so it says nothing about the active area.
- `j2k_missing_tlm`: every frame's TLM marker code rewritten as a COM comment
  marker of the same length. The segment length, the codestream and the MXF's KLV
  lengths are all unchanged, and doing it to all 48 frames rather than the first
  is what keeps `j2k_parameters_vary` off it. `codestream_bounds` walks the SOC+SIZ
  pairs through the whole file, and `first_codestream` is now its first entry.
- `j2k_parameters_vary`: the first frame's COD turns the multiple-component
  transform off. Of the parameters the per-frame comparison holds constant, it is
  the only one dcpdoctor reads for nothing but that comparison and the codestream
  summary. Codeblock size and decomposition levels both have a DCI rule in
  `validate_j2k_dci` and drew a `j2k_invalid_profile` when tried.

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

Re-run 2026-08-22 over the corpus brought up to dcpdoctor 0.5.0: conformant
injected timed-text documents, CPL hashes resealed alongside the PKL's, and nine
fixtures added for codes that used to ride along on another. `asdcp-info`,
`asdcp-unwrap` and `sox` on PATH so ClairMeta's MXF-essence checks run. Buckets
over 180 packages (7 baselines, 145 fixtures, 28 ECL references): BOTH_PASS 39,
BOTH_FAIL 84, DCPDOCTOR_ONLY_FAIL 21, CLAIRMETA_ONLY_FAIL 31, TOOL_ERROR 5. The
`picture_bitrate_measured` and `j2k_codestream_summary` fixtures are the 146th and
147th and are not differential subjects: they inject no defect, so there is no
catch to attribute.

dcpdoctor catches 145 of 145 injected defects, ClairMeta 113.

Most of CLAIRMETA_ONLY_FAIL reads worse than it is. The defect dcpdoctor rates
WARNING leaves the package passing, and the differential splits on pass/fail and
never sees a warning, so `subtitle_glyph_missing`, `reel_edit_rate_mismatch`,
`isdcf_naming_violation`, the annotation-text pair, the subtitle line and charset
limits, `subtitle_invalid_issue_date` and `subtitle_namespace_count` all land
there. None is a missed defect: `run_corpus.py` asserts every one of them fires.
`sound_no_mca`, `sound_invalid_sample_rate`, `mxf_invalid_structure` and
`j2k_legacy_ffff` are the same story on the essence side: the differential runs
each fixture on its manifest flags, so the code does fire, at WARNING or INFO,
where ClairMeta rejects the package outright.

- CLAIRMETA_ONLY_FAIL grew from 25 to 31, and all six additions are fixtures this
  pass added. `subtitle_invalid_issue_date` and `subtitle_namespace_count` are
  WARNING in dcpdoctor, and ClairMeta rejects the package on the shape of the
  injected asset rather than on the defect: `check_assets_cpl_uuid` and
  `check_subtitle_dcp_format`, because the corpus attaches its timed text as loose
  XML where a SMPTE package wants an ST 429-5 MXF wrap. Their four DoM twins
  (`dom_cpl_annotation_text_mismatch`, `dom_pkl_annotation_text_mismatch`,
  `dom_subtitle_invalid_issue_date`, `dom_subtitle_namespace_count`) add
  `check_document_signature`: DCP-o-matic signs its CPL and PKL for real, and
  `reseal` rewrites the XML, so every mutated DoM package carries a stale
  signature. It fires on 50 DoM packages in all and on neither clean DoM
  baseline, so it tracks the reseal and not the vendor. dcpdoctor's default
  signature check is presence-only, which is why it passes them. Recorded as an
  open gap in dcpdoctor's DESIGN_TODO (document signatures are not verified).
  When dcpdoctor verifies, these 50 move to BOTH_FAIL.
- DCPDOCTOR_ONLY_FAIL grew from 18 to 21, entirely on reference packages and
  entirely from 0.5.0's new checks: ECL08 on `timed_text_id_mismatch`, ECL29 on
  `partially_encrypted` and `subtitle_overlaps_reel`, ECL33 on
  `subtitle_overlaps_reel`. ClairMeta passes all three. Each is a real defect in a
  real third-party package that no corpus fixture had to be written to find.
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

- `check_assets_cpl_metadata` fails 12 packages, down from 64. Every one of the 64
  reported "Id metadata mismatch, CPL claims X but MXF Y" because dcpwizard
  minted a CPL asset Id that was not the MXF's own AssetUUID, and dcpwizard
  c1d73a6 fixed the mint. Every valid baseline is BOTH_PASS with no
  ClairMeta error at all, and the 14 fixtures the mismatch had dragged from
  DCPDOCTOR_ONLY_FAIL to BOTH_FAIL are back where their own defect puts them.
  What is left is packages that earn the check: `cpl_invalid_edit_rate`,
  `stereo_framerate`, `picture_invalid_resolution` and `mxf_asset_id_mismatch`
  each inject a CPL-versus-essence disagreement on purpose, `encrypted_no_kdm`
  and `partially_encrypted` give ClairMeta no key to probe the essence with, five
  of those are DoM twins as well, and ECL40 is a reference package that already
  failed it. All 11 fixtures are BOTH_FAIL.
- `mxf_asset_id_mismatch` is BOTH_FAIL. ClairMeta confirms it with
  `check_assets_cpl_metadata` and `check_assets_cpl_uuid`, so the fixture holds
  the defect dcpwizard used to emit and both tools now reject it.
- `check_picture_cpl_max_bitrate` fires at ERROR on three packages, all reference:
  ECL25 at 358.25 Mb/s, ECL42 at 593.55 over the 4K 500 limit, and ECL40. All
  three are CLAIRMETA_ONLY_FAIL, as is ECL39 on `check_cpl_reel_coherence` and
  `check_picture_cpl_encoding`. This is the essence divergence the reference bucket
  has carried since before this run, not new, and under 0.5.0 it is entirely a
  flags artifact: references go through dcpdoctor with no flags (see
  `diff/README.md`), so it never reads their essence. With `--check-mxf` dcpdoctor
  fails ECL25 on `j2k_bitrate_exceeded` at 358.2 Mb/s and ECL42 at 593.5 plus a
  per-component overrun at 96 fps, both agreeing with ClairMeta's numbers to the
  tenth, and fails ECL39 on three `j2k_poc_invalid` notes ClairMeta does not
  report at all. Only ECL40 passes dcpdoctor either way.
- Giving the reference packages `--check-mxf` in `scan_reference.py` and the
  differential would move ECL25, ECL39 and ECL42 from CLAIRMETA_ONLY_FAIL to
  BOTH_FAIL and cover `j2k_poc_invalid`, which no synthetic fixture can reach
  because neither grok nor the corpus writes a POC marker. Not done: it changes
  every reference package's recorded verdict at once, so it wants its own pass.
- The `j2k_bitrate_exceeded` fixture is BOTH_PASS. dcpdoctor rates it WARNING, and
  ClairMeta reports `check_picture_cpl_avg_bitrate` as a warning rather than the
  max-bitrate error, so neither tool fails the package and the differential, which
  splits on pass/fail, sees agreement. `valid/dcp_3d` fires nothing: it is built at
  100 Mb/s per eye and measures 200.01, where the old build measured 250.01.
- dcpdoctor's bitrate measurement agrees with asdcp-info on the fixture, "Peak
  frame bitrate 264.4 Mbps exceeds the DCI limit of 250 Mbps (frame 29 of 48)",
  and stays silent on the 200 Mb/s baseline. `j2k_bitrate_exceeded` coverage is a
  real measurement now, not the static HFR INFO string from `hfr_stereo.rs`.
- ClairMeta reads sound essence, so it catches `sound_invalid_sample_rate`
  (check_sound_cpl_sampling), `sound_invalid_quantization`
  (check_sound_cpl_quantization) and `sound_no_mca` (check_sound_cpl_channels_odd).
Three codes, five of the CLAIRMETA_ONLY_FAIL packages once the DoM twins of
`reel_discontinuity` and `bv21_pkl_no_xml_ext` are counted, are unchanged policy
divergences where dcpdoctor flags the same defect at WARNING so the package
"passes". Kept at WARNING deliberately, no SMPTE "shall" demands rejection:
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
- DCPDOCTOR_ONLY_FAIL 21, where dcpdoctor is the only tool catching the defect:
  duplicate_asset_id, missing_cpl, cpl_invalid_content_kind,
  composition_metadata_asset_mismatch, the six certificate fixtures,
  empty_file_in_package, mxf_unreadable, manifest_size_mismatch,
  j2k_invalid_component_count, main_sound_config_invalid, j2k_guard_bits, the
  DoM twins of duplicate_asset_id and empty_file_in_package, and the three
  reference packages ECL08, ECL29 and ECL33.
- Reference packages run through dcpdoctor with no flags, so it does not read
  their essence while ClairMeta does. Under 0.5.0 that is the whole of the
  reference-side divergence: `--check-mxf` flips ECL25, ECL39 and ECL42 to FAIL.
  The note that used to sit here, that the flag changed no verdict, was true of an
  older dcpdoctor and is not true now.
- `mediainfo` is still absent. Only `probe_mediainfo` uses it and no DCP check
  calls that, so it changes nothing.

## Remaining coverage gaps: 15 of 120 codes (2026-08-26)

`ALL_CODES` is 120 and 105 are exercised. `UNCOVERED_REASONS` carries a reason for
each of the 15, and they fall into three groups.

One is reachable only through the reference packages and only with an essence
flag: `j2k_poc_invalid`, three notes on ECL39. See the differential section for
why the references run with no flags and what changing that would cost.

Seven need a document shape `corpus_gen.py` does not build: `closed_caption_layout`
(cue lines carrying VAlign/VPosition), `timed_text_size_exceeded` (a timed-text
asset over the Bv2.1 byte cap), `subtitle_font_too_large` (an ST 429-5 MXF wrap
carrying an oversized font, where the corpus writes loose XML),
`subtitle_missing_from_reel` and `closed_caption_count_mismatch` (a multi-reel
composition with timed text on some reels only), plus `subtitle_language_mismatch`
(two subtitle assets disagreeing on `<Language>`) and
`closed_caption_interop_overlap` (two overlapping cues in an Interop caption
asset).

The rest need essence or inputs the corpus has no builder for:
`projector_4k_stereo_support` (4K stereoscopic essence), the three KDM rules
(`kdm_thumbprint_invalid`, `kdm_content_authenticator_invalid`,
`kdm_assume_trust_conflict`), and `cpl_invalid_language`, which cannot be isolated
at all: the CPL language elements are `xs:language`, so a bogus tag draws
`xml_schema_violation` with it. `schema_validation_skipped` fires only when no
schema directory is found, and dcpdoctor ships `schemas/`, so nothing here can
reach it. `check_skipped` already rides along on `j2k_legacy_ffff`, whose injected
0xFFFF stops the marker walk, but isolating it would mean staging a missing tool
or an unreadable input.

One earlier entry on this list is worth keeping as a warning about the others:
`sound_invalid_block_align` was recorded as unreachable because
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
