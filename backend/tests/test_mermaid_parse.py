# ============================================================
# test_mermaid_parse.py: flowchart 엣지/라벨 파서 회귀 테스트
# ============================================================

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from converters.pptx_shapes import parse_mermaid, _clean_label


def test_clean_label_preserves_br_as_newline():
    assert _clean_label('A<br/>B') == "A\nB"
    assert _clean_label('A<br>B') == "A\nB"


def test_arrow_not_eaten_by_optional_label():
    """POD -->|ensure| DET 에서 src_label이 ' -'가 되면 안 됨."""
    d = parse_mermaid("""
    flowchart LR
      POD -->|ensure| DET[DS: npu-op-detector]
    """)
    assert d.nodes["POD"].label == "POD"
    assert d.nodes["DET"].label == "DS: npu-op-detector"
    assert len(d.edges) == 1
    assert d.edges[0].label == "ensure"


def test_double_dash_inside_node_label():
    """라벨 안의 --leader-elect 를 화살표로 오인하지 않음."""
    d = parse_mermaid("""
    flowchart LR
      DEP --> POD["Operator Pod<br/>/manager --leader-elect=true<br/>RollingUpdate"]
    """)
    assert "leader-elect" not in d.nodes
    assert "POD" in d.nodes
    assert "leader-elect" in d.nodes["POD"].label
    assert "\n" in d.nodes["POD"].label
    assert d.edges[0].source == "DEP"
    assert d.edges[0].target == "POD"


def test_parens_inside_bracket_label():
    """[...] 안 (Go module) 때문에 줄 매칭이 실패하면 안 됨."""
    d = parse_mermaid("""
    flowchart TD
      ROOT["operator/"] --> OP["kcloud-operator/<br/>(Go module)"]
    """)
    assert d.nodes["ROOT"].label == "operator/"
    assert "Go module" in d.nodes["OP"].label
    assert d.edges[0].source == "ROOT"
    assert d.edges[0].target == "OP"


def test_root_not_overwritten_to_dash():
    d = parse_mermaid("""
    flowchart TD
      ROOT["operator/"] --> OP["kcloud-operator/"]
      ROOT --> UTIL["util/"]
    """)
    assert d.nodes["ROOT"].label == "operator/"
    assert d.nodes["UTIL"].label == "util/"


def test_html_entities_in_label():
    d = parse_mermaid("""
    flowchart LR
      A --> DRV["DS: npu-op-driver-&lt;vendor&gt;-&lt;model&gt;"]
    """)
    assert "<vendor>" in d.nodes["DRV"].label
