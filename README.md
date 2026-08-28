# DCI CTP Test Suite

[![CI](https://github.com/PostPerfection/dci-ctp/actions/workflows/ci.yml/badge.svg)](https://github.com/PostPerfection/dci-ctp/actions/workflows/ci.yml)

[Documentation](https://postperfection.github.io/dci-ctp/)

Compliance Test Plan (CTP) validation test suite for [dcpdoctor](https://github.com/PostPerfection/dcpdoctor).

Based on the [DCI Compliance Test Plan v1.5.0](https://documents.dcimovies.com/CTP/release/1.5.0/) and the [ISDCF SMPTE-DCP test content](https://www.isdcf.com/smpte-dcp-tests/).

## Structure

```
tests/
├── isdcf/                # ISDCF SMPTE Bv2.1 test DCPs (5.1 + 7.1), downloaded
├── synthetic/            # minimal DCPs for specific edge cases, generated
│   ├── valid/            # should pass validation
│   └── invalid/          # should fail with specific errors
├── generated/            # DCPs created by dcpwizard
└── fixtures/app2e/       # App 2E IMPs, the one committed fixture set
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
├── download_isdcf.sh     # fetch the ISDCF reference content (~2GB)
└── download_clairmeta_data.sh  # fetch the ClairMeta ECL reference set (~1.5GB)
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
them once per error code, resealing the CPL and PKL hashes so only the intended
code fires. Two baselines are synthesised rather than built: `valid/dcp_signed`'s
certificate twin `valid/dcp_certificate_chain`, and `valid/dcp_all_markers`, the
base package carrying every marker `--strict` names so a fixture that drops one
has something to be measured against.
`scripts/run_corpus.py` reads `corpus/manifest.json` and asserts each fixture's code
fires AND is absent on the valid baseline (so a test can't pass when the check is
unwired), and that the fixture emits nothing beyond its expected codes, its
declared `also_emits` and whatever its baseline already emits. This is the
trust-critical proof that each check works and none is dead.

The harness only refuses or reports, it never repairs a product's output. A
mutation that injects the defect under test is the corpus working as designed. An
edit that fixes what dcpwizard or dcpdoctor got wrong hides the defect from every
consumer of the corpus: one such edit (reordering the 429-10 stereo element to
where the schema requires it) hid a schema violation on every 3D package until it
was found by hand. If a generated package needs correcting, the bug is the
generator's and gets fixed there.

```bash
./scripts/download_clairmeta_data.sh
DCPWIZARD=../dcpwizard/rust/target/release/dcpwizard ./scripts/build_corpus.sh
DCPDOCTOR=../dcpdoctor/rust/target/release/dcpdoctor python3 scripts/run_corpus.py
```

`build_corpus.sh` and `run_corpus.py` both need the ClairMeta ECL set and fail
naming the missing directory when it is absent.

Coverage against dcpdoctor master: 123 of 124 codes are exercised (110 by isolated
synthetic + subcommand fixtures, 13 more by the ClairMeta reference packages).
`ALL_CODES` in `run_corpus.py` is the full `Code::as_str` enum, so the headline
count and the uncovered list share one denominator (123 + 1 = 124). The one
uncovered code carries its reason in `UNCOVERED_REASONS`, which `run_corpus.py`
prints: `projector_4k_stereo_support` reads a `<Resolution>` element that no CPL
schema defines, so no mastering tool writes one and no conformant package can
fire it. Most fixtures run through `dcpdoctor validate`; seven codes reachable
only through other subcommands (kdm, auto-qc) use a `subcommand_fixtures`
manifest section. A code that reports a measurement rather
than a verdict is covered by the flag that gates it: the fixture and its baseline
are the same clean package with the gate on and off.

`build_corpus.sh` needs every tool its fixtures use and fails naming the one it
could not find: dcpwizard, imfwizard, grok's `grk_compress` on PATH, DCP-o-matic's
`dcpomatic2_create` and `dcpomatic2_cli`, and the python `cryptography` package
that `corpus_gen.py` builds the ST 430-2 certificate chains with. It also builds
`asdcp-wrap` once from `dcpwizard/extern/asdcplib` and caches it: dcpwizard and
postkit enforce DCI on their own wrap paths and subset the fonts they embed, so
the raw wrapper is the only way to get non-DCI essence into an AS-DCP MXF or a
font of a chosen size into an ST 429-5 timed-text MXF (`subtitle_font_too_large`).

### ClairMeta reference packages

`scripts/scan_reference.py` runs dcpdoctor against the [ClairMeta_Data](https://github.com/ClairMeta/ClairMeta_Data)
ECL set (28 real third-party DCPs: Interop + SMPTE, 3D, Atmos, HFR, encryption,
plus deliberate defects like ECL39's mismatched wavelet levels) and records each
one's observed verdict into the manifest under `reference_packages`. They run with
`--check-mxf`, so dcpdoctor reads their essence, and the flag list is recorded on
each entry so `run_corpus.py` and the differential replay the same command. These are
fetched, not vendored (~1.5 GB): `scripts/download_clairmeta_data.sh` clones the
pinned commit, or set `CLAIRMETA_DATA` to an existing clone. CI fetches them the
same way, into an `actions/cache` entry keyed on that script, so every job that
reads them runs the reference packages. They are
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

ClairMeta's MXF-essence checks shell out to `asdcp-info` and `asdcp-unwrap`, and
raise rather than bypassing when they are absent, which lands the package in
TOOL_ERROR. `build_corpus.sh` builds both from `dcpwizard/extern/asdcplib` and
caches them beside `asdcp-wrap`, so put that directory on PATH before running the
differential. `sox` gates the audio checks the same way. Photon is never invoked:
the IMF IMPs in the corpus land as TOOL_ERROR under ClairMeta, which is a DCP
validator.

Full-corpus result with `asdcp-unwrap` and `asdcp-info` on PATH (205 packages:
7 baselines, 170 negative fixtures, 28 ECL references): BOTH_PASS 36,
BOTH_FAIL 120, DCPDOCTOR_ONLY_FAIL 30, CLAIRMETA_ONLY_FAIL 14, TOOL_ERROR 5.
dcpdoctor caught 170/170 injected fixture defects, ClairMeta 133/170. The
manifest declares more fixtures than that: the flag-gated and env-gated ones
inject no defect, and an IMP lands as TOOL_ERROR under ClairMeta whatever it
holds, so the differential leaves both out.

All five TOOL_ERROR rows are ClairMeta raising instead of returning a verdict.
Four are ClairMeta defects on packages dcpdoctor reads fine: `cpl_missing_reel`
and `dom_cpl_missing_reel` raise a `TypeError` on a CPL with no ReelList,
`dom_signature_invalid` and `dom_certificate_chain_broken` raise one on a
tampered signature and cert chain. The fifth, `picture_invalid_frame_rate`, is
the IMF IMP, whose CPL carries a SequenceList rather than the ReelList a DCP
validator looks for.

The differential runs as an optional, non-blocking CI job that uploads the report
as an artifact, over the same 205 packages: CI fetches the ECL set too.

## Signature survey vs xmlsec1

`scripts/signature_survey.py` walks the given roots (default: the corpus) for XML
documents carrying an XML-DSig signature, gets a per-document verdict from both
dcpdoctor and `xmlsec1 --verify`, and exits nonzero on any document where one
tool verifies and the other rejects. Documents xmlsec1 cannot process (rsa-sha1
Interop signatures under a strict crypto policy, Signature elements it cannot
parse) render no verdict and are listed instead of compared, as are bare CPL/PKL
files outside any package.

```bash
DCPDOCTOR=../dcpdoctor/rust/target/release/dcpdoctor \
python3 scripts/signature_survey.py corpus ../dcpdoctor/tests
```

This is a standing, blocking CI step (in the `synthetic` job), not a one-off
investigation: the first run of this comparison found verifier defects no unit
test had (see dcpdoctor's DESIGN_TODO, "Document signatures verify against the
document"). Point it at the ECL clone too when that is on disk.

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
- ISDCF test content and the ClairMeta ECL set (downloaded separately due to size)

CI builds both binaries, fetches the ISDCF content and the ClairMeta ECL set into `actions/cache` entries, creates the synthetic and generated fixtures, and runs the full suite on every push and pull request. A missing fixture directory fails the job rather than skipping the cases that read it.

## Downloading the reference content

```bash
./scripts/download_isdcf.sh
./scripts/download_clairmeta_data.sh
```

The first downloads the SMPTE Bv2.1 test DCPs (~2GB) from ISDCF into `tests/isdcf`. The second clones the ClairMeta ECL set (~1.5GB) at its pinned commit into `../dci-ctp-work/ClairMeta_Data`, or into `CLAIRMETA_DATA` when that is set.

## License

AGPL-3.0-or-later. Copyright (C) 2026 Grok Image Compression Inc. See [LICENSE](LICENSE).
