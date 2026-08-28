#!/usr/bin/env python3
"""Cross-tool signature survey: dcpdoctor vs xmlsec1 over every signed document.

Walks the given roots (default: the corpus) for XML documents carrying an
XML-DSig Signature, gets a per-document verdict from both tools, and fails on
any document where one tool says the signature verifies and the other says it
does not. Documents xmlsec1 cannot process (rsa-sha1 refused by the crypto
policy, malformed Signature elements it cannot parse) render no verdict and are
listed instead of compared.

Usage: DCPDOCTOR=/path/to/dcpdoctor signature_survey.py [root ...]
"""

import os
import re
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DCPDOCTOR = os.environ.get("DCPDOCTOR") or shutil.which("dcpdoctor") or ""

DSIG_NAMESPACE = "http://www.w3.org/2000/09/xmldsig#"
SIGNATURE_INVALID_LINE = re.compile(r"signature_invalid - .* \((.+)\)")
XMLSEC_ERROR_TOKEN = re.compile(r"error=(\d+):([^:]+)")
KDM_ID_ATTRIBUTES = [
    "--id-attr:Id", "AuthenticatedPublic",
    "--id-attr:Id", "AuthenticatedPrivate",
]


def has_dsig_signature(content):
    if "Signature" not in content:
        return False
    return DSIG_NAMESPACE in content


def root_element(content):
    for match in re.finditer(r"<([\w.-]+:)?(\w+)[\s>]", content):
        if match.group(2) not in ("xml",):
            return match.group(2)
    return ""


def find_signed_documents(roots):
    """-> {path: kind} where kind is cpl, pkl, kdm or unknown."""
    kinds = {
        "CompositionPlaylist": "cpl",
        "PackingList": "pkl",
        "DCinemaSecurityMessage": "kdm",
    }
    documents = {}
    for root in roots:
        for dirpath, _, names in os.walk(root):
            for name in sorted(names):
                if not name.lower().endswith(".xml"):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except OSError:
                    continue
                if has_dsig_signature(content):
                    documents[path] = kinds.get(root_element(content), "unknown")
    return documents


def package_directory(path):
    d = os.path.dirname(path)
    for _ in range(4):
        entries = os.listdir(d)
        if "ASSETMAP.xml" in entries or "ASSETMAP" in entries:
            return d
        d = os.path.dirname(d)
    return None


def dcpdoctor_verdicts(documents):
    """-> {path: 'OK' | 'FAIL'} via one validate per package plus kdm runs."""
    verdicts = {}
    packages = {}
    for path, kind in documents.items():
        if kind == "kdm":
            out = subprocess.run(
                [DCPDOCTOR, "kdm", path],
                capture_output=True, text=True).stdout
            failed = any(os.path.realpath(m.group(1)) == os.path.realpath(path)
                         for m in SIGNATURE_INVALID_LINE.finditer(out))
            verdicts[path] = "FAIL" if failed else "OK"
        else:
            package = package_directory(path)
            if package is None:
                verdicts[path] = "NO_VERDICT"
                continue
            packages.setdefault(package, []).append(path)
    for package, paths in sorted(packages.items()):
        out = subprocess.run(
            [DCPDOCTOR, "validate", "-v", "--no-hashes", package],
            capture_output=True, text=True).stdout
        failed = {os.path.realpath(m.group(1))
                  for m in SIGNATURE_INVALID_LINE.finditer(out)}
        for path in paths:
            verdicts[path] = "FAIL" if os.path.realpath(path) in failed else "OK"
    return verdicts


def xmlsec_verdict(path, kind):
    """-> ('OK' | 'FAIL' | 'NO_VERDICT', detail)."""
    command = ["xmlsec1", "--verify", "--insecure"]
    if kind == "kdm":
        command += KDM_ID_ATTRIBUTES
    command.append(path)
    result = subprocess.run(command, capture_output=True, text=True)
    output = result.stdout + result.stderr
    errors = ["error={}:{}".format(*m.groups())
              for m in XMLSEC_ERROR_TOKEN.finditer(output)]
    detail = ";".join(sorted(set(errors)))
    if re.search(r"^OK$", output, re.M):
        return "OK", detail
    if re.search(r"^FAIL$", output, re.M):
        # error 12 is a crypto verdict (digest or signature value mismatch).
        # anything else means xmlsec1 could not process the signature.
        if all(e.startswith("error=12:") for e in set(errors)) and errors:
            return "FAIL", detail
        return "NO_VERDICT", detail or "failed with no error detail"
    last_line = output.strip().splitlines()[-1] if output.strip() else "no output"
    return "NO_VERDICT", detail or last_line


def main():
    roots = [os.path.abspath(r) for r in sys.argv[1:]] or [os.path.join(REPO, "corpus")]
    if not DCPDOCTOR or not os.access(DCPDOCTOR, os.X_OK):
        print(f"ERROR: dcpdoctor not found at {DCPDOCTOR or '(unset)'}. Set DCPDOCTOR "
              f"to the binary or put dcpdoctor on PATH", file=sys.stderr)
        return 2
    documents = find_signed_documents(roots)
    if not documents:
        print(f"ERROR: no signed documents under {roots}", file=sys.stderr)
        return 2

    ours = dcpdoctor_verdicts(documents)
    disagreements = []
    unjudged = []
    unverified_ok = []
    compared = 0
    for path, kind in sorted(documents.items()):
        theirs, detail = xmlsec_verdict(path, kind)
        mine = ours[path]
        if theirs == "NO_VERDICT" or mine == "NO_VERDICT":
            unjudged.append((path, mine, detail))
            if mine == "OK":
                unverified_ok.append(path)
            continue
        compared += 1
        if mine != theirs:
            disagreements.append((path, mine, theirs, detail))

    print(f"{len(documents)} signed documents, {compared} compared, "
          f"{len(unjudged)} no verdict from xmlsec1, "
          f"{len(disagreements)} disagreements")
    for path, mine, theirs, detail in disagreements:
        print(f"DISAGREE dcpdoctor={mine} xmlsec1={theirs} {path} [{detail}]")
    for path, mine, detail in unjudged:
        print(f"NO_VERDICT dcpdoctor={mine} {path} [{detail}]")
    if unverified_ok:
        print(f"note: {len(unverified_ok)} documents pass dcpdoctor but xmlsec1 "
              "rendered no verdict (expected for rsa-sha1 Interop under a strict "
              "crypto policy)")
    return 1 if disagreements else 0


if __name__ == "__main__":
    sys.exit(main())
