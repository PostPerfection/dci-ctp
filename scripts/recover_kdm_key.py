#!/usr/bin/env python3
# independently recover the content keys from a dcpwizard KDM using the
# recipient private key, then check them against the plaintext keys.json.
#
# this is a from-scratch decoder of the smpte st 430-1 key block. the RSA
# unwrap is done by openssl (a different implementation than the rsa crate
# dcpwizard used to wrap), so a passing run is genuine cross-implementation
# proof that the KDM delivers the right key.
#
# usage: recover_kdm_key.py <kdm.xml> <recipient.key> <keys.json>
# prints the recovered picture (MDIK) key hex to stdout on success.

import sys, os, re, json, uuid, base64, subprocess, tempfile

# st 430-1 table 6: fixed structure id that opens every key block
STRUCT_ID = bytes([0xf1,0xdc,0x12,0x44,0x60,0x16,0x9a,0x0e,
                   0x85,0xbc,0x30,0x06,0x42,0xf8,0x66,0xab])
BLOCK_LEN = 138

def die(msg):
    sys.stderr.write(f"FAIL: {msg}\n")
    sys.exit(1)

def rsa_oaep_sha1_decrypt(ciphertext, key_path):
    # dcpwizard wraps with rsa-oaep-mgf1p (OAEP, SHA-1 digest + MGF1-SHA1).
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(ciphertext); cin = f.name
    cout = cin + ".out"
    try:
        r = subprocess.run(
            ["openssl", "pkeyutl", "-decrypt", "-inkey", key_path,
             "-in", cin, "-out", cout,
             "-pkeyopt", "rsa_padding_mode:oaep",
             "-pkeyopt", "rsa_oaep_md:sha1",
             "-pkeyopt", "rsa_mgf1_md:sha1"],
            capture_output=True, text=True)
        if r.returncode != 0:
            die(f"openssl RSA-OAEP decrypt failed: {r.stderr.strip()}")
        return open(cout, "rb").read()
    finally:
        for p in (cin, cout):
            try: os.unlink(p)
            except OSError: pass

def parse_block(b):
    if len(b) != BLOCK_LEN:
        die(f"key block is {len(b)} bytes, expected {BLOCK_LEN}")
    if b[0:16] != STRUCT_ID:
        die("key block structure id mismatch (not a valid ST 430-1 block)")
    return {
        "cpl_id": str(uuid.UUID(bytes=b[36:52])),
        "key_type": b[52:56].decode("ascii", "replace"),
        "key_id": str(uuid.UUID(bytes=b[56:72])),
        "not_before": b[72:97].decode("ascii", "replace"),
        "not_after": b[97:122].decode("ascii", "replace"),
        "key_hex": b[122:138].hex(),
    }

def main():
    if len(sys.argv) != 4:
        die("usage: recover_kdm_key.py <kdm.xml> <recipient.key> <keys.json>")
    kdm_path, key_path, keys_path = sys.argv[1:4]

    xml = open(kdm_path, "r").read()
    ciphers = re.findall(r"<CipherValue>([^<]+)</CipherValue>", xml)
    if not ciphers:
        die("no <CipherValue> elements in KDM")

    keys = json.load(open(keys_path))
    by_id = {k["key_id"]: k for k in keys["keys"]}

    recovered = []
    for i, cv in enumerate(ciphers):
        raw = base64.b64decode(cv.strip())
        if len(raw) != 256:
            die(f"cipher {i} is {len(raw)} bytes, expected 256 (RSA-2048)")
        block = rsa_oaep_sha1_decrypt(raw, key_path)
        info = parse_block(block)
        ref = by_id.get(info["key_id"])
        if ref is None:
            die(f"recovered key id {info['key_id']} not present in keys.json")
        if ref["content_key_hex"] != info["key_hex"]:
            die(f"key {info['key_id']}: recovered {info['key_hex']} "
                f"!= keys.json {ref['content_key_hex']}")
        if info["cpl_id"] != keys["cpl_id"]:
            die(f"key {info['key_id']}: cpl {info['cpl_id']} != keys.json {keys['cpl_id']}")
        sys.stderr.write(
            f"  recovered {info['key_type']} key_id={info['key_id']} "
            f"key={info['key_hex']} MATCH keys.json\n")
        recovered.append(info)

    # every plaintext key must have been delivered by the KDM
    if len(recovered) != len(keys["keys"]):
        die(f"KDM delivered {len(recovered)} keys, keys.json has {len(keys['keys'])}")

    mdik = [r for r in recovered if r["key_type"] == "MDIK"]
    if not mdik:
        die("no MDIK (picture) key in KDM")
    # picture key + its key id, for the essence decrypt step
    print(f"{mdik[0]['key_hex']} {mdik[0]['key_id']}")

if __name__ == "__main__":
    main()
