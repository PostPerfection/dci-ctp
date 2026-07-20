# Planned

The per-error-code corpus (`scripts/build_corpus.sh` + `run_corpus.py`) now proves
37 of 49 dcpdoctor codes fire through `dcpdoctor validate`, each non-vacuously
(the code is asserted absent on the valid baseline). Reel continuity, stereo,
MCA labeling, supplemental, signature, subtitle, and manifest-compare paths all
have real fixtures now. ClairMeta reference packages are recorded in the manifest
for differential testing.

## Findings to act on in dcpdoctor (from differential vs ClairMeta)

- Signature verification is SHA-256-only. `postkit::xmldsig::verify_enveloped` /
  `verify_document_enveloped` hardcode `sha256` + RSA-SHA256 and ignore the
  document's `DigestMethod`/`SignatureMethod`. Every `xmldsig#sha1`-signed DCP
  (all 25 ClairMeta ECL packages) gets a false `signature_invalid`. Read the
  declared algorithm and dispatch SHA-1 vs SHA-256.
- No XSD schema validation in `validate`. Both dcpwizard baselines are schema-
  invalid (ASSETMAP missing `IssueDate`; CPL `ContentTitleText` before
  `IssueDate`) and dcpdoctor passes them; ClairMeta rejects them. Wire schema
  validation (the `xml_schema_violation` code exists but only fires via Photon /
  schema-validate). Note: dcpwizard itself emits out-of-order elements, fix there
  too.
- VF handling: dcpdoctor errors `cross_ref_broken` on a VF's external OV
  references (ECL02/ECL10); ClairMeta treats external refs as WARNING. Decide
  whether an OV-less VF should hard-fail.
- Interop required elements: `missing_required_element` fires on Interop encrypted
  CPLs (ECL29/ECL33) that ClairMeta accepts; check SMPTE-only rules aren't applied
  to Interop.
- Coverage gaps with no dcpdoctor equivalent: deep certificate-rule checks
  (basic constraints, key usage, extensions, RSA validity, DCI role, org name,
  thumbprint), sound blockalign/quantization, CPL label/metadata schema, foreign/
  empty-file hygiene.

## Findings to act on in dcpdoctor

- `kdm_not_yet_valid` is DEAD CODE: the `Code` enum defines it but nothing emits
  it (the KDM validator uses `kdm_required` for both not-yet-valid and expired).
  Either wire it or remove the variant.
- `invalid_uuid` (compliance.rs) is still unreachable from `validate`; wire the
  compliance UUID check into the validate path, then add an isolated fixture.

## Remaining coverage gaps (need real essence or another subcommand)

- `picture_invalid_resolution`, `sound_invalid_sample_rate`,
  `j2k_invalid_profile`, `j2k_invalid_component_count`: need a real MXF/J2K
  codestream with the specific defect. dcpwizard emits DCI-conformant essence, so
  produce these by re-wrapping a non-DCI source or hand-building an MXF descriptor.
- `picture_invalid_frame_rate`: IMF-only; build an IMP (imfwizard) whose picture
  frame rate disagrees with the CPL edit rate.
- `mxf_invalid_structure`, `sound_clipping`, `sound_silent`, `kdm_expired`,
  `xml_schema_violation`: only reachable via non-validate subcommands
  (studio/mxf_advanced, auto-qc/loudness, kdm, schema-validate). Cover them under
  those commands or wire them into validate first.
