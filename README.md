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
├── run_tests.sh          # run the suite, creates synthetic fixtures if missing
├── create_synthetic.sh   # write the synthetic fixtures
├── generate.sh           # generate DCPs from source material via dcpwizard
└── download_isdcf.sh     # fetch the ISDCF reference content (~2GB)
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
- `dcpwizard` binary and `ffmpeg`, for the generated fixtures
- ISDCF test content (downloaded separately due to size)

CI builds both binaries, creates the synthetic and generated fixtures, and runs packaging, composition, picture, integrity, audio, and security on every push and pull request. The isdcf category is not run in CI, the content is a 2GB download.

## Downloading ISDCF Test Content

```bash
./scripts/download_isdcf.sh
```

This downloads the SMPTE Bv2.1 test DCPs (~2GB) from ISDCF.
