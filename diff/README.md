# Differential validation: dcpdoctor vs ClairMeta

Runs both validators over the shared corpus (`../corpus/manifest.json`: baselines,
negative fixtures, ClairMeta ECL reference packages) and classifies every package
into BOTH_PASS / BOTH_FAIL / DCPDOCTOR_ONLY_FAIL / CLAIRMETA_ONLY_FAIL / TOOL_ERROR.
Writes `report.json` and `report.md`.

```bash
DCPDOCTOR=../../dcpdoctor/rust/target/release/dcpdoctor \
CLAIRMETA_DATA=../../dci-ctp-work/ClairMeta_Data \
uv run differential.py
```

Env:
- `DCPDOCTOR`: dcpdoctor release binary (defaults to the sibling checkout path).
- `CLAIRMETA_DATA`: ClairMeta_Data clone root. If absent, the ECL reference packages
  are skipped and only baselines + fixtures run.

## Notes

- ClairMeta 1.6.2 via uv. Its MXF-essence checks need `asdcp-info` (asdcplib); when
  that binary is absent they bypass, so this compares XML/structure/signature/cert
  checks only. Install asdcplib to also diff essence-level checks.
- Photon is never invoked: the one IMF IMP in the corpus lands as TOOL_ERROR under
  ClairMeta, which is a DCP validator.
- The negative fixtures all derive from one dcpwizard base that ClairMeta already
  rejects on schema grounds, so the harness attributes ClairMeta's catch of each
  injected defect by diffing the fixture's failed-check set against its baseline's,
  not by the raw verdict.
