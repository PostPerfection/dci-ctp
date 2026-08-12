# DCI CTP Test Suite

[![CI](https://github.com/PostPerfection/dci-ctp/actions/workflows/ci.yml/badge.svg)](https://github.com/PostPerfection/dci-ctp/actions/workflows/ci.yml)

[Documentation](https://postperfection.github.io/dci-ctp/)

Compliance Test Plan (CTP) validation test suite for [dcpdoctor](https://github.com/PostPerfection/dcpdoctor).

Based on the [DCI Compliance Test Plan v1.5.0](https://documents.dcimovies.com/CTP/release/1.5.0/) and the [ISDCF SMPTE-DCP test content](https://www.isdcf.com/smpte-dcp-tests/).

## Structure

```
tests/                    # all fixtures are generated, none are committed
├── isdcf/                # ISDCF SMPTE Bv2.1 test DCPs (5.1 + 7.1)
├── synthetic/            # minimal DCPs for specific edge cases
│   ├── valid/            # should pass validation
│   └── invalid/          # should fail with specific errors
└── generated/            # DCPs created by dcpwizard
scripts/
├── build_corpus.sh       # build the real baselines + per-code negative corpus
├── corpus_gen.py         # clone+mutate the baselines into per-code fixtures
├── run_corpus.py         # assert each code fires and is absent on its baseline
├── scan_reference.py     # record ClairMeta ECL reference-package verdicts
├── run_tests.sh          # run the suite, creates synthetic fixtures if missing
├── create_synthetic.sh   # write the synthetic fixtures
├── generate.sh           # generate DCPs from source material via dcpwizard
├── verify_encryption.sh  # prove the encryption + KDM chain by independent decrypt
├── recover_kdm_key.py    # RSA-unwrap the content key from a KDM (openssl)
└── download_isdcf.sh     # fetch the ISDCF reference content (~2GB)
tools/
└── decrypt-check/        # tiny rust helper: decrypt an encrypted mxf with a key
```

## What the suite actually checks

The suite drives `dcpdoctor validate`, so it covers exactly the rules that command enforces. Synthetic MXFs are zero-filled stubs, so picture/audio essence checks only run against the real generated and ISDCF DCPs.

| Category | CTP Section | Covered here |
|----------|-------------|--------------|
| Packaging | §4 | Missing ASSETMAP, DCP with no CPL/PKL, valid SMPTE + Interop structure parse |
| Composition | §5 | Malformed CPL XML, missing CPL, ContentKind (strict), EditRate (strict), broken CPL→ASSETMAP cross-reference (`--ov`) and external-OV supplemental reference |
| Presentation | §9 | Missing required FFMC/LFMC markers (strict), marker with no Offset |
| Integrity | — | PKL hash mismatch |
| Picture | §6 | Valid 2K flat/scope DCPs parse and pass |
| Audio | §7 | 48 kHz PCM in the real generated MXF (`--check-mxf`); ISDCF 5.1/7.1 when downloaded |
| Security | §8 | Unencrypted DCP validates; encrypted content detected; encrypted-without-KDM flagged; encrypted ISDCF validates structurally |

Not covered by this table: J2K profile/bitrate, UUID-format, VOLINDEX/MXF-extension rules, and negative audio essence cases. Those either need a real fixture the suite can't cheaply generate (essence checks want a non-stub MXF) or the rule isn't wired into `dcpdoctor validate`.

## Per-error-code negative corpus

`scripts/build_corpus.sh` builds the real DCPs dcpwizard produces (labeled 5.1
base, stereoscopic 3D, Atmos AuxData, mono, and an encrypted unsigned build) plus
non-DCI J2K essence and an IMF IMP, then `scripts/corpus_gen.py` clones and mutates
them once per error code, resealing PKL hashes so only the intended code fires.
`scripts/run_corpus.py` reads `corpus/manifest.json` and asserts each fixture's code
fires AND is absent on the valid baseline (so a test can't pass when the check is
unwired). This is the trust-critical proof that each check works and none is dead.

```bash
DCPWIZARD=../dcpwizard/rust/target/release/dcpwizard ./scripts/build_corpus.sh
DCPDOCTOR=../dcpdoctor/rust/target/release/dcpdoctor python3 scripts/run_corpus.py
```

Coverage: all 81 dcpdoctor codes are exercised (74 by isolated synthetic +
subcommand fixtures, 7 more by the ClairMeta reference packages). `ALL_CODES` in
`run_corpus.py` is the full `Code::as_str` enum, so the headline count and the
uncovered list share one denominator (81 + 0 = 81). Most fixtures run through
`dcpdoctor validate`; four codes reachable only through other subcommands (kdm,
auto-qc) use a `subcommand_fixtures` manifest section. `run_corpus.py` prints the
full per-code list and nothing is uncovered.

A fixture flag of the form `@name` is a file inside the package being validated,
and `%name` one inside the corpus. `sound_invalid_block_align` uses the second
form for its KDM and recipient key, which dcpdoctor needs to read the encrypted
sound essence and which would be foreign files inside the package.

The certificate fixtures need the python `cryptography` package, which
`corpus_gen.py` uses to build their ST 430-2 chains. The picture/J2K and IMF
fixtures need grok's `grk_compress` on PATH and a vendored
`asdcp-wrap`, which `build_corpus.sh` builds once from `dcpwizard/extern/asdcplib`
and caches. dcpwizard/postkit enforce DCI on their own wrap paths, so `asdcp-wrap`
is the only way to get non-DCI essence into an AS-DCP MXF. If either tool is absent
those fixtures are skipped (recorded in the run output), not failed.

### ClairMeta reference packages

`scripts/scan_reference.py` runs dcpdoctor against the [ClairMeta_Data](https://github.com/ClairMeta/ClairMeta_Data)
ECL set (28 real third-party DCPs: Interop + SMPTE, 3D, Atmos, HFR, encryption,
plus deliberate defects like ECL39's mismatched wavelet levels) and records each
one's observed verdict into the manifest under `reference_packages`. These are
fetched, not vendored (~1.5 GB); set `CLAIRMETA_DATA` to the clone path. They are
the shared corpus a differential-testing pass diffs against ClairMeta's own
results, and they give real coverage of `certificate_expired` (their signing
certs expired in 2025) and `j2k_bitrate_exceeded` (real HFR/4K essence).

## Differential validation vs ClairMeta

`diff/differential.py` (uv project, ClairMeta 1.6.2) runs dcpdoctor and
[ClairMeta](https://github.com/ClairMeta/ClairMeta) over the whole corpus and
classifies every package (BOTH_PASS / BOTH_FAIL / DCPDOCTOR_ONLY_FAIL /
CLAIRMETA_ONLY_FAIL / TOOL_ERROR), writing `diff/report.{json,md}`.

```bash
DCPDOCTOR=../dcpdoctor/rust/target/release/dcpdoctor \
CLAIRMETA_DATA=../../dci-ctp-work/ClairMeta_Data \
uv run --project diff diff/differential.py
```

ClairMeta's MXF-essence checks need `asdcp-info` (asdcplib); absent it they bypass,
so this diffs XML/structure/signature/cert checks. Photon is never invoked: the one
IMF IMP in the corpus lands as TOOL_ERROR under ClairMeta, which is a DCP validator.

Full-corpus result (78 packages): BOTH_PASS 35, BOTH_FAIL 29, DCPDOCTOR_ONLY_FAIL 8,
CLAIRMETA_ONLY_FAIL 3, TOOL_ERROR 3. dcpdoctor caught 47/47 injected fixture defects,
ClairMeta 31/47. Key findings:

- **DCPDOCTOR_ONLY_FAIL 8**: dcpdoctor is stricter and each is defensible
  (duplicate_asset_id, missing_cpl, cpl_invalid_content_kind, empty_file_in_package,
  sound_invalid_quantization, mxf_unreadable, mxf_hash_mismatch, j2k_invalid_component_count).
- **CLAIRMETA_ONLY_FAIL 3**: dcpdoctor flags the same defect at WARNING deliberately
  (reel_discontinuity, pkl_missing_asset_reference, bv21_pkl_no_xml_ext); no SMPTE
  "shall" demands rejection for these.
- **Escalated to ERROR 2026-07-23**: cpl_mismatched_durations, subtitle_font_missing,
  and the subtitle namespace codes moved from WARNING to ERROR, each on cited SMPTE
  "shall" text (see the dcpdoctor code comments).
- **TOOL_ERROR 3**: ClairMeta crashes on cpl_missing_reel and ECL08, and errors on
  the IMF IMP (an IMP is a Photon job, not a ClairMeta DCP).

The differential runs as an optional, non-blocking CI job (uploads the report as
an artifact); the ECL packages aren't fetched in CI, so it runs on the baselines
and fixtures there.

## Encryption + KDM verification

`scripts/verify_encryption.sh` proves dcpwizard's encryption and KDM chain is
cryptographically correct without any projector or media-block hardware, by an
independent decrypt roundtrip. dcpwizard's own tests only prove encrypt/decrypt
with the same key; this closes the real trust gap: does the KDM actually deliver
a key that decrypts the content?

```bash
DCPWIZARD=../dcpwizard/rust/target/release/dcpwizard ./scripts/verify_encryption.sh
```

Steps, each fail-loud:

1. Generate a signer and a **recipient** RSA-2048 cert with openssl (we hold the
   recipient private key, which is what makes independent recovery possible).
2. `dcpwizard create --encrypt --key-out keys.json` builds a small encrypted DCP;
   `keys.json` holds the plaintext AES-128 content keys (MDIK picture, MDAK sound).
3. `dcpwizard kdm --cert recipient.pem --keys keys.json ...` binds those keys to
   the recipient.
4. `recover_kdm_key.py` decodes the KDM from scratch: base64 the `CipherValue`,
   **RSA-OAEP (SHA-1 / MGF1-SHA1) unwrap with openssl** (a different implementation
   than the `rsa` crate dcpwizard wrapped with), parse the 138-byte ST 430-1 key
   block (structure id, CPL id, key type, key id, validity, AES key at bytes
   122..138), and assert every recovered key equals `keys.json`.
5. The `decrypt-check` helper (`tools/decrypt-check/`, asdcplib-rs, same rev
   dcpwizard uses) decrypts the encrypted picture MXF with the recovered key: all
   frames must decrypt (SMPTE 429-6 check value + HMAC only validate under the
   correct key), a wrong key must fail, and a no-key read must return ciphertext.
6. Negative control: a KDM addressed to a *different* recipient must not unwrap
   with our private key.

What this proves: KDM key delivery (ST 430-1 RSA-OAEP wrap) and essence decryption
(SMPTE 429-6 AES-128-CBC) are cryptographically correct end to end. A key that
passes the encrypted check value + HMAC is the exact encryption key, so the
decrypted essence is the original plaintext by construction. Out of scope:
playback on real media-block/projector hardware, and forensic marking.

Runs as an optional, non-blocking CI job in ~a few seconds (needs openssl,
ffmpeg, and a dcpwizard build).

## Quick Start

```bash
# Create the small generated fixtures
./scripts/create_synthetic.sh

# Run all tests against a local dcpdoctor build
./scripts/run_tests.sh --dcpdoctor ../dcpdoctor/rust/target/release/dcpdoctor

# Run with verbose output
./scripts/run_tests.sh -v

# Run against specific category
./scripts/run_tests.sh --category packaging
```

## Requirements

- `dcpdoctor` binary (Rust release build)
- `dcpwizard` binary and `ffmpeg`, for the generated fixtures
- ISDCF test content (downloaded separately due to size)

CI builds both binaries, creates the synthetic and generated fixtures, and runs the full suite on every push and pull request. The isdcf cases skip in CI since the content is a 2GB download.

## Downloading ISDCF Test Content

```bash
./scripts/download_isdcf.sh
```

This downloads the SMPTE Bv2.1 test DCPs (~2GB) from ISDCF.

## License

AGPL-3.0-or-later. Copyright (C) 2026 Grok Image Compression Inc. See [LICENSE](LICENSE).
