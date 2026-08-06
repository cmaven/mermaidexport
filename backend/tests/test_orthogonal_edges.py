# ============================================================
# test_orthogonal_edges.py: Graphviz waypoints → PPTX/draw.io 직교선
# ============================================================

import os
import sys
import shutil

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from converters.pptx_shapes import (
    parse_mermaid,
    apply_graphviz_layout,
    _simplify_polyline,
    _fit_canvas,
    mermaid_to_pptx,
    SLIDE_W,
    SLIDE_H,
)
from converters.layout_graph import is_available, compute_graphviz_layout, LNode, LEdge


SAMPLE = """
flowchart LR
  subgraph G["Group"]
    A[Alpha]
    B[Beta]
  end
  C[Gamma]
  A -->|go| B
  B --> C
"""


def test_simplify_polyline_collinear():
    pts = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (2.0, 1.0)]
    out = _simplify_polyline(pts)
    assert out == [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0)]


@pytest.mark.skipif(not is_available(), reason="graphviz dot not installed")
def test_apply_graphviz_assigns_edge_points():
    diagram = parse_mermaid(SAMPLE)
    ok = apply_graphviz_layout(diagram)
    assert ok is True
    with_pts = [e for e in diagram.edges if len(e.points) >= 2]
    assert len(with_pts) >= 1
    # 슬라이드가 기본 16:9보다 작지 않음
    assert diagram.slide_w >= SLIDE_W - 0.01
    assert diagram.slide_h >= SLIDE_H - 0.01
    # 노드 높이가 강제 축소로 찌그러지지 않음
    assert min(n.h for n in diagram.nodes.values()) >= 0.4


@pytest.mark.skipif(not is_available(), reason="graphviz dot not installed")
def test_mermaid_to_pptx_uses_polyline_without_error():
    data = mermaid_to_pptx(SAMPLE, title="ortho-test")
    assert data[:2] == b"PK"  # zip/pptx
    assert len(data) > 2000


@pytest.mark.skipif(not is_available(), reason="graphviz dot not installed")
def test_fit_canvas_expands_instead_of_crush():
    diagram = parse_mermaid(SAMPLE)
    assert apply_graphviz_layout(diagram)
    # 인위적으로 멀리 밀어 확장 유도
    for n in diagram.nodes.values():
        n.x += 20
        n.y += 10
    _fit_canvas(diagram, 0.3, 0.9, max_w=100.0, max_h=100.0)
    assert diagram.slide_w > SLIDE_W
    assert diagram.slide_h > SLIDE_H


@pytest.mark.skipif(not is_available(), reason="graphviz dot not installed")
def test_layout_graph_returns_edge_waypoints():
    nodes = [LNode("A", "A"), LNode("B", "B"), LNode("C", "C")]
    edges = [LEdge("A", "B", "x"), LEdge("B", "C", "")]
    layout = compute_graphviz_layout(nodes, edges, [], "LR")
    assert layout is not None
    assert any(len(e.points) >= 2 for e in layout.edges)
