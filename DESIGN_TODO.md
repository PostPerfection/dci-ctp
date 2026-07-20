# Planned

The README coverage table matches what the suite tests, and the script/doc bugs from the audit are fixed. dcpdoctor's core `validate` now enforces markers, cross-references, and encryption detection, so those have real negative fixtures and tests (`marker_missing`, `marker_invalid`, `cross_ref_broken`, `encryption_detected`, `kdm_required`).

Remaining work needs real fixtures or dcpdoctor support:

- Real (or minimal valid) J2K picture MXF fixture to enable §6 essence checks (profile/bitrate/resolution). Synthetic MXFs are zero-filled stubs. Note: `--deep-j2k` currently reports a false bitrate overrun on the generated fixture, so that path can't be used as-is.
- Negative audio fixture: a real MXF with a non-48/96 kHz sample rate to exercise `sound_invalid_sample_rate`.
- UUID-format checks aren't wired into `dcpdoctor validate` (the check in dcpdoctor's compliance module is still dead code). Wire it in dcpdoctor first, then add a fixture here.
- Reel continuity, stereo, and MCA-labeling checks are now wired but untested here; add fixtures if the coverage is worth it (stereo/continuity need multi-reel or 3D fixtures).
