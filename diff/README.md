# Differential validation: dcpdoctor vs ClairMeta

Runs both validators over the shared corpus (`../corpus/manifest.json`: baselines,
negative fixtures, ClairMeta ECL reference packages) and classifies every package
into BOTH_PASS / BOTH_FAIL / DCPDOCTOR_ONLY_FAIL / CLAIRMETA_ONLY_FAIL / TOOL_ERROR.
Writes `report.json` and `report.md`.

```bash
PATH="/tmp/ctp-corpus-src/asdcplib-build/src:$HOME/.pixi/bin:$PATH" \
DCPDOCTOR=../../dcpdoctor/rust/target/release/dcpdoctor \
CLAIRMETA_DATA=../../dci-ctp-work/ClairMeta_Data \
uv run differential.py
```

ClairMeta finds `asdcp-info`, `asdcp-unwrap` and `sox` on PATH (`shutil.which`), and
without them its MXF-essence checks bypass silently. Build the asdcplib tools out of
tree (never into the dcpwizard checkout) and install sox per user:

```bash
cmake -S ~/src/PostPerfection/dcpwizard/extern/asdcplib -B /tmp/ctp-corpus-src/asdcplib-build
cmake --build /tmp/ctp-corpus-src/asdcplib-build --target asdcp-info asdcp-unwrap -j"$(nproc)"
pixi global install sox
```

Env:
- `DCPDOCTOR`: dcpdoctor release binary (defaults to the sibling checkout path).
- `CLAIRMETA_DATA`: ClairMeta_Data clone root. If absent, the ECL reference packages
  are skipped and only baselines + fixtures run.

## Notes

- ClairMeta 1.6.2 via uv. With the three tools above on PATH its MXF-essence checks
  run, so the comparison covers essence (probe metadata, bitrate, sound stats) as
  well as XML/structure/signature/cert. `mediainfo` is a fourth ClairMeta dependency
  but only `probe_mediainfo` uses it, which no DCP check calls, so its absence
  changes nothing here.
- Reference packages go through dcpdoctor with `--check-mxf`, recorded as the `flags`
  key on each manifest entry, so both tools read their essence.
- Photon is never invoked: the one IMF IMP in the corpus lands as TOOL_ERROR under
  ClairMeta, which is a DCP validator.
- The negative fixtures all derive from one dcpwizard base that ClairMeta already
  rejects on schema grounds, so the harness attributes ClairMeta's catch of each
  injected defect by diffing the fixture's failed-check set against its baseline's,
  not by the raw verdict.
