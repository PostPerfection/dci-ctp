# DCI CTP Test Suite

[![CI](https://github.com/PostPerfection/dci-ctp/actions/workflows/ci.yml/badge.svg)](https://github.com/PostPerfection/dci-ctp/actions/workflows/ci.yml)

[Documentation](https://postperfection.github.io/dci-ctp/)

Compliance Test Plan (CTP) validation test suite for [dcpdoctor](https://github.com/PostPerfection/dcpdoctor).

Based on the [DCI Compliance Test Plan v1.5.0](https://documents.dcimovies.com/CTP/release/1.5.0/) and the [ISDCF SMPTE-DCP test content](https://www.isdcf.com/smpte-dcp-tests/).

## Structure

```
tests/
├── isdcf/          # ISDCF SMPTE Bv2.1 test DCPs (5.1 + 7.1)
├── synthetic/      # Hand-crafted DCPs for specific edge cases
│   ├── valid/      # Should pass validation
│   └── invalid/    # Should fail with specific errors
└── generated/      # DCPs created by dcpwizard for testing
scripts/
├── run_tests.sh    # Run full test suite
└── generate.sh     # Generate test DCPs from source material
```

## Test Categories (DCI CTP Sections)

| Category | CTP Section | Tests |
|----------|-------------|-------|
| Packaging | §4 | VOLINDEX, ASSETMAP, PKL, MXF extensions |
| Composition | §5 | CPL structure, UUID format, ContentKind, EditRate |
| Picture | §6 | J2K profile, resolution, bitrate |
| Audio | §7 | PCM format, sample rate, bit depth |
| Security | §8 | Encryption, KDM references |
| Presentation | §9 | Markers, duration |

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
- ISDCF test content (downloaded separately due to size)

CI creates the synthetic fixtures and runs packaging, composition, picture, and integrity checks on every push and pull request.

## Downloading ISDCF Test Content

```bash
./scripts/download_isdcf.sh
```

This downloads the SMPTE Bv2.1 test DCPs (~2GB) from ISDCF.
