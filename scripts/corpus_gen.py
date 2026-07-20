#!/usr/bin/env python3
"""Generate the dcpdoctor negative-test corpus.

Clones a real base DCP (built by build_corpus.sh via dcpwizard) once per error
code and applies a minimal mutation that triggers exactly that code, then
reseals the PKL hashes so no unrelated code fires. Writes a machine-readable
manifest a differential-testing agent can consume.

Each fixture keeps the base's real J2K/PCM MXFs (hardlinked, so essence checks
run for real), and only the XML is edited.
"""

import base64
import glob
import hashlib
import json
import os
import re
import shutil
import sys

CORPUS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "corpus"))
BASE = os.path.join(CORPUS, "valid", "dcp_ov")


def sha1_b64(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode()


def find(dirpath, pat):
    hits = glob.glob(os.path.join(dirpath, pat))
    if not hits:
        raise FileNotFoundError(f"{pat} in {dirpath}")
    return hits[0]


def clone(dest, copy_mxf=False):
    """Copy a fixture from BASE. MXFs are hardlinked unless copy_mxf (so a
    fixture that mutates essence gets its own writable copy)."""
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    for name in os.listdir(BASE):
        src = os.path.join(BASE, name)
        dst = os.path.join(dest, name)
        if name.endswith(".mxf") and not copy_mxf:
            os.link(src, dst)
        else:
            shutil.copy2(src, dst)
    return dest


def cpl_path(d):
    return find(d, "CPL_*.xml")


def pkl_path(d):
    return find(d, "PKL_*.xml")


def am_path(d):
    return os.path.join(d, "ASSETMAP.xml")


def read(p):
    with open(p) as f:
        return f.read()


def write(p, s):
    with open(p, "w") as f:
        f.write(s)


def assetmap_id_to_path(d):
    """Map urn-stripped asset id -> filesystem path from the ASSETMAP."""
    s = read(am_path(d))
    out = {}
    for m in re.finditer(r"<Asset>([\s\S]*?)</Asset>", s):
        block = m.group(1)
        idm = re.search(r"<Id>(urn:uuid:[^<]+)</Id>", block)
        pm = re.search(r"<Path>([^<]+)</Path>", block)
        if idm and pm:
            out[idm.group(1)] = pm.group(1).strip()
    return out


def reseal(d):
    """Recompute every PKL asset Hash+Size from the actual files so the only
    remaining defect is the intended one. Assets whose file is absent are left
    as-is (their hash check is skipped by dcpdoctor anyway)."""
    id2path = assetmap_id_to_path(d)
    pkl = pkl_path(d)
    s = read(pkl)

    def fix_asset(m):
        block = m.group(0)
        idm = re.search(r"<Id>(urn:uuid:[^<]+)</Id>", block)
        if not idm:
            return block
        rel = id2path.get(idm.group(1))
        if not rel:
            return block
        fp = os.path.join(d, rel)
        if not os.path.exists(fp):
            return block
        h = sha1_b64(fp)
        size = os.path.getsize(fp)
        block = re.sub(r"<Hash>[^<]*</Hash>", f"<Hash>{h}</Hash>", block)
        block = re.sub(r"<Size>[^<]*</Size>", f"<Size>{size}</Size>", block)
        return block

    s = re.sub(r"<Asset>[\s\S]*?</Asset>", fix_asset, s)
    write(pkl, s)


# ── mutation helpers ─────────────────────────────────────────────────────────

def pic_id(d):
    c = read(cpl_path(d))
    return re.search(r"<MainPicture>\s*<Id>(urn:uuid:[^<]+)</Id>", c).group(1)


SUB_ID = "urn:uuid:5b17e100-1111-2222-3333-444444444444"
DCST_NS = "http://www.smpte-ra.org/schemas/428-7/2010/DCST"


def add_subtitle(d, sub_xml):
    """Attach a MainSubtitle track (referencing sub.xml) to the first reel so
    dcpdoctor's subtitle validator runs on the given subtitle document."""
    write(os.path.join(d, "sub.xml"), sub_xml)
    p = cpl_path(d)
    block = ("        <MainSubtitle>\n"
             f"          <Id>{SUB_ID}</Id>\n"
             "          <EditRate>24 1</EditRate>\n"
             "          <IntrinsicDuration>48</IntrinsicDuration>\n"
             "          <Duration>48</Duration>\n"
             "        </MainSubtitle>\n")
    s = read(p).replace("        </MainSound>", "        </MainSound>\n" + block, 1)
    write(p, s)
    am = read(am_path(d))
    asset = ("    <Asset>\n"
             f"      <Id>{SUB_ID}</Id>\n"
             "      <ChunkList><Chunk><Path>sub.xml</Path></Chunk></ChunkList>\n"
             "    </Asset>\n")
    am = am.replace("  </AssetList>", asset + "  </AssetList>", 1)
    write(am_path(d), am)


def dcst(*, ns=True, sub_id=True, reel_number=True, language=True, load_font=True,
         time_in="00:00:01:00", time_out="00:00:02:00", broken=False):
    """Build a SMPTE DCST subtitle document, omitting parts to trigger a code."""
    xmlns = f' xmlns="{DCST_NS}"' if ns else ' xmlns="urn:example:not-dcst"'
    parts = [f'<?xml version="1.0" encoding="UTF-8"?>\n<SubtitleReel{xmlns}>']
    if sub_id:
        parts.append(f"  <Id>{SUB_ID}</Id>")
    if reel_number:
        parts.append("  <ReelNumber>1</ReelNumber>")
    if language:
        parts.append("  <Language>en</Language>")
    if load_font:
        parts.append('  <LoadFont ID="Arial">urn:uuid:0</LoadFont>')
    parts.append("  <SubtitleList>")
    if broken:
        parts.append(f'    <Subtitle SpotNumber="1" TimeIn="{time_in}" TimeOut="{time_out}"><Text>hi</Broken')
        return "\n".join(parts)
    parts.append(f'    <Subtitle SpotNumber="1" TimeIn="{time_in}" TimeOut="{time_out}"><Text>hi</Text></Subtitle>')
    parts.append("  </SubtitleList>\n</SubtitleReel>")
    return "\n".join(parts)


# ── fixtures ─────────────────────────────────────────────────────────────────
# each entry: (name, expected_codes, flags, baseline, notes, mutate_fn, reseal)
FIXTURES = []


def fixture(name, codes, flags, notes, reseal_after=True, copy_mxf=False,
            also=None, baseline="valid/dcp_ov", baseline_flags=None):
    def deco(fn):
        FIXTURES.append({
            "name": name, "codes": codes, "flags": flags, "notes": notes,
            "reseal": reseal_after, "copy_mxf": copy_mxf, "fn": fn,
            "also": also or [], "baseline": baseline,
            "baseline_flags": baseline_flags,
        })
        return fn
    return deco


@fixture("missing_assetmap", ["missing_assetmap"], [],
         "ASSETMAP removed", reseal_after=False)
def _(d):
    os.remove(am_path(d))


@fixture("xml_parse_error", ["xml_parse_error"], [],
         "ASSETMAP truncated mid-tag so XML parse fails", reseal_after=False)
def _(d):
    write(am_path(d), "<?xml version=\"1.0\"?><AssetMap><Id>urn:uuid:"
          "\n<broken")


@fixture("duplicate_asset_id", ["duplicate_asset_id"], [],
         "two ASSETMAP assets share one Id")
def _(d):
    s = read(am_path(d))
    # duplicate the sound asset block: its Id now appears twice, every CPL ref
    # still resolves so only DuplicateAssetId fires
    blocks = re.findall(r"[ \t]*<Asset>[\s\S]*?</Asset>\n", s)
    snd = next(b for b in blocks if ".mxf" in b and "sound" in b)
    s = s.replace(snd, snd + snd, 1)
    write(am_path(d), s)


@fixture("asset_not_found", ["asset_not_found"], [],
         "ASSETMAP points the picture chunk at a nonexistent file")
def _(d):
    s = read(am_path(d))
    s = re.sub(r"<Path>picture_[^<]+</Path>", "<Path>missing_picture.mxf</Path>", s)
    write(am_path(d), s)


@fixture("missing_pkl", ["missing_pkl"], [],
         "PKL file and its ASSETMAP entry removed", reseal_after=False)
def _(d):
    pk = os.path.basename(pkl_path(d))
    os.remove(pkl_path(d))
    s = read(am_path(d))
    # drop the Asset block that carries PackingList=true
    s = re.sub(r"<Asset>(?:(?!</Asset>)[\s\S])*?<PackingList>true</PackingList>[\s\S]*?</Asset>\s*",
               "", s)
    write(am_path(d), s)


@fixture("pkl_hash_mismatch", ["pkl_hash_mismatch"], [],
         "PKL Hash for the picture MXF corrupted", reseal_after=False)
def _(d):
    s = read(pkl_path(d))
    # corrupt the first mxf asset hash (picture)
    def bad(m):
        return m.group(0).replace(re.search(r"<Hash>([^<]+)</Hash>", m.group(0)).group(1),
                                  "AAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    # target the picture asset (application/mxf, first one)
    blocks = re.split(r"(?<=</Asset>)", s)
    for i, b in enumerate(blocks):
        if "application/mxf" in b:
            blocks[i] = bad(re.match(r"[\s\S]*", b))
            break
    write(pkl_path(d), "".join(blocks))


@fixture("pkl_missing_asset_reference", ["pkl_missing_asset_reference"], [],
         "PKL references an Id absent from the ASSETMAP", reseal_after=False)
def _(d):
    s = read(pkl_path(d))
    extra = ("    <Asset>\n"
             "      <Id>urn:uuid:deadbeef-0000-0000-0000-000000000000</Id>\n"
             "      <Hash>AAAAAAAAAAAAAAAAAAAAAAAAAAA=</Hash>\n"
             "      <Size>1</Size>\n"
             "      <Type>application/mxf</Type>\n"
             "    </Asset>\n")
    s = s.replace("  </AssetList>", extra + "  </AssetList>", 1)
    write(pkl_path(d), s)


@fixture("missing_cpl", ["missing_cpl"], [],
         "CPL file plus its ASSETMAP and PKL entries removed")
def _(d):
    cpl = os.path.basename(cpl_path(d))
    cid = re.search(r"<Id>(urn:uuid:[^<]+)</Id>", read(cpl_path(d))).group(1)
    os.remove(cpl_path(d))
    for p in (am_path(d), pkl_path(d)):
        s = read(p)
        s = re.sub(r"<Asset>(?:(?!</Asset>)[\s\S])*?" + re.escape(cid) + r"[\s\S]*?</Asset>\s*",
                   "", s)
        write(p, s)


@fixture("cpl_missing_reel", ["cpl_missing_reel"], [],
         "CPL ReelList emptied")
def _(d):
    p = cpl_path(d)
    s = re.sub(r"<ReelList>[\s\S]*</ReelList>", "<ReelList></ReelList>", read(p))
    write(p, s)


@fixture("cpl_invalid_content_kind", ["cpl_invalid_content_kind"], ["--strict"],
         "ContentKind set to a non-standard value (strict)")
def _(d):
    p = cpl_path(d)
    write(p, read(p).replace("<ContentKind>test</ContentKind>",
                             "<ContentKind>INVALID_KIND</ContentKind>"))


@fixture("cpl_invalid_duration", ["cpl_invalid_duration"], [],
         "MainPicture and MainSound Duration set to 0")
def _(d):
    p = cpl_path(d)
    write(p, read(p).replace("<Duration>48</Duration>", "<Duration>0</Duration>"))


@fixture("cpl_mismatched_durations", ["cpl_mismatched_durations"], [],
         "MainSound Duration differs from MainPicture Duration")
def _(d):
    p = cpl_path(d)
    s = read(p)
    # change only the sound reel's Duration (second occurrence)
    parts = s.split("<Duration>48</Duration>")
    s = parts[0] + "<Duration>48</Duration>" + "<Duration>24</Duration>".join(parts[1:])
    write(p, s)


@fixture("cpl_invalid_edit_rate", ["cpl_invalid_edit_rate"], ["--strict"],
         "MainPicture EditRate set to 13 1 (non-DCI, strict)")
def _(d):
    p = cpl_path(d)
    s = read(p)
    # first EditRate is the picture's
    s = s.replace("<EditRate>24 1</EditRate>", "<EditRate>13 1</EditRate>", 1)
    write(p, s)


@fixture("isdcf_naming_violation", ["isdcf_naming_violation"], [],
         "ContentTitleText replaced with a non-ISDCF name")
def _(d):
    p = cpl_path(d)
    s = re.sub(r"<ContentTitleText>[^<]*</ContentTitleText>",
               "<ContentTitleText>BadNameNoFields</ContentTitleText>", read(p))
    write(p, s)


@fixture("encrypted_no_kdm", ["encryption_detected", "kdm_required"], [],
         "KeyId added to MainPicture, no KDM present")
def _(d):
    p = cpl_path(d)
    s = read(p).replace("</MainPicture>",
                        "  <KeyId>urn:uuid:00000000-0000-0000-0000-0000000000bb</KeyId>\n"
                        "        </MainPicture>", 1)
    write(p, s)


@fixture("markers_bad", ["marker_missing", "marker_invalid"], ["--strict"],
         "MainMarkers present, required FFMC/LFMC absent, a marker lacks Offset")
def _(d):
    p = cpl_path(d)
    mm = ("        <MainMarkers>\n"
          "          <Id>urn:uuid:00000000-0000-0000-0000-0000000000aa</Id>\n"
          "          <EditRate>24 1</EditRate>\n"
          "          <IntrinsicDuration>48</IntrinsicDuration>\n"
          "          <MarkerList>\n"
          "            <Marker><Label>FFOC</Label></Marker>\n"
          "          </MarkerList>\n"
          "        </MainMarkers>\n")
    s = read(p).replace("        <MainSound>", mm + "        <MainSound>", 1)
    write(p, s)


@fixture("cross_ref_broken", ["cross_ref_broken"], [],
         "MainPicture Id points at an asset absent from ASSETMAP/PKL")
def _(d):
    p = cpl_path(d)
    s = re.sub(r"(<MainPicture>\s*<Id>)urn:uuid:[0-9a-fA-F-]+",
               r"\1urn:uuid:deadbeef-0000-0000-0000-000000000000", read(p), count=1)
    write(p, s)


@fixture("supplemental_opl", ["supplemental_opl_missing"], [],
         "CPL carries an OPL marker (supplemental/version file)")
def _(d):
    p = cpl_path(d)
    s = read(p).replace("</ReelList>",
                        "</ReelList>\n  <OPL>urn:uuid:00000000-0000-0000-0000-0000000000cc</OPL>", 1)
    write(p, s)


@fixture("supplemental_no_ov", ["supplemental_ov_not_provided"], [],
         "Supplemental CPL references an asset not in this package and no --ov given",
         also=["supplemental_opl_missing"])
def _(d):
    p = cpl_path(d)
    s = read(p)
    s = s.replace("</ReelList>",
                  "</ReelList>\n  <OPL>urn:uuid:00000000-0000-0000-0000-0000000000cc</OPL>", 1)
    s = re.sub(r"(<MainPicture>\s*<Id>)urn:uuid:[0-9a-fA-F-]+",
               r"\1urn:uuid:deadbeef-0000-0000-0000-000000000000", s, count=1)
    write(p, s)


@fixture("reel_discontinuity", ["reel_discontinuity"], [],
         "Second reel EntryPoint does not follow the first")
def _(d):
    p = cpl_path(d)
    s = read(p)
    reel = re.search(r"<Reel>[\s\S]*?</Reel>", s).group(0)
    reel2 = reel
    # new reel Id
    reel2 = re.sub(r"(<Reel>\s*<Id>)urn:uuid:[0-9a-fA-F-]+",
                   r"\1urn:uuid:00000000-0000-0000-0000-0000000000f2", reel2, count=1)
    # break continuity: picture EntryPoint 0 -> 100
    reel2 = reel2.replace("<EntryPoint>0</EntryPoint>", "<EntryPoint>100</EntryPoint>")
    s = s.replace("</ReelList>", reel2 + "\n  </ReelList>", 1)
    write(p, s)


@fixture("stereo_mismatch", ["stereo_mismatch"], [],
         "MainStereoscopicPicture has LeftEye but no RightEye")
def _(d):
    p = cpl_path(d)
    pid = pic_id(d)
    block = ("        <MainStereoscopicPicture>\n"
             f"          <Id>{pid}</Id>\n"
             "          <EditRate>24 1</EditRate>\n"
             "          <IntrinsicDuration>48</IntrinsicDuration>\n"
             "          <Duration>48</Duration>\n"
             f"          <LeftEye><Id>{pid}</Id></LeftEye>\n"
             "        </MainStereoscopicPicture>\n")
    s = read(p).replace("        <MainSound>", block + "        <MainSound>", 1)
    write(p, s)


@fixture("signature_invalid", ["signature_invalid"], [],
         "CPL carries an enveloped ds:Signature with a bogus SignatureValue")
def _(d):
    p = cpl_path(d)
    sig = ('<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">'
           '<ds:SignedInfo><ds:Reference URI="">'
           '<ds:DigestValue>AAAA</ds:DigestValue></ds:Reference></ds:SignedInfo>'
           '<ds:SignatureValue>bogus</ds:SignatureValue></ds:Signature>')
    s = read(p).replace("</CompositionPlaylist>", sig + "</CompositionPlaylist>", 1)
    write(p, s)


@fixture("certificate_chain_broken", ["certificate_chain_broken"], [],
         "ds:Signature embeds a certificate blob that is valid base64 but not DER",
         also=["signature_invalid"])
def _(d):
    p = cpl_path(d)
    badcert = base64.b64encode(b"this is not a certificate").decode()
    sig = ('<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">'
           '<ds:SignedInfo><ds:Reference URI="">'
           '<ds:DigestValue>AAAA</ds:DigestValue></ds:Reference></ds:SignedInfo>'
           '<ds:SignatureValue>bogus</ds:SignatureValue>'
           '<ds:KeyInfo><ds:X509Data><ds:X509Certificate>' + badcert +
           '</ds:X509Certificate></ds:X509Data></ds:KeyInfo></ds:Signature>')
    s = read(p).replace("</CompositionPlaylist>", sig + "</CompositionPlaylist>", 1)
    write(p, s)


@fixture("sound_no_mca", ["sound_invalid_channel_count"], [],
         "Sound reel without MCA labeling (base state); baseline dcp_mca has labels",
         baseline="valid/dcp_mca")
def _(d):
    pass  # base already lacks MCA labeling


@fixture("subtitle_parse_error", ["subtitle_parse_error"], [],
         "MainSubtitle references a malformed DCST XML document")
def _(d):
    add_subtitle(d, dcst(broken=True))


@fixture("subtitle_invalid_timing", ["subtitle_invalid_timing"], [],
         "Subtitle cue TimeIn is not before TimeOut")
def _(d):
    add_subtitle(d, dcst(time_in="00:00:02:00", time_out="00:00:01:00"))


@fixture("subtitle_font_missing", ["subtitle_font_missing"], [],
         "Subtitle reel has no LoadFont element")
def _(d):
    add_subtitle(d, dcst(load_font=False))


@fixture("subtitle_missing_id", ["missing_required_element"], [],
         "Subtitle reel is missing its SubtitleID/Id identifier")
def _(d):
    add_subtitle(d, dcst(sub_id=False))


@fixture("subtitle_wrong_namespace", ["smpte_namespace_wrong"], [],
         "SMPTE DCP's subtitle document does not use the SMPTE DCST namespace")
def _(d):
    add_subtitle(d, dcst(ns=False))


@fixture("mxf_unreadable", ["mxf_unreadable"], ["--check-mxf"],
         "Picture MXF truncated to non-MXF bytes; PKL resealed to it",
         copy_mxf=True)
def _(d):
    mxf = find(d, "picture_*.mxf")
    with open(mxf, "wb") as f:
        f.write(b"NOT AN MXF FILE" * 4)


@fixture("manifest_size_mismatch", ["mxf_hash_mismatch"], ["--manifest", "@refmanifest_bad.json"],
         "validate --manifest with a reference size that differs from the picture MXF",
         reseal_after=False, baseline="invalid/manifest_size_mismatch",
         baseline_flags=["--manifest", "@refmanifest_good.json"])
def _(d):
    mxf = os.path.basename(find(d, "picture_*.mxf"))
    real = os.path.getsize(os.path.join(d, mxf))
    write(os.path.join(d, "refmanifest_good.json"),
          json.dumps({"assets": [{"filename": mxf, "size": real}]}))
    write(os.path.join(d, "refmanifest_bad.json"),
          json.dumps({"assets": [{"filename": mxf, "size": real + 999}]}))


@fixture("bv21_pkl_no_xml_ext", ["smpte_naming_violation"], ["--bv21"],
         "PKL file renamed to drop .xml; BV2.1 flags the naming (--bv21 also adds "
         "advisory marker/element notes, not asserted)", reseal_after=False,
         also=["marker_missing", "missing_required_element", "sound_invalid_channel_count"])
def _(d):
    pkl = pkl_path(d)
    newname = os.path.basename(pkl)[:-4]  # strip .xml
    os.rename(pkl, os.path.join(d, newname))
    s = read(am_path(d))
    s = s.replace(os.path.basename(pkl), newname)
    write(am_path(d), s)


def build_mca_baseline():
    """Valid variant of the base with MCA labeling on the sound reel, to prove
    sound_invalid_channel_count is absent when labeling is present."""
    d = clone(os.path.join(CORPUS, "valid", "dcp_mca"))
    p = cpl_path(d)
    s = read(p).replace("</MainSound>",
                        "  <MCALabelDictionaryId>urn:smpte:ul:060e2b34.0401010d.03020201.00000000</MCALabelDictionaryId>\n"
                        "        </MainSound>", 1)
    write(p, s)
    reseal(d)


def main():
    if not os.path.isdir(BASE):
        print(f"ERROR: base DCP missing at {BASE}; run build_corpus.sh first", file=sys.stderr)
        sys.exit(1)

    build_mca_baseline()

    manifest = {"baselines": [], "fixtures": []}
    manifest["baselines"].append({
        "dir": "valid/dcp_ov", "package_type": "dcp", "is_valid_baseline": True,
        "flags": ["--strict", "--check-mxf"], "expected_codes": [],
        "notes": "real DCP built by dcpwizard; validates clean",
    })
    manifest["baselines"].append({
        "dir": "valid/dcp_mca", "package_type": "dcp", "is_valid_baseline": True,
        "flags": ["--strict", "--check-mxf"], "expected_codes": [],
        "notes": "base with MCA channel labeling added",
    })

    inv = os.path.join(CORPUS, "invalid")
    if os.path.exists(inv):
        shutil.rmtree(inv)

    for f in FIXTURES:
        d = clone(os.path.join(inv, f["name"]), copy_mxf=f["copy_mxf"])
        f["fn"](d)
        if f["reseal"]:
            reseal(d)
        manifest["fixtures"].append({
            "dir": f"invalid/{f['name']}",
            "package_type": "dcp",
            "is_valid_baseline": False,
            "expected_codes": f["codes"],
            "also_emits": f["also"],
            "flags": f["flags"],
            "baseline": f["baseline"],
            "baseline_flags": f["baseline_flags"],
            "notes": f["notes"],
        })
        print(f"  built invalid/{f['name']} -> {', '.join(f['codes'])}")

    with open(os.path.join(CORPUS, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nmanifest: {os.path.join(CORPUS, 'manifest.json')}")
    print(f"fixtures: {len(FIXTURES)}")


if __name__ == "__main__":
    main()
