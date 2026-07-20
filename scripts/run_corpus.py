#!/usr/bin/env python3
"""Run dcpdoctor against the negative-test corpus and assert per-code coverage.

For every fixture the manifest declares an expected code, this:
  1. asserts the code fires on the fixture (the note's `code` field, anchored),
  2. asserts the same code is ABSENT on the fixture's valid baseline, so a test
     can't pass vacuously when the check is unwired.
Valid baselines must validate with zero error notes.
"""

import json
import os
import re
import subprocess
import sys

CORPUS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "corpus"))
DCPDOCTOR = os.environ.get(
    "DCPDOCTOR",
    os.path.expanduser("~/src/PostPerfection/dcpdoctor/rust/target/release/dcpdoctor"),
)
CLAIRMETA = os.environ.get(
    "CLAIRMETA_DATA",
    os.path.expanduser("~/src/PostPerfection/dci-ctp-work/ClairMeta_Data"),
)

# every dcpdoctor Code (dcpdoctor-core/src/lib.rs), for the coverage report
ALL_CODES = [
    "missing_assetmap", "missing_pkl", "missing_cpl", "asset_not_found",
    "duplicate_asset_id", "xml_parse_error", "xml_schema_violation", "invalid_uuid",
    "missing_required_element", "pkl_hash_mismatch", "pkl_missing_asset_reference",
    "cpl_invalid_duration", "cpl_mismatched_durations", "cpl_missing_reel",
    "cpl_invalid_edit_rate", "cpl_invalid_content_kind", "mxf_unreadable",
    "mxf_hash_mismatch", "mxf_invalid_structure", "signature_invalid",
    "certificate_expired", "certificate_chain_broken", "smpte_naming_violation",
    "smpte_namespace_wrong", "interop_namespace_wrong", "picture_invalid_resolution",
    "picture_invalid_frame_rate", "j2k_bitrate_exceeded", "j2k_invalid_profile",
    "j2k_invalid_component_count", "sound_invalid_sample_rate", "sound_invalid_channel_count",
    "sound_clipping", "sound_silent", "subtitle_parse_error", "subtitle_invalid_timing",
    "subtitle_font_missing", "isdcf_naming_violation", "encryption_detected",
    "kdm_required", "kdm_expired", "kdm_not_yet_valid", "reel_discontinuity",
    "stereo_mismatch", "marker_missing", "marker_invalid", "cross_ref_broken",
    "supplemental_opl_missing", "supplemental_ov_not_provided",
]

# codes not reachable through `dcpdoctor validate`, with why (honest gaps)
UNCOVERED_REASONS = {
    "xml_schema_violation": "only via Photon (Java) / schema-validate subcommand, not validate",
    "invalid_uuid": "emitted only in compliance.rs, which `validate` never calls",
    "mxf_hash_mismatch": "only via --manifest compare / Photon; no plain-validate path",
    "mxf_invalid_structure": "only mxf_advanced.rs / studio stereo / premium; not core validate",
    "smpte_naming_violation": "only compliance.rs / advanced.rs (--bv21); needs a bv21 fixture",
    "picture_invalid_resolution": "needs a real MXF at a non-DCI resolution (--check-mxf --strict)",
    "picture_invalid_frame_rate": "IMF-only (imf.rs); needs an IMP with pic/edit-rate mismatch",
    "j2k_invalid_profile": "needs --deep-j2k on essence with a non-DCI J2K profile",
    "j2k_invalid_component_count": "needs --deep-j2k on essence with != 3 components",
    "sound_invalid_sample_rate": "needs a real MXF at a non-48/96kHz rate (--check-mxf)",
    "sound_clipping": "audio.rs, only via auto-qc/loudness subcommands",
    "sound_silent": "audio.rs, only via auto-qc/loudness subcommands",
    "kdm_expired": "kdm_advanced.rs, only via the kdm subcommand with a KDM file",
    "kdm_not_yet_valid": "DEAD CODE: no emit site anywhere in dcpdoctor",
}

GREEN, RED, YELLOW, CYAN, NC = "\033[0;32m", "\033[0;31m", "\033[1;33m", "\033[0;36m", "\033[0m"


def run(dirpath, flags):
    full = os.path.join(CORPUS, dirpath)
    # a flag of the form "@name" resolves to a file inside the fixture dir
    resolved = [os.path.join(full, f[1:]) if f.startswith("@") else f for f in flags]
    cmd = [DCPDOCTOR, "validate", "-v", *resolved, full]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.stdout + p.stderr


def code_fires(output, code):
    # match the note's code field: "[SEVERITY] code - message"
    return re.search(r"\]\s" + re.escape(code) + r"\s-\s", output) is not None


def error_notes(output):
    return re.findall(r"\[ERROR\]\s(\S+)\s-", output)


def main():
    if not os.access(DCPDOCTOR, os.X_OK):
        print(f"{RED}ERROR: dcpdoctor not found/executable at {DCPDOCTOR}{NC}")
        sys.exit(1)

    with open(os.path.join(CORPUS, "manifest.json")) as f:
        manifest = json.load(f)

    passed = failed = 0
    covered = set()

    print(f"{CYAN}== valid baselines =={NC}")
    for b in manifest["baselines"]:
        out = run(b["dir"], b["flags"])
        errs = error_notes(out)
        if not errs and "Result: PASS" in out:
            print(f"  {GREEN}PASS{NC} {b['dir']} (clean)")
            passed += 1
        else:
            print(f"  {RED}FAIL{NC} {b['dir']} (expected clean, got errors: {errs})")
            failed += 1

    print(f"\n{CYAN}== negative fixtures =={NC}")
    for fx in manifest["fixtures"]:
        out = run(fx["dir"], fx["flags"])
        base_flags = fx.get("baseline_flags") or fx["flags"]
        base_out = run(fx["baseline"], base_flags)
        for code in fx["expected_codes"]:
            fires = code_fires(out, code)
            absent = not code_fires(base_out, code)
            if fires and absent:
                print(f"  {GREEN}PASS{NC} {fx['dir']}: {code}")
                passed += 1
                covered.add(code)
            elif not fires:
                print(f"  {RED}FAIL{NC} {fx['dir']}: {code} did NOT fire")
                failed += 1
            else:
                print(f"  {RED}FAIL{NC} {fx['dir']}: {code} also fires on baseline "
                      f"{fx['baseline']} (vacuous)")
                failed += 1

    # Reference packages: real third-party DCPs, verified only if fetched.
    ref_covered = set()
    refs = manifest.get("reference_packages", {}).get("packages", [])
    ecl = os.path.join(CLAIRMETA, "DCP", "ECL-SET")
    if refs and os.path.isdir(ecl):
        print(f"\n{CYAN}== reference packages (ClairMeta) =={NC}")
        for pkg in refs:
            d = os.path.join(ecl, pkg["dir"])
            if not os.path.isdir(d):
                print(f"  {YELLOW}SKIP{NC} {pkg['id']} (not fetched)")
                continue
            out = run(d, [])
            recorded = set(pkg["observed_codes"])
            still = {c for c in recorded if code_fires(out, c)}
            ref_covered |= still
            if still == recorded:
                print(f"  {GREEN}PASS{NC} {pkg['id']} ({len(still)} codes reproduce)")
                passed += 1
            else:
                print(f"  {RED}FAIL{NC} {pkg['id']} missing {sorted(recorded - still)}")
                failed += 1
    else:
        print(f"\n{YELLOW}reference packages not fetched (set CLAIRMETA_DATA); "
              f"skipping{NC}")

    all_covered = covered | ref_covered
    uncovered = [c for c in ALL_CODES if c not in all_covered]

    print(f"\n{CYAN}=============================={NC}")
    print(f"Results: {GREEN}{passed} passed{NC}, {RED}{failed} failed{NC}")
    print(f"\nCoverage: {len(all_covered)}/{len(ALL_CODES)} codes exercised via "
          f"`dcpdoctor validate`")
    print(f"  isolated synthetic fixtures: {len(covered)}")
    print(f"  additional via reference packages: {len(ref_covered - covered)}")
    if uncovered:
        print(f"\nUncovered ({len(uncovered)}):")
        for c in uncovered:
            reason = UNCOVERED_REASONS.get(c, "no fixture yet")
            print(f"  - {c}: {reason}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
