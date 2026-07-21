// independently decrypt an encrypted jp2k picture mxf with a content key.
// proves the key really unlocks the essence: every frame must decrypt (the
// smpte 429-6 check value + hmac only validate under the correct key), a wrong
// key must fail, and no key must yield ciphertext != plaintext.
//
// usage: decrypt-check <picture.mxf> <key-hex-32> [<expected-key-id-uuid>]

use asdcplib::LabelSet;
use asdcplib::crypto::{AesDecContext, HmacContext};
use asdcplib::jp2k::MxfReader;

fn die(msg: String) -> ! {
    eprintln!("FAIL: {msg}");
    std::process::exit(1);
}

fn parse_key(s: &str) -> [u8; 16] {
    let s = s.trim();
    if s.len() != 32 {
        die(format!("key must be 32 hex chars, got {}", s.len()));
    }
    let mut k = [0u8; 16];
    for i in 0..16 {
        k[i] = u8::from_str_radix(&s[i * 2..i * 2 + 2], 16)
            .unwrap_or_else(|_| die(format!("bad hex in key: {s}")));
    }
    k
}

fn uuid_no_hyphen(u: &str) -> String {
    u.trim().replace('-', "").to_lowercase()
}

fn read_all(mxf: &str, key: Option<[u8; 16]>, frames: u32) -> Result<Vec<Vec<u8>>, String> {
    let mut reader = MxfReader::new();
    reader.open_read(mxf).map_err(|e| format!("open_read: {e:?}"))?;
    let mut dec = key.map(|k| {
        let mut d = AesDecContext::new();
        d.init_key(&k).expect("dec init_key");
        d
    });
    let mut hmac = key.map(|k| {
        let mut h = HmacContext::new();
        h.init_key(&k, LabelSet::Smpte).expect("hmac init_key");
        h
    });
    let mut out = Vec::new();
    for i in 0..frames {
        let mut buf = vec![0u8; 8 * 1024 * 1024];
        let n = reader
            .read_frame(i, &mut buf, dec.as_mut(), hmac.as_mut())
            .map_err(|e| format!("read_frame {i}: {e:?}"))?;
        buf.truncate(n);
        out.push(buf);
    }
    reader.close().ok();
    Ok(out)
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 3 {
        die("usage: decrypt-check <picture.mxf> <key-hex-32> [expected-key-id]".into());
    }
    let mxf = &args[1];
    let key = parse_key(&args[2]);
    let expected_kid = args.get(3).map(|s| uuid_no_hyphen(s));

    // header must advertise encryption + integrity, and the key id must match
    let mut reader = MxfReader::new();
    reader
        .open_read(mxf)
        .unwrap_or_else(|e| die(format!("open_read: {e:?}")));
    let info = reader
        .writer_info()
        .unwrap_or_else(|e| die(format!("writer_info: {e:?}")));
    let desc = reader
        .picture_descriptor()
        .unwrap_or_else(|e| die(format!("picture_descriptor: {e:?}")));
    reader.close().ok();

    if !info.encrypted_essence {
        die("mxf header does not advertise encrypted essence".into());
    }
    let mxf_kid = hex(&info.cryptographic_key_id);
    println!("mxf cryptographic_key_id = {mxf_kid}");
    println!("frames = {}, {}x{}", desc.container_duration, desc.stored_width, desc.stored_height);
    if let Some(exp) = &expected_kid {
        if &mxf_kid != exp {
            die(format!("mxf key id {mxf_kid} != expected {exp}"));
        }
        println!("key id matches expected KDM/keys key id");
    }

    let frames = desc.container_duration;
    if frames == 0 {
        die("mxf reports zero frames".into());
    }

    // 1. correct key: every frame decrypts (check value + hmac pass)
    let plain = read_all(mxf, Some(key), frames)
        .unwrap_or_else(|e| die(format!("decrypt with recovered key failed: {e}")));
    let total: usize = plain.iter().map(|f| f.len()).sum();
    // each decrypted frame must be a real jp2k codestream (SOC marker ff4f)
    for (i, f) in plain.iter().enumerate() {
        if f.len() < 4 || f[0] != 0xff || f[1] != 0x4f {
            die(format!("decrypted frame {i} is not a jp2k codestream (no SOC marker)"));
        }
    }
    println!("decrypted {frames} frames, {total} bytes, all start with jp2k SOC marker");

    // 2. wrong key: decryption must fail (check value rejects it)
    let mut wrong = key;
    wrong[0] ^= 0xff;
    match read_all(mxf, Some(wrong), 1) {
        Ok(_) => die("wrong key decrypted a frame; check value not enforced".into()),
        Err(_) => println!("wrong key correctly rejected"),
    }

    // 3. no key: read returns ciphertext, never the plaintext frame
    let cipher = read_all(mxf, None, 1)
        .unwrap_or_else(|e| die(format!("ciphertext read failed: {e}")));
    if cipher[0] == plain[0] {
        die("ciphertext equals plaintext; essence was not actually encrypted".into());
    }
    println!("no-key read yields ciphertext != plaintext");

    println!("PASS: KDM-recovered key decrypts the encrypted essence; wrong key fails");
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}
