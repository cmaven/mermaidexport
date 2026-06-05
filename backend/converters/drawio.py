# ============================================================
# drawio.py: Mermaid → draw.io (mxGraph XML) 범용 변환기
# 상세: Mermaid 코드를 파싱하여 draw.io에서 편집 가능한 XML 생성
# 생성일: 2026-04-07 | 수정일: 2026-04-07
# ============================================================

import re
import xml.etree.ElementTree as ET
from typing import TypedDict

from converters.text_metrics import estimate_text_size_px


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


def _parse_sequence_events(code: str) -> list[dict]:
    """sequenceDiagram 코드를 순서 보존 이벤트 리스트로 파싱한다.

    제어 프레임(alt/opt/loop/par/critical/break/rect), else, end, Note,
    메시지를 등장 순서대로 보존한다. 공유 파서 — pptx/excalidraw가 import.

    이벤트 dict 스키마:
        {"kind":"msg","src":str,"dst":str,"label":str,"dashed":bool}
        {"kind":"note","pos":"over"|"left of"|"right of","actors":[str,...],"text":str}
        {"kind":"group_start","type":str,"label":str}
        {"kind":"group_else","label":str}
        {"kind":"group_end"}
    """
    events: list[dict] = []

    def _clean(text: str) -> str:
        return re.sub(r'<br\s*/?>', ' ', text).strip()

    msg_re   = re.compile(r'^(\w+)\s*(-?->>?|--?>|-->>|->>)\s*(\w+)\s*:\s*(.+)$')
    note_re  = re.compile(r'^Note\s+(over|left of|right of)\s+([^:]+):\s*(.+)$')
    group_re = re.compile(r'^(alt|opt|loop|par|critical|break|rect)\b\s*(.*)$')
    else_re  = re.compile(r'^else\b\s*(.*)$')
    end_re   = re.compile(r'^end\s*$')

    for raw in code.splitlines():
        line = raw.strip()
        if not line or line.startswith("%%") or line == "sequenceDiagram":
            continue
        # participant/actor 선언은 이벤트로 만들지 않음
        if re.match(r'^(?:participant|actor)\b', line):
            continue

        m = note_re.match(line)
        if m:
            actors = [a.strip() for a in m.group(2).split(",") if a.strip()]
            events.append({
                "kind": "note", "pos": m.group(1),
                "actors": actors, "text": _clean(m.group(3)),
            })
            continue

        m = msg_re.match(line)
        if m:
            arrow = m.group(2)
            events.append({
                "kind": "msg", "src": m.group(1), "dst": m.group(3),
                "label": _clean(m.group(4)), "dashed": "--" in arrow,
            })
            continue

        m = else_re.match(line)
        if m:
            events.append({"kind": "group_else", "label": _clean(m.group(1))})
            continue

        if end_re.match(line):
            events.append({"kind": "group_end"})
            continue

        m = group_re.match(line)
        if m:
            events.append({
                "kind": "group_start", "type": m.group(1), "label": _clean(m.group(2)),
            })
            continue

    return events


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
    "edgeStyle=orthogonalEdgeStyle;curved=0;rounded=0;"
    "orthogonalLoop=1;jettySize=auto;html=1;"
    "fontFamily=NanumSquare;fontSize=11;"
)
_STYLE_DASHED_EDGE = _STYLE_SOLID_EDGE + "dashed=1;dashPattern=8 4;"
_STYLE_THICK_EDGE  = _STYLE_SOLID_EDGE + "strokeWidth=3;"
_STYLE_SEQ_PARTICIPANT = (
    "rounded=1;whiteSpace=wrap;html=1;"
    f"fillColor={NODE_COLORS[0][0]};strokeColor={NODE_COLORS[0][1]};fontColor={TEXT_COLOR};"
    "fontStyle=1;fontSize=12;fontFamily=NanumSquare;"
)
_STYLE_SEQ_LIFELINE = "endArrow=none;dashed=1;strokeColor=#93c5fd;"
_STYLE_SEQ_MSG_SOLID  = "edgeStyle=orthogonalEdgeStyle;curved=0;rounded=0;orthogonalLoop=1;jettySize=auto;fontFamily=NanumSquare;fontSize=11;"
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


def _edge_anchor(
    src_abs: tuple,
    src_size: tuple,
    tgt_abs: tuple,
    tgt_size: tuple,
) -> tuple:
    """두 노드의 절대좌표 + 크기로 orthogonal 엣지 exit/entry 앵커를 계산한다.

    Returns:
        (exitX, exitY, entryX, entryY)
        수평 이동이 지배적이면 좌우 연결, 수직이면 상하 연결.
    """
    sx, sy = src_abs
    sw, sh = src_size
    tx, ty = tgt_abs
    tw, th = tgt_size
    dx = (tx + tw / 2.0) - (sx + sw / 2.0)
    dy = (ty + th / 2.0) - (sy + sh / 2.0)
    if abs(dx) > abs(dy):
        # 좌우 이동
        return (1.0, 0.5, 0.0, 0.5) if dx > 0 else (0.0, 0.5, 1.0, 0.5)
    # 상하 이동
    return (0.5, 1.0, 0.5, 0.0) if dy > 0 else (0.5, 0.0, 0.5, 1.0)


def _make_abs_pos(
    node_to_sg: dict,
    sg_offset_map: dict,
    local_positions: dict,
    standalone_positions: dict,
):
    """절대좌표 조회 클로저를 반환한다.

    서브그래프 소속 노드: 서브그래프 origin + 로컬 좌표
    독립 노드: standalone_positions 에서 직접 반환
    """
    def _abs_pos(nid: str) -> tuple:
        sg = node_to_sg.get(nid)
        if sg and sg in sg_offset_map:
            ox, oy = sg_offset_map[sg]
            lx, ly = local_positions[nid]
            return (ox + lx, oy + ly)
        return standalone_positions[nid]

    return _abs_pos


# ──────────────────────────────────────────────
# 레이아웃 계산
# ──────────────────────────────────────────────

_NODE_W      = 160   # fallback 고정 폭 (text_metrics 미사용 시)
_NODE_H      = 60    # fallback 고정 높이 (text_metrics 미사용 시)
_H_SPACING   = 180   # 서브그래프 간 수평 간격
_V_SPACING   = 100   # 서브그래프 아래 독립 노드 수직 오프셋
_H_GAP       = 30    # 그리드 내 노드 간 수평 여백
_V_GAP       = 20    # 그리드 내 노드 간 수직 여백
_SG_PADDING  = 50    # 서브그래프 패딩
_SG_HEADER   = 40    # 서브그래프 헤더 높이 (swimlane startSize)
_COLS        = 4     # 그리드 열 수


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
    w: float,
    h: float,
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
) -> str:
    """엣지 mxCell을 추가하고 cell id를 반환한다."""
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
    ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})
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
) -> str:
    """flowchart/graph 다이어그램 XML을 생성한다."""
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

    # ── [A] 노드 크기 사전 계산 (text_metrics 기반) ──────────────────
    node_sizes: dict[str, tuple] = {}
    for node in nodes:
        w_px, h_px = estimate_text_size_px(node["label"], font_size_px=13)
        w = max(120, w_px)
        h = max(50, h_px)
        if node["shape"] == "diamond":
            w, h = int(w * 1.15), int(h * 1.25)
        node_sizes[node["id"]] = (w, h)

    # 서브그래프 소속 맵
    node_to_sg: dict[str, str] = {}
    for sg in subgraphs:
        for nid in sg["nodes"]:
            node_to_sg[nid] = sg["id"]

    # ── [A] 서브그래프별 그리드 레이아웃 사전 계산 ───────────────────
    # 보강 권고 #2: 열별 max(node.w), 행별 max(node.h) 기반 좌표
    sg_layout: dict[str, dict] = {}
    for sg in subgraphs:
        member_nodes = [n for n in nodes if n["id"] in sg["nodes"]]
        if not member_nodes:
            sg_layout[sg["id"]] = {
                "col_x": {}, "row_y": {}, "sg_w": 200.0, "sg_h": 150.0,
            }
            continue

        col_max_w: dict[int, float] = {}
        row_max_h: dict[int, float] = {}
        for i, mn in enumerate(member_nodes):
            c, r = i % _COLS, i // _COLS
            mw, mh = node_sizes[mn["id"]]
            col_max_w[c] = max(col_max_w.get(c, 0.0), float(mw))
            row_max_h[r] = max(row_max_h.get(r, 0.0), float(mh))

        # 열별 누적 x, 행별 누적 y
        col_x: dict[int, float] = {}
        cx = float(_SG_PADDING)
        for c in sorted(col_max_w):
            col_x[c] = cx
            cx += col_max_w[c] + _H_GAP

        row_y: dict[int, float] = {}
        ry = float(_SG_HEADER + _SG_PADDING)
        for r in sorted(row_max_h):
            row_y[r] = ry
            ry += row_max_h[r] + _V_GAP

        n_cols = len(col_max_w)
        n_rows = len(row_max_h)
        sg_w = (sum(col_max_w.values())
                + _H_GAP * max(n_cols - 1, 0)
                + 2 * _SG_PADDING)
        sg_h = (_SG_HEADER
                + sum(row_max_h.values())
                + _V_GAP * max(n_rows - 1, 0)
                + 2 * _SG_PADDING)
        sg_layout[sg["id"]] = {
            "col_x": col_x, "row_y": row_y,
            "sg_w": sg_w, "sg_h": sg_h,
        }

    # 독립 노드의 base_y 결정 (서브그래프 최대 하단 기준)
    base_y_standalone = 30.0
    if subgraphs:
        max_bottom = 0.0
        for sg in subgraphs:
            if any(n["id"] in sg["nodes"] for n in nodes):
                sg_h = sg_layout[sg["id"]]["sg_h"]
                max_bottom = max(max_bottom, 30.0 + sg_h)
        base_y_standalone = max_bottom + _V_SPACING

    # 독립 노드 그리드 레이아웃 사전 계산
    standalone_nodes = [n for n in nodes if n["id"] not in node_to_sg]
    sa_col_max_w: dict[int, float] = {}
    sa_row_max_h: dict[int, float] = {}
    for i, n in enumerate(standalone_nodes):
        c, r = i % _COLS, i // _COLS
        nw, nh = node_sizes[n["id"]]
        sa_col_max_w[c] = max(sa_col_max_w.get(c, 0.0), float(nw))
        sa_row_max_h[r] = max(sa_row_max_h.get(r, 0.0), float(nh))

    sa_col_x: dict[int, float] = {}
    cx = 30.0
    for c in sorted(sa_col_max_w):
        sa_col_x[c] = cx
        cx += sa_col_max_w[c] + _H_GAP

    sa_row_y: dict[int, float] = {}
    ry = base_y_standalone
    for r in sorted(sa_row_max_h):
        sa_row_y[r] = ry
        ry += sa_row_max_h[r] + _V_GAP

    # ── [B] 위치 추적 dict (anchor 연산용) ──────────────────────────
    local_positions: dict[str, tuple] = {}       # sg 내부 상대좌표
    standalone_positions: dict[str, tuple] = {}  # 독립 노드 절대좌표

    # node_id → cell_id 맵핑
    id_map: dict[str, str] = {}

    # 서브그래프 컨테이너 추가 (동적 크기: sg_layout 기반)
    sg_id_map: dict[str, str] = {}
    sg_offset_map: dict[str, tuple] = {}

    cur_sg_x = 30.0
    for sg in subgraphs:
        member_nodes = [n for n in nodes if n["id"] in sg["nodes"]]
        if not member_nodes:
            continue

        layout = sg_layout[sg["id"]]
        sg_w = layout["sg_w"]
        sg_h = layout["sg_h"]

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
        cur_sg_x += sg_w + _H_SPACING   # 동적 sg_w 기준 증분

    # 노드 추가 (동적 크기 + 위치 dict 채움)
    sg_local_idx: dict[str, int] = {sg["id"]: 0 for sg in subgraphs}

    for node_idx, node in enumerate(nodes):
        style = _shape_to_style(node["shape"], node_idx)
        w, h = node_sizes[node["id"]]

        if node["id"] in node_to_sg:
            sg_id = node_to_sg[node["id"]]
            sg_parent_cid = sg_id_map.get(sg_id, "1")
            idx = sg_local_idx[sg_id]
            sg_local_idx[sg_id] += 1
            layout = sg_layout[sg_id]
            c, r = idx % _COLS, idx // _COLS
            lx = layout["col_x"].get(c, float(_SG_PADDING))
            ly = layout["row_y"].get(r, float(_SG_HEADER + _SG_PADDING))
            cid = _add_node_cell(root, node["id"], node["label"], style,
                                  lx, ly, w, h, parent=sg_parent_cid, next_id=next_id)
            local_positions[node["id"]] = (lx, ly)   # [B] 로컬 좌표 기록
        else:
            i = standalone_nodes.index(node)
            c, r = i % _COLS, i // _COLS
            x = sa_col_x.get(c, 30.0)
            y = sa_row_y.get(r, base_y_standalone)
            cid = _add_node_cell(root, node["id"], node["label"], style,
                                  x, y, w, h, parent="1", next_id=next_id)
            standalone_positions[node["id"]] = (x, y)  # [B] 절대 좌표 기록

        id_map[node["id"]] = cid

    # [B] _abs_pos 클로저 생성
    abs_pos = _make_abs_pos(node_to_sg, sg_offset_map, local_positions, standalone_positions)

    # 엣지 추가 (orthogonal anchor 포함)
    for edge in edges:
        src_cid = id_map.get(edge["source"])
        tgt_cid = id_map.get(edge["target"])
        if src_cid is None or tgt_cid is None:
            continue

        # 엣지 parent: 두 노드가 같은 서브그래프에 있으면 해당 서브그래프
        src_sg = node_to_sg.get(edge["source"])
        tgt_sg = node_to_sg.get(edge["target"])
        edge_parent = (
            sg_id_map.get(src_sg, "1")
            if src_sg and src_sg == tgt_sg
            else "1"
        )

        # [B] anchor 계산
        try:
            src_abs = abs_pos(edge["source"])
            tgt_abs = abs_pos(edge["target"])
            src_sz = node_sizes[edge["source"]]
            tgt_sz = node_sizes[edge["target"]]
            ex, ey, enx, eny = _edge_anchor(src_abs, src_sz, tgt_abs, tgt_sz)
            anchor = (
                f"exitX={ex};exitY={ey};exitDx=0;exitDy=0;"
                f"entryX={enx};entryY={eny};entryDx=0;entryDy=0;"
            )
            style = _edge_style(edge["style"]) + anchor
        except Exception:
            style = _edge_style(edge["style"])

        _add_edge_cell(root, src_cid, tgt_cid, edge["label"], style,
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
    events: list[dict],
    title: str,
) -> str:
    """sequenceDiagram XML을 생성한다.

    이벤트 스트림(_parse_sequence_events)을 y-커서로 순회하며 메시지 화살표,
    Note 박스, 제어 프레임(alt/opt/loop/par/critical/break/rect)을 렌더한다.
    프레임은 메시지보다 먼저 add 되어 뒤 레이어에 놓인다(화살표를 가리지 않음).
    중첩 그룹은 스택으로 처리한다.
    """
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

    # y-스텝을 차지하는 이벤트(메시지 + Note) 개수로 생명선 높이 산정
    n_steps = sum(1 for e in events if e["kind"] in ("msg", "note"))
    lifeline_total_h = (n_steps + 1) * _SEQ_MSG_H

    # ── 참여자 박스 + 생명선 ─────────────────────────────────────
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
        ET.SubElement(geo, "mxPoint",
            x=str(cx), y=str(_SEQ_LIFELINE_TOP),
            **{"as": "sourcePoint"},
        )
        ET.SubElement(geo, "mxPoint",
            x=str(cx), y=str(_SEQ_LIFELINE_TOP + lifeline_total_h),
            **{"as": "targetPoint"},
        )

    # ── 하단 참여자 박스 반복 (생명선 끝, mermaid 스타일) ──────────
    bottom_y = _SEQ_LIFELINE_TOP + lifeline_total_h
    for i, p in enumerate(participants):
        px = _SEQ_START_X + i * _SEQ_H_GAP
        p_style = _node_style_for_index("rectangle", i) + "fontStyle=1;fontSize=12;"
        cid = next_id()
        cell = ET.SubElement(root, "mxCell",
            id=cid, value=p["label"], style=p_style, vertex="1", parent="1",
        )
        ET.SubElement(cell, "mxGeometry",
            x=str(px), y=str(bottom_y),
            width=str(_SEQ_P_W), height=str(_SEQ_P_H),
            **{"as": "geometry"},
        )

    # ── 이벤트 1차 순회: y-좌표 할당 + 프레임 스택 처리 ──────────────
    frames: list[dict] = []           # 완료된 그룹 프레임
    notes: list[dict] = []            # (y, actors, text)
    msgs: list[dict] = []             # (y, src, dst, label, dashed)
    stack: list[dict] = []            # 열린 그룹 (중첩)
    step = 0

    def _touch(xs: set, *pids) -> None:
        for pid in pids:
            if pid in p_center_x:
                xs.add(p_center_x[pid])

    for ev in events:
        k = ev["kind"]
        if k == "msg":
            y = _SEQ_LIFELINE_TOP + (step + 1) * _SEQ_MSG_H
            msgs.append({"y": y, "src": ev["src"], "dst": ev["dst"],
                         "label": ev["label"], "dashed": ev["dashed"]})
            for fr in stack:
                _touch(fr["xs"], ev["src"], ev["dst"])
            step += 1
        elif k == "note":
            y = _SEQ_LIFELINE_TOP + (step + 1) * _SEQ_MSG_H
            notes.append({"y": y, "actors": ev["actors"], "text": ev["text"]})
            for fr in stack:
                _touch(fr["xs"], *ev["actors"])
            step += 1
        elif k == "group_start":
            stack.append({
                "type": ev["type"], "label": ev["label"],
                "y_top": _SEQ_LIFELINE_TOP + (step + 1) * _SEQ_MSG_H - int(_SEQ_MSG_H * 0.6),
                "xs": set(),
            })
        elif k == "group_else":
            if stack:
                stack[-1].setdefault("else_ys", []).append(
                    _SEQ_LIFELINE_TOP + step * _SEQ_MSG_H + int(_SEQ_MSG_H * 0.4))
        elif k == "group_end":
            if stack:
                fr = stack.pop()
                fr["y_bot"] = _SEQ_LIFELINE_TOP + step * _SEQ_MSG_H + int(_SEQ_MSG_H * 0.4)
                frames.append(fr)
                if stack:                      # 부모 그룹이 자식 x-범위 포함
                    stack[-1]["xs"] |= fr["xs"]

    # x-범위 폴백: 전체 참여자 span
    all_cx = list(p_center_x.values())
    span_min = min(all_cx) if all_cx else _SEQ_START_X
    span_max = max(all_cx) if all_cx else _SEQ_START_X + _SEQ_P_W
    _PAD_X = 70

    # ── 프레임(뒤 레이어, 메시지보다 먼저 add) ────────────────────
    for fr in frames:
        xs = fr["xs"]
        x_lo = (min(xs) if xs else span_min) - _PAD_X
        x_hi = (max(xs) if xs else span_max) + _PAD_X
        fw = max(120, x_hi - x_lo)
        fh = max(_SEQ_MSG_H, fr["y_bot"] - fr["y_top"])
        label = f"<b>{_esc_html(fr['type'])}</b>"
        if fr["label"]:
            label += f" {_esc_html(fr['label'])}"
        fstyle = (
            "rounded=1;arcSize=4;dashed=1;dashPattern=6 4;fillColor=none;"
            "strokeColor=#94a3b8;verticalAlign=top;align=left;html=1;"
            "spacingLeft=8;spacingTop=2;fontFamily=NanumSquare;fontSize=11;"
            "fontStyle=2;fontColor=#64748b;"
        )
        fr_cid = next_id()
        fcell = ET.SubElement(root, "mxCell",
            id=fr_cid, value=label, style=fstyle, vertex="1", parent="1")
        ET.SubElement(fcell, "mxGeometry",
            x=str(x_lo), y=str(fr["y_top"]),
            width=str(fw), height=str(fh), **{"as": "geometry"})

        # else 구분선
        for ey in fr.get("else_ys", []):
            div_cid = next_id()
            dcell = ET.SubElement(root, "mxCell",
                id=div_cid, value="",
                style="endArrow=none;dashed=1;strokeColor=#94a3b8;",
                edge="1", parent="1")
            dgeo = ET.SubElement(dcell, "mxGeometry", relative="1", **{"as": "geometry"})
            ET.SubElement(dgeo, "mxPoint", x=str(x_lo), y=str(ey), **{"as": "sourcePoint"})
            ET.SubElement(dgeo, "mxPoint", x=str(x_lo + fw), y=str(ey), **{"as": "targetPoint"})

    # ── Note 박스 (연노랑) ───────────────────────────────────────
    _NOTE_H = 36
    for nt in notes:
        cxs = [p_center_x[a] for a in nt["actors"] if a in p_center_x]
        if cxs:
            nc = (min(cxs) + max(cxs)) / 2.0
        else:
            nc = (span_min + span_max) / 2.0
        nw = max(140, min(260, len(nt["text"]) * 7 + 24))
        nx = nc - nw / 2.0
        ny = nt["y"] - _NOTE_H / 2.0
        nstyle = (
            "rounded=0;whiteSpace=wrap;html=1;fillColor=#fff8c5;strokeColor=#d4a72c;"
            "fontColor=#594b00;fontFamily=NanumSquare;fontSize=11;"
        )
        nt_cid = next_id()
        ncell = ET.SubElement(root, "mxCell",
            id=nt_cid, value=_esc_html(nt["text"]), style=nstyle, vertex="1", parent="1")
        ET.SubElement(ncell, "mxGeometry",
            x=str(nx), y=str(ny), width=str(nw), height=str(_NOTE_H), **{"as": "geometry"})

    # ── 메시지 화살표 ────────────────────────────────────────────
    for m in msgs:
        my = m["y"]
        sx = p_center_x.get(m["src"], 0.0)
        tx = p_center_x.get(m["dst"], 0.0)

        style = _STYLE_SEQ_MSG_DASHED if m["dashed"] else _STYLE_SEQ_MSG_SOLID
        style += "exitX=0.5;exitY=0.5;exitDx=0;exitDy=0;entryX=0.5;entryY=0.5;entryDx=0;entryDy=0;"

        msg_cid = next_id()
        msg_cell = ET.SubElement(root, "mxCell",
            id=msg_cid, value=m["label"],
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

def _esc_html(text: str) -> str:
    """HTML value 속성용 최소 이스케이프 (&, <, >)."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _serialize_xml(root_el: ET.Element) -> str:
    """ET.Element를 들여쓰기 포함 XML 문자열로 변환한다."""
    ET.indent(root_el, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        root_el, encoding="unicode", xml_declaration=False
    )


# ──────────────────────────────────────────────
# 진단/타입 판별 + erDiagram 파서
# ──────────────────────────────────────────────

def _first_meaningful_line(code: str) -> str:
    """%% 주석·빈 줄을 건너뛴 첫 의미 있는 줄을 반환한다.

    Mermaid 블록 첫 줄이 '%% source: ...' 주석인 경우 타입 판별이
    빗나가는 문제를 막는다.
    """
    for line in code.splitlines():
        s = line.strip()
        if s and not s.startswith("%%"):
            return s
    return ""


def _er_cardinality(rel: str) -> str:
    """erDiagram 관계 토큰(||--o{ 등)을 'L:R' 카디널리티 문자열로 변환한다."""
    left, right = rel, ""
    for sep in ("--", ".."):
        if sep in rel:
            left, right = rel.split(sep, 1)
            break

    def _side(tok: str) -> str:
        if "{" in tok or "}" in tok:
            return "N"
        if "|" in tok:
            return "1"
        if "o" in tok:
            return "0"
        return ""

    l, r = _side(left), _side(right)
    return f"{l}:{r}" if l and r else ""


def _parse_er(code: str) -> tuple[list[str], list[tuple[str, str, str]]]:
    """erDiagram 코드를 (entities, relations)로 파싱한다.

    Returns:
        entities:  엔티티명 목록 (선언/관계 등장 순서 보존)
        relations: (source, target, label) 튜플 목록 (label = '카디널리티  텍스트')
    """
    entities: list[str] = []
    relations: list[tuple[str, str, str]] = []
    current: str | None = None

    def _add_entity(name: str) -> None:
        if name not in entities:
            entities.append(name)

    rel_re = re.compile(r'^(\w+)\s+([|}{o.\-]+)\s+(\w+)\s*(?::\s*(.+))?$')

    for raw in code.splitlines():
        line = raw.strip()
        if not line or line.startswith("%%") or line == "erDiagram":
            continue
        if current is not None:
            if line == "}":
                current = None
            continue  # 속성 라인은 박스 단순화를 위해 수집만 생략
        m = re.match(r'^(\w+)\s*\{$', line)
        if m:
            current = m.group(1)
            _add_entity(current)
            continue
        m = rel_re.match(line)
        if m:
            a, rel, b, lbl = m.group(1), m.group(2), m.group(3), (m.group(4) or "")
            _add_entity(a)
            _add_entity(b)
            card = _er_cardinality(rel)
            text = _strip_quotes(lbl).strip()
            label = f"{card}  {text}" if (card and text) else (card or text)
            relations.append((a, b, label))

    return entities, relations


def _parse_er_attrs(code: str) -> dict[str, list[tuple[str, str]]]:
    """erDiagram 엔티티 블록 'Name { ... }' 내부 속성 라인을 수집한다.

    각 속성 라인을 공백 분리해 첫 토큰=type, 둘째 토큰=name(없으면 type만)으로
    토큰화한다. 'Name {' / '}' / 관계 라인은 제외. 공유 파서 — pptx/excalidraw가 import.

    Returns:
        {엔티티명: [(type, name), ...]}
    """
    attrs: dict[str, list[tuple[str, str]]] = {}
    current: str | None = None

    for raw in code.splitlines():
        line = raw.strip()
        if not line or line.startswith("%%") or line == "erDiagram":
            continue
        if current is not None:
            if line == "}":
                current = None
                continue
            tokens = line.split()
            if not tokens:
                continue
            typ = tokens[0]
            name = tokens[1] if len(tokens) > 1 else ""
            attrs[current].append((typ, name))
            continue
        m = re.match(r'^(\w+)\s*\{$', line)
        if m:
            current = m.group(1)
            attrs.setdefault(current, [])
            continue
        # 관계 라인 등은 무시

    return attrs


# ──────────────────────────────────────────────
# erDiagram 테이블 XML 빌더
# ──────────────────────────────────────────────

_ER_HEADER_H = 30
_ER_ROW_H    = 20
_ER_CHAR_W   = 7.5
_ER_PAD_W    = 18
_ER_COL_GAP  = 70
_ER_ROW_GAP  = 60


def _er_table_html(entity: str, rows: list[tuple[str, str]],
                   fill: str, stroke: str, text_color: str) -> str:
    """엔티티를 HTML <table>(헤더 행 + type|name 컬럼 행, 격자선)로 직렬화한다.

    draw.io는 html=1 라벨의 <table>을 실제 격자 표로 렌더한다.
    """
    head = (
        "<table cellspacing=\"0\" cellpadding=\"0\" "
        "style=\"width:100%;border-collapse:collapse;font-size:11px\">"
        f"<tr><td colspan=\"2\" style=\"background-color:{fill};color:{text_color};"
        f"font-weight:bold;text-align:center;border:1px solid {stroke};padding:4px\">"
        f"{_esc_html(entity)}</td></tr>"
    )
    body = ""
    for (t, n) in rows:
        body += (
            "<tr>"
            f"<td style=\"border:1px solid {stroke};background-color:#ffffff;"
            f"padding:2px 6px;color:#475569\">{_esc_html(t)}</td>"
            f"<td style=\"border:1px solid {stroke};background-color:#ffffff;"
            f"padding:2px 6px;color:#1e293b\">{_esc_html(n)}</td>"
            "</tr>"
        )
    return head + body + "</table>"


def _build_er_xml(
    entities: list[str],
    relations: list[tuple[str, str, str]],
    attrs: dict[str, list[tuple[str, str]]],
    title: str,
) -> str:
    """erDiagram 을 HTML 멀티라인 테이블 박스 + 관계 엣지로 렌더한다.

    각 엔티티 = '<b>Entity</b><hr>type name<br>…' HTML 박스(헤더 + 컬럼 행).
    노드 높이는 (헤더 + 속성수*행높이)로 동적 산정한다.
    """
    next_id = _make_id_gen()

    mxfile = ET.Element("mxfile", host="drawio.py", version="21.0.0")
    diagram = ET.SubElement(mxfile, "diagram", id="diagram-1", name=title or "ER")
    mxgraph_model = ET.SubElement(diagram, "mxGraphModel",
        dx="1422", dy="762", grid="1", gridSize="10",
        guides="1", tooltips="1", connect="1", arrows="1",
        fold="1", page="1", pageScale="1",
        pageWidth="1600", pageHeight="900",
        math="0", shadow="0",
    )
    root = ET.SubElement(mxgraph_model, "root")
    _make_root_cells(root)

    # ── 노드 크기 사전 계산 ──────────────────────────
    sizes: dict[str, tuple[float, float]] = {}
    for e in entities:
        rows = attrs.get(e, [])
        lines = [f"{t} {n}".strip() for (t, n) in rows]
        longest = max([len(e)] + [len(s) for s in lines]) if lines else len(e)
        w = max(160, int(longest * _ER_CHAR_W) + _ER_PAD_W)
        h = _ER_HEADER_H + len(rows) * _ER_ROW_H + (6 if rows else 0)
        sizes[e] = (float(w), float(h))

    # ── 가변높이 그리드 레이아웃 (열별 max폭 / 행별 max높이) ────────
    cols = _COLS
    col_w: dict[int, float] = {}
    row_h: dict[int, float] = {}
    for i, e in enumerate(entities):
        c, r = i % cols, i // cols
        w, h = sizes[e]
        col_w[c] = max(col_w.get(c, 0.0), w)
        row_h[r] = max(row_h.get(r, 0.0), h)

    col_x: dict[int, float] = {}
    cx = 40.0
    for c in sorted(col_w):
        col_x[c] = cx
        cx += col_w[c] + _ER_COL_GAP

    row_y: dict[int, float] = {}
    ry = 40.0
    for r in sorted(row_h):
        row_y[r] = ry
        ry += row_h[r] + _ER_ROW_GAP

    # ── 엔티티 박스 추가 ─────────────────────────────────────────
    id_map: dict[str, str] = {}
    for i, e in enumerate(entities):
        c, r = i % cols, i // cols
        w, h = sizes[e]
        x = col_x[c]
        y = row_y[r]
        fill, stroke = NODE_COLORS[i % len(NODE_COLORS)]
        style = (
            f"rounded=0;whiteSpace=wrap;html=1;verticalAlign=top;"
            f"fillColor=none;strokeColor=none;fontColor={TEXT_COLOR};"
            f"fontFamily=NanumSquare;fontSize=11;"
            f"spacing=0;spacingLeft=0;spacingRight=0;spacingTop=0;spacingBottom=0;"
        )
        value = _er_table_html(e, attrs.get(e, []), fill, stroke, TEXT_COLOR)
        cid = _add_node_cell(root, e, value, style, x, y, w, h, next_id=next_id)
        id_map[e] = cid

    # ── 관계 엣지 (카디널리티 라벨 유지) ──────────────────────────
    for (a, b, lbl) in relations:
        sc = id_map.get(a)
        tc = id_map.get(b)
        if sc is None or tc is None:
            continue
        _add_edge_cell(root, sc, tc, lbl, _STYLE_SOLID_EDGE, next_id=next_id)

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
    first_line = _first_meaningful_line(code)

    # 시퀀스 다이어그램 분기 — 이벤트 스트림으로 제어 프레임/Note 포함 렌더
    if first_line.startswith("sequenceDiagram"):
        participants, _ = _parse_sequence(code)
        events = _parse_sequence_events(code)
        return _build_sequence_xml(participants, events, title)

    # erDiagram 분기 — 엔티티=HTML 멀티라인 테이블 박스, 관계=카디널리티 엣지
    if first_line.startswith("erDiagram"):
        entities, relations = _parse_er(code)
        attrs = _parse_er_attrs(code)
        if entities:
            return _build_er_xml(entities, relations, attrs, title)

    # flowchart / graph 분기
    if re.match(r'(?:graph|flowchart)\s+', first_line):
        direction = _detect_direction(code)
        nodes      = parse_mermaid_nodes(code)
        edges      = parse_mermaid_edges(code)
        subgraphs  = parse_mermaid_subgraphs(code)
        return _build_flowchart_xml(nodes, edges, subgraphs, direction, title)

    # 기타 타입: 노드/엣지 파싱만 시도
    nodes     = parse_mermaid_nodes(code)
    edges     = parse_mermaid_edges(code)
    subgraphs = parse_mermaid_subgraphs(code)

    if not nodes and not edges:
        raise ValueError(
            f"지원하지 않는 Mermaid 다이어그램 타입이거나 파싱 가능한 내용이 없습니다: "
            f"{first_line!r}"
        )

    return _build_flowchart_xml(nodes, edges, subgraphs, "TB", title)
