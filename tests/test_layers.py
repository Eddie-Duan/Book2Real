"""Layer builder: geometry helpers that must not regress.

The fills here were debugged the hard way (a wrap-around `np.roll` once painted a
slab of the page's sky-blue border into the hole where the figures were), so the
tests pin the properties that were actually violated.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from book2real.vision import layers as L  # noqa: E402


def _page():
    rgb = np.zeros((120, 160, 3), np.uint8)
    rgb[:, :] = (200, 120, 90)          # warm paper
    rgb[:10, :] = (40, 180, 220)        # a cold border on purpose
    rgb[:, -10:] = (40, 180, 220)
    return rgb


def test_dilate_and_bbox():
    m = np.zeros((50, 60), bool)
    m[10:20, 15:25] = True
    assert L._bbox(m) == (15, 10, 25, 20)
    grown = L._dilate(m, 3)
    assert grown.sum() > m.sum()
    x0, y0, x1, y1 = L._bbox(grown)
    assert x0 < 15 and x1 > 25


def test_ring_fill_does_not_pull_the_page_border_in():
    """A wide hole must be filled from its rim, never from the far page edge."""
    rgb = _page()
    hole = np.zeros(rgb.shape[:2], bool)
    hole[40:80, 60:100] = True
    out = L.ring_fill(rgb, hole, rings=60)
    inside = out[hole].astype(int)
    cold = (inside[:, 2] > inside[:, 0] + 40) & (inside[:, 1] > inside[:, 0] + 20)
    # a few pixels may drift towards the rim colours; the old bug made them all cold
    assert cold.mean() < 0.15, f"{cold.mean():.2f} of the hole went cold"


def test_ring_fill_leaves_outside_untouched():
    rgb = _page()
    hole = np.zeros(rgb.shape[:2], bool)
    hole[30:50, 30:60] = True
    out = L.ring_fill(rgb, hole, rings=90)
    assert np.array_equal(out[~hole], rgb[~hole])


def test_scan_fill_uses_row_neighbours():
    rgb = _page()
    hole = np.zeros(rgb.shape[:2], bool)
    hole[40:44, 60:70] = True
    out = L.scan_fill(rgb, hole)
    assert np.array_equal(out[~hole], rgb[~hole])
    assert out[hole].astype(int).std(axis=0).max() < 40


def test_fill_background_reports_a_method_and_keeps_the_paper():
    rgb = _page()
    hole = np.zeros(rgb.shape[:2], bool)
    hole[40:80, 60:100] = True
    out, method = L.fill_background(rgb, hole.copy(), hole)
    assert method in {"inpaint", "telea", "scan", "none"}
    assert np.array_equal(out[~hole], rgb[~hole])


def test_empty_hole_is_a_no_op():
    rgb = _page()
    hole = np.zeros(rgb.shape[:2], bool)
    out, method = L.fill_background(rgb, hole, hole)
    assert method == "none"
    assert np.array_equal(out, rgb)
