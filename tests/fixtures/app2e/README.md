# App 2E picture descriptor fixtures

Five IMPs, one clean and one per App 2E picture-descriptor code. Each is a
directory holding ASSETMAP.xml, PKL.xml (real SHA-1 hashes and sizes), an App 2E
CPL.xml, a one-frame AS-02 picture track file and a one-frame AS-02 sound track
file.

| directory | `dcpdoctor validate --imf` reports |
| --- | --- |
| `clean` | no error and no warning |
| `cinema_profile` | `picture_not_imf_profile` |
| `colour_missing` | `picture_colour_missing` |
| `label_mismatch` | `picture_coding_label_mismatch` |
| `layout_mismatch` | `picture_pixel_layout_mismatch` |

Every package also draws two INFO notes, one for the CPL carrying no
EssenceDescriptorList and one for Photon not being installed.

Regenerate them from the dcpdoctor repository:

```
cargo run -p dcpdoctor-core --example write_app2e_fixtures -- <this directory>
```

The picture essence is the IMF 4K codestream from dcpdoctor's
`tests/fixtures/j2c`. `cinema_profile` declares Rsize 0x0003 on it rather than
using the repository's real cinema codestream, which is 64x64 and would fire
`picture_invalid_resolution` as well.
