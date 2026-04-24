"""
Hincapie Photography — Box intake & organization script (demo).

Two modes:
  organize   walk an existing archive, rename + upload into Box under
             <shoot>/<YYYY-MM-DD>/<canonical-name> with dupe-hash skip.
  intake     watch a staging folder, do the same for every new file that
             lands (polling loop; can be swapped to fs-notify or cron).

Ships with a --mock flag that exercises every step except the Box upload
(no Box credentials required). Run mock first to sanity-check the folder
plan, then flip to --mode live with BOX_DEVELOPER_TOKEN or JWT config.

Author: Yang Gao (demo for Upwork Box intake job, Apr 2026).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from PIL import Image
from PIL.ExifTags import TAGS

LOG = logging.getLogger("box_intake")

# --------------------------- EXIF + naming ---------------------------

_SHOOT_FROM_DIR_RE = re.compile(r"[A-Za-z0-9_\-]+")

def read_exif(path: Path) -> dict:
    """Return a shallow dict of the tags we care about."""
    try:
        img = Image.open(path)
        raw = img._getexif() or {}
    except Exception as e:
        LOG.warning("exif read failed on %s: %s", path.name, e)
        return {}
    out = {}
    for tag_id, val in raw.items():
        name = TAGS.get(tag_id, str(tag_id))
        out[name] = val
    return out

def shoot_date(exif: dict) -> Optional[datetime]:
    for key in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
        v = exif.get(key)
        if not v:
            continue
        try:
            return datetime.strptime(v, "%Y:%m:%d %H:%M:%S")
        except ValueError:
            continue
    return None

def infer_shoot_name(file: Path, override: Optional[str]) -> str:
    """Prefer override > parent folder > 'unsorted'."""
    if override:
        return _sanitize(override)
    parent = file.parent.name
    if parent and parent not in ("samples", "", "."):
        return _sanitize(parent)
    return "unsorted"

def _sanitize(s: str) -> str:
    s = s.strip().lower().replace(" ", "_")
    return "".join(ch for ch in s if ch.isalnum() or ch in "_-") or "shoot"

def canonical_name(src: Path, dt: datetime, seq: int, sku: Optional[str]) -> str:
    stem_clean = _sanitize(src.stem)
    base = f"{dt:%Y%m%d_%H%M%S}"
    if sku:
        return f"{base}_{_sanitize(sku)}_{seq:03d}{src.suffix.lower()}"
    return f"{base}_{stem_clean}_{seq:03d}{src.suffix.lower()}"

def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

# --------------------------- plan record -----------------------------

@dataclass
class Plan:
    src: str
    shoot: str
    date: str            # YYYY-MM-DD
    box_path: str        # e.g. hincapie_spring_ride/2026-04-20/20260420_100600_IMG_1011_001.jpg
    sha256: str
    bytes: int
    camera: Optional[str] = None
    action: str = "upload"           # upload | skip_dupe | skip_no_exif

# --------------------------- Box backends ----------------------------

class BoxBackend:
    """Tiny interface both mock + live implement."""
    def ensure_folder(self, path_segments: list[str]) -> str:
        raise NotImplementedError
    def upload(self, local_path: Path, parent_id: str, remote_name: str, sha256: str) -> dict:
        raise NotImplementedError
    def close(self) -> None:
        pass

class MockBox(BoxBackend):
    def __init__(self, root_name: str = "Hincapie"):
        self._folders: dict[tuple, str] = {(): f"mock:{root_name}"}
        self._files: set[str] = set()
        self._next = 1

    def ensure_folder(self, segs: list[str]) -> str:
        key = tuple(segs)
        if key in self._folders:
            return self._folders[key]
        parent = self.ensure_folder(segs[:-1])
        fid = f"mock:folder:{'/'.join(segs)}"
        self._folders[key] = fid
        LOG.info("  [mock] mkdir %s  (parent=%s)", "/".join(segs), parent)
        return fid

    def upload(self, local_path, parent_id, remote_name, sha256):
        path_key = f"{parent_id}/{remote_name}"
        if path_key in self._files:
            LOG.info("  [mock] skip-dupe  %s  (already uploaded)", remote_name)
            return {"id": f"mock:{self._next}", "status": "duplicate"}
        self._files.add(path_key)
        self._next += 1
        LOG.info("  [mock] UPLOAD  %-32s  -> %s  (%d bytes, sha=%s)",
                 local_path.name, remote_name, local_path.stat().st_size, sha256[:12])
        return {"id": f"mock:{self._next-1}", "status": "uploaded"}


class LiveBox(BoxBackend):
    """Wraps box_sdk_gen. Auth via BOX_DEVELOPER_TOKEN env (easiest) or JWT."""
    def __init__(self, root_folder_id: str):
        from box_sdk_gen import BoxClient, BoxDeveloperTokenAuth
        token = os.environ.get("BOX_DEVELOPER_TOKEN")
        if not token:
            raise SystemExit("BOX_DEVELOPER_TOKEN env var required for --mode live")
        self.client = BoxClient(auth=BoxDeveloperTokenAuth(token=token))
        self._folder_cache: dict[tuple, str] = {(): root_folder_id}

    def ensure_folder(self, segs: list[str]) -> str:
        key = tuple(segs)
        if key in self._folder_cache:
            return self._folder_cache[key]
        parent_id = self.ensure_folder(segs[:-1])
        name = segs[-1]
        # List items to find existing
        items = self.client.folders.get_folder_items(folder_id=parent_id).entries
        for it in items:
            if it.name == name and it.type == "folder":
                self._folder_cache[key] = it.id
                return it.id
        # Create
        from box_sdk_gen import CreateFolderParent
        new = self.client.folders.create_folder(name=name, parent=CreateFolderParent(id=parent_id))
        self._folder_cache[key] = new.id
        LOG.info("  [box]  mkdir %s (id=%s)", "/".join(segs), new.id)
        return new.id

    def upload(self, local_path, parent_id, remote_name, sha256):
        # Pre-upload dupe check: Box's /files/content supports If-Match by sha
        try:
            items = self.client.folders.get_folder_items(folder_id=parent_id).entries
            for it in items:
                if it.name == remote_name and it.type == "file":
                    LOG.info("  [box]  skip-dupe %s (exists as %s)", remote_name, it.id)
                    return {"id": it.id, "status": "duplicate"}
        except Exception as e:
            LOG.warning("  [box]  listing failed, proceeding anyway: %s", e)
        from box_sdk_gen import UploadFileAttributes, UploadFileAttributesParentField
        with open(local_path, "rb") as fh:
            attr = UploadFileAttributes(
                name=remote_name,
                parent=UploadFileAttributesParentField(id=parent_id),
            )
            res = self.client.uploads.upload_file(attributes=attr, file=fh)
        fid = res.entries[0].id
        LOG.info("  [box]  UPLOAD %s -> id=%s", remote_name, fid)
        return {"id": fid, "status": "uploaded"}

# --------------------------- planning --------------------------------

def build_plan(src_dir: Path, shoot_override: Optional[str], sku_prefix: Optional[str],
               existing_hashes: set[str]) -> list[Plan]:
    plans: list[Plan] = []
    seq_per_key: dict[tuple, int] = {}
    files = sorted(p for p in src_dir.rglob("*")
                   if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".png"})
    for fp in files:
        exif = read_exif(fp)
        dt = shoot_date(exif)
        if not dt:
            plans.append(Plan(src=str(fp), shoot="unsorted", date="0000-00-00",
                              box_path=f"unsorted/{fp.name}", sha256="", bytes=fp.stat().st_size,
                              camera=None, action="skip_no_exif"))
            continue
        shoot = infer_shoot_name(fp, shoot_override)
        date_str = dt.strftime("%Y-%m-%d")
        key = (shoot, date_str)
        seq_per_key[key] = seq_per_key.get(key, 0) + 1
        seq = seq_per_key[key]
        name = canonical_name(fp, dt, seq, sku_prefix)
        sha = sha256_file(fp)
        action = "skip_dupe" if sha in existing_hashes else "upload"
        existing_hashes.add(sha)
        plans.append(Plan(src=str(fp), shoot=shoot, date=date_str,
                          box_path=f"{shoot}/{date_str}/{name}",
                          sha256=sha, bytes=fp.stat().st_size,
                          camera=exif.get("Model"), action=action))
    return plans

def execute_plan(plans: Iterable[Plan], backend: BoxBackend) -> dict:
    stats = {"uploaded": 0, "skip_dupe": 0, "skip_no_exif": 0, "bytes": 0}
    for p in plans:
        if p.action == "skip_no_exif":
            LOG.info("  SKIP no-exif  %s", Path(p.src).name)
            stats["skip_no_exif"] += 1
            continue
        if p.action == "skip_dupe":
            LOG.info("  SKIP dupe-hash %s  (sha=%s)", Path(p.src).name, p.sha256[:12])
            stats["skip_dupe"] += 1
            continue
        segs = p.box_path.split("/")[:-1]
        parent_id = backend.ensure_folder(segs)
        backend.upload(Path(p.src), parent_id, Path(p.box_path).name, p.sha256)
        stats["uploaded"] += 1
        stats["bytes"] += p.bytes
    return stats

# --------------------------- CLI -------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["organize", "intake"])
    ap.add_argument("--src", required=True, help="local source folder to scan")
    ap.add_argument("--mode", choices=["mock", "live"], default="mock")
    ap.add_argument("--box-root-id", default="0", help="Box root folder id for live mode (0 = All Files)")
    ap.add_argument("--shoot", help="override shoot name (else inferred from parent folder)")
    ap.add_argument("--sku-prefix", help="SKU prefix to bake into filenames")
    ap.add_argument("--plan-out", default="logs/plan.jsonl")
    ap.add_argument("--poll-sec", type=int, default=5, help="intake poll interval")
    ap.add_argument("--once", action="store_true", help="intake: run a single pass and exit")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    src = Path(args.src).expanduser().resolve()
    if not src.is_dir():
        raise SystemExit(f"source folder not found: {src}")

    backend = MockBox() if args.mode == "mock" else LiveBox(root_folder_id=args.box_root_id)

    def run_once(existing_hashes: set[str]):
        LOG.info("scanning %s (mode=%s)", src, args.mode)
        plans = build_plan(src, args.shoot, args.sku_prefix, existing_hashes)
        Path(args.plan_out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.plan_out, "w") as fh:
            for p in plans:
                fh.write(json.dumps(asdict(p)) + "\n")
        LOG.info("plan written to %s (%d entries)", args.plan_out, len(plans))
        stats = execute_plan(plans, backend)
        LOG.info("done: %s", stats)
        return stats

    hash_index_path = Path("logs/hash_index.json")
    if hash_index_path.exists():
        hashes: set[str] = set(json.loads(hash_index_path.read_text()))
        LOG.info("loaded %d prior hashes from %s", len(hashes), hash_index_path)
    else:
        hashes = set()

    def persist():
        hash_index_path.parent.mkdir(parents=True, exist_ok=True)
        hash_index_path.write_text(json.dumps(sorted(hashes)))

    if args.command == "organize":
        run_once(hashes)
        persist()
    else:
        LOG.info("intake poll every %ds on %s (ctrl-c to stop)", args.poll_sec, src)
        while True:
            run_once(hashes)
            persist()
            if args.once:
                break
            time.sleep(args.poll_sec)

    backend.close()

if __name__ == "__main__":
    main()
