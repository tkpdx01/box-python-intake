# Box intake & archive-organize script — demo

Tiny Python script for **Hincapie Photography** Box-API intake job
([Upwork posting](https://www.upwork.com/jobs/Box-API-Python-Script-File-Organization-Intake-Workflow_~022046974791979760218/)).

Two commands:

- **`organize`** — walk an existing archive folder, read EXIF, rename
  canonically, and upload into Box under
  `<shoot>/<YYYY-MM-DD>/<canonical-name>`.
- **`intake`** — same thing, but as a polling loop: every `--poll-sec`
  seconds it re-scans the staging folder and uploads any new file. Can
  be run one-shot via `--once` (fine for cron).

Ships with a `--mode mock` so you can see the full plan (folders to
create, filenames to use, dupe-skips, no-EXIF skips) without any Box
credentials. Switch to `--mode live` once a Box developer token is
wired up and every call goes for real.

Spec assumptions — easy to retune to `Hincapie_Photography_Technical_Spec.docx`:

- Folder layout: `<shoot_name>/<YYYY-MM-DD>/<file>`. Swap to `<YYYY>/<shoot>/` or any other in `build_plan` (3 lines).
- Canonical filename: `YYYYMMDD_HHMMSS_<sku>_<seq:03>.jpg`. Sequence is per (shoot, date). Edit `canonical_name()` to change.
- Dedup: SHA-256 of file bytes, persisted to `logs/hash_index.json`, so re-running never re-uploads.
- EXIF-missing files go to a skip list (never silently uploaded to wrong folder).

---

## Quickstart (mock)

```bash
python3 -m venv .venv
.venv/bin/pip install Pillow piexif box_sdk_gen
.venv/bin/python make_samples.py        # writes 9 JPGs with EXIF into samples/
.venv/bin/python box_intake.py organize \
    --src samples --mode mock \
    --shoot hincapie_spring_ride --sku-prefix HIN-SPR
```

Output (excerpt — full run in `logs/run-organize-mock.log`):

```
scanning .../samples (mode=mock)
plan written to logs/plan.jsonl (9 entries)
  [mock] mkdir hincapie_spring_ride
  [mock] mkdir hincapie_spring_ride/2026-04-20
  [mock] UPLOAD  IMG_1011.jpg   -> 20260420_100600_hin-spr_001.jpg  (sha=89e6bd7c3595)
  [mock] UPLOAD  IMG_1012.jpg   -> 20260420_101100_hin-spr_002.jpg  (sha=7e4015efaa6c)
  [mock] UPLOAD  IMG_1013.jpg   -> 20260420_101600_hin-spr_003.jpg  (sha=b9d646eb5665)
  [mock] mkdir hincapie_spring_ride/2026-04-22
  [mock] UPLOAD  IMG_1021.jpg   -> 20260422_100700_hin-spr_001.jpg
  ...
done: {'uploaded': 9, 'skip_dupe': 0, 'skip_no_exif': 0, 'bytes': 42024}
```

Re-run → every file is now a hash dupe:

```
loaded 9 prior hashes from logs/hash_index.json
  SKIP dupe-hash IMG_1011.jpg  (sha=89e6bd7c3595)
  ...
done: {'uploaded': 0, 'skip_dupe': 9, 'skip_no_exif': 1, 'bytes': 0}
```

Intake mode — drop a new file into `samples/` and run a single pass:

```bash
.venv/bin/python box_intake.py intake --src samples --mode mock \
    --shoot hincapie_merino_sprint --sku-prefix HIN-MS --once
```

## Live mode

Grab a Box developer token from
<https://developer.box.com/guides/authentication/tokens/developer-tokens/>
(valid 60 min; fine for the demo approval run).

```bash
export BOX_DEVELOPER_TOKEN="xxx"
.venv/bin/python box_intake.py organize \
    --src /path/to/archive --mode live \
    --box-root-id 0 \
    --shoot 2026_spring_classic --sku-prefix CLASSIC
```

For production, swap `BoxDeveloperTokenAuth` → `BoxJWTAuth` or
`BoxCCGAuth` (server app) — one-line change in `LiveBox.__init__`,
nothing else moves.

## What's in the box

```
box_intake.py         # 220 LOC, all logic
make_samples.py       # writes 9 fake JPGs with EXIF for demo
samples/              # input folder used by the demo
logs/
  plan.jsonl          # every run's plan record (one JSON per file)
  hash_index.json     # persistent dupe index (sha256 set)
  run-*.log           # captured console output from each demo run
```

## Design notes

- **Plan-then-execute** — `build_plan()` runs first, writes `plan.jsonl`,
  then `execute_plan()` consumes it. You can eyeball the plan before
  running live, or diff two plans to see what changed.
- **SHA-based dedup, not name-based** — two files with the same bytes
  but different names (common after renames) don't double-upload.
- **Per-(shoot, date) sequence** — sequence resets per date so the
  filenames stay short and searchable.
- **EXIF-missing is loud, not silent** — we don't guess a date from
  mtime. Skipped + logged so you see them in the daily run.
- **Intake poll loop is swap-ready** — replace `while True: … sleep` with
  `watchdog.observers.Observer` if you want real-time, or keep polling
  for cron simplicity.

## Not included / stretch items

- Chunked upload for files >50 MB (Box requires `upload_session`).
  Added in ~30 min when we move off small JPGs.
- Box folder rate-limit handling (429 → backoff). Current code logs and
  proceeds; for production we'd wrap `ensure_folder`/`upload` with a
  retry-decorator.
- Raw CR2/NEF EXIF extraction. Pillow handles JPG; for raw files add
  `rawpy` or `exiftool` shell-out.

All three are ~1 h each on top of the base script.
