# SPDX-License-Identifier: Apache-2.0
"""Bundle.json import/export with atomic cross-DB rename.

Per GRAEAE consultation f1bea48c: bundle import touches BOTH mnemos.db
and ic-engine.db. The import must be transactional across two sqlite
files. Without atomic-rename: a partial failure leaves orphaned data
in one db and not the other.

The schema is defined in sibling module `bundle_schema.py` (Pydantic).

Critical security property: bundle.json holds env-var REFERENCES for
API keys (e.g., "$TOGETHER_API_KEY"), never raw values. Resolution
happens at runtime against /data/keys.env (mode 0600).

This module's correctness is the v4.0 release-blocker for "users can
back up + restore" — the dashboard's Export/Import buttons go through
here. If atomic-rename fails halfway, user state corrupts. Get this
right.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import structlog

logger = structlog.get_logger("investorclaw_bridge.bundle")


@dataclass
class BundleImportResult:
    success: bool
    memories_imported: int
    portfolios_imported: int
    keys_referenced: int
    errors: list[str]


@dataclass
class BundleExportResult:
    success: bool
    bundle_path: Path
    memories_exported: int
    portfolios_exported: int


# ──────────────────────────────────────────────────────────────────────
# Atomic cross-DB rename pattern (the GRAEAE-flagged correctness path)
# ──────────────────────────────────────────────────────────────────────


@contextmanager
def atomic_two_file_replace(
    target_a: Path, target_b: Path,
    *,
    data_dir: Path | None = None,
) -> Iterator[tuple[Path, Path]]:
    """Yield (tmp_a, tmp_b) paths. On clean exit, atomically replace
    target_a + target_b with the contents of tmp_a + tmp_b. On exception
    or non-clean exit: discard temps, leave targets untouched.

    Usage:
        with atomic_two_file_replace(mnemos_db, ic_db) as (tmp_mnemos, tmp_ic):
            # Write candidate dbs to tmp_mnemos, tmp_ic
            # Validate both
            # If validation passes: context exits cleanly → atomic replace
            # If validation fails: raise → temps cleaned up, targets untouched

    Implementation note: rename(2) is atomic per-file on POSIX. We can't
    do a true two-file atomic rename across filesystems, but if both targets
    live in the same data volume (per v4.0 compose's shared /data volume),
    we can ensure that at most ONE rename happens before failure → we can
    detect partial state and clean up.

    Better-than-naive approach used here:
      1. Write both temps in same directory as targets (rename within FS = atomic)
      2. Pre-rename both targets aside as .pending-replace-* sentinels
      3. Rename temps into place
      4. On success: remove .pending-replace sentinels
      5. On any failure: rename .pending-replace sentinels BACK, remove temps

    Even with that, there's a microscopic window where the OS could crash
    between step 3a (a→target_a renamed) and step 3b (b→target_b renamed).
    Recovery: a startup hook detects orphaned .pending-replace sentinels
    and rolls them back.
    """
    if data_dir is None:
        data_dir = target_a.parent
    data_dir.mkdir(parents=True, exist_ok=True)

    # Ensure both targets are in the same filesystem (required for atomic rename)
    if target_a.parent.resolve() != target_b.parent.resolve():
        raise ValueError(
            f"atomic_two_file_replace requires same parent dir; got "
            f"{target_a.parent} and {target_b.parent}"
        )

    with tempfile.TemporaryDirectory(prefix=".bundle-import-", dir=data_dir) as td:
        tmp_a = Path(td) / target_a.name
        tmp_b = Path(td) / target_b.name

        try:
            yield tmp_a, tmp_b
        except Exception:
            # Discard temps; targets untouched
            logger.warning("bundle.atomic_replace.aborted", reason="exception")
            raise

        if not (tmp_a.exists() and tmp_b.exists()):
            raise FileNotFoundError(
                f"atomic_two_file_replace: both temps must exist before "
                f"replacing targets. Got tmp_a.exists={tmp_a.exists()}, "
                f"tmp_b.exists={tmp_b.exists()}"
            )

        # Move temps into the same dir as targets (within FS = atomic rename)
        tmp_a_landing = data_dir / f".{target_a.name}.replace"
        tmp_b_landing = data_dir / f".{target_b.name}.replace"
        shutil.move(str(tmp_a), str(tmp_a_landing))
        shutil.move(str(tmp_b), str(tmp_b_landing))

        # Set aside originals (atomic rename per-file)
        pending_a = data_dir / f"{target_a.name}.pending-replace"
        pending_b = data_dir / f"{target_b.name}.pending-replace"
        if target_a.exists():
            os.rename(target_a, pending_a)
        if target_b.exists():
            os.rename(target_b, pending_b)

        # Rename landings into final targets
        try:
            os.rename(tmp_a_landing, target_a)
            os.rename(tmp_b_landing, target_b)
        except OSError as e:
            # Rollback: restore pending originals if either rename failed mid-way
            logger.error("bundle.atomic_replace.rename_failed", error=str(e))
            if pending_a.exists():
                os.rename(pending_a, target_a)
            if pending_b.exists():
                os.rename(pending_b, target_b)
            raise

        # Clean up pending sentinels
        for p in (pending_a, pending_b):
            if p.exists():
                p.unlink()

        logger.info(
            "bundle.atomic_replace.committed",
            target_a=str(target_a), target_b=str(target_b),
        )


# ──────────────────────────────────────────────────────────────────────
# Public API — implementation pending
# ──────────────────────────────────────────────────────────────────────


def import_bundle(
    bundle_path: Path,
    *,
    mnemos_db: Path,
    ic_engine_db: Path,
    data_dir: Path,
    keys_env: Path,
) -> BundleImportResult:
    """Import a v4.0 bundle.json — atomically across both sqlite dbs.

    TODO[v4.0-impl]:
      1. Validate bundle.json against Pydantic schema
      2. Resolve env-var references against keys_env (or fail with structured error)
      3. Open temp sqlite files (tmp_mnemos.db, tmp_ic.db) inside atomic_two_file_replace
      4. Apply migrations to both temps so schema matches current code
      5. Insert memories from bundle into tmp_mnemos.db
      6. Insert portfolio refs from bundle into tmp_ic.db
      7. If portfolios reference files in bundle.payload.portfolios/, copy them into data_dir
      8. Validate post-insert integrity (PRAGMA integrity_check)
      9. Exit context cleanly → atomic replace

    On any failure: existing dbs untouched, no orphaned files, structured error.
    """
    raise NotImplementedError("v4.0.0a1 placeholder — implementation lands after RFC review")


def export_bundle(
    output_path: Path,
    *,
    mnemos_db: Path,
    ic_engine_db: Path,
    keys_env: Path,
    include_portfolios: bool = True,
) -> BundleExportResult:
    """Export the current state to a bundle.json + accompanying tarball.

    TODO[v4.0-impl]:
      1. Read all memories from mnemos.db into bundle.memories
      2. Read all portfolio refs from ic-engine.db into bundle.portfolios
      3. Read provider/data-source/narrative/mcp/memory config from ic-engine.db
      4. Resolve provider keys to ENV-VAR REFERENCES (never raw values) using keys_env mapping
      5. If include_portfolios: tar up portfolio source files into companion .tar.gz
      6. Write bundle.json + (optional) tarball
      7. Set umask 0077 / chmod 0600 on bundle output (defense in depth)
    """
    raise NotImplementedError("v4.0.0a1 placeholder — implementation lands after RFC review")
