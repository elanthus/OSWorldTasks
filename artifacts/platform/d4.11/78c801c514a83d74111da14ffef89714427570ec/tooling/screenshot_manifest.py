import hashlib, json, struct, sys
from pathlib import Path
shots = Path(sys.argv[1]); revision = sys.argv[2]; states = json.loads(Path(sys.argv[3]).read_text())
entries = []
for png in sorted(shots.glob("*.png")):
    data = png.read_bytes(); w, h = struct.unpack(">II", data[16:24])
    entries.append({"path": png.name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "width": w, "height": h, **states.get(png.name, {"state": "UNDESCRIBED"})})
manifest = {"schema_version": "pixelgym-platform-screenshot-manifest-v1", "captured_revision": revision,
  "redaction": "Browser chrome is excluded; no credentials, hostnames, usernames, private provider payloads, expected answers, or privileged bounding boxes are shown.", "screenshots": entries}
(shots / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n"); print(json.dumps([(e["path"], e["state"]) for e in entries], indent=1))
