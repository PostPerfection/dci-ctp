#!/usr/bin/env bash
set -euo pipefail

# DCI CTP Test Suite Runner
# Runs dcpdoctor against test DCPs and validates expected results

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
DCPDOCTOR="${DCPDOCTOR:-$(command -v dcpdoctor 2>/dev/null || true)}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

VERBOSE=""
CATEGORY=""
PASSED=0
FAILED=0

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -v, --verbose     Show detailed output"
    echo "  --category CAT    Run only specified category"
    echo "  --dcpdoctor PATH  Path to dcpdoctor binary"
    echo "  -h, --help        Show this help"
}

CATEGORIES="packaging composition picture integrity audio security presentation isdcf generated"

while [[ $# -gt 0 ]]; do
    case $1 in
        -v|--verbose) VERBOSE="-v"; shift ;;
        --category) CATEGORY="$2"; shift 2 ;;
        --dcpdoctor) DCPDOCTOR="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1"; usage; exit 1 ;;
    esac
done

if [[ -n "$CATEGORY" && " $CATEGORIES " != *" $CATEGORY "* ]]; then
    echo -e "${RED}ERROR: unknown category '$CATEGORY'${NC}"
    echo "Valid categories: $CATEGORIES"
    exit 1
fi

if [[ ! -x "$DCPDOCTOR" ]]; then
    echo -e "${RED}ERROR: dcpdoctor not found${NC}"
    echo "Set DCPDOCTOR, use --dcpdoctor PATH, or put dcpdoctor on PATH"
    exit 1
fi

# Synthetic fixtures are generated, not committed. encrypted_no_kdm is created last,
# so its absence means no run or an interrupted one.
if [[ ! -d "$REPO_DIR/tests/synthetic/invalid/encrypted_no_kdm" ]]; then
    echo -e "${YELLOW}Synthetic fixtures missing, creating them...${NC}"
    "$SCRIPT_DIR/create_synthetic.sh"
    echo ""
fi

echo -e "${CYAN}DCI CTP Test Suite${NC}"
echo -e "dcpdoctor: $DCPDOCTOR"
echo -e "version: $($DCPDOCTOR --version 2>/dev/null || echo 'unknown')"
echo ""

# Test runner: expects PASS or specific error codes
run_test() {
    local name="$1"
    local dcp_dir="$2"
    local expect="$3"  # "pass", "fail", or specific error code
    local extra_flags="${4:-}"

    if [[ ! -d "$dcp_dir" ]]; then
        local fetch_hint
        case "$dcp_dir" in
            *"/tests/isdcf/"*)     fetch_hint="scripts/download_isdcf.sh" ;;
            *"/tests/generated/"*) fetch_hint="scripts/generate.sh" ;;
            *)                     fetch_hint="scripts/create_synthetic.sh" ;;
        esac
        echo -e "  ${RED}FAIL${NC} $name (missing: $dcp_dir, run $fetch_hint)"
        FAILED=$((FAILED + 1))
        return
    fi

    local output
    output=$($DCPDOCTOR validate --strict $VERBOSE $extra_flags "$dcp_dir" 2>&1) || true

    local result_line
    result_line=$(echo "$output" | grep "^Result:" || echo "Result: UNKNOWN")

    case "$expect" in
        pass)
            if echo "$result_line" | grep -q "PASS"; then
                echo -e "  ${GREEN}PASS${NC} $name"
                PASSED=$((PASSED + 1))
            else
                echo -e "  ${RED}FAIL${NC} $name (expected PASS, got: $result_line)"
                [[ -n "$VERBOSE" ]] && echo "$output" | sed 's/^/    /' || true
                FAILED=$((FAILED + 1))
            fi
            ;;
        fail)
            if echo "$result_line" | grep -q "FAIL"; then
                echo -e "  ${GREEN}PASS${NC} $name (correctly detected errors)"
                PASSED=$((PASSED + 1))
            else
                echo -e "  ${RED}FAIL${NC} $name (expected FAIL, got: $result_line)"
                [[ -n "$VERBOSE" ]] && echo "$output" | sed 's/^/    /' || true
                FAILED=$((FAILED + 1))
            fi
            ;;
        errors-only:*)
            local only_code="${expect#errors-only:}"
            local error_lines unexpected_lines
            error_lines=$(echo "$output" | grep '^\[ERROR\]' || true)
            unexpected_lines=$(echo "$error_lines" | grep -v "\] $only_code - " || true)
            if echo "$result_line" | grep -q "FAIL" && [[ -n "$error_lines" && -z "$unexpected_lines" ]]; then
                echo -e "  ${GREEN}PASS${NC} $name (only expected error: $only_code)"
                PASSED=$((PASSED + 1))
            else
                echo -e "  ${RED}FAIL${NC} $name (expected only '$only_code' errors, got: $result_line, first unexpected: $(echo "$unexpected_lines" | head -n 1))"
                [[ -n "$VERBOSE" ]] && echo "$output" | sed 's/^/    /' || true
                FAILED=$((FAILED + 1))
            fi
            ;;
        *)
            # Expect specific error code. Match the note's code field ("[SEVERITY] code - ..."),
            # not the whole output, so an input path that echoes the code can't pass the test.
            if echo "$output" | grep -qE "\] $expect - "; then
                echo -e "  ${GREEN}PASS${NC} $name (found expected error: $expect)"
                PASSED=$((PASSED + 1))
            else
                echo -e "  ${RED}FAIL${NC} $name (expected error '$expect' not found)"
                [[ -n "$VERBOSE" ]] && echo "$output" | sed 's/^/    /' || true
                FAILED=$((FAILED + 1))
            fi
            ;;
    esac
}

# ====== PACKAGING TESTS (CTP Section 4) ======
if [[ -z "$CATEGORY" || "$CATEGORY" == "packaging" ]]; then
    echo -e "${CYAN}── Packaging Tests (CTP §4) ──${NC}"
    
    run_test "CTP-PKG: missing ASSETMAP detected" \
        "$REPO_DIR/tests/synthetic/invalid/missing_assetmap" \
        "missing_assetmap"
    
    run_test "CTP-PKG: empty DCP detected" \
        "$REPO_DIR/tests/synthetic/invalid/empty_dcp" \
        "fail"
    
    # synthetic mxfs are zero stubs, so essence inspection is off for them
    run_test "CTP-PKG: valid SMPTE packaging" \
        "$REPO_DIR/tests/synthetic/valid/minimal_smpte" \
        "pass" "--no-mxf --no-deep-j2k"
    
    run_test "CTP-PKG: valid Interop packaging" \
        "$REPO_DIR/tests/synthetic/valid/minimal_interop" \
        "pass" "--no-mxf --no-deep-j2k"
    echo ""
fi

# ====== COMPOSITION TESTS (CTP Section 5) ======
if [[ -z "$CATEGORY" || "$CATEGORY" == "composition" ]]; then
    echo -e "${CYAN}── Composition Tests (CTP §5) ──${NC}"
    
    run_test "CTP-CPL: bad XML detected" \
        "$REPO_DIR/tests/synthetic/invalid/bad_xml" \
        "fail"
    
    # ContentKind validation (strict mode)
    run_test "CTP-CPL: invalid content kind rejected" \
        "$REPO_DIR/tests/synthetic/invalid/bad_content_kind" \
        "cpl_invalid_content_kind"
    
    # EditRate validation (strict mode)
    run_test "CTP-CPL: invalid edit rate rejected" \
        "$REPO_DIR/tests/synthetic/invalid/bad_edit_rate" \
        "cpl_invalid_edit_rate"
    
    run_test "CTP-CPL: missing CPL" \
        "$REPO_DIR/tests/synthetic/invalid/missing_cpl" \
        "missing_cpl"

    # CPL asset Id absent from the ASSETMAP. Without --ov this is treated as a
    # supplemental/VF package that references an external OV (warning). With --ov
    # given and the id resolving in neither the package nor the OV, it is a real
    # broken cross-reference. Point --ov at the fixture itself so the id resolves
    # nowhere and cross_ref_broken fires.
    run_test "CTP-CPL: broken cross-reference detected" \
        "$REPO_DIR/tests/synthetic/invalid/bad_cross_ref" \
        "cross_ref_broken" \
        "--ov $REPO_DIR/tests/synthetic/invalid/bad_cross_ref"

    # Same fixture without --ov: assumed to be a VF referencing an external OV
    run_test "CTP-CPL: missing OV for supplemental reference" \
        "$REPO_DIR/tests/synthetic/invalid/bad_cross_ref" \
        "supplemental_ov_not_provided"
    echo ""
fi

# ====== PRESENTATION TESTS (CTP Section 9) ======
if [[ -z "$CATEGORY" || "$CATEGORY" == "presentation" ]]; then
    echo -e "${CYAN}── Presentation Tests (CTP §9) ──${NC}"

    # Required FFMC/LFMC markers absent (strict mode)
    run_test "CTP-PRES: missing required marker detected" \
        "$REPO_DIR/tests/synthetic/invalid/bad_markers" \
        "marker_missing"

    # Marker with a label but no Offset
    run_test "CTP-PRES: malformed marker detected" \
        "$REPO_DIR/tests/synthetic/invalid/bad_markers" \
        "marker_invalid"
    echo ""
fi

# ====== PICTURE TESTS (CTP Section 6) ======
if [[ -z "$CATEGORY" || "$CATEGORY" == "picture" ]]; then
    echo -e "${CYAN}── Picture Tests (CTP §6) ──${NC}"
    
    # synthetic mxfs are zero stubs, so essence inspection is off for them
    run_test "CTP-PIC: valid 2K scope resolution" \
        "$REPO_DIR/tests/synthetic/valid/scope_2k" \
        "pass" "--no-mxf --no-deep-j2k"
    
    run_test "CTP-PIC: valid 2K flat resolution" \
        "$REPO_DIR/tests/synthetic/valid/flat_2k" \
        "pass" "--no-mxf --no-deep-j2k"
    echo ""
fi

# ====== INTEGRITY TESTS ======
if [[ -z "$CATEGORY" || "$CATEGORY" == "integrity" ]]; then
    echo -e "${CYAN}── Integrity Tests ──${NC}"
    
    run_test "CTP-INT: hash mismatch detected" \
        "$REPO_DIR/tests/synthetic/invalid/bad_hash" \
        "pkl_hash_mismatch"
    echo ""
fi

# ====== AUDIO TESTS (CTP Section 7) ======
if [[ -z "$CATEGORY" || "$CATEGORY" == "audio" ]]; then
    echo -e "${CYAN}── Audio Tests (CTP §7) ──${NC}"
    
    # Generated DCP has real audio MXF — validate sample rate
    run_test "CTP-AUD: valid 48kHz audio (generated DCP)" \
        "$REPO_DIR/tests/generated/short_2k_24fps" \
        "pass" "--check-mxf"
    
    # the isdcf signer chain expired in 2023 and dcpdoctor reports it since 1.3.0
    # ISDCF 5.1 has proper audio
    ISDCF_51="$REPO_DIR/tests/isdcf/SMPTE_TST-1-Bv21_51-71_20170110_SMPTE_Folders/SMPTE_TST-1-Bv21_S_EN-EN-CCAP_US_51-HI-VI_2K_ISDCF_20170110_DTB_SMPTE_OV"
    run_test "CTP-AUD: ISDCF 5.1 audio valid" \
        "$ISDCF_51" \
        "errors-only:certificate_expired" "--check-mxf"
    
    # ISDCF 7.1 has proper audio
    ISDCF_71="$REPO_DIR/tests/isdcf/SMPTE_TST-1-Bv21_51-71_20170110_SMPTE_Folders/SMPTE_TST-1-Bv21_S_EN-EN-CCAP_US_71-HI-VI_2K_ISDCF_20170110_DTB_SMPTE_OV"
    run_test "CTP-AUD: ISDCF 7.1 audio valid" \
        "$ISDCF_71" \
        "errors-only:certificate_expired" "--check-mxf"
    echo ""
fi

# ====== SECURITY TESTS (CTP Section 8) ======
if [[ -z "$CATEGORY" || "$CATEGORY" == "security" ]]; then
    echo -e "${CYAN}── Security Tests (CTP §8) ──${NC}"
    
    # Generated DCP should be unencrypted (no KeyId references)
    run_test "CTP-SEC: unencrypted DCP validates" \
        "$REPO_DIR/tests/generated/short_2k_24fps" \
        "pass"

    # Encrypted CPL (KeyId present) is detected
    run_test "CTP-SEC: encrypted content detected" \
        "$REPO_DIR/tests/synthetic/invalid/encrypted_no_kdm" \
        "encryption_detected"

    # Encrypted content with no KDM in the package is flagged
    run_test "CTP-SEC: missing KDM for encrypted content detected" \
        "$REPO_DIR/tests/synthetic/invalid/encrypted_no_kdm" \
        "kdm_required"

    # ISDCF content is encrypted — should still validate structure
    ISDCF_51="$REPO_DIR/tests/isdcf/SMPTE_TST-1-Bv21_51-71_20170110_SMPTE_Folders/SMPTE_TST-1-Bv21_S_EN-EN-CCAP_US_51-HI-VI_2K_ISDCF_20170110_DTB_SMPTE_OV"
    run_test "CTP-SEC: encrypted ISDCF DCP validates" \
        "$ISDCF_51" \
        "errors-only:certificate_expired" "--check-mxf"
    echo ""
fi

# ====== ISDCF REFERENCE CONTENT ======
if [[ -z "$CATEGORY" || "$CATEGORY" == "isdcf" ]]; then
    echo -e "${CYAN}── ISDCF Reference Content ──${NC}"
    
    ISDCF_BASE="$REPO_DIR/tests/isdcf/SMPTE_TST-1-Bv21_51-71_20170110_SMPTE_Folders"
    
    run_test "ISDCF Bv2.1 5.1 surround" \
        "$ISDCF_BASE/SMPTE_TST-1-Bv21_S_EN-EN-CCAP_US_51-HI-VI_2K_ISDCF_20170110_DTB_SMPTE_OV" \
        "errors-only:certificate_expired" "--check-mxf"
    
    run_test "ISDCF Bv2.1 7.1 surround" \
        "$ISDCF_BASE/SMPTE_TST-1-Bv21_S_EN-EN-CCAP_US_71-HI-VI_2K_ISDCF_20170110_DTB_SMPTE_OV" \
        "errors-only:certificate_expired" "--check-mxf"
    echo ""
fi

# ====== GENERATED CONTENT ======
if [[ -z "$CATEGORY" || "$CATEGORY" == "generated" ]]; then
    echo -e "${CYAN}── Generated DCP Tests ──${NC}"
    
    generated_count=0
    for dcp_dir in "$REPO_DIR/tests/generated"/*/; do
        [[ -d "$dcp_dir" ]] || continue
        name=$(basename "$dcp_dir")
        run_test "GEN: $name" "$dcp_dir" "pass"
        generated_count=$((generated_count + 1))
    done
    if [[ $generated_count -eq 0 ]]; then
        echo -e "  ${RED}FAIL${NC} no generated DCPs in $REPO_DIR/tests/generated (run scripts/generate.sh)"
        FAILED=$((FAILED + 1))
    fi
    echo ""
fi

# ====== SUMMARY ======
TOTAL=$((PASSED + FAILED))
echo -e "${CYAN}══════════════════════════════${NC}"
echo -e "Results: ${GREEN}${PASSED} passed${NC}, ${RED}${FAILED} failed${NC} (${TOTAL} total)"

if [[ $FAILED -gt 0 ]]; then
    exit 1
fi
