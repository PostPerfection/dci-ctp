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
import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import struct
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CORPUS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "corpus"))
BASE = os.path.join(CORPUS, "valid", "dcp_ov")
MONO = os.path.join(CORPUS, ".mono_src")  # unlabeled-sound source (built by build_corpus.sh)
THREE_D = os.path.join(CORPUS, "valid", "dcp_3d")
ATMOS = os.path.join(CORPUS, "valid", "dcp_atmos")
ENC_SRC = os.path.join(CORPUS, ".enc_src")  # encrypted (unsigned) DCP, built by build_corpus.sh
BITRATE_SRC = os.path.join(CORPUS, ".bitrate_src")  # 3D at full 2K bandwidth, over the DCI peak
IMF_SRC = os.path.join(CORPUS, "valid", "imf_ov")  # IMF IMP, built by build_corpus.sh
NONDCI_MXF = os.path.join(CORPUS, ".nondci", "nondci_res.mxf")  # non-DCI J2K wrapped by asdcp-wrap
# the second mastering tool in the corpus. Every fixture derived from BASE
# resolves its targets by content, so the same mutation applies to these too.
DOM_BASE = os.path.join(CORPUS, "valid", "dcp_dom_ov")
DOM_INTEROP = os.path.join(CORPUS, "valid", "dcp_dom_interop")

# an enveloped ds:Signature is enough for check_dcp_signed (presence-only); its
# value need not verify. dcpwizard emits unsigned encrypted packages, so the
# signed baseline is synthesised by injecting this.
FAKE_SIG = (
    '<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">'
    '<ds:SignedInfo><ds:Reference URI="">'
    "<ds:DigestValue>AAAA</ds:DigestValue></ds:Reference></ds:SignedInfo>"
    "<ds:SignatureValue>AA==</ds:SignatureValue></ds:Signature>"
)


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


def clone(dest, copy_mxf=False, src=BASE):
    """Copy a fixture from `src` (default BASE). MXFs are hardlinked unless
    copy_mxf (so a fixture that mutates essence gets its own writable copy)."""
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    for name in os.listdir(src):
        s = os.path.join(src, name)
        dst = os.path.join(dest, name)
        if name.endswith(".mxf") and not copy_mxf:
            os.link(s, dst)
        else:
            shutil.copy2(s, dst)
    return dest


def track_file(d, element):
    """The essence file a CPL track element points at, resolved id -> path
    through the ASSETMAP. A track file's name is the mastering tool's business,
    so resolving by id is the only way this works across vendors."""
    asset_id = cpl_asset_id(d, element)
    relative = assetmap_id_to_path(d).get(f"urn:uuid:{asset_id}")
    if not relative:
        raise FileNotFoundError(f"{element} asset {asset_id} not in the ASSETMAP of {d}")
    return os.path.join(d, relative)


def sound_mxf(d):
    return track_file(d, "MainSound")


def picture_mxf(d):
    """The picture track file, stereoscopic or not."""
    for element in ("MainPicture", "MainStereoscopicPicture"):
        try:
            return track_file(d, element)
        except (AttributeError, FileNotFoundError):
            continue
    raise FileNotFoundError(f"no picture track file in {d}")


def patch_bytes(path, old_hex, new_hex, count=1):
    """Byte-patch a copied essence file: replace old_hex with new_hex (equal
    length). Fails loudly if the expected pattern is not present exactly once."""
    old, new = bytes.fromhex(old_hex), bytes.fromhex(new_hex)
    assert len(old) == len(new), "patch length mismatch"
    data = open(path, "rb").read()
    n = data.count(old)
    assert n == count, f"expected {count} occurrence(s) of {old_hex} in {path}, found {n}"
    open(path, "wb").write(data.replace(old, new, count))


def local_tag_value(path, tag_hex, length):
    """Current value of an MXF local tag, as an int. The tag must appear exactly
    once so a coincidental byte match cannot pass silently."""
    header = bytes.fromhex(tag_hex + f"{length:04x}")
    data = open(path, "rb").read()
    n = data.count(header)
    assert n == 1, f"expected 1 occurrence of local tag {tag_hex} in {path}, found {n}"
    start = data.index(header) + len(header)
    return int.from_bytes(data[start:start + length], "big")


def patch_local_tag(path, tag_hex, length, new_value):
    """Set an MXF local tag, reading the current value rather than assuming it,
    so a fixture holds against essence from any mastering tool."""
    old = local_tag_value(path, tag_hex, length)
    width = length * 2
    patch_bytes(
        path,
        tag_hex + f"{length:04x}" + f"{old:0{width}x}",
        tag_hex + f"{length:04x}" + f"{new_value:0{width}x}",
    )
    return old


def break_mxf_footer(path):
    """Corrupt the SMPTE 377-1 footer partition pack key in an MXF's tail so
    check_mxf_partitions reports a missing footer partition. The header partition
    is left intact, so read_mxf_info still succeeds (no mxf_unreadable)."""
    # 13-byte partition pack key prefix (mxf_advanced.rs PARTITION_PACK_KEY)
    key = bytes.fromhex("060e2b34020501010d01020101")
    data = bytearray(open(path, "rb").read())
    scan_start = max(0, len(data) - 65536)  # dcpdoctor only scans the last 64 KiB
    target = None
    i = scan_start
    while (j := data.find(key, i)) >= 0:
        if data[j + 13] == 0x04:  # 0x04 = footer partition
            target = j
        i = j + 1
    assert target is not None, f"no footer partition in tail of {path}"
    data[target + 13] = 0x00
    open(path, "wb").write(data)


def replace_picture_mxf(d, src_mxf):
    """Swap the picture MXF in a fixture for another (non-DCI) essence, keeping
    the original filename so the ASSETMAP/PKL still resolve. reseal fixes hashes."""
    shutil.copy(src_mxf, picture_mxf(d))


def patch_j2k_component_count(mxf_path):
    """Byte-patch the first frame's SIZ Csiz field 3 -> 4 so the codestream
    declares 4 components. asdcp-wrap refuses to wrap a 4-component essence, so
    the count is patched into a valid 3-component wrap instead."""
    data = bytearray(open(mxf_path, "rb").read())
    soc = data.find(bytes.fromhex("FF51"))  # SIZ marker
    assert soc >= 0, f"no SIZ marker in {mxf_path}"
    # SIZ layout: FF51 Lsiz(2) Rsiz(2) then 8x 4-byte size params, then Csiz(2)
    csiz = soc + 2 + 2 + 2 + 4 * 8
    assert data[csiz:csiz + 2] == b"\x00\x03", "first SIZ is not 3-component"
    data[csiz:csiz + 2] = b"\x00\x04"
    open(mxf_path, "wb").write(data)


def first_codestream(mxf_path):
    """(bytes, start, end) of the first J2K codestream in a picture MXF, located
    by the SOC+SIZ marker pair and bounded by the next frame's."""
    data = bytearray(open(mxf_path, "rb").read())
    soc = data.find(b"\xff\x4f\xff\x51")
    assert soc >= 0, f"no J2K codestream in {mxf_path}"
    nxt = data.find(b"\xff\x4f\xff\x51", soc + 4)
    return data, soc, nxt if nxt >= 0 else len(data)


def main_header_marker(data, start, end, marker):
    """Offset of a marker segment in a codestream's main header, walking the
    segments from SOC and stopping at the first SOT/SOD."""
    pos = start + 2
    while pos + 4 <= end:
        m = bytes(data[pos:pos + 2])
        if m in (b"\xff\x90", b"\xff\x93"):
            return None
        if m == marker:
            return pos
        seg = int.from_bytes(data[pos + 2:pos + 4], "big")
        if seg < 2:
            return None
        pos += 2 + seg
    return None


def patch_j2k_guard_bits(mxf_path):
    """Zero the guard-bit field of the first frame's QCD marker. RDD 52 requires
    1 guard bit at 2K, so 0 is a violation check_guard_bits_mxf reports."""
    data, s, e = first_codestream(mxf_path)
    qcd = main_header_marker(data, s, e, b"\xff\x5c")
    assert qcd is not None, f"no QCD marker in the first frame of {mxf_path}"
    sqcd = qcd + 4  # FF5C, Lqcd(2), then Sqcd
    assert data[sqcd] >> 5 == 1, f"first frame does not declare 1 guard bit ({data[sqcd] >> 5})"
    data[sqcd] &= 0x1F
    open(mxf_path, "wb").write(data)


def patch_j2k_legacy_ffff(mxf_path):
    """Write 0xFF 0xFF into the first frame's entropy data at a byte position
    that is 254 mod 256 from the codestream start, the SMPTE Cat. 862 legacy
    decoder condition. Positions inside the first tile-part need no realignment,
    so the patch site is picked just past its SOD."""
    data, s, e = first_codestream(mxf_path)
    # walk to the first SOT, then on to its SOD
    pos = s + 2
    while bytes(data[pos:pos + 2]) != b"\xff\x90":
        pos += 2 + int.from_bytes(data[pos + 2:pos + 4], "big")
        assert pos < e, f"no SOT in the first frame of {mxf_path}"
    psot = int.from_bytes(data[pos + 6:pos + 10], "big")
    limit = s + psot if psot else e
    while bytes(data[pos:pos + 2]) != b"\xff\x93":
        pos += 2 + int.from_bytes(data[pos + 2:pos + 4], "big")
        assert pos < e, f"no SOD in the first frame of {mxf_path}"
    p = pos + 2 - s + 64  # 64 bytes into the entropy data
    p += (254 - p) % 256
    assert s + p + 2 < limit, f"no room in the first tile-part of {mxf_path}"
    data[s + p] = 0xFF
    data[s + p + 1] = 0xFF
    open(mxf_path, "wb").write(data)


def find_by_root_element(d, root):
    """The XML in `d` whose root element has this local name. Identifying a
    document by its content rather than its filename is what lets a corpus hold
    packages from more than one mastering tool: dcpwizard writes CPL_*.xml and
    PKL_*.xml, DCP-o-matic writes cpl_*.xml and pkl_*.xml."""
    for name in sorted(os.listdir(d)):
        path = os.path.join(d, name)
        if not os.path.isfile(path) or not name.lower().endswith(".xml"):
            continue
        with open(path, errors="replace") as f:
            head = f.read(4096)
        # the first element that is not a declaration, comment or doctype. An
        # ASSETMAP carries a <PackingList> flag per asset, so anything looser
        # than the root element matches the wrong document.
        opening = re.search(r"<\s*(?![?!])(?:[\w.-]+:)?([\w.-]+)", head)
        if opening and opening.group(1) == root:
            return path
    raise FileNotFoundError(f"no {root} document in {d}")


def cpl_path(d):
    return find_by_root_element(d, "CompositionPlaylist")


def pkl_path(d):
    return find_by_root_element(d, "PackingList")


def am_path(d):
    # ST 429-9 allows either name, and Interop packages use the bare one
    for name in ("ASSETMAP.xml", "ASSETMAP"):
        path = os.path.join(d, name)
        if os.path.isfile(path):
            return path
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
    """Recompute every PKL asset Hash+Size, then every ASSETMAP chunk Length,
    from the actual files so the only remaining defect is the intended one.
    Assets whose file is absent are left as-is (their hash check is skipped by
    dcpdoctor anyway)."""
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

    # DCP-o-matic writes a Length per chunk where dcpwizard writes none, so a
    # mutation that changes an XML file's size leaves a second defect behind.
    # Runs after the PKL rewrite so the PKL's own chunk gets its new size.
    def fix_chunk(m):
        block = m.group(0)
        rel = re.search(r"<Path>([^<]+)</Path>", block)
        if not rel or "<Length>" not in block:
            return block
        fp = os.path.join(d, rel.group(1))
        if not os.path.exists(fp):
            return block
        return re.sub(r"<Length>[^<]*</Length>",
                      f"<Length>{os.path.getsize(fp)}</Length>", block)

    am = am_path(d)
    write(am, re.sub(r"<Chunk>[\s\S]*?</Chunk>", fix_chunk, read(am)))


def assetmap_length_disagrees(d):
    """True when a chunk declares a Length the file on disk does not have.
    A mutation that resizes an XML file leaves this behind wherever Length is
    declared at all, which is DCP-o-matic's packages and not dcpwizard's, so the
    same mutation emits an extra code on one vendor and not the other."""
    am = am_path(d)
    if not os.path.isfile(am):
        return False
    for m in re.finditer(r"<Chunk>[\s\S]*?</Chunk>", read(am)):
        block = m.group(0)
        length = re.search(r"<Length>\s*(\d+)\s*</Length>", block)
        rel = re.search(r"<Path>([^<]+)</Path>", block)
        if not length or not rel:
            continue
        fp = os.path.join(d, rel.group(1).strip())
        if os.path.exists(fp) and os.path.getsize(fp) != int(length.group(1)):
            return True
    return False


# ── mutation helpers ─────────────────────────────────────────────────────────

def cpl_asset_id(d, element):
    """Bare asset id of a CPL track element. Tolerates a namespace prefix and
    any element order inside the asset, which vendors do differ on."""
    body = re.search(
        rf"<(?:[\w.-]+:)?{element}\b[^>]*>([\s\S]*?)</(?:[\w.-]+:)?{element}>",
        read(cpl_path(d)),
    ).group(1)
    return re.search(r"<(?:[\w.-]+:)?Id>\s*urn:uuid:([0-9a-fA-F-]{36})", body).group(1).lower()


def pic_id(d):
    return f"urn:uuid:{cpl_asset_id(d, 'MainPicture')}"


SUB_ID = "urn:uuid:5b17e100-1111-2222-3333-444444444444"
# the id the mxf_asset_id_mismatch fixture puts in place of the picture asset id
REWRITTEN_PICTURE_ID = "5b17e100-1111-2222-3333-444444444477"
CCAP_ID = "urn:uuid:5b17e100-1111-2222-3333-444444444455"
FONT_ID = "urn:uuid:5b17e100-1111-2222-3333-444444444466"
DCST_NS = "http://www.smpte-ra.org/schemas/428-7/2010/DCST"
# the caption track element carries the digicine CC-CPL namespace, as a real
# Bv2.1 package does. The 429-7 AssetList ends in xs:any namespace="##other"
# processContents="lax", so a foreign-namespace element is schema-clean there,
# and dcpdoctor picks the schema from the root element namespace, so the CPL
# still validates as SMPTE.
CC_NS = "http://www.digicine.com/PROTO-ASDCP-CC-CPL-20070926#"


def add_timed_text(d, xml, *, element="MainSubtitle", asset_id=SUB_ID,
                   filename="sub.xml", ns_decl=""):
    """Attach a timed-text track (referencing `filename`) to the first reel and
    register the file in the ASSETMAP, so dcpdoctor's subtitle/closed-caption
    checks run on the given document."""
    write(os.path.join(d, filename), xml)
    p = cpl_path(d)
    block = (f"        <{element}{ns_decl}>\n"
             f"          <Id>{asset_id}</Id>\n"
             "          <EditRate>24 1</EditRate>\n"
             "          <IntrinsicDuration>48</IntrinsicDuration>\n"
             "          <Duration>48</Duration>\n"
             f"        </{element}>\n")
    s = read(p).replace("        </MainSound>", "        </MainSound>\n" + block, 1)
    write(p, s)
    add_assetmap_entry(d, asset_id, filename)


def add_assetmap_entry(d, asset_id, filename):
    am = read(am_path(d))
    asset = ("    <Asset>\n"
             f"      <Id>{asset_id}</Id>\n"
             f"      <ChunkList><Chunk><Path>{filename}</Path></Chunk></ChunkList>\n"
             "    </Asset>\n")
    write(am_path(d), am.replace("  </AssetList>", asset + "  </AssetList>", 1))


def add_subtitle(d, sub_xml):
    add_timed_text(d, sub_xml)


def add_closed_caption(d, ccap_xml):
    """Attach a ClosedCaption track so check_timed_text_content runs the
    closed-caption limits (stricter than the subtitle ones) on the document."""
    add_timed_text(d, ccap_xml, element="cc:ClosedCaption", asset_id=CCAP_ID,
                   filename="ccap.xml", ns_decl=f' xmlns:cc="{CC_NS}"')


def dcst(*, ns=True, sub_id=True, reel_number=True, language=True, load_font=True,
         time_in="00:00:01:00", time_out="00:00:02:00", broken=False,
         lines=("hi",), time_code_rate=None):
    """Build a SMPTE DCST timed-text document, omitting or overriding parts to
    trigger a code. Each entry in `lines` becomes one <Text> element, which is
    how dcpdoctor counts displayed lines."""
    xmlns = f' xmlns="{DCST_NS}"' if ns else ' xmlns="urn:example:not-dcst"'
    parts = [f'<?xml version="1.0" encoding="UTF-8"?>\n<SubtitleReel{xmlns}>']
    if sub_id:
        parts.append(f"  <Id>{SUB_ID}</Id>")
    if reel_number:
        parts.append("  <ReelNumber>1</ReelNumber>")
    if language:
        parts.append("  <Language>en</Language>")
    if time_code_rate is not None:
        parts.append(f"  <TimeCodeRate>{time_code_rate}</TimeCodeRate>")
    if load_font:
        parts.append(f'  <LoadFont ID="Arial">{FONT_ID}</LoadFont>')
    parts.append("  <SubtitleList>")
    if broken:
        parts.append(f'    <Subtitle SpotNumber="1" TimeIn="{time_in}" TimeOut="{time_out}"><Text>hi</Broken')
        return "\n".join(parts)
    text = "".join(f"<Text>{line}</Text>" for line in lines)
    parts.append(f'    <Subtitle SpotNumber="1" TimeIn="{time_in}" TimeOut="{time_out}">{text}</Subtitle>')
    parts.append("  </SubtitleList>\n</SubtitleReel>")
    return "\n".join(parts)


def dcsubtitle(*, lines=("hi",), font_uri="font.ttf",
               time_in="00:00:01:00", time_out="00:00:02:00"):
    """Build an Interop DCSubtitle document. Interop references its font by URI
    rather than by asset urn, which is how the glyph check resolves one."""
    text = "".join(f"<Text>{line}</Text>" for line in lines)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<DCSubtitle Version="1.0" xmlns="http://www.digicine.com/PROTO-ASDCP-TT-DEF" '
            f'SubtitleID="{SUB_ID}">\n'
            "  <ReelNumber>1</ReelNumber>\n"
            "  <Language>en</Language>\n"
            f'  <LoadFont Id="Arial" URI="{font_uri}"/>\n'
            '  <Font Id="Arial">\n'
            f'    <Subtitle SpotNumber="1" TimeIn="{time_in}" TimeOut="{time_out}">{text}</Subtitle>\n'
            "  </Font>\n</DCSubtitle>\n")


def make_font(chars):
    """Minimal sfnt carrying nothing but a format-12 cmap that maps `chars` to
    sequential glyph ids. Every other code point resolves to glyph 0, which is
    what dcpdoctor's glyph-coverage check reports as missing."""
    n = len(chars)
    sub = struct.pack(">HHIII", 12, 0, 16 + 12 * n, 0, n)
    for i, c in enumerate(chars):
        sub += struct.pack(">III", ord(c), ord(c), i + 1)
    cmap = struct.pack(">HHHHI", 0, 1, 3, 10, 12) + sub
    header = struct.pack(">IHHHH", 0x00010000, 1, 16, 0, 0)
    return header + b"cmap" + struct.pack(">III", 0, 28, len(cmap)) + cmap


# ── certificate chains ───────────────────────────────────────────────────────
# ST 430-2 profile chains built with `cryptography`: a self-signed root, an
# intermediate, and a signer leaf, all sha256WithRSA / 2048-bit / e=65537, all
# sharing one Organization, each carrying its public-key thumbprint as
# dnQualifier. cert_rules.rs decides a cert's role by whether it issues another
# cert in the chain, so the leaf is the only cert that carries a defect.

CERT_ORGANIZATION = ".dci-ctp.corpus"
CERT_ORGANIZATIONAL_UNIT = ".Signature.dci-ctp.corpus"
CERT_NOT_BEFORE = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
CERT_NOT_AFTER = datetime.datetime(2040, 1, 1, tzinfo=datetime.timezone.utc)
ROOT_COMMON_NAME = ".dci-ctp.root"
INTERMEDIATE_COMMON_NAME = ".dci-ctp.intermediate"
# a 430-2 CommonName carries its role token before the first '.', so the CA CNs
# start with '.' (empty role) and only the signer leaf spells a role out
LEAF_COMMON_NAME = "CS.Signature.dci-ctp.corpus"
CA_KEY_USAGE = {"key_cert_sign": True, "crl_sign": True}
LEAF_KEY_USAGE = {"digital_signature": True, "key_encipherment": True}
KEY_USAGE_BITS = ("digital_signature", "content_commitment", "key_encipherment",
                  "data_encipherment", "key_agreement", "key_cert_sign", "crl_sign",
                  "encipher_only", "decipher_only")
# well-formed base64 of a 20-byte value that is not any key's SHA-1
WRONG_THUMBPRINT = "MTIzNDU2Nzg5MDEyMzQ1Njc4OTA="


def public_key_thumbprint(public_key):
    """Base64(SHA-1(subjectPublicKey BIT STRING payload)), the 430-2 dnQualifier.
    For an RSA key the BIT STRING payload is the DER of RSAPublicKey."""
    der = public_key.public_bytes(serialization.Encoding.DER,
                                  serialization.PublicFormat.PKCS1)
    return base64.b64encode(hashlib.sha1(der).digest()).decode()


def certificate_name(common_name, organization, dn_qualifier):
    return x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, CERT_ORGANIZATIONAL_UNIT),
        x509.NameAttribute(NameOID.DN_QUALIFIER, dn_qualifier),
    ])


def issue_certificate(subject, key, issuer, issuer_key, serial, basic_constraints_ca,
                      key_usage):
    bits = {name: key_usage.get(name, False) for name in KEY_USAGE_BITS}
    return (x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(serial)
            .not_valid_before(CERT_NOT_BEFORE)
            .not_valid_after(CERT_NOT_AFTER)
            .add_extension(x509.BasicConstraints(ca=basic_constraints_ca, path_length=None),
                           critical=True)
            .add_extension(x509.KeyUsage(**bits), critical=True)
            .sign(issuer_key, hashes.SHA256())
            .public_bytes(serialization.Encoding.DER))


CERTIFICATE_AUTHORITIES = []


def certificate_authorities():
    """(root der, intermediate der, intermediate name, intermediate key), built
    once per run and shared by the clean baseline and every cert fixture."""
    if not CERTIFICATE_AUTHORITIES:
        root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        intermediate_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        root_name = certificate_name(ROOT_COMMON_NAME, CERT_ORGANIZATION,
                                     public_key_thumbprint(root_key.public_key()))
        intermediate_name = certificate_name(
            INTERMEDIATE_COMMON_NAME, CERT_ORGANIZATION,
            public_key_thumbprint(intermediate_key.public_key()))
        root = issue_certificate(root_name, root_key, root_name, root_key, 1, True,
                                 CA_KEY_USAGE)
        intermediate = issue_certificate(intermediate_name, intermediate_key, root_name,
                                         root_key, 2, True, CA_KEY_USAGE)
        CERTIFICATE_AUTHORITIES.extend([root, intermediate, intermediate_name,
                                        intermediate_key])
    return CERTIFICATE_AUTHORITIES


def certificate_chain(*, leaf_key_size=2048, leaf_common_name=LEAF_COMMON_NAME,
                      leaf_organization=CERT_ORGANIZATION,
                      leaf_basic_constraints_ca=False, leaf_key_usage=LEAF_KEY_USAGE,
                      leaf_dn_qualifier=None):
    """DER chain (leaf, intermediate, root). Every argument left at its default
    yields a chain that satisfies every rule in cert_rules.rs."""
    root, intermediate, intermediate_name, intermediate_key = certificate_authorities()
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=leaf_key_size)
    leaf_name = certificate_name(
        leaf_common_name, leaf_organization,
        leaf_dn_qualifier or public_key_thumbprint(leaf_key.public_key()))
    leaf = issue_certificate(leaf_name, leaf_key, intermediate_name, intermediate_key, 3,
                             leaf_basic_constraints_ca, leaf_key_usage)
    return [leaf, intermediate, root]


def signature_with_chain(ders):
    """An enveloped ds:Signature carrying `ders` in ds:KeyInfo. The SignedInfo is
    complete enough for the 429-16 schema, but the digest and SignatureValue are
    placeholders, so signature_invalid always rides along."""
    certs = "".join(
        f"<ds:X509Certificate>{base64.b64encode(der).decode()}</ds:X509Certificate>"
        for der in ders)
    return ('<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#">'
            "<ds:SignedInfo>"
            '<ds:CanonicalizationMethod Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"/>'
            '<ds:SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"/>'
            '<ds:Reference URI="">'
            "<ds:Transforms>"
            '<ds:Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/>'
            "</ds:Transforms>"
            '<ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>'
            f'<ds:DigestValue>{base64.b64encode(bytes(32)).decode()}</ds:DigestValue>'
            "</ds:Reference></ds:SignedInfo>"
            f"<ds:SignatureValue>{base64.b64encode(bytes(256)).decode()}</ds:SignatureValue>"
            f"<ds:KeyInfo><ds:X509Data>{certs}</ds:X509Data></ds:KeyInfo>"
            "</ds:Signature>")


def sign_cpl_with_chain(d, ders):
    p = cpl_path(d)
    write(p, read(p).replace("</CompositionPlaylist>",
                             signature_with_chain(ders) + "</CompositionPlaylist>", 1))


# ── fixtures ─────────────────────────────────────────────────────────────────
# each entry: (name, expected_codes, flags, baseline, notes, mutate_fn, reseal)
FIXTURES = []


def fixture(name, codes, flags, notes, reseal_after=True, copy_mxf=False,
            also=None, baseline="valid/dcp_ov", baseline_flags=None, src=BASE,
            requires=None, vendor_portable=True):
    def deco(fn):
        FIXTURES.append({
            "name": name, "codes": codes, "flags": flags, "notes": notes,
            "reseal": reseal_after, "copy_mxf": copy_mxf, "fn": fn,
            "also": also or [], "baseline": baseline,
            "baseline_flags": baseline_flags, "src": src,
            "requires": requires or [], "vendor_portable": vendor_portable,
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
    sound_id = cpl_asset_id(d, "MainSound")
    blocks = re.findall(r"[ \t]*<Asset>[\s\S]*?</Asset>\n", s)
    snd = next(b for b in blocks if sound_id in b)
    s = s.replace(snd, snd + snd, 1)
    write(am_path(d), s)


@fixture("asset_not_found", ["asset_not_found"], [],
         "ASSETMAP points the picture chunk at a nonexistent file")
def _(d):
    picture = os.path.basename(picture_mxf(d))
    s = read(am_path(d))
    s = s.replace(f"<Path>{picture}</Path>", "<Path>missing_picture.mxf</Path>", 1)
    write(am_path(d), s)


@fixture("assetmap_invalid_name", ["assetmap_invalid_name"], [],
         "SMPTE asset map named ASSETMAP instead of ASSETMAP.xml")
def _(d):
    # reseal runs after this and am_path accepts either name, so the rename is
    # the only defect left
    os.rename(os.path.join(d, "ASSETMAP.xml"), os.path.join(d, "ASSETMAP"))


@fixture("assetmap_size_mismatch", ["assetmap_size_mismatch"], [],
         "ASSETMAP declares a chunk Length that is not the file's size",
         reseal_after=False)
def _(d):
    # ST 429-9 §7.4 lets Length be absent, and dcpwizard writes none, so the
    # violation has to be a declared Length that disagrees with the file
    def wrong_length(m):
        block = m.group(0)
        if "<Length>" in block:
            return re.sub(r"<Length>[^<]*</Length>", "<Length>1</Length>", block)
        return block.replace("</Chunk>", "  <Length>1</Length>\n        </Chunk>")

    write(am_path(d), re.sub(r"<Chunk>[\s\S]*?</Chunk>", wrong_length,
                             read(am_path(d)), count=1))


@fixture("missing_pkl", ["missing_pkl"], [],
         "PKL file and its ASSETMAP entry removed", reseal_after=False)
def _(d):
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


@fixture("pkl_size_mismatch", ["pkl_size_mismatch"], [],
         "PKL Size for the picture MXF wrong (hash left correct, so only the "
         "size check fires)", reseal_after=False)
def _(d):
    s = read(pkl_path(d))
    blocks = re.split(r"(?<=</Asset>)", s)
    for i, b in enumerate(blocks):
        if "application/mxf" in b:
            blocks[i] = re.sub(r"<Size>\d+</Size>", "<Size>12345</Size>", b, count=1)
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
    # DCP-o-matic writes a MainMarkers asset carrying its own EditRate ahead of
    # MainPicture, so the first EditRate in the reel is not the picture's
    s = re.sub(r"(<(?:[\w.-]+:)?MainPicture\b[^>]*>[\s\S]*?<(?:[\w.-]+:)?EditRate>)24 1",
               r"\g<1>13 1", read(p), count=1)
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
         "MainMarkers present, required FFMC/LFMC absent, a marker lacks Offset",
         # DCP-o-matic writes no FFMC/LFMC, so its clean package already reports
         # marker_missing and the same mutation proves nothing there
         vendor_portable=False)
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


@fixture("reel_edit_rate_mismatch", ["reel_edit_rate_mismatch"], [],
         "MainMarkers EditRate differs from the reel's picture EditRate")
def _(d):
    p = cpl_path(d)
    s = read(p)
    existing = re.search(
        r"<(?:[\w.-]+:)?MainMarkers[\s>][\s\S]*?</(?:[\w.-]+:)?MainMarkers>", s)
    if existing:
        block = re.sub(r"<EditRate>[^<]*</EditRate>", "<EditRate>13 1</EditRate>",
                       existing.group(0), count=1)
        s = s[:existing.start()] + block + s[existing.end():]
    else:
        # FFOC/LFOC are both present, so the marker checks stay quiet and the
        # rate is the only defect
        markers = ("        <MainMarkers>\n"
                   "          <Id>urn:uuid:00000000-0000-0000-0000-0000000000ab</Id>\n"
                   "          <EditRate>13 1</EditRate>\n"
                   "          <IntrinsicDuration>48</IntrinsicDuration>\n"
                   "          <MarkerList>\n"
                   "            <Marker><Label>FFOC</Label><Offset>1</Offset></Marker>\n"
                   "            <Marker><Label>LFOC</Label><Offset>47</Offset></Marker>\n"
                   "          </MarkerList>\n"
                   "        </MainMarkers>\n")
        s = s.replace("        <MainSound>", markers + "        <MainSound>", 1)
    write(p, s)


@fixture("composition_metadata_asset_mismatch",
         ["composition_metadata_asset_mismatch"], [],
         "CompositionMetadataAsset IntrinsicDuration differs from the reel picture")
def _(d):
    p = cpl_path(d)

    def bump(m):
        return re.sub(
            r"<(?:[\w.-]+:)?IntrinsicDuration>(\d+)</(?:[\w.-]+:)?IntrinsicDuration>",
            lambda n: f"<IntrinsicDuration>{int(n.group(1)) + 1}</IntrinsicDuration>",
            m.group(0), count=1)

    write(p, re.sub(
        r"<(?:[\w.-]+:)?CompositionMetadataAsset[\s>][\s\S]*?"
        r"</(?:[\w.-]+:)?CompositionMetadataAsset>",
        bump, read(p), count=1))


@fixture("cross_ref_broken", ["cross_ref_broken"], ["--ov", "@."],
         "MainPicture Id points at an asset absent from ASSETMAP/PKL; --ov points "
         "at the fixture itself so the id resolves in neither package nor OV and "
         "cross_ref_broken fires (without --ov it downgrades to supplemental_ov_not_provided)")
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


CERTIFICATE_BASELINE = "valid/dcp_certificate_chain"
# the injected signature never verifies, so every cert fixture emits this too
CERTIFICATE_ALSO_EMITS = ["signature_invalid"]


@fixture("certificate_basic_constraints_invalid",
         ["certificate_basic_constraints_invalid"], [],
         "Signer leaf carries Basic Constraints cA=TRUE. Every other field of "
         "the chain is conformant", also=CERTIFICATE_ALSO_EMITS, baseline=CERTIFICATE_BASELINE)
def _(d):
    sign_cpl_with_chain(d, certificate_chain(leaf_basic_constraints_ca=True))


@fixture("certificate_key_usage_invalid", ["certificate_key_usage_invalid"], [],
         "Signer leaf Key Usage asserts keyEncipherment but not digitalSignature",
         also=CERTIFICATE_ALSO_EMITS, baseline=CERTIFICATE_BASELINE)
def _(d):
    sign_cpl_with_chain(d, certificate_chain(leaf_key_usage={"key_encipherment": True}))


@fixture("certificate_key_size_invalid", ["certificate_key_size_invalid"], [],
         "Signer leaf holds a 3072-bit RSA key, off the 430-2 profile's 2048",
         also=CERTIFICATE_ALSO_EMITS, baseline=CERTIFICATE_BASELINE)
def _(d):
    sign_cpl_with_chain(d, certificate_chain(leaf_key_size=3072))


@fixture("certificate_role_invalid", ["certificate_role_invalid"], [],
         "Signer leaf CommonName starts with '.', so its role token is empty and "
         "matches the CA roles instead of being distinct",
         also=CERTIFICATE_ALSO_EMITS, baseline=CERTIFICATE_BASELINE)
def _(d):
    sign_cpl_with_chain(d, certificate_chain(leaf_common_name=".Signature.dci-ctp.corpus"))


@fixture("certificate_thumbprint_invalid", ["certificate_thumbprint_invalid"], [],
         "Signer leaf dnQualifier is a well-formed base64 value that is not the "
         "SHA-1 of its own public key", also=CERTIFICATE_ALSO_EMITS, baseline=CERTIFICATE_BASELINE)
def _(d):
    sign_cpl_with_chain(d, certificate_chain(leaf_dn_qualifier=WRONG_THUMBPRINT))


@fixture("certificate_organization_inconsistent",
         ["certificate_organization_inconsistent"], [],
         "Signer leaf Organization differs from the two CA certificates'",
         also=CERTIFICATE_ALSO_EMITS, baseline=CERTIFICATE_BASELINE)
def _(d):
    sign_cpl_with_chain(d, certificate_chain(leaf_organization=".other-org.corpus"))


@fixture("sound_no_mca", ["sound_invalid_channel_count"], ["--check-mxf"],
         "Real mono DCP whose sound MXF carries no ST 429-12 MCA subdescriptors; "
         "the 5.1 base (dcp_ov) is labeled so the INFO is absent there. dcpdoctor "
         "reads the MXF subdescriptors, not the CPL, so this is non-vacuous.",
         src=MONO, copy_mxf=True)
def _(d):
    pass  # the mono build already lacks MCA soundfield labels


@fixture("foreign_file_in_package", ["foreign_file_in_package"], [],
         "A file in the package directory that the ASSETMAP does not reference")
def _(d):
    write(os.path.join(d, "stray_notes.txt"), "not referenced by the ASSETMAP\n")


@fixture("empty_file_in_package", ["empty_file_in_package"], [],
         "A zero-byte file in the package directory")
def _(d):
    open(os.path.join(d, "empty.dat"), "wb").close()


@fixture("invalid_uuid", ["invalid_uuid"], [],
         "A urn:uuid: token in the CPL is malformed; compliance::check_uuids "
         "(wired into validate) flags it", reseal_after=False)
def _(d):
    # corrupt the reel Id into a malformed urn:uuid the uuid check scans
    p = cpl_path(d)
    s = re.sub(r"(<Reel>\s*<Id>)urn:uuid:[0-9a-fA-F-]+",
               r"\1urn:uuid:not-a-valid-uuid", read(p), count=1)
    write(p, s)


@fixture("xml_schema_violation", ["xml_schema_violation"], [],
         "CPL carries an element not allowed by the 429-16 CPL schema; schema "
         "validation is on by default (vendored schemas/), so no env var is needed",
         reseal_after=False)
def _(d):
    p = cpl_path(d)
    s = read(p).replace("</ContentTitleText>",
                        "</ContentTitleText><BogusElement>x</BogusElement>", 1)
    write(p, s)


@fixture("sound_invalid_sample_rate", ["sound_invalid_sample_rate"], ["--check-mxf"],
         "Sound MXF AudioSamplingRate KLV byte-patched 48000/1 -> 44100/1 so the "
         "prober reports a non-DCI rate", copy_mxf=True)
def _(d):
    # rational 48000/1 = 0000BB80 00000001 -> 44100/1 = 0000AC44 00000001
    patch_bytes(sound_mxf(d), "0000BB8000000001", "0000AC4400000001")


@fixture("sound_invalid_quantization", ["sound_invalid_quantization"],
         ["--check-mxf"],
         "Sound MXF QuantizationBits (local tag 3d01) patched to 16 bits, which "
         "DCI does not allow",
         copy_mxf=True)
def _(d):
    patch_local_tag(sound_mxf(d), "3d01", 4, 16)


@fixture("sound_invalid_block_align", ["sound_invalid_block_align"],
         ["--check-mxf"],
         "Sound MXF WaveAudioDescriptor BlockAlign (local tag 3d0a) patched one "
         "channel short of channels x bytes-per-sample",
         copy_mxf=True)
def _(d):
    mxf = sound_mxf(d)
    bytes_per_sample = local_tag_value(mxf, "3d01", 4) // 8
    channels = local_tag_value(mxf, "3d07", 4)
    patch_local_tag(mxf, "3d0a", 2, (channels - 1) * bytes_per_sample)


@fixture("stereo_framerate", ["stereo_mismatch"], ["--check-mxf"],
         "Real 3D DCP whose MainStereoscopicPicture FrameRate is not twice the "
         "EditRate (ST 429-10); part-1b relationship check", src=THREE_D)
def _(d):
    p = cpl_path(d)
    write(p, read(p).replace("<FrameRate>48 1</FrameRate>",
                             "<FrameRate>24 1</FrameRate>", 1))


@fixture("aux_data_atmos", ["aux_data_detected"], ["--check-mxf"],
         "Real Atmos DCP; the ST 429-18 AuxData track surfaces aux_data_detected "
         "(absent on the non-Atmos base)", src=ATMOS, reseal_after=False)
def _(d):
    pass  # the atmos build already carries the AuxData track


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
    mxf = picture_mxf(d)
    with open(mxf, "wb") as f:
        f.write(b"NOT AN MXF FILE" * 4)


@fixture("manifest_size_mismatch", ["mxf_hash_mismatch"], ["--manifest", "@refmanifest_bad.json"],
         "validate --manifest with a reference size that differs from the picture MXF",
         reseal_after=False, baseline="invalid/manifest_size_mismatch",
         baseline_flags=["--manifest", "@refmanifest_good.json"])
def _(d):
    mxf = os.path.basename(picture_mxf(d))
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


@fixture("mxf_invalid_structure", ["mxf_invalid_structure"], ["--check-mxf"],
         "Picture MXF footer partition pack key corrupted so the SMPTE 377-1 "
         "footer is absent; validate --check-mxf flags the structure. The header "
         "still parses, so read_mxf_info succeeds and no mxf_unreadable fires.",
         copy_mxf=True)
def _(d):
    break_mxf_footer(picture_mxf(d))


@fixture("interop_namespace_wrong", ["interop_namespace_wrong"], [],
         "Interop package whose subtitle document uses the SMPTE DCST namespace; "
         "validate_subtitle runs with Standard::Interop and flags the non-Interop "
         "namespace. The SMPTE equivalent is subtitle_wrong_namespace.",
         reseal_after=False, src=DOM_INTEROP, baseline="valid/dcp_dom_interop")
def _(d):
    # the source package is really Interop. dcpdoctor takes the standard from the
    # asset map's namespace, so renaming the file to ASSETMAP proves nothing
    add_subtitle(d, dcst(ns=True))  # SMPTE DCST ns, wrong for an Interop package
    reseal(d)


@fixture("dcp_not_signed", ["dcp_not_signed"], [],
         "Real encrypted DCP built by dcpwizard: it carries KeyIds but no "
         "CPL/PKL ds:Signature, so check_dcp_signed fires. The baseline is the "
         "same package with synthetic signatures injected (encrypted + signed).",
         src=ENC_SRC, reseal_after=False, also=["encryption_detected", "kdm_required"],
         baseline="valid/dcp_encrypted_signed")
def _(d):
    pass  # dcpwizard's encrypted package is already unsigned


@fixture("picture_invalid_resolution",
         ["picture_invalid_resolution", "j2k_invalid_profile"],
         ["--check-mxf", "--strict"],
         "1920x1080 non-DCI J2K (grok, no cinema profile) wrapped by the vendored "
         "asdcp-wrap and swapped in for the picture MXF; the MXF descriptor "
         "resolution trips picture_invalid_resolution and the plain codestream "
         "trips j2k_invalid_profile", copy_mxf=True, requires=[NONDCI_MXF])
def _(d):
    replace_picture_mxf(d, NONDCI_MXF)


@fixture("j2k_invalid_component_count", ["j2k_invalid_component_count"],
         ["--check-mxf", "--deep-j2k"],
         "Picture MXF first-frame SIZ Csiz byte-patched 3 -> 4 so the codestream "
         "declares 4 components (asdcp-wrap refuses a 4-component essence, so the "
         "count is patched into a valid 3-component wrap)", copy_mxf=True)
def _(d):
    patch_j2k_component_count(picture_mxf(d))


@fixture("picture_invalid_frame_rate", ["picture_invalid_frame_rate"], [],
         "IMF IMP whose CPL EditRate (25 1) differs from the 24 fps picture "
         "essence; the IMF validate path (imf.rs) flags the pic/edit-rate "
         "mismatch", src=IMF_SRC, reseal_after=False, baseline="valid/imf_ov",
         requires=[IMF_SRC])
def _(d):
    p = cpl_path(d)
    write(p, read(p).replace("<EditRate>24 1</EditRate>", "<EditRate>25 1</EditRate>"))


@fixture("non_ascii_filename", ["non_ascii_filename"], [],
         "A file whose name carries non-ASCII characters, which "
         "check_non_ascii_names "
         "scans every name in the package directory. The file is unreferenced, so "
         "the foreign-file note rides along.",
         also=["foreign_file_in_package"])
def _(d):
    write(os.path.join(d, "café_notes.txt"), "non-ascii filename\n")


@fixture("reel_too_short", ["reel_too_short"], [],
         "Reel picture/sound Duration cut to 12 frames at 24 fps (0.5s), under the "
         "ST 429-7 one-second minimum. Both tracks are cut together so the "
         "durations stay coherent and only the length check fires.")
def _(d):
    p = cpl_path(d)
    write(p, read(p).replace("<Duration>48</Duration>", "<Duration>12</Duration>"))


@fixture("main_sound_config_invalid", ["main_sound_config_invalid"], [],
         "CompositionMetadataAsset MainSoundConfiguration set to the literal "
         "'None' easyDCP emits, which has no soundfield/channels form")
def _(d):
    p = cpl_path(d)
    s = re.sub(r"(<meta:MainSoundConfiguration>)[^<]*(</meta:MainSoundConfiguration>)",
               r"\1None\2", read(p))
    write(p, s)


@fixture("subtitle_frame_rate_mismatch", ["subtitle_frame_rate_mismatch"], [],
         "Subtitle document declares TimeCodeRate 25 against the 24 fps "
         "composition edit rate (ST 428-7 §5.9)")
def _(d):
    add_subtitle(d, dcst(time_code_rate=25))


@fixture("subtitle_line_count", ["subtitle_line_count"], [],
         "One subtitle cue carries four <Text> lines, one over the Bv2.1 §7.2.7 "
         "limit of three")
def _(d):
    add_subtitle(d, dcst(lines=("one", "two", "three", "four")))


@fixture("subtitle_line_length", ["subtitle_line_length"], [],
         "One subtitle line is 85 characters, over the 79-character maximum")
def _(d):
    add_subtitle(d, dcst(lines=("x" * 85,)))


@fixture("subtitle_glyph_missing", ["subtitle_glyph_missing"], [],
         "Interop DCSubtitle whose LoadFont URI resolves to a minimal sfnt with a "
         "cmap covering only the ASCII the cue uses, so the star has no glyph. "
         "This exercises the URI form. The SMPTE ST 428-7 form (LoadFont carrying "
         "the font asset urn as element text) resolves through the ASSETMAP.",
         reseal_after=False)
def _(d):
    with open(os.path.join(d, "font.ttf"), "wb") as f:
        f.write(make_font(["H", "i", " "]))
    add_assetmap_entry(d, FONT_ID, "font.ttf")
    add_timed_text(d, dcsubtitle(lines=("Hi ★",)))
    reseal(d)
    os.rename(am_path(d), os.path.join(d, "ASSETMAP"))  # -> detected as Interop


@fixture("closed_caption_line_count", ["closed_caption_line_count"], [],
         "One closed-caption cue carries four <Text> lines, one over the "
         "Bv2.1 §7.2.6 limit of "
         "three (an error for captions, a warning for subtitles)")
def _(d):
    add_closed_caption(d, dcst(lines=("one", "two", "three", "four")))


@fixture("closed_caption_line_length", ["closed_caption_line_length"], [],
         "One closed-caption line is 40 characters, over the 32-character limit")
def _(d):
    add_closed_caption(d, dcst(lines=("a" * 40,)))


@fixture("closed_caption_charset", ["closed_caption_charset"], [],
         "Closed-caption text uses a character outside the ISDCF Doc 9 set "
         "(ISO 8859-1 plus U+266A). The music note in the same line must not be "
         "flagged")
def _(d):
    add_closed_caption(d, dcst(lines=("♪ music ★",)))


@fixture("mxf_asset_id_mismatch", ["mxf_asset_id_mismatch"], [],
         "The picture asset id is rewritten to a fresh uuid in the CPL, PKL and "
         "ASSETMAP together, so the package resolves the asset but the MXF still "
         "declares its own AssetUUID. This is what dcpwizard emitted before "
         "c1d73a6")
def _(d):
    # read the id once, before the CPL rewrite changes what pic_id returns, and
    # match <Id> only so the ASSETMAP <Path> keeps naming the file on disk
    old = pic_id(d)[len("urn:uuid:"):]
    for p in (cpl_path(d), pkl_path(d), am_path(d)):
        write(p, re.sub(rf"(<Id>urn:uuid:){old}(</Id>)",
                        rf"\g<1>{REWRITTEN_PICTURE_ID}\g<2>", read(p)))


@fixture("j2k_bitrate_exceeded", ["j2k_bitrate_exceeded"], ["--check-mxf"],
         "Real 3D DCP encoded at the full 250 Mb/s 2K bandwidth. Both eyes share "
         "one edit unit, so the measured peak lands over the DCI limit. The "
         "baseline is the same package encoded at 100 Mb/s per eye",
         src=BITRATE_SRC, baseline="valid/dcp_3d", reseal_after=False)
def _(d):
    pass  # the essence is already over the limit


@fixture("j2k_guard_bits", ["j2k_guard_bits"], ["--deep-j2k"],
         "First frame's QCD guard-bit field zeroed. SMPTE RDD 52 requires 1 guard "
         "bit at 2K, so run_deep_j2k's per-frame scan reports frame 0",
         copy_mxf=True)
def _(d):
    patch_j2k_guard_bits(picture_mxf(d))


@fixture("j2k_legacy_ffff", ["j2k_legacy_ffff"], ["--check-mxf"],
         "0xFF 0xFF written into the first frame's entropy data at a byte position "
         "254 mod 256 from the codestream start, the Dolby Cat. 862 legacy-decoder "
         "condition (SMPTE Legacy Compatibility Note 1)",
         copy_mxf=True)
def _(d):
    patch_j2k_legacy_ffff(picture_mxf(d))


def build_encrypted_signed_baseline():
    """The dcp_not_signed baseline: clone the encrypted source and inject an
    enveloped ds:Signature into the CPL and PKL so the package reads as signed."""
    dest = os.path.join(CORPUS, "valid", "dcp_encrypted_signed")
    clone(dest, src=ENC_SRC)
    cp = cpl_path(dest)
    write(cp, read(cp).replace("</CompositionPlaylist>", FAKE_SIG + "</CompositionPlaylist>", 1))
    pk = pkl_path(dest)
    write(pk, read(pk).replace("</PackingList>", FAKE_SIG + "</PackingList>", 1))
    reseal(dest)  # refresh the CPL hash the PKL carries


def build_certificate_chain_baseline():
    """The baseline for the six certificate-rule fixtures: the base DCP signed
    with a chain that satisfies every rule in cert_rules.rs, so each fixture's
    code is asserted absent against a chain whose only difference is the defect."""
    dest = os.path.join(CORPUS, *CERTIFICATE_BASELINE.split("/"))
    clone(dest)
    sign_cpl_with_chain(dest, certificate_chain())
    reseal(dest)


def fixup_stereo_order(d):
    """dcpwizard emits the 429-10 MainStereoscopicPicture before MainSound, but
    the 429-16 CPL schema matches the stereo element via xs:any (which must follow
    the known track elements), so real 3D CPLs put MainSound first. Reorder to the
    schema-valid form and reseal. Hand-edit workaround for a dcpwizard quirk."""
    p = cpl_path(d)
    s = read(p)
    stereo = re.search(
        r"[ \t]*<[\w-]*:?MainStereoscopicPicture[\s\S]*?</[\w-]*:?MainStereoscopicPicture>\n", s
    )
    sound = re.search(r"[ \t]*<MainSound>[\s\S]*?</MainSound>\n", s)
    if stereo and sound:
        s = s.replace(stereo.group(0) + sound.group(0), sound.group(0) + stereo.group(0))
        write(p, s)
    reseal(d)


def main():
    if not os.path.isdir(BASE):
        print(f"ERROR: base DCP missing at {BASE}; run build_corpus.sh first", file=sys.stderr)
        sys.exit(1)

    # make the real 3D packages schema-valid (dcpwizard element-order quirk)
    for stereo_dir in (THREE_D, BITRATE_SRC):
        if os.path.isdir(stereo_dir):
            fixup_stereo_order(stereo_dir)

    # the dcp_not_signed baseline (encrypted + synthetic signatures)
    if os.path.isdir(ENC_SRC):
        build_encrypted_signed_baseline()

    # the certificate-rule baseline (base DCP signed with a conformant chain)
    build_certificate_chain_baseline()

    manifest = {"baselines": [], "fixtures": []}
    # real dcpwizard packages that must validate clean (0 errors). dcp_ov is the
    # 5.1 labeled base; dcp_3d / dcp_atmos are the new 429-10 / 429-18 types.
    manifest["baselines"].append({
        "dir": "valid/dcp_ov", "package_type": "dcp", "is_valid_baseline": True,
        "flags": ["--strict", "--check-mxf"], "expected_codes": [],
        "notes": "real 5.1 DCP built by dcpwizard (MCA labeled); validates clean",
    })
    manifest["baselines"].append({
        "dir": "valid/dcp_3d", "package_type": "dcp", "is_valid_baseline": True,
        "flags": ["--strict", "--check-mxf"], "expected_codes": [],
        "notes": "stereoscopic 3D (ST 429-10) DCP; FrameRate = 2x EditRate, "
                 "Jpeg2000Stereo essence; validates clean",
    })
    manifest["baselines"].append({
        "dir": "valid/dcp_atmos", "package_type": "dcp", "is_valid_baseline": True,
        "flags": ["--strict", "--check-mxf"], "expected_codes": [],
        "notes": "Atmos AuxData (ST 429-18) DCP; validates clean (aux_data_detected "
                 "is INFO, not an error)",
    })

    # DCP-o-matic packages, present only when dcpomatic2_cli was available
    for path, name, standard in ((DOM_BASE, "valid/dcp_dom_ov", "SMPTE"),
                                 (DOM_INTEROP, "valid/dcp_dom_interop", "Interop")):
        if os.path.isdir(path):
            manifest["baselines"].append({
                "dir": name, "package_type": "dcp", "is_valid_baseline": True,
                "flags": ["--strict", "--check-mxf"], "expected_codes": [],
                "notes": f"real 5.1 {standard} DCP built by DCP-o-matic; validates clean",
            })

    inv = os.path.join(CORPUS, "invalid")
    if os.path.exists(inv):
        shutil.rmtree(inv)

    def build_fixture(f, name, src, baseline):
        missing = [r for r in f["requires"] if not os.path.exists(r)]
        if missing:
            print(f"  SKIP invalid/{name} (missing {', '.join(missing)})")
            return
        d = clone(os.path.join(inv, name), copy_mxf=f["copy_mxf"], src=src)
        f["fn"](d)
        if f["reseal"]:
            reseal(d)
        also = list(f["also"])
        # recorded from the built package rather than declared per fixture: the
        # DCP-o-matic variants reuse these same definitions, and only they
        # declare a chunk Length that a resized XML file can contradict
        if assetmap_length_disagrees(d) and "assetmap_size_mismatch" not in (
                list(f["codes"]) + also):
            also.append("assetmap_size_mismatch")
        manifest["fixtures"].append({
            "dir": f"invalid/{name}",
            "package_type": "dcp",
            "is_valid_baseline": False,
            "expected_codes": f["codes"],
            "also_emits": also,
            "flags": f["flags"],
            "baseline": baseline,
            "baseline_flags": f["baseline_flags"],
            "notes": f["notes"],
        })
        print(f"  built invalid/{name} -> {', '.join(f['codes'])}")

    for f in FIXTURES:
        if not os.path.isdir(f["src"]):
            print(f"  SKIP invalid/{f['name']} (source {f['src']} not built)")
            continue
        build_fixture(f, f["name"], f["src"], f["baseline"])

    # the same mutations on a DCP-o-matic package, so a code proves it fires on
    # two mastering tools' output. Fixtures checked against another baseline are
    # left out: it has no DoM twin, and asserting the code is absent from
    # dcp_dom_ov instead would pass on a package that could never emit it.
    if os.path.isdir(DOM_BASE):
        for f in FIXTURES:
            if f["vendor_portable"] and f["src"] == BASE and f["baseline"] == "valid/dcp_ov":
                build_fixture(f, f"dom_{f['name']}", DOM_BASE, "valid/dcp_dom_ov")

    # fixtures reachable only through non-validate subcommands. Each runs
    # `dcpdoctor <subcommand> [args]` where an @name arg resolves to a file in
    # corpus/subcmd. auto-qc prints findings as text (not Codes), so those carry
    # a `match` map from code -> substring; kdm emits real Code notes.
    manifest["subcommand_fixtures"] = [
        {"name": "kdm_expired", "subcommand": "kdm", "dir": "subcmd",
         "args": ["@kdm_expired.xml"], "baseline_args": ["@kdm_valid.xml"],
         "expected_codes": ["kdm_expired"],
         "notes": "KDM ContentKeysNotValidAfter in the past"},
        {"name": "kdm_not_yet_valid", "subcommand": "kdm", "dir": "subcmd",
         "args": ["@kdm_future.xml"], "baseline_args": ["@kdm_valid.xml"],
         "expected_codes": ["kdm_not_yet_valid"],
         "notes": "KDM ContentKeysNotValidBefore in the future"},
        {"name": "sound_clipping", "subcommand": "auto-qc", "dir": "subcmd",
         "args": ["--audio", "@clip.wav"], "baseline_args": ["--audio", "@normal.wav"],
         "expected_codes": ["sound_clipping"], "match": {"sound_clipping": "Audio clipping"},
         "notes": "full-scale audio; auto-qc reports clipping as a finding string"},
        {"name": "sound_silent", "subcommand": "auto-qc", "dir": "subcmd",
         "args": ["--audio", "@silent.wav"], "baseline_args": ["--audio", "@normal.wav"],
         "expected_codes": ["sound_silent"], "match": {"sound_silent": "Audio silence"},
         "notes": "near-silent audio; auto-qc reports silence as a finding string"},
    ]
    # drop any subcommand fixture whose input files were not built
    subcmd_dir = os.path.join(CORPUS, "subcmd")
    manifest["subcommand_fixtures"] = [
        sf for sf in manifest["subcommand_fixtures"]
        if all(not a.startswith("@") or os.path.exists(os.path.join(subcmd_dir, a[1:]))
               for a in sf["args"])
    ]

    # keep the reference_packages section scan_reference.py appended, or a
    # regen silently drops coverage from 73 to 65 until it is re-run
    path = os.path.join(CORPUS, "manifest.json")
    if os.path.exists(path):
        with open(path) as f:
            old = json.load(f)
        if "reference_packages" in old:
            manifest["reference_packages"] = old["reference_packages"]
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nmanifest: {os.path.join(CORPUS, 'manifest.json')}")
    print(f"fixtures: {len(manifest['fixtures'])} + "
          f"{len(manifest['subcommand_fixtures'])} subcommand")


if __name__ == "__main__":
    main()
