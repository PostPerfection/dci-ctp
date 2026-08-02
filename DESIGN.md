# Design

DCI Compliance Test Plan (CTP) test suite for dcpdoctor. Shell scripts generate synthetic DCP fixtures (not committed) and run `dcpdoctor validate` against them, checking expected error codes per CTP category.

## Layout

- `scripts/create_synthetic.sh`: builds synthetic fixture DCPs (assetmap/PKL/CPL plus zero-filled MXF stubs).
- `scripts/generate.sh`: builds a real DCP via dcpwizard (real picture/sound MXFs) for essence checks.
- `scripts/verify_encryption.sh` + `recover_kdm_key.py` + `tools/decrypt-check/`: independent proof of the encryption + KDM chain (see below).
- `scripts/run_tests.sh`: runs categories of expect-pass/expect-fail cases; for specific error codes it matches the note's code field, not the whole output.
- CI runs the full suite in one invocation. The isdcf cases skip because their 2GB content isn't downloaded.

## What each category tests

The suite only exercises rules that `dcpdoctor validate` enforces. Synthetic MXFs are zero-filled, so essence checks (J2K, sample rate) only run against the real generated and ISDCF DCPs.

- Packaging (§4): missing ASSETMAP, DCP with no CPL/PKL, valid SMPTE + Interop parse.
- Composition (§5): malformed CPL XML, missing CPL, ContentKind and EditRate under `--strict`, broken CPL→ASSETMAP cross-reference (`cross_ref_broken` with `--ov`, `supplemental_ov_not_provided` without).
- Presentation (§9): missing required FFMC/LFMC markers under `--strict` (`marker_missing`), marker with no Offset (`marker_invalid`).
- Integrity: PKL hash mismatch.
- Picture (§6): valid 2K flat/scope DCPs parse and pass (no essence-level J2K checks on stubs).
- Audio (§7): 48 kHz PCM in the real generated MXF via `--check-mxf`; ISDCF 5.1/7.1 when downloaded.
- Security (§8): unencrypted DCP validates; encrypted content detected (`encryption_detected`); encrypted-without-KDM flagged (`kdm_required`); encrypted ISDCF validates structurally.

The markers, cross-reference, and encryption cases use synthetic fixtures built with the intended defect; `create_synthetic.sh` reseals the CPL hash into the PKL afterwards so only the intended code fires, not a stray `pkl_hash_mismatch`.

## Per-error-code corpus

`scripts/build_corpus.sh` + `corpus_gen.py` build a real base DCP (dcpwizard,
real J2K/PCM) once and clone+mutate it per error code into `corpus/invalid/*`,
resealing PKL hashes so only the intended code fires. `scripts/run_corpus.py`
reads `corpus/manifest.json` and, for each fixture, asserts the code fires and is
absent on its valid baseline (`corpus/valid/dcp_ov`, or `dcp_mca` for the MCA
case). Isolation: no unrelated ERROR-severity note leaks in any fixture. The
corpus is generated, never committed (gitignored).

`corpus/manifest.json` schema:
- `baselines[]`: `{dir, package_type, is_valid_baseline, flags, expected_codes}`
- `fixtures[]`: `{dir, package_type, is_valid_baseline, expected_codes, also_emits, flags, baseline, baseline_flags, notes}`
- `reference_packages`: `{root_env, note, packages[]}` where each package is
  `{id, dir, source, standard, features, observed_result, observed_codes}`.

A flag of the form `@name` in a fixture's `flags` resolves to a file inside the
fixture dir (used for `--manifest`). `baseline_flags` lets the non-vacuity check
run different flags on the baseline (e.g. a correct vs wrong size manifest).

## Reference packages (ClairMeta)

`scripts/scan_reference.py` records dcpdoctor's observed verdict for each
ClairMeta_Data ECL package. Fetched, not vendored (~1.5 GB, `CLAIRMETA_DATA`
env). These become the shared corpus for differential testing against
ClairMeta's own results, and give real coverage of `certificate_expired`
(expired signing certs) and `j2k_bitrate_exceeded` (real HFR/4K essence).

## Coverage: 73 of 80 codes

65 codes have isolated synthetic or subcommand fixtures, and 8 more come from
the ClairMeta reference packages. `run_corpus.py` prints the live list and a reason
per gap. Uncovered (7): the six deep certificate-rule codes, which only fire
together on real malformed chains, and `sound_invalid_block_align`, which
`validate` cannot reach because ffprobe derives block_align from channels x
bit-depth (covered by an mxf.rs unit test).

Fixture machinery beyond the plain clone+mutate:

- Timed text: `add_timed_text` attaches a MainSubtitle or ClosedCaption track to
  the first reel and registers the document in the ASSETMAP. `dcst()` builds the
  SMPTE DCST document with one `<Text>` element per displayed line, which is how
  dcpdoctor counts lines. The caption limits (3 lines, 32 characters, ISDCF Doc 9
  charset) are errors where the subtitle ones are warnings.
- The caption track element carries the digicine CC-CPL namespace, as a real
  Bv2.1 CCAP package does, so the three caption fixtures also prove that
  declaring it does not push a SMPTE CPL onto the Interop schema (dcpdoctor picks
  the standard from the root element's namespace, not a substring).
- Fonts: `make_font()` writes a minimal sfnt whose only table is a format-12
  cmap, so any code point outside the listed set has no glyph. The
  `subtitle_glyph_missing` fixture is an Interop DCSubtitle, exercising the URI
  form. The SMPTE ST 428-7 form (LoadFont carrying the font asset urn as element
  text) resolves through the ASSETMAP.
- J2K byte patches walk the codestream markers rather than searching for byte
  patterns: `patch_j2k_guard_bits` zeroes the first frame's QCD guard-bit field
  (RDD 52 requires 1 at 2K, checked by `--deep-j2k`), `patch_j2k_legacy_ffff`
  writes 0xFF 0xFF into its entropy data at a byte position 254 mod 256 from the
  codestream start, the Dolby Cat. 862 legacy-decoder condition.

## Encryption + KDM verification (independent decrypt roundtrip)

dcpwizard's own tests only prove postkit encrypt/decrypt with the same key. The
real trust gap is whether the KDM delivers a key that actually decrypts the
content. `scripts/verify_encryption.sh` closes it without projector hardware:

1. openssl mints a signer + a recipient RSA-2048 cert; we hold the recipient
   private key.
2. `dcpwizard create --encrypt --key-out keys.json` builds a small encrypted DCP.
   keys.json is the KeyBundle: `{cpl_id, keys:[{key_type Mdik/Mdak, key_id,
   asset_uuid, content_key_hex}]}`, one AES-128 key per essence.
3. `dcpwizard kdm --cert recipient.pem --keys keys.json` binds those keys.
4. `recover_kdm_key.py` independently recovers each content key: base64 the
   `EncryptedKey/CipherData/CipherValue`, RSA-OAEP unwrap with **openssl**
   (SHA-1 digest + MGF1-SHA1, per `rsa-oaep-mgf1p`; a different implementation
   than the `rsa` crate postkit wrapped with), then parse the 138-byte ST 430-1
   key block: structure id `f1dc1244...` [0..16], signer thumbprint [16..36],
   CPL id [36..52], key type [52..56], key id [56..72], not-before/after
   [72..122], AES key [122..138]. Every recovered key must equal keys.json.
5. `tools/decrypt-check/` (Rust, asdcplib-rs pinned to the same git rev dcpwizard
   uses) decrypts the encrypted picture MXF with the recovered MDIK key. asdcplib
   verifies the SMPTE 429-6 encrypted check value and per-frame HMAC, which only
   validate under the exact content key, so a passing decrypt means the delivered
   key is the encryption key and the plaintext is recovered by construction. The
   helper also asserts a wrong key fails and a no-key read returns ciphertext, and
   that the MXF's `cryptographic_key_id` matches the KDM/keys key id.
6. Negative control: a KDM addressed to a different recipient must not unwrap with
   our private key.

Proves: KDM key delivery (ST 430-1 RSA-OAEP wrap) + essence decryption
(SMPTE 429-6 AES-128-CBC) are cryptographically correct end to end. Out of scope:
playback on real media-block/projector hardware, forensic marking. Runs as an
optional non-blocking CI job (`encryption`) in a few seconds.

## Differential validation (dcpdoctor vs ClairMeta)

`diff/differential.py` (uv project, ClairMeta 1.6.2) runs both validators over the
whole corpus and classifies every package. ClairMeta's MXF-essence checks need
`asdcp-info`; absent it, they bypass, so this diffs XML/structure/signature/cert
checks. IMF-vs-Photon is not run: the corpus has no IMF packages. Writes
`diff/report.{json,md}` (gitignored, regenerated). Run:

```bash
DCPDOCTOR=../dcpdoctor/rust/target/release/dcpdoctor \
CLAIRMETA_DATA=../../dci-ctp-work/ClairMeta_Data \
uv run --project diff diff/differential.py
```

Full-corpus result (2 baselines + 32 fixtures + 28 ECL references = 62):
BOTH_FAIL 21, DCPDOCTOR_ONLY_FAIL 25, CLAIRMETA_ONLY_FAIL 14, TOOL_ERROR 2,
BOTH_PASS 0. dcpdoctor caught 32/32 injected fixture defects; ClairMeta 21/32.

Method: every fixture derives from one dcpwizard base that ClairMeta already
rejects on schema grounds, so ClairMeta's catch of an injected defect is measured
by the checks that newly fail vs the baseline, not by the raw verdict.

### dcpdoctor bug found: SHA-256-only signature verification

All 25 DCPDOCTOR_ONLY_FAIL are ClairMeta-clean ECL DCPs that dcpdoctor rejects with
`signature_invalid`. Root cause: `postkit::xmldsig` hardcodes SHA-256 for the
reference digest and the RSA signature (`sha256(&c14n(..))`,
`Pkcs1v15Sign::new::<sha2::Sha256>()`) and never reads the document's declared
`DigestMethod`/`SignatureMethod`. The ECL CPLs/PKLs are signed with
`xmldsig#sha1`, so the recomputed digest never matches. Fix: read the declared
algorithm and dispatch SHA-1 vs SHA-256. Secondary stricter divergence: two Interop
packages emit `missing_required_element` (SMPTE rules applied to Interop). dcpdoctor
now matches ClairMeta on external OV references: a VF package referencing an asset
absent from the package emits a `supplemental_ov_not_provided` WARNING, and
`cross_ref_broken` fires only when `--ov <dir>` is given and the id resolves in
neither the package nor the OV. `certificate_expired` (25 pkgs) is a defensible stricter
policy: ClairMeta downgrades expired certs to INFO.

### dcpdoctor coverage gaps ClairMeta exposed

- XSD schema validation: not wired into `validate`. Both dcpwizard baselines are
  schema-invalid (`check_am_xml`: ASSETMAP missing required `IssueDate`;
  `check_cpl_xml`: `ContentTitleText` before `IssueDate`, out of SMPTE 429-7/429-9
  order) and dcpdoctor passes them clean. ClairMeta rejects both. This is the
  largest gap: any element-order/required-element schema violation slips through.
- Deep certificate-rule compliance: ClairMeta runs ~40 `check_certif_*` /
  `check_sign_*` checks (basic constraints, key usage, extensions, RSA validity,
  organization name, public-key thumbprint, DCI role, issuer/serial coherence)
  with no dcpdoctor equivalent.
- Sound essence descriptors: `check_sound_cpl_blockalign`, `_quantization`.
- CPL label/metadata schema: `check_assets_cpl_labels(_schema)`, `_metadata`.
- Foreign/empty file hygiene: `check_dcp_foreign_files`, `check_dcp_empty_dir`,
  `check_*_empty_text_fields`.

### where dcpdoctor is ahead of ClairMeta

ClairMeta missed 11/32 injected defects dcpdoctor catches: `duplicate_asset_id`,
`missing_cpl`, `cpl_invalid_content_kind`, required FFMC/LFMC markers
(`markers_bad`), `supplemental_opl`, `signature_invalid` (the synthetic broken
sig), `certificate_chain_broken`, MCA labeling (`sound_no_mca`), `mxf_unreadable`,
external-manifest compare (`manifest_size_mismatch`), plus `isdcf_naming_violation`
(INFO in both). ClairMeta also crashes internally (KeyError) on some malformed
CPLs; the 2 TOOL_ERROR are the reel-less-CPL fixture (crash) and ECL08 (a VF that
needs asdcp-unwrap to relink, unavailable). The 2 reference BOTH_FAIL (ECL31/ECL32,
non-coherent encrypted) are real agreement: ClairMeta flags `check_cpl_reel_coherence`.
