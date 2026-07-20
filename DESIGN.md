# Design

DCI Compliance Test Plan (CTP) test suite for dcpdoctor. Shell scripts generate synthetic DCP fixtures (not committed) and run `dcpdoctor validate` against them, checking expected error codes per CTP category.

## Layout

- `scripts/create_synthetic.sh`: builds synthetic fixture DCPs (assetmap/PKL/CPL plus zero-filled MXF stubs).
- `scripts/generate.sh`: builds a real DCP via dcpwizard (real picture/sound MXFs) for essence checks.
- `scripts/run_tests.sh`: runs categories of expect-pass/expect-fail cases; for specific error codes it matches the note's code field, not the whole output.
- CI runs the full suite in one invocation. The isdcf cases skip because their 2GB content isn't downloaded.

## What each category tests

The suite only exercises rules that `dcpdoctor validate` enforces. Synthetic MXFs are zero-filled, so essence checks (J2K, sample rate) only run against the real generated and ISDCF DCPs.

- Packaging (§4): missing ASSETMAP, DCP with no CPL/PKL, valid SMPTE + Interop parse.
- Composition (§5): malformed CPL XML, missing CPL, ContentKind and EditRate under `--strict`, broken CPL→ASSETMAP cross-reference (`cross_ref_broken`).
- Presentation (§9): missing required FFMC/LFMC markers under `--strict` (`marker_missing`), marker with no Offset (`marker_invalid`).
- Integrity: PKL hash mismatch.
- Picture (§6): valid 2K flat/scope DCPs parse and pass (no essence-level J2K checks on stubs).
- Audio (§7): 48 kHz PCM in the real generated MXF via `--check-mxf`; ISDCF 5.1/7.1 when downloaded.
- Security (§8): unencrypted DCP validates; encrypted content detected (`encryption_detected`); encrypted-without-KDM flagged (`kdm_required`); encrypted ISDCF validates structurally.

The markers, cross-reference, and encryption cases use synthetic fixtures built with the intended defect; `create_synthetic.sh` reseals the CPL hash into the PKL afterwards so only the intended code fires, not a stray `pkl_hash_mismatch`.

## Known coverage gaps

These aren't tested and the README says so. They need a real fixture the suite can't cheaply generate, or the rule isn't wired into `dcpdoctor validate`:

- J2K profile/bitrate/resolution (needs a real, non-stub picture MXF).
- UUID-format, VOLINDEX, and MXF-extension checks (not in the validate path; UUID check exists in dcpdoctor but is dead code).
- Negative audio essence (bad sample rate/bit depth), which needs a real MXF with the defect.
