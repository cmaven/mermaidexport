# ============================================================
# drawio.py: Mermaid → draw.io (mxGraph XML) 범용 변환기
# 상세: Mermaid 코드를 파싱하여 draw.io에서 편집 가능한 XML 생성
# 생성일: 2026-04-07 | 수정일: 2026-05-18
# ============================================================

import base64
import re
import xml.etree.ElementTree as ET
from typing import TypedDict


# ──────────────────────────────────────────────
# TypedDict 정의
# ──────────────────────────────────────────────

class NodeDict(TypedDict):
    id: str
    label: str
    shape: str          # rectangle | rounded | diamond | circle | parallelogram


class EdgeDict(TypedDict):
    source: str
    target: str
    label: str
    style: str          # solid | dashed | thick


class SubgraphDict(TypedDict):
    id: str
    label: str
    nodes: list[str]    # 소속 노드 id 목록


# ──────────────────────────────────────────────
# 파싱 유틸리티
# ──────────────────────────────────────────────

# 노드 선언 패턴: ID[label], ID(label), ID{label}, ID((label)), ID[/label/]
_NODE_PATTERNS: list[tuple[str, str]] = [
    (r'(\w[\w\-]*)\[\(/(.+?)\)/\]', "circle"),           # [( )] 원통(stadium)
    (r'(\w[\w\-]*)\(\((.+?)\)\)',   "circle"),            # (( )) 원
    (r'(\w[\w\-]*)\[/(.+?)/\]',    "parallelogram"),      # [/ /] 평행사변형
    (r'(\w[\w\-]*)\{(.+?)\}',      "diamond"),            # { } 마름모
    (r'(\w[\w\-]*)\((.+?)\)',      "rounded"),             # ( ) 둥근 사각형
    (r'(\w[\w\-]*)\[(.+?)\]',      "rectangle"),          # [ ] 사각형
]

# 엣지 패턴: ==>, -.->. -->  (라벨 포함/미포함)
_EDGE_PATTERNS: list[tuple[str, str]] = [
    (r'(\w[\w\-]*)\s*==(?:>|=[^>]*>)\s*(\w[\w\-]*)',  "thick"),
    (r'(\w[\w\-]*)\s*-\.->\s*(\w[\w\-]*)',             "dashed"),
    (r'(\w[\w\-]*)\s*--+(?:\|([^|]+)\|)?-*>\s*(\w[\w\-]*)', "solid"),
    (r'(\w[\w\-]*)\s*--\s+([^-]+?)\s+-->\s*(\w[\w\-]*)',    "solid"),
]


def _strip_quotes(text: str) -> str:
    """인용부호 제거."""
    text = text.strip()
    if (text.startswith('"') and text.endswith('"')) or \
       (text.startswith("'") and text.endswith("'")):
        return text[1:-1].strip()
    return text


def _is_keyword(token: str) -> bool:
    """Mermaid 예약어 여부 확인."""
    keywords = {
        "graph", "flowchart", "subgraph", "end", "direction",
        "TB", "TD", "LR", "RL", "BT",
        "sequenceDiagram", "participant", "actor",
        "classDiagram", "stateDiagram", "gantt", "pie",
        "erDiagram", "gitGraph", "mindmap",
    }
    return token in keywords


def parse_mermaid_nodes(code: str) -> list[NodeDict]:
    """Mermaid 코드에서 노드 목록을 추출한다.

    Returns:
        id, label, shape 필드를 가진 NodeDict 리스트
    """
    nodes: dict[str, NodeDict] = {}

    for line in code.splitlines():
        line = line.strip()
        if not line or line.startswith("%%"):
            continue

        for pattern, shape in _NODE_PATTERNS:
            for m in re.finditer(pattern, line):
                node_id = m.group(1)
                if _is_keyword(node_id):
                    continue
                label = _strip_quotes(m.group(2))
                if node_id not in nodes:
                    nodes[node_id] = NodeDict(id=node_id, label=label, shape=shape)

        # 라벨 없이 엣지에서만 등장하는 노드도 수집
        for edge_pat, _ in _EDGE_PATTERNS:
            for m in re.finditer(edge_pat, line):
                groups = m.groups()
                src = groups[0]
                tgt = groups[-1]
                for nid in (src, tgt):
                    if nid and not _is_keyword(nid) and nid not in nodes:
                        nodes[nid] = NodeDict(id=nid, label=nid, shape="rectangle")

    return list(nodes.values())


def parse_mermaid_edges(code: str) -> list[EdgeDict]:
    """Mermaid 코드에서 엣지 목록을 추출한다.

    Returns:
        source, target, label, style 필드를 가진 EdgeDict 리스트
    """
    edges: list[EdgeDict] = []
    seen: set[tuple[str, str]] = set()

    for line in code.splitlines():
        line = line.strip()
        if not line or line.startswith("%%"):
            continue

        # thick (==>)
        m = re.search(r'(\w[\w\-]*)\s*=={1,3}>\s*(\w[\w\-]*)', line)
        if m:
            src, tgt = m.group(1), m.group(2)
            if not _is_keyword(src) and not _is_keyword(tgt):
                key = (src, tgt)
                if key not in seen:
                    seen.add(key)
                    edges.append(EdgeDict(source=src, target=tgt, label="", style="thick"))
            continue

        # dashed (-.->)
        m = re.search(r'(\w[\w\-]*)\s*-\.->\s*(\w[\w\-]*)', line)
        if m:
            src, tgt = m.group(1), m.group(2)
            if not _is_keyword(src) and not _is_keyword(tgt):
                key = (src, tgt)
                if key not in seen:
                    seen.add(key)
                    edges.append(EdgeDict(source=src, target=tgt, label="", style="dashed"))
            continue

        # 라벨 포함 solid: A -- text --> B  또는  A -->|text| B
        m = re.search(r'(\w[\w\-]*)\s*--\s+(.+?)\s+-->\s*(\w[\w\-]*)', line)
        if m:
            src, lbl, tgt = m.group(1), m.group(2).strip(), m.group(3)
            if not _is_keyword(src) and not _is_keyword(tgt):
                key = (src, tgt)
                if key not in seen:
                    seen.add(key)
                    edges.append(EdgeDict(source=src, target=tgt, label=lbl, style="solid"))
            continue

        m = re.search(r'(\w[\w\-]*)\s*--+>\|([^|]+)\|\s*(\w[\w\-]*)', line)
        if m:
            src, lbl, tgt = m.group(1), m.group(2).strip(), m.group(3)
            if not _is_keyword(src) and not _is_keyword(tgt):
                key = (src, tgt)
                if key not in seen:
                    seen.add(key)
                    edges.append(EdgeDict(source=src, target=tgt, label=lbl, style="solid"))
            continue

        # 라벨 없는 solid: A --> B
        m = re.search(r'(\w[\w\-]*)\s*--+>\s*(\w[\w\-]*)', line)
        if m:
            src, tgt = m.group(1), m.group(2)
            if not _is_keyword(src) and not _is_keyword(tgt):
                key = (src, tgt)
                if key not in seen:
                    seen.add(key)
                    edges.append(EdgeDict(source=src, target=tgt, label="", style="solid"))

    return edges


def parse_mermaid_subgraphs(code: str) -> list[SubgraphDict]:
    """Mermaid 코드에서 subgraph 목록을 추출한다.

    Returns:
        id, label, nodes 필드를 가진 SubgraphDict 리스트
    """
    subgraphs: list[SubgraphDict] = []
    current: SubgraphDict | None = None

    for line in code.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("%%"):
            continue

        # subgraph 시작
        m = re.match(r'^subgraph\s+(\w[\w\-]*)\s*(?:\[(.+?)\])?', stripped)
        if m:
            sg_id = m.group(1)
            sg_label = _strip_quotes(m.group(2)) if m.group(2) else sg_id
            current = SubgraphDict(id=sg_id, label=sg_label, nodes=[])
            subgraphs.append(current)
            continue

        if stripped == "end":
            current = None
            continue

        if current is not None:
            # 이 라인에서 노드 id 수집
            for pattern, _ in _NODE_PATTERNS:
                for nm in re.finditer(pattern, stripped):
                    nid = nm.group(1)
                    if not _is_keyword(nid) and nid not in current["nodes"]:
                        current["nodes"].append(nid)
            # 엣지에서도 노드 추출
            for edge_pat, _ in _EDGE_PATTERNS:
                for em in re.finditer(edge_pat, stripped):
                    groups = em.groups()
                    for nid in (groups[0], groups[-1]):
                        if nid and not _is_keyword(nid) and nid not in current["nodes"]:
                            current["nodes"].append(nid)

    return subgraphs


# ──────────────────────────────────────────────
# 시퀀스 다이어그램 파싱
# ──────────────────────────────────────────────

class _SeqParticipant(TypedDict):
    id: str
    label: str


class _SeqMessage(TypedDict):
    source: str
    target: str
    label: str
    style: str      # solid | dashed | dotted


def _parse_sequence(code: str) -> tuple[list[_SeqParticipant], list[_SeqMessage]]:
    """sequenceDiagram 코드에서 참여자/메시지를 추출한다."""
    participants: list[_SeqParticipant] = []
    messages: list[_SeqMessage] = []
    seen_p: set[str] = set()

    def _add_participant(pid: str, plabel: str = "") -> None:
        if pid not in seen_p and not _is_keyword(pid):
            seen_p.add(pid)
            participants.append(_SeqParticipant(id=pid, label=plabel or pid))

    for line in code.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("%%"):
            continue

        # participant / actor 선언
        m = re.match(r'^(?:participant|actor)\s+(\w[\w\s\-]*?)(?:\s+as\s+(.+))?$', stripped)
        if m:
            pid = m.group(1).strip()
            plabel = m.group(2).strip() if m.group(2) else pid
            _add_participant(pid, plabel)
            continue

        # 메시지 패턴: A ->> B: text  /  A -->> B: text  / A -> B: text
        m = re.match(
            r'^(\w[\w\s\-]*?)\s*(-{1,2}>>?|->|-->|--x|-x)\s*(\w[\w\s\-]*?)\s*:\s*(.*)$',
            stripped,
        )
        if m:
            src = m.group(1).strip()
            arrow = m.group(2)
            tgt = m.group(3).strip()
            lbl = m.group(4).strip()

            _add_participant(src)
            _add_participant(tgt)

            if "--" in arrow:
                style = "dashed"
            else:
                style = "solid"

            messages.append(_SeqMessage(source=src, target=tgt, label=lbl, style=style))

    return participants, messages


# ──────────────────────────────────────────────
# draw.io XML 스타일 (공통 palette.py 기반)
# ──────────────────────────────────────────────
from converters.palette import NODE_COLORS, SUBGRAPH_COLORS, TEXT_COLOR


def _node_style_for_index(shape: str, color_idx: int) -> str:
    """노드 인덱스에 맞는 draw.io 스타일 문자열을 생성한다."""
    fill, stroke = NODE_COLORS[color_idx % len(NODE_COLORS)]
    base_shapes = {
        "rectangle":    "rounded=1;whiteSpace=wrap;html=1;",
        "rounded":      "rounded=1;arcSize=50;whiteSpace=wrap;html=1;",
        "diamond":      "rhombus;whiteSpace=wrap;html=1;",
        "circle":       "ellipse;whiteSpace=wrap;html=1;",
        "parallelogram": "shape=parallelogram;whiteSpace=wrap;html=1;",
    }
    base = base_shapes.get(shape, base_shapes["rectangle"])
    return f"{base}fillColor={fill};strokeColor={stroke};fontColor={TEXT_COLOR};fontFamily=NanumSquare;fontSize=13;"


def _subgraph_style_for_index(sg_idx: int) -> str:
    """서브그래프 인덱스에 맞는 swimlane 스타일을 생성한다."""
    fill, stroke = SUBGRAPH_COLORS[sg_idx % len(SUBGRAPH_COLORS)]
    return (
        f"swimlane;startSize=30;rounded=1;whiteSpace=wrap;html=1;horizontal=1;collapsible=0;"
        f"fillColor={fill};strokeColor={stroke};fontColor={stroke};"
        f"fontStyle=1;fontFamily=NanumSquare;fontSize=14;"
    )


_STYLE_SOLID_EDGE = (
    "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;"
    "curved=1;jettySize=auto;html=1;fontFamily=NanumSquare;fontSize=11;"
)
_STYLE_DASHED_EDGE = _STYLE_SOLID_EDGE + "dashed=1;dashPattern=8 4;"
_STYLE_THICK_EDGE  = _STYLE_SOLID_EDGE + "strokeWidth=3;"
_STYLE_SEQ_PARTICIPANT = (
    "rounded=1;whiteSpace=wrap;html=1;"
    f"fillColor={NODE_COLORS[0][0]};strokeColor={NODE_COLORS[0][1]};fontColor={TEXT_COLOR};"
    "fontStyle=1;fontSize=12;fontFamily=NanumSquare;"
)
_STYLE_SEQ_LIFELINE = "endArrow=none;dashed=1;strokeColor=#93c5fd;"
_STYLE_SEQ_MSG_SOLID  = "edgeStyle=orthogonalEdgeStyle;orthogonalLoop=1;jettySize=auto;fontFamily=NanumSquare;fontSize=11;"
_STYLE_SEQ_MSG_DASHED = _STYLE_SEQ_MSG_SOLID + "dashed=1;dashPattern=8 4;"


def _shape_to_style(shape: str, color_idx: int = 0) -> str:
    return _node_style_for_index(shape, color_idx)


def _edge_style(style: str) -> str:
    mapping = {
        "solid":  _STYLE_SOLID_EDGE,
        "dashed": _STYLE_DASHED_EDGE,
        "thick":  _STYLE_THICK_EDGE,
    }
    return mapping.get(style, _STYLE_SOLID_EDGE)


# ──────────────────────────────────────────────
# 레이아웃 계산
# ──────────────────────────────────────────────

_NODE_W      = 160
_NODE_H      = 60
_H_SPACING   = 180   # 수평 간격
_V_SPACING   = 100   # 수직 간격
_SG_PADDING  = 50    # 서브그래프 패딩
_SG_HEADER   = 40    # 서브그래프 헤더 높이 (swimlane startSize)
_COLS        = 4     # 열 수 (서브그래프 없을 때)


def _layout_nodes(
    nodes: list[NodeDict],
    subgraphs: list[SubgraphDict],
    direction: str = "TB",
) -> dict[str, tuple[float, float]]:
    """노드별 (x, y) 좌표를 계산한다.

    Args:
        direction: TB/TD (위→아래), LR (왼→오른), 등
    Returns:
        {node_id: (x, y)} 매핑
    """
    positions: dict[str, tuple[float, float]] = {}

    # 서브그래프 소속 맵
    node_to_sg: dict[str, str] = {}
    for sg in subgraphs:
        for nid in sg["nodes"]:
            node_to_sg[nid] = sg["id"]

    # 서브그래프 내부 배치
    sg_offset_x = _SG_PADDING
    sg_offset_y = _SG_PADDING

    for sg in subgraphs:
        member_nodes = [n for n in nodes if n["id"] in sg["nodes"]]
        for local_idx, node in enumerate(member_nodes):
            col = local_idx % _COLS
            row = local_idx // _COLS
            # 서브그래프 내 로컬 좌표 (draw.io swimlane의 자식은 상대좌표)
            lx = _SG_PADDING + col * _H_SPACING
            ly = _SG_HEADER + _SG_PADDING + row * _V_SPACING
            positions[node["id"]] = (lx, ly)

    # 서브그래프 자체 배치 (수평 배치)
    cur_sg_x = 30.0
    for sg in subgraphs:
        member_nodes = [n for n in nodes if n["id"] in sg["nodes"]]
        if not member_nodes:
            continue
        cols_used = min(len(member_nodes), _COLS)
        rows_used = (len(member_nodes) + _COLS - 1) // _COLS
        sg_w = cols_used * _H_SPACING + _SG_PADDING
        sg_h = _SG_HEADER + rows_used * _V_SPACING + _SG_PADDING
        # sg 위치는 _layout_subgraphs() 에서 처리; 여기선 플레이스홀더
        positions[f"__sg__{sg['id']}"] = (cur_sg_x, 30.0)
        positions[f"__sg__{sg['id']}__size"] = (sg_w, sg_h)
        cur_sg_x += sg_w + _H_SPACING

    # 서브그래프 미소속 노드 배치
    standalone = [n for n in nodes if n["id"] not in node_to_sg]
    base_x = 30.0
    base_y = 30.0

    if subgraphs:
        # 서브그래프 아래에 배치
        max_sg_y = 0.0
        for sg in subgraphs:
            size_key = f"__sg__{sg['id']}__size"
            if size_key in positions:
                _, h = positions[size_key]
                sg_y = positions[f"__sg__{sg['id']}"][1]
                max_sg_y = max(max_sg_y, sg_y + h)
        base_y = max_sg_y + _V_SPACING

    for idx, node in enumerate(standalone):
        col = idx % _COLS
        row = idx // _COLS
        x = base_x + col * _H_SPACING
        y = base_y + row * _V_SPACING
        positions[node["id"]] = (x, y)

    return positions


# ──────────────────────────────────────────────
# XML 빌더 헬퍼
# ──────────────────────────────────────────────

import itertools as _itertools


def _make_id_gen() -> callable:
    """요청별 로컬 ID 생성기를 반환한다 (동시성 안전)."""
    counter = _itertools.count(3)  # 0,1 은 root/default 예약, 2부터 시작
    return lambda: str(next(counter))


def _make_root_cells(root: ET.Element) -> None:
    """mxGraph 필수 루트 셀 (id=0, id=1) 생성."""
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")


def _add_node_cell(
    root: ET.Element,
    node_id: str,
    label: str,
    style: str,
    x: float,
    y: float,
    w: float = _NODE_W,
    h: float = _NODE_H,
    parent: str = "1",
    next_id: callable = None,
) -> str:
    """노드 mxCell을 추가하고 cell id를 반환한다."""
    cid = next_id()
    cell = ET.SubElement(root, "mxCell",
        id=cid,
        value=label,
        style=style,
        vertex="1",
        parent=parent,
    )
    ET.SubElement(cell, "mxGeometry",
        x=str(x), y=str(y), width=str(w), height=str(h),
        **{"as": "geometry"},
    )
    return cid


def _add_edge_cell(
    root: ET.Element,
    source_cid: str,
    target_cid: str,
    label: str,
    style: str,
    parent: str = "1",
    next_id: callable = None,
    waypoints: list[tuple[float, float]] | None = None,
) -> str:
    """엣지 mxCell을 추가하고 cell id를 반환한다.

    waypoints: [(x_px, y_px), ...] — SVG/layout 경유점을 mxGeometry Array[points]로 삽입.
               첫 점(source)과 마지막 점(target)은 제외하고 중간 경유점만 포함.
    """
    cid = next_id()
    cell = ET.SubElement(root, "mxCell",
        id=cid,
        value=label,
        style=style,
        edge="1",
        source=source_cid,
        target=target_cid,
        parent=parent,
    )
    geo = ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})
    # SVG path 경유점 → draw.io mxGeometry Array[points]
    mid_pts = waypoints[1:-1] if waypoints and len(waypoints) > 2 else []
    if mid_pts:
        arr = ET.SubElement(geo, "Array", **{"as": "points"})
        for px, py in mid_pts:
            ET.SubElement(arr, "mxPoint", x=f"{px:.1f}", y=f"{py:.1f}")
    return cid


# ──────────────────────────────────────────────
# flowchart / graph 변환 핵심 로직
# ──────────────────────────────────────────────

def _detect_direction(code: str) -> str:
    """graph/flowchart 방향 지시어 감지."""
    m = re.search(r'(?:graph|flowchart)\s+(TB|TD|LR|RL|BT)', code)
    if m:
        return m.group(1)
    return "TB"


def _build_flowchart_xml(
    nodes: list[NodeDict],
    edges: list[EdgeDict],
    subgraphs: list[SubgraphDict],
    direction: str,
    title: str,
    mermaid_code: str = "",
) -> str:
    """flowchart/graph 다이어그램 XML을 생성한다."""
    # 새 레이아웃 엔진(mmdc SVG) 시도 — 성공 시 dagre 좌표 그대로 사용
    if mermaid_code:
        try:
            from converters.layout_engine import compute_layout_via_mmdc
            layout = compute_layout_via_mmdc(
                mermaid_code,
                target_w_in=13.0,
                target_h_in=8.0,
                puppeteer_config="/app/backend/puppeteer-config.json",
            )
        except Exception:
            layout = None
        if layout is not None and layout.nodes:
            return _build_flowchart_xml_from_layout(layout, edges, title)

    next_id = _make_id_gen()

    # XML 트리 구성
    mxfile = ET.Element("mxfile", host="drawio.py", version="21.0.0")
    diagram = ET.SubElement(mxfile, "diagram",
        id="diagram-1",
        name=title or "Diagram",
    )
    mxgraph_model = ET.SubElement(diagram, "mxGraphModel",
        dx="1422", dy="762", grid="1", gridSize="10",
        guides="1", tooltips="1", connect="1", arrows="1",
        fold="1", page="1", pageScale="1",
        pageWidth="1600", pageHeight="900",
        math="0", shadow="0",
    )
    root = ET.SubElement(mxgraph_model, "root")
    _make_root_cells(root)

    # 레이아웃 계산
    positions = _layout_nodes(nodes, subgraphs, direction)

    # node_id → cell_id 맵핑
    id_map: dict[str, str] = {}

    # 서브그래프 컨테이너 추가
    sg_id_map: dict[str, str] = {}
    sg_offset_map: dict[str, tuple[float, float]] = {}  # sg_id → (x, y)

    cur_sg_x = 30.0
    for sg in subgraphs:
        member_nodes = [n for n in nodes if n["id"] in sg["nodes"]]
        if not member_nodes:
            continue

        cols_used = min(len(member_nodes), _COLS)
        rows_used = (len(member_nodes) + _COLS - 1) // _COLS
        sg_w = cols_used * _H_SPACING + _SG_PADDING * 2
        sg_h = _SG_HEADER + rows_used * _V_SPACING + _SG_PADDING * 2

        sg_cid = next_id()
        sg_cell = ET.SubElement(root, "mxCell",
            id=sg_cid,
            value=sg["label"],
            style=_subgraph_style_for_index(subgraphs.index(sg)),
            vertex="1",
            parent="1",
        )
        ET.SubElement(sg_cell, "mxGeometry",
            x=str(cur_sg_x), y="30",
            width=str(sg_w), height=str(sg_h),
            **{"as": "geometry"},
        )
        sg_id_map[sg["id"]] = sg_cid
        sg_offset_map[sg["id"]] = (cur_sg_x, 30.0)
        cur_sg_x += sg_w + _H_SPACING

    # 서브그래프 미소속 노드의 base_y 결정
    base_y_standalone = 30.0
    if subgraphs:
        max_bottom = 0.0
        for sg in subgraphs:
            member_nodes = [n for n in nodes if n["id"] in sg["nodes"]]
            if not member_nodes:
                continue
            rows_used = (len(member_nodes) + _COLS - 1) // _COLS
            sg_h = _SG_HEADER + rows_used * _V_SPACING + _SG_PADDING * 2
            max_bottom = max(max_bottom, 30.0 + sg_h)
        base_y_standalone = max_bottom + _V_SPACING

    # 서브그래프 소속 노드 맵
    node_to_sg: dict[str, str] = {}
    for sg in subgraphs:
        for nid in sg["nodes"]:
            node_to_sg[nid] = sg["id"]

    # 노드 추가
    standalone_nodes = [n for n in nodes if n["id"] not in node_to_sg]
    sg_local_idx: dict[str, int] = {sg["id"]: 0 for sg in subgraphs}

    for node_idx, node in enumerate(nodes):
        style = _shape_to_style(node["shape"], node_idx)
        w = _NODE_W
        h = _NODE_H if node["shape"] != "diamond" else 70

        if node["id"] in node_to_sg:
            sg_id = node_to_sg[node["id"]]
            sg_parent_cid = sg_id_map.get(sg_id, "1")
            idx = sg_local_idx[sg_id]
            sg_local_idx[sg_id] += 1
            col = idx % _COLS
            row = idx // _COLS
            lx = _SG_PADDING + col * _H_SPACING
            ly = _SG_HEADER + _SG_PADDING + row * _V_SPACING
            cid = _add_node_cell(root, node["id"], node["label"], style,
                                  lx, ly, w, h, parent=sg_parent_cid, next_id=next_id)
        else:
            idx = standalone_nodes.index(node)
            col = idx % _COLS
            row = idx // _COLS
            x = 30.0 + col * _H_SPACING
            y = base_y_standalone + row * _V_SPACING
            cid = _add_node_cell(root, node["id"], node["label"], style,
                                  x, y, w, h, parent="1", next_id=next_id)

        id_map[node["id"]] = cid

    # 엣지 추가
    for edge in edges:
        src_cid = id_map.get(edge["source"])
        tgt_cid = id_map.get(edge["target"])
        if src_cid is None or tgt_cid is None:
            continue

        # 엣지 parent: 두 노드가 같은 서브그래프에 있으면 해당 서브그래프
        src_sg = node_to_sg.get(edge["source"])
        tgt_sg = node_to_sg.get(edge["target"])
        if src_sg and src_sg == tgt_sg:
            edge_parent = sg_id_map.get(src_sg, "1")
        else:
            edge_parent = "1"

        _add_edge_cell(root, src_cid, tgt_cid,
                       edge["label"], _edge_style(edge["style"]),
                       parent=edge_parent, next_id=next_id)

    # XML 직렬화
    return _serialize_xml(mxfile)


# ──────────────────────────────────────────────
# 시퀀스 다이어그램 변환 로직
# ──────────────────────────────────────────────

_SEQ_P_W      = 140
_SEQ_P_H      = 50
_SEQ_H_GAP    = 200   # 참여자 간 수평 간격
_SEQ_MSG_H    = 60    # 메시지 간 수직 간격
_SEQ_START_X  = 40
_SEQ_START_Y  = 40
_SEQ_LIFELINE_TOP = _SEQ_START_Y + _SEQ_P_H


def _build_sequence_xml(
    participants: list[_SeqParticipant],
    messages: list[_SeqMessage],
    title: str,
) -> str:
    """sequenceDiagram XML을 생성한다."""
    next_id = _make_id_gen()

    mxfile = ET.Element("mxfile", host="drawio.py", version="21.0.0")
    diagram = ET.SubElement(mxfile, "diagram",
        id="diagram-1",
        name=title or "Sequence",
    )
    mxgraph_model = ET.SubElement(diagram, "mxGraphModel",
        dx="1422", dy="762", grid="1", gridSize="10",
        guides="1", tooltips="1", connect="1", arrows="1",
        fold="1", page="1", pageScale="1",
        pageWidth="1600", pageHeight="900",
        math="0", shadow="0",
    )
    root = ET.SubElement(mxgraph_model, "root")
    _make_root_cells(root)

    lifeline_total_h = len(messages) * _SEQ_MSG_H + _SEQ_MSG_H

    # 참여자 박스 + 생명선
    p_center_x: dict[str, float] = {}
    for i, p in enumerate(participants):
        px = _SEQ_START_X + i * _SEQ_H_GAP
        cx = px + _SEQ_P_W / 2
        p_center_x[p["id"]] = cx

        # 참여자 박스 (인덱스 기반 팔레트 색상 적용)
        p_style = _node_style_for_index("rectangle", i)
        p_style += "fontStyle=1;fontSize=12;"
        cid = next_id()
        cell = ET.SubElement(root, "mxCell",
            id=cid, value=p["label"],
            style=p_style,
            vertex="1", parent="1",
        )
        ET.SubElement(cell, "mxGeometry",
            x=str(px), y=str(_SEQ_START_Y),
            width=str(_SEQ_P_W), height=str(_SEQ_P_H),
            **{"as": "geometry"},
        )

        # 생명선 (수직 점선) - 참여자 stroke 색상 매칭
        _, stroke = NODE_COLORS[i % len(NODE_COLORS)]
        lifeline_style = f"endArrow=none;dashed=1;strokeColor={stroke};opacity=50;"
        ll_cid = next_id()
        ll_cell = ET.SubElement(root, "mxCell",
            id=ll_cid, value="",
            style=lifeline_style,
            edge="1", parent="1",
        )
        geo = ET.SubElement(ll_cell, "mxGeometry",
            relative="1", **{"as": "geometry"},
        )
        src_pt = ET.SubElement(geo, "mxPoint",
            x=str(cx), y=str(_SEQ_LIFELINE_TOP),
            **{"as": "sourcePoint"},
        )
        tgt_pt = ET.SubElement(geo, "mxPoint",
            x=str(cx), y=str(_SEQ_LIFELINE_TOP + lifeline_total_h),
            **{"as": "targetPoint"},
        )

    # 메시지 화살표
    for idx, msg in enumerate(messages):
        my = _SEQ_LIFELINE_TOP + (idx + 1) * _SEQ_MSG_H
        sx = p_center_x.get(msg["source"], 0.0)
        tx = p_center_x.get(msg["target"], 0.0)

        style = _STYLE_SEQ_MSG_SOLID if msg["style"] == "solid" else _STYLE_SEQ_MSG_DASHED
        style += "exitX=0.5;exitY=0.5;exitDx=0;exitDy=0;entryX=0.5;entryY=0.5;entryDx=0;entryDy=0;"

        msg_cid = next_id()
        msg_cell = ET.SubElement(root, "mxCell",
            id=msg_cid, value=msg["label"],
            style=style,
            edge="1", parent="1",
        )
        geo = ET.SubElement(msg_cell, "mxGeometry",
            relative="1", **{"as": "geometry"},
        )
        ET.SubElement(geo, "mxPoint",
            x=str(sx), y=str(my), **{"as": "sourcePoint"})
        ET.SubElement(geo, "mxPoint",
            x=str(tx), y=str(my), **{"as": "targetPoint"})

    return _serialize_xml(mxfile)


# ──────────────────────────────────────────────
# XML 직렬화
# ──────────────────────────────────────────────

def _serialize_xml(root_el: ET.Element) -> str:
    """ET.Element를 들여쓰기 포함 XML 문자열로 변환한다."""
    ET.indent(root_el, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        root_el, encoding="unicode", xml_declaration=False
    )


# ──────────────────────────────────────────────
# mmdc SVG 기반 레이아웃 → draw.io XML (신규)
# ──────────────────────────────────────────────

# inch → draw.io 픽셀 환산 (1 inch = 96 px). 표준 CSS 픽셀과 일치.
_INCH_TO_PX = 96.0
_DRAWIO_PADDING = 20.0   # 페이지 좌상단 여백 (px)
# swimlane 헤더 높이 (draw.io 기본 startSize=30)
_SWIMLANE_HEADER_PX = 30.0


def _build_flowchart_xml_from_layout(layout, edges: list, title: str) -> str:
    """layout_engine 결과(LayoutResult)와 파싱된 edges를 받아 draw.io XML 생성.

    - 노드 좌표/크기는 layout 결과 그대로 사용
    - 서브그래프(클러스터)는 swimlane으로 렌더, 자식 노드는 상대좌표
    - 엣지 라우팅은 draw.io의 orthogonalEdgeStyle에 위임
    """
    next_id = _make_id_gen()

    mxfile = ET.Element("mxfile", host="drawio.py", version="21.0.0")
    diagram = ET.SubElement(mxfile, "diagram", id="diagram-1", name=title or "Diagram")

    page_w = int(layout.canvas_w * _INCH_TO_PX + _DRAWIO_PADDING * 2)
    page_h = int(layout.canvas_h * _INCH_TO_PX + _DRAWIO_PADDING * 2)
    mxgraph_model = ET.SubElement(
        diagram, "mxGraphModel",
        dx="1422", dy="762", grid="1", gridSize="10",
        guides="1", tooltips="1", connect="1", arrows="1",
        fold="1", page="1", pageScale="1",
        pageWidth=str(max(page_w, 1200)),
        pageHeight=str(max(page_h, 700)),
        math="0", shadow="0",
    )
    root = ET.SubElement(mxgraph_model, "root")
    _make_root_cells(root)

    # 1) 클러스터(swimlane) 추가 + id 매핑
    sg_cid_map: dict[str, str] = {}
    sg_pos_map: dict[str, tuple[float, float]] = {}   # cluster_id → (abs_x_px, abs_y_px)
    cluster_idx_map: dict[str, int] = {cl.id: i for i, cl in enumerate(layout.clusters)}

    for idx, cl in enumerate(layout.clusters):
        ax = _DRAWIO_PADDING + cl.x * _INCH_TO_PX
        ay = _DRAWIO_PADDING + cl.y * _INCH_TO_PX
        aw = cl.w * _INCH_TO_PX
        ah = cl.h * _INCH_TO_PX
        cid = next_id()
        cell = ET.SubElement(
            root, "mxCell",
            id=cid, value=cl.label,
            style=_subgraph_style_for_index(idx),
            vertex="1", parent="1",
        )
        ET.SubElement(
            cell, "mxGeometry",
            x=f"{ax:.1f}", y=f"{ay:.1f}",
            width=f"{aw:.1f}", height=f"{ah:.1f}",
            **{"as": "geometry"},
        )
        sg_cid_map[cl.id] = cid
        sg_pos_map[cl.id] = (ax, ay)

    # 2) 노드 추가 (cluster_id 있으면 swimlane 자식 + 상대좌표)
    node_cid_map: dict[str, str] = {}
    node_idx_counter = 0
    for nid, node in layout.nodes.items():
        # 팔레트 인덱스: 클러스터 단위 우선
        if node.cluster_id and node.cluster_id in cluster_idx_map:
            palette_idx = cluster_idx_map[node.cluster_id]
        else:
            palette_idx = node_idx_counter
        node_idx_counter += 1

        style = _node_style_for_index(node.shape, palette_idx)
        nw = node.w * _INCH_TO_PX
        nh = node.h * _INCH_TO_PX

        if node.cluster_id and node.cluster_id in sg_cid_map:
            parent_cid = sg_cid_map[node.cluster_id]
            sg_ax, sg_ay = sg_pos_map[node.cluster_id]
            # swimlane 내부 상대좌표: header 영역 보정
            abs_nx = _DRAWIO_PADDING + node.x * _INCH_TO_PX
            abs_ny = _DRAWIO_PADDING + node.y * _INCH_TO_PX
            rel_x = abs_nx - sg_ax
            rel_y = abs_ny - sg_ay
            # swimlane 자식은 헤더 아래 영역에 두는 게 시각적으로 자연스러움
            # (이미 SVG 좌표가 헤더 안쪽이지만, 음수 되지 않게 보장)
            rel_x = max(rel_x, 5.0)
            rel_y = max(rel_y, _SWIMLANE_HEADER_PX + 5.0)
            cid = next_id()
            cell = ET.SubElement(
                root, "mxCell",
                id=cid, value=node.label, style=style,
                vertex="1", parent=parent_cid,
            )
            ET.SubElement(
                cell, "mxGeometry",
                x=f"{rel_x:.1f}", y=f"{rel_y:.1f}",
                width=f"{nw:.1f}", height=f"{nh:.1f}",
                **{"as": "geometry"},
            )
        else:
            ax = _DRAWIO_PADDING + node.x * _INCH_TO_PX
            ay = _DRAWIO_PADDING + node.y * _INCH_TO_PX
            cid = next_id()
            cell = ET.SubElement(
                root, "mxCell",
                id=cid, value=node.label, style=style,
                vertex="1", parent="1",
            )
            ET.SubElement(
                cell, "mxGeometry",
                x=f"{ax:.1f}", y=f"{ay:.1f}",
                width=f"{nw:.1f}", height=f"{nh:.1f}",
                **{"as": "geometry"},
            )
        node_cid_map[nid] = cid

    # 3) 엣지: layout.edges가 있으면 그것을 우선, 없으면 인자로 받은 edges 사용.
    #         라우팅은 draw.io에 위임 (orthogonalEdgeStyle).
    if layout.edges:
        for le in layout.edges:
            src_cid = node_cid_map.get(le.source)
            tgt_cid = node_cid_map.get(le.target)
            if not src_cid or not tgt_cid:
                continue
            style = _STYLE_DASHED_EDGE if le.dashed else _STYLE_SOLID_EDGE
            # le.points는 inches 단위 — px로 변환 후 waypoints 삽입
            wpts = [
                (_DRAWIO_PADDING + x * _INCH_TO_PX, _DRAWIO_PADDING + y * _INCH_TO_PX)
                for x, y in le.points
            ] if le.points else None
            _add_edge_cell(
                root, src_cid, tgt_cid, le.label, style,
                parent="1", next_id=next_id, waypoints=wpts,
            )
    else:
        for edge in edges:
            src_cid = node_cid_map.get(edge["source"])
            tgt_cid = node_cid_map.get(edge["target"])
            if not src_cid or not tgt_cid:
                continue
            _add_edge_cell(
                root, src_cid, tgt_cid, edge["label"], _edge_style(edge["style"]),
                parent="1", next_id=next_id,
            )

    return _serialize_xml(mxfile)


# ──────────────────────────────────────────────
# ER 다이어그램 전용 빌더 (layout 경로 + PNG 폴백)
# ──────────────────────────────────────────────

def _build_er_xml_from_layout(layout, title: str) -> str:
    """ER 다이어그램 LayoutResult → draw.io XML.

    클러스터(subgraph) 없음. 노드 label의 \\n을 <br>로 치환하여
    html=1 draw.io 셀에서 멀티라인 attribute 행이 표시되도록 한다.
    """
    next_id = _make_id_gen()

    mxfile = ET.Element("mxfile", host="drawio.py", version="21.0.0")
    diagram = ET.SubElement(mxfile, "diagram", id="diagram-1", name=title or "ER Diagram")

    page_w = int(layout.canvas_w * _INCH_TO_PX + _DRAWIO_PADDING * 2)
    page_h = int(layout.canvas_h * _INCH_TO_PX + _DRAWIO_PADDING * 2)
    mxgraph_model = ET.SubElement(
        diagram, "mxGraphModel",
        dx="1422", dy="762", grid="1", gridSize="10",
        guides="1", tooltips="1", connect="1", arrows="1",
        fold="1", page="1", pageScale="1",
        pageWidth=str(max(page_w, 1200)),
        pageHeight=str(max(page_h, 700)),
        math="0", shadow="0",
    )
    root = ET.SubElement(mxgraph_model, "root")
    _make_root_cells(root)

    # 노드 추가 (ER에는 cluster 없음 → 모두 parent="1")
    node_cid_map: dict[str, str] = {}
    for idx, (nid, node) in enumerate(layout.nodes.items()):
        # ER 노드 스타일: 기본 팔레트 + 멀티라인 상단 정렬
        style = _node_style_for_index(node.shape, idx)
        style += "verticalAlign=top;align=left;"

        ax = _DRAWIO_PADDING + node.x * _INCH_TO_PX
        ay = _DRAWIO_PADDING + node.y * _INCH_TO_PX
        nw = node.w * _INCH_TO_PX
        nh = node.h * _INCH_TO_PX

        cid = next_id()
        cell = ET.SubElement(
            root, "mxCell",
            id=cid,
            value=node.label.replace('\n', '<br>'),   # html=1 환경에서 줄바꿈
            style=style,
            vertex="1", parent="1",
        )
        ET.SubElement(
            cell, "mxGeometry",
            x=f"{ax:.1f}", y=f"{ay:.1f}",
            width=f"{nw:.1f}", height=f"{nh:.1f}",
            **{"as": "geometry"},
        )
        node_cid_map[nid] = cid

    # 엣지 추가 (cardinality 정보는 le.label에 이미 포함)
    for le in layout.edges:
        src_cid = node_cid_map.get(le.source)
        tgt_cid = node_cid_map.get(le.target)
        if not src_cid or not tgt_cid:
            continue
        edge_style = _STYLE_DASHED_EDGE if le.dashed else _STYLE_SOLID_EDGE
        wpts = [
            (_DRAWIO_PADDING + x * _INCH_TO_PX, _DRAWIO_PADDING + y * _INCH_TO_PX)
            for x, y in le.points
        ] if le.points else None
        _add_edge_cell(
            root, src_cid, tgt_cid, le.label, edge_style,
            parent="1", next_id=next_id, waypoints=wpts,
        )

    return _serialize_xml(mxfile)


def _build_png_embed_xml(mermaid_code: str, title: str) -> str:
    """mmdc PNG를 base64 임베드한 단일 이미지 셀 draw.io XML 반환.

    ER SVG 파싱이 실패(노드 0개 또는 예외)할 때 사용하는 폴백 경로.
    draw.io에서 편집은 불가하나 시각 정확성은 mmdc 렌더와 동일.
    """
    from converters.png import _png_via_mmdc  # 지연 임포트 (순환 방지)
    try:
        png_bytes = _png_via_mmdc(mermaid_code)
    except Exception as exc:
        raise ValueError(
            f"ER 다이어그램 draw.io 변환 실패 (layout 파싱 + PNG 폴백 모두 실패): {exc}"
        ) from exc

    b64 = base64.b64encode(png_bytes).decode("ascii")

    next_id = _make_id_gen()
    mxfile = ET.Element("mxfile", host="drawio.py", version="21.0.0")
    diagram = ET.SubElement(mxfile, "diagram", id="diagram-1", name=title or "ER Diagram")
    mxgraph_model = ET.SubElement(
        diagram, "mxGraphModel",
        dx="1422", dy="762", grid="1", gridSize="10",
        guides="1", tooltips="1", connect="1", arrows="1",
        fold="1", page="1", pageScale="1",
        pageWidth="1600", pageHeight="900",
        math="0", shadow="0",
    )
    root_el = ET.SubElement(mxgraph_model, "root")
    _make_root_cells(root_el)

    img_style = (
        "shape=image;verticalLabelPosition=bottom;labelBackgroundColor=none;"
        "verticalAlign=top;imageAspect=1;"
        f"image=data:image/png;base64,{b64};"
    )
    cid = next_id()
    cell = ET.SubElement(
        root_el, "mxCell",
        id=cid, value="",
        style=img_style,
        vertex="1", parent="1",
    )
    ET.SubElement(
        cell, "mxGeometry",
        x="20", y="20", width="1200", height="700",
        **{"as": "geometry"},
    )
    return _serialize_xml(mxfile)


# ──────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────

def mermaid_to_drawio(mermaid_code: str, title: str = "") -> str:
    """Mermaid 코드를 draw.io (mxGraph XML) 형식으로 변환한다.

    Args:
        mermaid_code: 변환할 Mermaid 코드 문자열
        title:        다이어그램 탭 이름 (기본값 빈 문자열)

    Returns:
        완전한 .drawio 파일 내용(XML 문자열)

    Raises:
        ValueError: 지원하지 않는 다이어그램 타입일 때

    Examples:
        >>> xml = mermaid_to_drawio("graph LR\\n  A[Start] --> B[End]")
        >>> "<mxfile" in xml
        True
    """
    if not mermaid_code or not mermaid_code.strip():
        raise ValueError("mermaid_code가 비어 있습니다.")

    code = mermaid_code.strip()
    # `%% source: ...` 같은 주석을 건너뛰고 첫 다이어그램 지시문 추출
    from converters.palette import first_diagram_directive
    directive = first_diagram_directive(code)

    # 시퀀스 다이어그램 분기
    if directive.startswith("sequencediagram"):
        participants, messages = _parse_sequence(code)
        return _build_sequence_xml(participants, messages, title)

    # flowchart / graph 분기
    if directive.startswith("graph") or directive.startswith("flowchart"):
        direction = _detect_direction(code)
        nodes      = parse_mermaid_nodes(code)
        edges      = parse_mermaid_edges(code)
        subgraphs  = parse_mermaid_subgraphs(code)
        return _build_flowchart_xml(nodes, edges, subgraphs, direction, title, mermaid_code=code)

    # erDiagram 분기 — layout_engine SVG 파서 사용 + PNG 임베드 폴백
    if directive.startswith("erdiagram"):
        try:
            from converters.layout_engine import compute_layout_via_mmdc
            layout = compute_layout_via_mmdc(
                code,
                target_w_in=13.0,
                target_h_in=8.0,
                puppeteer_config="/app/backend/puppeteer-config.json",
            )
        except Exception:
            layout = None
        if layout is not None and layout.nodes:
            # 손상 레이아웃 감지: 엣지가 있는데 노드 매칭이 없으면 PNG 폴백
            if layout.edges:
                node_ids = set(layout.nodes.keys())
                matched = sum(
                    1 for e in layout.edges
                    if e.source in node_ids and e.target in node_ids
                )
                if matched == 0:
                    return _build_png_embed_xml(code, title)
            return _build_er_xml_from_layout(layout, title)
        # 폴백: mmdc PNG base64 임베드
        return _build_png_embed_xml(code, title)

    # 기타 타입: 노드/엣지 파싱만 시도
    nodes     = parse_mermaid_nodes(code)
    edges     = parse_mermaid_edges(code)
    subgraphs = parse_mermaid_subgraphs(code)

    if not nodes and not edges:
        raise ValueError(
            f"지원하지 않는 Mermaid 다이어그램 타입이거나 파싱 가능한 내용이 없습니다: "
            f"{first_line!r}"
        )

    return _build_flowchart_xml(nodes, edges, subgraphs, "TB", title, mermaid_code=code)
