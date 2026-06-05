# ============================================================
# test_edge_anchor.py: drawio.py의 _edge_anchor / _make_abs_pos 단위 테스트
# 상세: orthogonal 엣지 앵커 방향 + 절대좌표 변환 로직 검증
# 생성일: 2026-05-14
# ============================================================

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from converters.drawio import _edge_anchor, _make_abs_pos


# ──────────────────────────────────────────────────────────
# 1. _edge_anchor — 수평 방향 (좌→우)
# ──────────────────────────────────────────────────────────
def test_horizontal_anchor():
    """src가 좌측, tgt가 우측 → exit=우(1.0, 0.5), entry=좌(0.0, 0.5)"""
    src_abs  = (0.0,   0.0)
    src_size = (100.0, 50.0)
    tgt_abs  = (200.0, 0.0)
    tgt_size = (100.0, 50.0)

    ex, ey, enx, eny = _edge_anchor(src_abs, src_size, tgt_abs, tgt_size)

    assert ex  == 1.0, f"exitX should be 1.0 (right), got {ex}"
    assert ey  == 0.5, f"exitY should be 0.5 (middle), got {ey}"
    assert enx == 0.0, f"entryX should be 0.0 (left), got {enx}"
    assert eny == 0.5, f"entryY should be 0.5 (middle), got {eny}"


def test_horizontal_anchor_reverse():
    """src가 우측, tgt가 좌측 → exit=좌(0.0, 0.5), entry=우(1.0, 0.5)"""
    src_abs  = (300.0, 0.0)
    src_size = (100.0, 50.0)
    tgt_abs  = (0.0,   0.0)
    tgt_size = (100.0, 50.0)

    ex, ey, enx, eny = _edge_anchor(src_abs, src_size, tgt_abs, tgt_size)

    assert ex  == 0.0, f"exitX should be 0.0 (left), got {ex}"
    assert enx == 1.0, f"entryX should be 1.0 (right), got {enx}"


# ──────────────────────────────────────────────────────────
# 2. _edge_anchor — 수직 방향 (위→아래)
# ──────────────────────────────────────────────────────────
def test_vertical_anchor():
    """src가 위, tgt가 아래 → exit=하(0.5, 1.0), entry=상(0.5, 0.0)"""
    src_abs  = (0.0,   0.0)
    src_size = (100.0, 50.0)
    tgt_abs  = (0.0,   100.0)
    tgt_size = (100.0, 50.0)

    ex, ey, enx, eny = _edge_anchor(src_abs, src_size, tgt_abs, tgt_size)

    assert ex  == 0.5, f"exitX should be 0.5, got {ex}"
    assert ey  == 1.0, f"exitY should be 1.0 (bottom), got {ey}"
    assert enx == 0.5, f"entryX should be 0.5, got {enx}"
    assert eny == 0.0, f"entryY should be 0.0 (top), got {eny}"


def test_vertical_anchor_upward():
    """src가 아래, tgt가 위 → exit=상(0.5, 0.0), entry=하(0.5, 1.0)"""
    src_abs  = (0.0,   200.0)
    src_size = (100.0, 50.0)
    tgt_abs  = (0.0,   0.0)
    tgt_size = (100.0, 50.0)

    ex, ey, enx, eny = _edge_anchor(src_abs, src_size, tgt_abs, tgt_size)

    assert ey  == 0.0, f"exitY should be 0.0 (top), got {ey}"
    assert eny == 1.0, f"entryY should be 1.0 (bottom), got {eny}"


# ──────────────────────────────────────────────────────────
# 3. _make_abs_pos — 크로스 서브그래프 절대좌표 변환
# ──────────────────────────────────────────────────────────
def test_cross_subgraph_uses_abs_pos():
    """서브그래프 origin 기반으로 절대좌표를 올바르게 계산한다."""
    # sgA: origin (0, 0), n1 로컬 (10, 20) → 절대 (10, 20)
    # sgB: origin (500, 0), n2 로컬 (10, 20) → 절대 (510, 20)
    node_to_sg       = {"n1": "sgA", "n2": "sgB"}
    sg_offset_map    = {"sgA": (0.0, 0.0), "sgB": (500.0, 0.0)}
    local_positions  = {"n1": (10.0, 20.0), "n2": (10.0, 20.0)}
    standalone_positions = {}

    abs_pos = _make_abs_pos(node_to_sg, sg_offset_map, local_positions, standalone_positions)

    assert abs_pos("n1") == (10.0,  20.0), f"n1 절대좌표 오류: {abs_pos('n1')}"
    assert abs_pos("n2") == (510.0, 20.0), f"n2 절대좌표 오류: {abs_pos('n2')}"

    # n1(좌) → n2(우): 수평 우향 앵커
    ex, ey, enx, eny = _edge_anchor(
        abs_pos("n1"), (100.0, 50.0),
        abs_pos("n2"), (100.0, 50.0),
    )
    assert ex  == 1.0 and enx == 0.0, (
        f"크로스 서브그래프 수평 앵커 오류: exitX={ex}, entryX={enx}"
    )


# ──────────────────────────────────────────────────────────
# 4. _make_abs_pos — standalone 노드 폴백
# ──────────────────────────────────────────────────────────
def test_standalone_node_fallback():
    """서브그래프 미소속 노드는 standalone_positions 를 직접 반환한다."""
    node_to_sg           = {}          # 어떤 서브그래프에도 소속 없음
    sg_offset_map        = {}
    local_positions      = {}
    standalone_positions = {"n3": (100.0, 200.0)}

    abs_pos = _make_abs_pos(node_to_sg, sg_offset_map, local_positions, standalone_positions)

    result = abs_pos("n3")
    assert result == (100.0, 200.0), f"standalone 폴백 오류: {result}"
