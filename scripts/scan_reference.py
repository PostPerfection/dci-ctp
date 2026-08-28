#!/usr/bin/env python3
"""Scan the ClairMeta_Data ECL reference DCPs and record each one's dcpdoctor
verdict into the corpus manifest as `reference_packages`.

These are real third-party packages with real essence (Interop + SMPTE, 3D,
Atmos, HFR, encryption, plus deliberate defects like ECL39's mismatched wavelet
levels). They are fetched, not vendored (~1.5 GB), so paths are recorded
relative to a CLAIRMETA_DATA root the differential agent resolves at runtime.

We record the observed verdict (not an expected one): the differential-testing
agent diffs these against ClairMeta's own reference results.
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
ROOT = os.environ.get(
    "CLAIRMETA_DATA",
    os.path.join(os.path.dirname(REPO), "dci-ctp-work", "ClairMeta_Data"),
)
ECL = os.path.join(ROOT, "DCP", "ECL-SET")
# the reference packages carry real essence, so read it
REFERENCE_FLAGS = ["--check-mxf"]


def features(name):
    f = []
    if "IOP" in name:
        std = "interop"
    elif "SMPTE" in name:
        std = "smpte"
    else:
        std = "unknown"
    if "3D" in name:
        f.append("stereoscopic-3d")
    if "ATMOS" in name:
        f.append("atmos")
    if "NCCRYPT" in name:
        f.append("encrypted-noncoherent")
    elif "CRYPT" in name:
        f.append("encrypted")
    if "MULTI-PKL" in name:
        f.append("multi-pkl")
    if "-VF" in name or name.endswith("_VF"):
        f.append("version-file")
    for hfr in ("48", "50", "60", "96", "120"):
        if re.search(r"TST-[^_]*\b" + hfr + r"\b", name) or f"-{hfr}-" in name or f"-{hfr}_" in name:
            f.append(f"hfr-{hfr}")
            break
    if "UHD" in name:
        f.append("uhd")
    elif "_4K_" in name:
        f.append("4k")
    elif "_2K_" in name:
        f.append("2k")
    if "LVLS" in name:
        f.append("defect-mismatched-wavelet-levels")
    return std, f


def run(pkg_dir, flags):
    cmd = [DCPDOCTOR, "validate", "-v", *flags, pkg_dir]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return p.stdout + p.stderr


def parse(out):
    result = "UNKNOWN"
    m = re.search(r"^Result:\s+(\w+)", out, re.M)
    if m:
        result = m.group(1)
    codes = {}
    for sev, code in re.findall(r"\[(ERROR|WARNING|INFO)\]\s(\S+)\s-", out):
        codes.setdefault(code, sev)
    return result, codes


def main():
    if not os.path.isdir(ECL):
        print(f"ERROR: ClairMeta ECL set not found at {ECL}. Fetch it with "
              f"scripts/download_clairmeta_data.sh, or set CLAIRMETA_DATA to an "
              f"existing clone", file=sys.stderr)
        sys.exit(1)

    mf_path = os.path.join(CORPUS, "manifest.json")
    manifest = json.load(open(mf_path))

    pkgs = []
    for name in sorted(os.listdir(ECL)):
        d = os.path.join(ECL, name)
        if not (os.path.isdir(d) and (os.path.exists(os.path.join(d, "ASSETMAP.xml"))
                                      or os.path.exists(os.path.join(d, "ASSETMAP")))):
            continue
        std, feats = features(name)
        out = run(d, REFERENCE_FLAGS)
        result, codes = parse(out)
        entry = {
            "id": name.split("-")[0],
            "dir": name,
            "source": "clairmeta-ecl",
            "standard": std,
            "features": feats,
            "flags": REFERENCE_FLAGS,
            "observed_result": result,
            "observed_codes": codes,
        }
        pkgs.append(entry)
        print(f"  {entry['id']:7} {result:5} {std:7} codes={sorted(codes)}")

    manifest["reference_packages"] = {
        "root_env": "CLAIRMETA_DATA",
        "note": "fetched not vendored (~1.5GB); scripts/download_clairmeta_data.sh",
        "packages": pkgs,
    }
    json.dump(manifest, open(mf_path, "w"), indent=2)
    print(f"\nrecorded {len(pkgs)} reference packages into {mf_path}")


if __name__ == "__main__":
    main()
