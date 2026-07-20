#!/usr/bin/env python3
"""Differential validation: dcpdoctor vs ClairMeta over the shared corpus.

Runs both validators over the baselines, negative fixtures, and ClairMeta ECL
reference packages recorded in ../corpus/manifest.json, classifies every package
(BOTH_PASS / BOTH_FAIL / DCPDOCTOR_ONLY_FAIL / CLAIRMETA_ONLY_FAIL / TOOL_ERROR),
and writes report.json + report.md.

For negative fixtures every package derives from one dcpwizard base that ClairMeta
already rejects on schema grounds (check_am_xml/check_cpl_xml), so we attribute
ClairMeta's catch of the injected defect by diffing the fixture's failed-check set
against its baseline's (newly-failed checks), not by the raw verdict.

IMF-vs-Photon is not run: the corpus has no IMF packages, so Photon has nothing to
validate here. DCP-vs-ClairMeta is the whole comparison.

Run: uv run differential.py  (from the diff/ dir)
Env: DCPDOCTOR (binary path), CLAIRMETA_DATA (ECL clone root).
"""

import io
import json
import logging
import os
import re
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.abspath(os.path.join(HERE, "..", "corpus"))
DCPDOCTOR = os.environ.get(
    "DCPDOCTOR",
    os.path.expanduser("~/src/PostPerfection/dcpdoctor/rust/target/release/dcpdoctor"),
)
CLAIRMETA_DATA = os.environ.get(
    "CLAIRMETA_DATA",
    os.path.expanduser("~/src/PostPerfection/dci-ctp-work/ClairMeta_Data"),
)

# ClairMeta ERROR-level check name -> nearest dcpdoctor Code, for readability and
# for spotting checks with NO dcpdoctor equivalent (the coverage-gap list). None
# means dcpdoctor has no equivalent check.
CLAIRMETA_TO_CODE = {
    "check_am_xml": "xml_schema_violation",
    "check_cpl_xml": "xml_schema_violation",
    "check_pkl_xml": "xml_schema_violation",
    "check_vol_xml": "xml_schema_violation",
    "check_xml": "xml_schema_violation",
    "check_xml_constraints": "xml_schema_violation",
    "check_subtitle_cpl_xml": "subtitle_parse_error",
    "check_dcp_multiple_am_or_vol": "missing_assetmap",
    "check_assets_am_path": "asset_not_found",
    "check_assets_am_uuid": "duplicate_asset_id",
    "check_assets_am_size": None,
    "check_assets_pkl_hash": "pkl_hash_mismatch",
    "check_assets_pkl_size": None,
    "check_assets_pkl_referenced_by_assetamp": "pkl_missing_asset_reference",
    "check_cpl_referenced_by_pkl": "missing_pkl",
    "check_assets_cpl_hash": "pkl_hash_mismatch",
    "check_assets_cpl_uuid": "invalid_uuid",
    "check_assets_cpl_cut": "cpl_mismatched_durations",
    "check_cpl_reel_duration": "cpl_invalid_duration",
    "check_cpl_reel_duration_picture_sound": "cpl_mismatched_durations",
    "check_cpl_reels_cut": "reel_discontinuity",
    "check_cpl_reels_timed_text_coherence": None,
    "check_cpl_id_rfc4122": "invalid_uuid",
    "check_picture_cpl_editrate_framerate": "cpl_invalid_edit_rate",
    "check_picture_cpl_resolution": "picture_invalid_resolution",
    "check_picture_cpl_encoding": "j2k_invalid_profile",
    "check_picture_cpl_max_bitrate": "j2k_bitrate_exceeded",
    "check_picture_cpl_avg_bitrate": "j2k_bitrate_exceeded",
    "check_sound_cpl_channels": "sound_invalid_channel_count",
    "check_sound_cpl_sampling": "sound_invalid_sample_rate",
    "check_sound_cpl_channel_assignments": "sound_invalid_channel_count",
    "check_document_signature": "signature_invalid",
    "check_certif_signature": "certificate_chain_broken",
    "check_sign_chain_coherence": "certificate_chain_broken",
    "check_certif_date_expired": "certificate_expired",
    "check_subtitle_dcp_format": "smpte_namespace_wrong",
    "check_subtitle_cpl_st_timing": "subtitle_invalid_timing",
    "check_subtitle_cpl_font": "subtitle_font_missing",
    "check_subtitle_cpl_uuid": "missing_required_element",
    "check_link_ov_asset": "supplemental_ov_not_provided",
    "check_link_ov_coherence": "supplemental_ov_not_provided",
    # checks with no dcpdoctor equivalent (coverage gaps), grouped in report:
    "check_certif_basic_constraint": None,
    "check_certif_key_usage": None,
    "check_certif_extensions": None,
    "check_certif_rsa_validity": None,
    "check_certif_organization_name": None,
    "check_certif_publickey_thumbprint": None,
    "check_certif_role": None,
    "check_certif_fields": None,
    "check_dcp_foreign_files": None,
    "check_dcp_empty_dir": None,
    "check_cpl_empty_text_fields": None,
    "check_assets_cpl_labels": None,
    "check_assets_cpl_labels_schema": None,
    "check_assets_cpl_metadata": None,
    "check_sound_cpl_blockalign": None,
    "check_sound_cpl_quantization": None,
}


def run_dcpdoctor(path, flags):
    resolved = [os.path.join(path, f[1:]) if f.startswith("@") else f for f in flags]
    cmd = [DCPDOCTOR, "validate", "-v", *resolved, path]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    out = p.stdout + p.stderr
    m = re.search(r"^Result:\s+(\w+)", out, re.M)
    verdict = m.group(1) if m else "UNKNOWN"
    codes = {}
    for sev, code in re.findall(r"\[(ERROR|WARNING|INFO)\]\s(\S+)\s-", out):
        codes.setdefault(code, sev)
    error_codes = sorted(c for c, s in codes.items() if s == "ERROR")
    return {
        "verdict": "FAIL" if (verdict == "FAIL" or error_codes) else "PASS",
        "error_codes": error_codes,
        "all_codes": codes,
    }


def run_clairmeta(path):
    """Returns dict with verdict PASS/FAIL/ERROR and the ERROR-level check names."""
    from clairmeta import DCP  # imported lazily so the module import cost is paid once

    logging.getLogger("Clairmeta").disabled = True
    sink = io.StringIO()
    try:
        with redirect_stdout(sink), redirect_stderr(sink):
            dcp = DCP(path)
            _status, report = dcp.check()
            err_checks = sorted({c.name for c in report.checks_by_criticality("ERROR")})
            warn_checks = sorted({c.name for c in report.checks_by_criticality("WARNING")})
            # ClairMeta swallows a check that raises into an error message; flag it
            internal = sorted(
                {
                    c.name
                    for c in report.checks
                    for e in c.errors
                    if "internal error" in (e.message or "").lower()
                }
            )
            valid = report.is_valid()
        return {
            "verdict": "PASS" if valid else "FAIL",
            "error_checks": err_checks,
            "warning_checks": warn_checks,
            "internal_errors": internal,
        }
    except Exception as e:  # ClairMeta could not probe/parse this package at all
        return {
            "verdict": "ERROR",
            "error_checks": [],
            "warning_checks": [],
            "internal_errors": [],
            "exception": f"{type(e).__name__}: {str(e)[:200]}",
        }


def classify(dd, cm):
    if cm["verdict"] == "ERROR":
        return "TOOL_ERROR"
    dd_fail = dd["verdict"] == "FAIL"
    cm_fail = cm["verdict"] == "FAIL"
    if dd_fail and cm_fail:
        return "BOTH_FAIL"
    if not dd_fail and not cm_fail:
        return "BOTH_PASS"
    if dd_fail and not cm_fail:
        return "DCPDOCTOR_ONLY_FAIL"
    return "CLAIRMETA_ONLY_FAIL"


def main():
    if not os.access(DCPDOCTOR, os.X_OK):
        print(f"ERROR: dcpdoctor not executable at {DCPDOCTOR}", file=sys.stderr)
        sys.exit(2)
    # ClairMeta logs a dependency scan at import time; mute WARNING/below first
    logging.disable(logging.WARNING)
    import clairmeta  # noqa: F401
    logging.getLogger("Clairmeta").disabled = True

    manifest = json.load(open(os.path.join(CORPUS, "manifest.json")))
    records = []
    counts = {k: 0 for k in
              ("BOTH_PASS", "BOTH_FAIL", "DCPDOCTOR_ONLY_FAIL",
               "CLAIRMETA_ONLY_FAIL", "TOOL_ERROR")}

    # --- valid baselines: both should pass clean ---
    for b in manifest["baselines"]:
        path = os.path.join(CORPUS, b["dir"])
        dd = run_dcpdoctor(path, b["flags"])
        cm = run_clairmeta(path)
        bucket = classify(dd, cm)
        counts[bucket] += 1
        records.append({
            "group": "baseline", "id": b["dir"], "standard": "smpte",
            "classification": bucket, "dcpdoctor": dd, "clairmeta": cm,
            "expected_codes": b["expected_codes"],
        })

    # --- negative fixtures: attribute ClairMeta's catch by diffing vs baseline ---
    baseline_cm = {}  # cache ClairMeta failed-check set per baseline dir
    for fx in manifest["fixtures"]:
        path = os.path.join(CORPUS, fx["dir"])
        dd = run_dcpdoctor(path, fx["flags"])
        cm = run_clairmeta(path)
        bucket = classify(dd, cm)
        counts[bucket] += 1

        base_dir = fx["baseline"]
        if base_dir not in baseline_cm:
            baseline_cm[base_dir] = run_clairmeta(os.path.join(CORPUS, base_dir))
        base_err = set(baseline_cm[base_dir]["error_checks"])
        newly_failed = sorted(set(cm["error_checks"]) - base_err)
        # ClairMeta caught the injected defect if it newly-failed a check, hit an
        # internal error, or couldn't parse the package at all
        cm_caught = bool(newly_failed) or bool(cm["internal_errors"]) or \
            cm["verdict"] == "ERROR"
        dd_caught = all(
            c in dd["error_codes"] or c in dd["all_codes"]
            for c in fx["expected_codes"]
        )
        records.append({
            "group": "fixture", "id": fx["dir"], "standard": "smpte",
            "classification": bucket, "dcpdoctor": dd, "clairmeta": cm,
            "expected_codes": fx["expected_codes"],
            "dcpdoctor_caught_defect": dd_caught,
            "clairmeta_caught_defect": cm_caught,
            "clairmeta_newly_failed": newly_failed,
        })

    # --- ClairMeta ECL reference packages ---
    refs = manifest.get("reference_packages", {})
    ecl = os.path.join(CLAIRMETA_DATA, "DCP", "ECL-SET")
    ref_ran = os.path.isdir(ecl)
    if ref_ran:
        for pkg in refs.get("packages", []):
            path = os.path.join(ecl, pkg["dir"])
            if not os.path.isdir(path):
                continue
            dd = run_dcpdoctor(path, [])
            cm = run_clairmeta(path)
            bucket = classify(dd, cm)
            counts[bucket] += 1
            records.append({
                "group": "reference", "id": pkg["id"], "standard": pkg["standard"],
                "classification": bucket, "dcpdoctor": dd, "clairmeta": cm,
                "features": pkg.get("features", []),
            })

    # --- coverage-gap analysis: ClairMeta ERROR checks with no dcpdoctor equivalent ---
    fired = {}  # check name -> count of packages where it fired as ERROR
    for r in records:
        for c in r["clairmeta"]["error_checks"]:
            fired[c] = fired.get(c, 0) + 1
    unmapped = sorted(c for c in fired if CLAIRMETA_TO_CODE.get(c) is None)
    mapped = sorted(c for c in fired if CLAIRMETA_TO_CODE.get(c))

    # dcpdoctor false positives: it errors on a package ClairMeta validates clean,
    # on a substantive code. certificate_expired is a defensible stricter policy
    # (ClairMeta downgrades it to INFO), so it is excluded as a divergence, not a bug.
    POLICY_CODES = {"certificate_expired"}
    false_positives = []
    fp_by_code = {}
    for r in records:
        if r["classification"] != "DCPDOCTOR_ONLY_FAIL":
            continue
        substantive = sorted(set(r["dcpdoctor"]["error_codes"]) - POLICY_CODES)
        if substantive:
            false_positives.append({
                "id": r["id"], "standard": r["standard"],
                "features": r.get("features", []), "codes": substantive,
            })
            for c in substantive:
                fp_by_code[c] = fp_by_code.get(c, 0) + 1

    # fixture defect coverage: who catches each injected defect
    fixtures = [r for r in records if r["group"] == "fixture"]
    dd_caught = [r["id"] for r in fixtures if r["dcpdoctor_caught_defect"]]
    cm_missed = [r["id"] for r in fixtures if not r["clairmeta_caught_defect"]]
    cm_caught = [r["id"] for r in fixtures if r["clairmeta_caught_defect"]]

    summary = {
        "counts": counts,
        "total": sum(counts.values()),
        "reference_ran": ref_ran,
        "photon_imf": "not_run: corpus contains no IMF packages",
        "fixture_defect_coverage": {
            "total": len(fixtures),
            "dcpdoctor_caught": len(dd_caught),
            "clairmeta_caught": len(cm_caught),
            "clairmeta_missed": cm_missed,
        },
        "clairmeta_error_checks_fired": fired,
        "coverage_gaps_no_dcpdoctor_equivalent": unmapped,
        "clairmeta_checks_with_equivalent": mapped,
        "dcpdoctor_false_positives": false_positives,
        "dcpdoctor_false_positives_by_code": fp_by_code,
    }
    out = {"summary": summary, "records": records}
    json.dump(out, open(os.path.join(HERE, "report.json"), "w"), indent=2)
    write_markdown(out)

    print("\n== classification ==")
    for k, v in counts.items():
        print(f"  {k:20} {v}")
    print(f"  {'TOTAL':20} {sum(counts.values())}")
    print(f"\nfalse positives: {len(false_positives)}; "
          f"ClairMeta ERROR checks with no dcpdoctor equivalent: {len(unmapped)}")
    print(f"report.json + report.md written to {HERE}")


def write_markdown(out):
    s, recs = out["summary"], out["records"]
    L = []
    L.append("# Differential validation: dcpdoctor vs ClairMeta\n")
    L.append("Generated by `diff/differential.py`. ClairMeta 1.6.2 (asdcplib absent, "
             "so its MXF-essence checks bypass; XML/structure/signature/cert checks run).\n")
    L.append("## Classification counts\n")
    L.append("| Bucket | Count |")
    L.append("|---|---|")
    for k, v in s["counts"].items():
        L.append(f"| {k} | {v} |")
    L.append(f"| **TOTAL** | **{s['total']}** |\n")
    L.append(f"IMF-vs-Photon: {s['photon_imf']}.\n")

    fdc = s["fixture_defect_coverage"]
    L.append("## Defect coverage over negative fixtures\n")
    L.append(f"dcpdoctor caught {fdc['dcpdoctor_caught']}/{fdc['total']} injected defects; "
             f"ClairMeta caught {fdc['clairmeta_caught']}/{fdc['total']}.\n")
    L.append("ClairMeta missed (dcpdoctor checks these, ClairMeta does not flag them as a "
             "failure): " + ", ".join(os.path.basename(x) for x in fdc["clairmeta_missed"]) + ".\n")

    L.append("## dcpdoctor false positives / stricter divergences\n")
    if s["dcpdoctor_false_positives_by_code"]:
        L.append("dcpdoctor errors on packages ClairMeta validates clean. By code "
                 "(certificate_expired excluded as a defensible stricter policy):\n")
        L.append("| dcpdoctor code | packages |")
        L.append("|---|---|")
        for c, n in sorted(s["dcpdoctor_false_positives_by_code"].items(),
                           key=lambda kv: -kv[1]):
            L.append(f"| {c} | {n} |")
        L.append("")
        L.append("Package detail:\n")
        L.append("| Package | standard | codes |")
        L.append("|---|---|---|")
        for fp in s["dcpdoctor_false_positives"]:
            L.append(f"| {fp['id']} | {fp['standard']} | {', '.join(fp['codes'])} |")
        L.append("")
    else:
        L.append("None.\n")

    L.append("## dcpdoctor coverage gaps (ClairMeta ERROR checks with no dcpdoctor equivalent)\n")
    L.append("These ClairMeta checks fired at ERROR across the corpus and dcpdoctor has "
             "no equivalent code:\n")
    L.append("| ClairMeta check | packages hit |")
    L.append("|---|---|")
    for c in s["coverage_gaps_no_dcpdoctor_equivalent"]:
        L.append(f"| {c} | {s['clairmeta_error_checks_fired'][c]} |")
    L.append("")

    L.append("## Per-package results\n")
    L.append("| Group | Package | Standard | Class | dcpdoctor | ClairMeta |")
    L.append("|---|---|---|---|---|---|")
    for r in recs:
        dd = r["dcpdoctor"]["verdict"]
        cm = r["clairmeta"]["verdict"]
        L.append(f"| {r['group']} | {r['id']} | {r['standard']} | "
                 f"{r['classification']} | {dd} | {cm} |")
    L.append("")

    L.append("## Fixture defect coverage (does ClairMeta catch each injected defect?)\n")
    L.append("| Fixture | dcpdoctor code | dcpdoctor caught | ClairMeta caught | ClairMeta newly-failed checks |")
    L.append("|---|---|---|---|---|")
    for r in recs:
        if r["group"] != "fixture":
            continue
        nf = ", ".join(r["clairmeta_newly_failed"]) or "-"
        L.append(f"| {r['id']} | {', '.join(r['expected_codes'])} | "
                 f"{'yes' if r['dcpdoctor_caught_defect'] else 'NO'} | "
                 f"{'yes' if r['clairmeta_caught_defect'] else 'NO'} | {nf} |")
    L.append("")

    open(os.path.join(HERE, "report.md"), "w").write("\n".join(L))


if __name__ == "__main__":
    main()
