# SPDX-License-Identifier: Apache-2.0
"""Tests for the atomic_two_file_replace correctness property.

Per GRAEAE consultation f1bea48c: bundle import touches BOTH mnemos.db
and ic-engine.db; partial-failure semantics must be transactional. This
test surface is the cutover gate's safety check — if these fail, the
bundle import path is unsafe to ship.

Test coverage:
  - happy path: both files end up replaced when context exits cleanly
  - exception path: targets untouched, temps cleaned up
  - cross-FS rejection: raises ValueError if targets are on different filesystems
  - missing-temp rejection: raises FileNotFoundError if either temp missing at exit
  - rollback: existing target dbs restored if mid-rename os.rename() fails

Some tests use monkeypatching to inject failures at specific syscalls.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bridge"))

from investorclaw_bridge.bundle import atomic_two_file_replace  # noqa: E402


def test_happy_path_replaces_both(tmp_path: Path) -> None:
    """Clean exit replaces both targets atomically."""
    target_a = tmp_path / "mnemos.db"
    target_b = tmp_path / "ic-engine.db"
    target_a.write_text("OLD_A")
    target_b.write_text("OLD_B")

    with atomic_two_file_replace(target_a, target_b) as (tmp_a, tmp_b):
        tmp_a.write_text("NEW_A")
        tmp_b.write_text("NEW_B")

    assert target_a.read_text() == "NEW_A"
    assert target_b.read_text() == "NEW_B"
    # Sentinels and temps cleaned up
    assert not list(tmp_path.glob("*.pending-replace"))
    assert not list(tmp_path.glob(".*.replace"))


def test_exception_path_leaves_targets_untouched(tmp_path: Path) -> None:
    """Exception during context aborts replace — targets unchanged."""
    target_a = tmp_path / "mnemos.db"
    target_b = tmp_path / "ic-engine.db"
    target_a.write_text("OLD_A")
    target_b.write_text("OLD_B")

    with pytest.raises(RuntimeError, match="simulated failure"):
        with atomic_two_file_replace(target_a, target_b) as (tmp_a, tmp_b):
            tmp_a.write_text("PARTIAL_A")
            raise RuntimeError("simulated failure")

    assert target_a.read_text() == "OLD_A"
    assert target_b.read_text() == "OLD_B"
    # No orphaned sentinels
    assert not list(tmp_path.glob("*.pending-replace"))


def test_missing_temp_b_aborts(tmp_path: Path) -> None:
    """If only one temp is written, the operation aborts."""
    target_a = tmp_path / "mnemos.db"
    target_b = tmp_path / "ic-engine.db"
    target_a.write_text("OLD_A")
    target_b.write_text("OLD_B")

    with pytest.raises(FileNotFoundError, match="both temps must exist"):
        with atomic_two_file_replace(target_a, target_b) as (tmp_a, _tmp_b):
            tmp_a.write_text("NEW_A")
            # _tmp_b never written — context exit should fail

    assert target_a.read_text() == "OLD_A"
    assert target_b.read_text() == "OLD_B"


def test_cross_fs_rejection(tmp_path: Path) -> None:
    """Targets on different parent dirs are rejected (would break atomicity)."""
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    target_a = tmp_path / "mnemos.db"
    target_b = other_dir / "ic-engine.db"
    target_a.write_text("OLD_A")
    target_b.write_text("OLD_B")

    with pytest.raises(ValueError, match="same parent dir"):
        with atomic_two_file_replace(target_a, target_b):
            pass


def test_first_time_create(tmp_path: Path) -> None:
    """No pre-existing targets → context can still create them atomically."""
    target_a = tmp_path / "mnemos.db"
    target_b = tmp_path / "ic-engine.db"
    # neither exists yet

    with atomic_two_file_replace(target_a, target_b) as (tmp_a, tmp_b):
        tmp_a.write_text("FRESH_A")
        tmp_b.write_text("FRESH_B")

    assert target_a.read_text() == "FRESH_A"
    assert target_b.read_text() == "FRESH_B"


def test_rollback_on_partial_rename_failure(tmp_path: Path, monkeypatch) -> None:
    """If second os.rename() fails mid-commit, first is rolled back."""
    target_a = tmp_path / "mnemos.db"
    target_b = tmp_path / "ic-engine.db"
    target_a.write_text("OLD_A")
    target_b.write_text("OLD_B")

    real_rename = os.rename
    rename_calls = {"count": 0}

    def flaky_rename(src: str, dst: str) -> None:
        # Fail specifically on the rename of tmp_b_landing → target_b
        # (which is the second rename of the final-commit pair)
        if str(dst) == str(target_b) and rename_calls["count"] >= 4:
            # 4 prior renames = both .pending-replace sentinels written + one tmp landing → target_a
            raise OSError("simulated rename failure")
        rename_calls["count"] += 1
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", flaky_rename)

    with pytest.raises(OSError, match="simulated rename failure"):
        with atomic_two_file_replace(target_a, target_b) as (tmp_a, tmp_b):
            tmp_a.write_text("NEW_A")
            tmp_b.write_text("NEW_B")

    # Both targets should be the OLD content (rollback successful)
    # Note: target_a may briefly have been replaced before rollback; the rollback
    # rename moves the .pending-replace back over it.
    assert target_a.read_text() == "OLD_A"
    assert target_b.read_text() == "OLD_B"
