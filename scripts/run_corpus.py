#!/usr/bin/env python3
"""Run dcpdoctor against the negative-test corpus and assert per-code coverage.

For every fixture the manifest declares an expected code, this:
  1. asserts the code fires on the fixture (the note's `code` field, anchored),
  2. asserts the same code is ABSENT on the fixture's valid baseline, so a test
     can't pass vacuously when the check is unwired,
  3. asserts the fixture emits nothing beyond its expected codes, its declared
     `also_emits`, and whatever its baseline already emits, so a mutation cannot
     quietly carry a second defect.
Valid baselines must validate with zero error notes.
"""

import json
import os
import re
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(REPO, "corpus")
DCPDOCTOR = os.environ.get("DCPDOCTOR") or shutil.which("dcpdoctor") or ""
CLAIRMETA = os.environ.get(
    "CLAIRMETA_DATA",
    os.path.join(os.path.dirname(REPO), "dci-ctp-work", "ClairMeta_Data"),
)

# every dcpdoctor Code, in source order (dcpdoctor-core/src/lib.rs `Code::as_str`).
# This is the coverage denominator, so it must stay complete: any code a fixture
# or reference package emits has to appear here or the headline count and the
# uncovered list stop adding up.
ALL_CODES = [
    "missing_assetmap", "missing_pkl", "missing_cpl", "asset_not_found",
    "duplicate_asset_id", "assetmap_invalid_name", "assetmap_size_mismatch",
    "xml_parse_error", "xml_schema_violation", "schema_validation_skipped",
    "check_skipped",
    "invalid_uuid", "missing_required_element", "pkl_hash_mismatch",
    "pkl_size_mismatch", "pkl_missing_asset_reference",
    "pkl_annotation_text_mismatch", "cpl_invalid_duration",
    "cpl_mismatched_durations", "cpl_missing_reel", "cpl_invalid_edit_rate",
    "cpl_invalid_content_kind", "cpl_missing_hash", "cpl_pkl_hash_mismatch",
    "cpl_annotation_text_mismatch", "cpl_active_area_invalid",
    "cpl_invalid_language", "mxf_unreadable", "mxf_hash_mismatch",
    "mxf_invalid_structure", "mxf_asset_id_mismatch", "signature_invalid",
    "dcp_not_signed", "unencrypted_dcp_not_signed", "certificate_expired",
    "certificate_chain_broken", "certificate_basic_constraints_invalid",
    "certificate_key_usage_invalid", "certificate_key_size_invalid",
    "certificate_signature_algorithm_invalid", "certificate_role_invalid",
    "certificate_thumbprint_invalid", "certificate_organization_inconsistent",
    "smpte_naming_violation", "smpte_namespace_wrong",
    "interop_namespace_wrong", "picture_invalid_resolution",
    "picture_invalid_frame_rate", "picture_not_imf_profile",
    "picture_colour_missing", "picture_coding_label_mismatch",
    "picture_pixel_layout_mismatch", "j2k_bitrate_exceeded",
    "picture_bitrate_measured", "j2k_invalid_profile",
    "j2k_invalid_component_count", "j2k_legacy_ffff", "j2k_guard_bits",
    "j2k_missing_tlm", "j2k_poc_invalid", "j2k_parameters_vary",
    "j2k_codestream_summary", "sound_invalid_sample_rate",
    "sound_invalid_channel_count", "sound_invalid_quantization",
    "sound_invalid_block_align", "sound_clipping", "sound_silent",
    "main_sound_config_invalid", "sound_channel_config_invalid",
    "subtitle_parse_error", "subtitle_invalid_timing",
    "subtitle_frame_rate_mismatch", "subtitle_font_missing",
    "subtitle_glyph_missing", "subtitle_first_event_early",
    "subtitle_line_count", "subtitle_line_length", "subtitle_duration",
    "subtitle_spacing", "closed_caption_line_count",
    "closed_caption_line_length", "closed_caption_charset",
    "closed_caption_layout", "timed_text_size_exceeded",
    "timed_text_id_mismatch", "subtitle_empty_text",
    "subtitle_invalid_issue_date", "subtitle_namespace_count",
    "subtitle_entry_point", "closed_caption_entry_point",
    "subtitle_overlaps_reel", "subtitle_missing_from_reel",
    "subtitle_language_mismatch", "closed_caption_count_mismatch",
    "subtitle_font_too_large", "closed_caption_interop_overlap",
    "partially_encrypted", "projector_frame_rate_support",
    "projector_4k_stereo_support", "distributor_audio_channel_count",
    "isdcf_naming_violation", "encryption_detected", "kdm_required",
    "kdm_expired", "kdm_not_yet_valid", "kdm_thumbprint_invalid",
    "kdm_content_authenticator_invalid", "kdm_assume_trust_conflict",
    "reel_discontinuity", "reel_incoherent", "reel_too_short",
    "reel_edit_rate_mismatch", "composition_metadata_asset_mismatch",
    "stereo_mismatch", "marker_missing", "marker_invalid", "cross_ref_broken",
    "supplemental_opl_missing", "supplemental_ov_not_provided",
    "aux_data_detected", "foreign_file_in_package", "empty_file_in_package",
    "non_ascii_filename",
]

# codes not covered by an isolated `dcpdoctor validate` fixture, with why (honest
# gaps). Empty: every code has a fixture.
UNCOVERED_REASONS = {}

GREEN, RED, CYAN, NC = "\033[0;32m", "\033[0;31m", "\033[0;36m", "\033[0m"


def run(dirpath, flags, extra_env=None):
    full = os.path.join(CORPUS, dirpath)
    # a flag of the form "@name" resolves to a file inside the fixture dir
    resolved = [os.path.join(full, f[1:]) if f.startswith("@") else f for f in flags]
    cmd = [DCPDOCTOR, "validate", "-v", *resolved, full]
    env = dict(os.environ)
    # an env value of the form "@name" resolves to a path inside the corpus
    for name, value in (extra_env or {}).items():
        env[name] = os.path.join(CORPUS, value[1:]) if value.startswith("@") else value
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return p.stdout + p.stderr


def run_sub(subcommand, dirpath, args):
    # a flag of the form "@name" resolves to a file inside corpus/<dirpath>
    full = os.path.join(CORPUS, dirpath)
    resolved = [os.path.join(full, a[1:]) if a.startswith("@") else a for a in args]
    cmd = [DCPDOCTOR, subcommand, "-v", *resolved]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.stdout + p.stderr


def code_fires(output, code):
    # match the note's code field: "[SEVERITY] code - message"
    return re.search(r"\]\s" + re.escape(code) + r"\s-\s", output) is not None


def error_notes(output):
    return re.findall(r"\[ERROR\]\s(\S+)\s-", output)


def emitted_codes(output):
    """Every code the run reported, whatever the severity."""
    return set(re.findall(r"\[[A-Z]+\]\s(\S+)\s-\s", output))


def main():
    if not DCPDOCTOR or not os.access(DCPDOCTOR, os.X_OK):
        print(f"{RED}ERROR: dcpdoctor not found at {DCPDOCTOR or '(unset)'}{NC}")
        print("Set DCPDOCTOR to the binary or put dcpdoctor on PATH")
        sys.exit(1)

    with open(os.path.join(CORPUS, "manifest.json")) as f:
        manifest = json.load(f)

    ecl = os.path.join(CLAIRMETA, "DCP", "ECL-SET")
    if not os.path.isdir(ecl):
        print(f"{RED}ERROR: ClairMeta ECL set not found at {ecl}{NC}")
        print("Fetch it with scripts/download_clairmeta_data.sh, or set CLAIRMETA_DATA "
              "to an existing clone")
        sys.exit(1)

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
        out = run(fx["dir"], fx["flags"], fx.get("env"))
        # an empty baseline_flags is meaningful: a gated code's baseline is the
        # same package run with the gate off
        base_flags = fx["baseline_flags"] if fx.get("baseline_flags") is not None else fx["flags"]
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

        # the mutation has to leave one defect behind, not two. anything the
        # baseline already emits is not this fixture's doing.
        declared = set(fx["expected_codes"]) | set(fx.get("also_emits", []))
        stray = emitted_codes(out) - declared - emitted_codes(base_out)
        if stray:
            print(f"  {RED}FAIL{NC} {fx['dir']}: emits {', '.join(sorted(stray))} "
                  f"beyond its declared codes")
            failed += 1
        else:
            passed += 1

    # Subcommand fixtures: codes reachable only through a non-validate subcommand
    # (kdm, auto-qc). auto-qc prints findings as text, so a fixture may carry a
    # `match` map (code -> substring) instead of the anchored code format.
    subfx = manifest.get("subcommand_fixtures", [])
    if subfx:
        print(f"\n{CYAN}== subcommand fixtures (kdm / auto-qc) =={NC}")
    for sf in subfx:
        out = run_sub(sf["subcommand"], sf["dir"], sf["args"])
        base_out = run_sub(sf["subcommand"], sf["dir"], sf["baseline_args"])
        match = sf.get("match", {})

        def hit(text, code):
            return match[code] in text if code in match else code_fires(text, code)

        for code in sf["expected_codes"]:
            fires = hit(out, code)
            absent = not hit(base_out, code)
            if fires and absent:
                print(f"  {GREEN}PASS{NC} {sf['name']} ({sf['subcommand']}): {code}")
                passed += 1
                covered.add(code)
            elif not fires:
                print(f"  {RED}FAIL{NC} {sf['name']} ({sf['subcommand']}): {code} did NOT fire")
                failed += 1
            else:
                print(f"  {RED}FAIL{NC} {sf['name']} ({sf['subcommand']}): {code} also fires "
                      f"on baseline (vacuous)")
                failed += 1

    # Reference packages: real third-party DCPs from the ClairMeta ECL set.
    ref_covered = set()
    refs = manifest.get("reference_packages", {}).get("packages", [])
    if not refs:
        print(f"{RED}ERROR: manifest has no reference_packages; "
              f"run scripts/scan_reference.py{NC}")
        sys.exit(1)
    print(f"\n{CYAN}== reference packages (ClairMeta) =={NC}")
    for pkg in refs:
        d = os.path.join(ecl, pkg["dir"])
        if not os.path.isdir(d):
            print(f"  {RED}FAIL{NC} {pkg['id']} (missing: {d}, "
                  f"run scripts/download_clairmeta_data.sh)")
            failed += 1
            continue
        out = run(d, pkg["flags"])
        recorded = set(pkg["observed_codes"])
        still = {c for c in recorded if code_fires(out, c)}
        ref_covered |= still
        if still == recorded:
            print(f"  {GREEN}PASS{NC} {pkg['id']} ({len(still)} codes reproduce)")
            passed += 1
        else:
            print(f"  {RED}FAIL{NC} {pkg['id']} missing {sorted(recorded - still)}")
            failed += 1

    all_set = set(ALL_CODES)
    # count only codes that are in ALL_CODES, so the headline and the uncovered
    # list share one denominator and always add up
    all_covered = (covered | ref_covered) & all_set
    uncovered = [c for c in ALL_CODES if c not in all_covered]

    print(f"\n{CYAN}=============================={NC}")
    print(f"Results: {GREEN}{passed} passed{NC}, {RED}{failed} failed{NC}")
    print(f"\nCoverage: {len(all_covered)}/{len(ALL_CODES)} codes exercised "
          f"({len(all_covered)} + {len(uncovered)} uncovered = {len(ALL_CODES)})")
    print(f"  isolated synthetic + subcommand fixtures: {len(covered & all_set)}")
    print(f"  additional via reference packages: {len((ref_covered - covered) & all_set)}")
    if uncovered:
        print(f"\nUncovered ({len(uncovered)}):")
        for c in uncovered:
            reason = UNCOVERED_REASONS.get(c, "no fixture yet")
            print(f"  - {c}: {reason}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
