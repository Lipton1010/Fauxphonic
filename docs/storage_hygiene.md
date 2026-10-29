# Storage hygiene



The verified portable is `dist/storage-verified-20261024/Fauxphonic/Fauxphonic.exe`.
Keep its entire folder together. Existing installed/previous portable 0.2.2 binaries
are unchanged; no installer was rebuilt. No historical directory is adopted or
purged automatically. The new build passed real GUI registry selection, native
GUI-to-worker export, and packaged storage-command checks. Evidence is in
`evidence/storage-final-verification.json` and `evidence/storage-packaged-acceptance.json`.

## Review and recover

In a development launch, open **Storage…**, then select one output folder,
`.test-runs`, or `.build-runs`. Opening the dialog performs no scan. The preview
lists exact files, logical bytes, candidates and reasons for preservation. It is
not an allocated-space or free-space forecast. Wait for active/queued jobs to
finish; the native control enforces this for its own queue.

**Quarantine reviewed payloads…** asks for confirmation, revalidates the preview,
and moves only those exact owned folders to `<selected-root>/.quarantine/` on
the same volume. A restore receipt is written **before** each move. An interrupted
batch may leave earlier folders quarantined; their receipts still restore them.
**Restore quarantine…** takes the corresponding `*.restore.json` and refuses to
overwrite an occupied original path. A prepared receipt can mean the move did
not happen: check whether its original or quarantined directory exists.

Quarantine reclaims **zero bytes**. To reclaim new intermediate payloads, use
**Permanently delete reviewed intermediates…** and accept its separate default-No
confirmation, or the explicit CLI command below. This unlinks only the fresh
manifest's exact files, with identity/size/mtime/link revalidation, never a whole
directory tree. It leaves compact owner/completion receipts and empty folders.
If a deletion is interrupted, the receipt stays `cleanup_in_progress`; remaining
files are protected for manual review and are never automatically retried.

There is no automatic historical purge. This thread neither imports nor executes
the cross-project audit manifest; that audit owns old artifacts and their approved
removal. Permanent deletion was exercised only on tiny synthetic test payloads.
Existing `.cache`, `build`, `dist`, benchmark audio,
datasets, fixtures, graft, selected models and user media are not candidates.

CLI equivalents use the same app code:

```powershell
.venv/Scripts/python.exe run_worker.py storage preview .test-runs --report new-preview.json
.venv/Scripts/python.exe run_worker.py storage quarantine new-preview.json --confirm
# Alternative to quarantine, after reviewing the exact preview:
.venv/Scripts/python.exe run_worker.py storage delete new-preview.json --confirm
.venv/Scripts/python.exe run_worker.py storage restore .test-runs/.quarantine/<name>.restore.json
```

Preview reports must have new filenames. Keep the preview for review; apply
refuses a changed payload, changed/missing final evidence, a live process owner,
an uncommitted native queue completion, reparse ancestors, links, shared files,
unexpected files, pinned receipts or an unvalidated state. File identities,
size and modification time are recorded; this is an ownership/freshness check,
not an adversarial filesystem sandbox. Source media and final outputs are always
outside disposable render folders. The operation never edits them.

## New ownership and retention boundaries

- Normal pytest runs use `.test-runs/<uuid>/payload/`. Unique fixture paths avoid
  pytest's Windows `current` junction aliases. A small `<uuid>.result.json`
  outside the payload keeps exit status, counts and bounded failure summaries.
  Only an exit-zero run receives a completion allowlist. Explicit `--basetemp`
  opts out because that external harness owns its directory; no old run is removed.
- `scripts/build_windows.ps1` uses `.build-runs/<uuid>/payload/` for PyInstaller
  work, owned by the launching PowerShell PID plus creation time. Successful
  package/inventory completion retains a separate compact checksum record.
  This records build success, **not** runtime or clean-machine acceptance.
  Failed builds remain unvalidated. Current binaries in `dist` are never targets.
- New renders use `.fauxphonic-<uuid>/` directly beside the chosen final, retaining
  bounded-block disk PCM. The receipt starts active. Only verified promotion of
  audio, stems and report creates a completed allowlist. For queued native renders,
  cleanup additionally opens the existing SQLite database read-only and requires
  the matching job/output to be committed `complete`. No queue schema changed.
  Failed, cancelled and crashed renders remain protected, including older legacy
  crash folders. Missing cleanup metadata cannot turn a verified audio success
  into a failed export; the scratch is preserved and the worker explains why.

Preview retains the newest three successful test payloads and two successful
build payloads while younger than 14 days. Older successful payloads become
**preview candidates**, never automatic deletions. Completed render intermediates
are candidates after their owner exits. All failed/unvalidated runs are retained;
there is no automatic expiry of recovery assets. A receipt can be explicitly
pinned with `"pinned": true`. Compact result records remain outside moved payloads.
Successful render scratch is now retained until explicit review and cleanup: plan disk space
accordingly. The existing bounded preview cache and normal analysis/playback
temporary-file lifecycle are unchanged.

## Reuse exact models offline

**Capabilities… → Use shared registry…** reads an explicitly chosen schema-1
registry and selects the existing exact `silero-vad/v6.2.1/onnx/default` file.
It checks unique ID, ONNX format, version, pinned size and full SHA-256 before
saving the existing folder. No registry mutation, copy or download occurs.
Both GUI and worker subsequently use that folder and recheck its hash; a missing
or altered selection fails without a fallback.

```powershell
.venv/Scripts/python.exe run_worker.py resolve-model --registry <registry.json>
.venv/Scripts/python.exe run_worker.py resolve-model --registry <registry.json> --verify
.venv/Scripts/python.exe run_worker.py resolve-model --registry <registry.json> --select
```

Without `--registry`, resolution uses the explicit selected/default model.
Metadata-only resolution labels `hash_verified: false`. Selection always hashes
the entire 2.3 MB artifact. Other revisions, formats and quantizations cannot
silently substitute for it. Only Silero is supported by this consumer.

The explicit acquisition script checks this resolver before a download, skips
existing hash-matching artifacts and preserves mismatches. A missing portable
build input can be staged from the verified existing selected file instead of
downloaded again. New downloads use per-artifact exclusive locks, unique partials,
hash verification and no-overwrite promotion. A stale lock requires inspection;
the script never steals it. `-RegistryPath <path>` selects/reuses the registered
file without staging or networking. License text stays pinned separately.

Proposed shared-registry follow-up only: add explicit publisher/repository,
immutable revision, architecture/task, quantization, component/shard and license
provenance to a future agreed schema. This consumer does not change that central
schema or conflate other apps' formats or variants.

## Validation scope

Source checks run offscreen with one numerical thread.
They cover receipt freshness, permanent-deletion confirmation, quarantine/restore, failed and active preservation,
retention preview, simulated links/shared files, exact model reuse, and a real
native QProcess render with committed SQLite state and a decoded 192,001-frame
WAV. No GPU, foreground desktop interaction, listening claim or model download.
The portable build and short offline packaged acceptance are complete. Both CLI
and native GUI exports passed at 706,889 frames, mono 48 kHz. All 368 payload hashes
and the embedded GUI/worker code were checked. Forty focused CPU tests pass.
No dependency installation or model download was needed. The packaged Capabilities
check exposed an ONNX namespace without a provider API in the GUI (the runtime
Python code is intentionally worker-only); unavailable provider inspection is now
handled, and unexpected capability errors are visible. The actual worker still
verifies the selected exact model and performs CPU inference.

For a future isolated bundle, use `scripts/build_windows.ps1 -OutputRoot 'dist/<new-name>'`. Custom output roots must be new and stay under this repository's
`dist`; this preserves the previous release. `Fauxphonic.exe --data-dir <absolute-folder>` selects a separate queue/cache directory. Model selection still
uses its explicit model-store settings. The acceptance scripts use a private
LOCALAPPDATA and this argument, never the user's existing queue/settings.

Re-run `scripts/storage_packaged_smoke.py` with `--bundle`, `--registry` and
`--powershell` pointing to the existing workspace PowerShell host. No execution
policy override is used. The script retains an immutable per-run acceptance record;
its latest summary is separate so future runs do not invalidate earlier receipts.

New redundant build/candidate payloads from this verification were removed only
through exact revalidated manifests (709,232,702 logical bytes, not measured free
space). The accepted 300,400,842-byte portable folder remains, along with about
79 MB of recent test payloads, synthetic finals, recovery data and compact records. Failed
QA recovery was retained; older releases, source media, datasets and selected
models were untouched. Exact retention paths and cleanup records are in
`evidence/storage-artifact-retention.json` and `evidence/storage-new-artifact-cleanup-result.json`.
