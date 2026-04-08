# ============================================================
# excalidraw.py: Mermaid → Excalidraw JSON 범용 변환기
# 상세: Mermaid 코드를 파싱하여 Excalidraw에서 편집 가능한 JSON 생성
#       flowchart/graph 및 sequenceDiagram 지원
# 생성일: 2026-04-07 | 수정일: 2026-04-07
# ============================================================

import json
import random
import re
import uuid
from typing import Optional


# ── 색상 팔레트 (공통 palette.py에서 가져옴) ──────────────────────
from converters.palette import NODE_COLORS, SUBGRAPH_COLORS

_NODE_COLORS = [
    {"fill": fill, "stroke": stroke} for fill, stroke in NODE_COLORS
]

_SUBGRAPH_COLOR_LIST = [
    {"fill": fill, "stroke": stroke} for fill, stroke in SUBGRAPH_COLORS
]
_SUBGRAPH_COLOR = _SUBGRAPH_COLOR_LIST[0]  # 기본값

# ── 레이아웃 상수 ────────────────────────────────────────────────
_NODE_WIDTH = 160
_NODE_HEIGHT = 60
_H_SPACING = 200   # 노드 간 수평 간격
_V_SPACING = 120   # 노드 간 수직 간격
_SUBGRAPH_PADDING = 30  # 서브그래프 내부 여백

# ── 시퀀스 다이어그램 레이아웃 상수 ──────────────────────────────
_SEQ_PARTICIPANT_WIDTH = 160
_SEQ_PARTICIPANT_HEIGHT = 50
_SEQ_PARTICIPANT_H_SPACING = 200   # 참여자 간 수평 간격
_SEQ_MESSAGE_V_SPACING = 70        # 메시지 간 수직 간격
_SEQ_START_X = 60
_SEQ_START_Y = 60
_LINE_COLOR = "#475569"


def _new_id() -> str:
    """Excalidraw 요소용 고유 ID를 생성한다."""
    return str(uuid.uuid4())


def _parse_sequence(mermaid_code: str) -> tuple[list[dict], list[dict]]:
    """Mermaid sequenceDiagram 코드를 파싱하여 참여자와 메시지를 반환한다.

    Args:
        mermaid_code: sequenceDiagram 형식의 Mermaid 코드.

    Returns:
        (participants, messages) 튜플.
        - participants: [{"id": str, "label": str}] 리스트
        - messages: [{"source": str, "target": str, "label": str, "style": str}] 리스트
          style: "solid" 또는 "dashed"
    """
    participants: list[dict] = []
    participant_ids: list[str] = []
    messages: list[dict] = []

    # 화살표 패턴: ->> (solid), -->> (dashed), -> (solid), --> (dashed)
    msg_pattern = re.compile(
        r'^([\w\s]+?)\s*(->>|-->>|->|-->)\s*([\w\s]+?)\s*:\s*(.*)$'
    )

    lines = mermaid_code.strip().splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.lower().startswith('sequencediagram'):
            continue
        # 주석 제거
        if line.startswith('%%'):
            continue

        # participant / actor 파싱
        part_match = re.match(
            r'^(?:participant|actor)\s+([\w\s]+?)(?:\s+as\s+(.+))?$',
            line,
            re.IGNORECASE,
        )
        if part_match:
            pid = part_match.group(1).strip()
            label = (part_match.group(2) or pid).strip()
            if pid not in participant_ids:
                participants.append({"id": pid, "label": label})
                participant_ids.append(pid)
            continue

        # 메시지 파싱
        msg_match = msg_pattern.match(line)
        if msg_match:
            src = msg_match.group(1).strip()
            arrow = msg_match.group(2)
            dst = msg_match.group(3).strip()
            label = msg_match.group(4).strip()
            # -- 포함 여부로 점선/실선 결정
            style = "dashed" if arrow.startswith('--') else "solid"
            messages.append({
                "source": src,
                "target": dst,
                "label": label,
                "style": style,
            })
            # 암묵적 참여자 추가 (메시지에만 등장하는 경우)
            for pid in (src, dst):
                if pid not in participant_ids:
                    participants.append({"id": pid, "label": pid})
                    participant_ids.append(pid)

    return participants, messages


def _build_sequence_elements(
    participants: list[dict],
    messages: list[dict],
) -> list[dict]:
    """시퀀스 다이어그램 Excalidraw 요소를 생성한다.

    Args:
        participants: _parse_sequence() 반환값.
        messages: _parse_sequence() 반환값.

    Returns:
        Excalidraw elements 리스트.
    """
    elements: list[dict] = []

    if not participants:
        return elements

    # 참여자별 x 좌표 계산
    participant_x: dict[str, int] = {}
    participant_rect_id: dict[str, str] = {}

    for i, p in enumerate(participants):
        x = _SEQ_START_X + i * _SEQ_PARTICIPANT_H_SPACING
        participant_x[p["id"]] = x

        color = _NODE_COLORS[i % len(_NODE_COLORS)]
        rect_id = _new_id()
        text_id = _new_id()
        participant_rect_id[p["id"]] = rect_id

        # 참여자 박스 (rectangle, roundness type 3)
        elements.append({
            "type": "rectangle",
            "version": 1,
            "versionNonce": random.randint(1, 999999),
            "isDeleted": False,
            "id": rect_id,
            "fillStyle": "solid",
            "strokeWidth": 1,
            "strokeStyle": "solid",
            "roughness": 0,
            "opacity": 100,
            "angle": 0,
            "x": x,
            "y": _SEQ_START_Y,
            "width": _SEQ_PARTICIPANT_WIDTH,
            "height": _SEQ_PARTICIPANT_HEIGHT,
            "strokeColor": color["stroke"],
            "backgroundColor": color["fill"],
            "seed": random.randint(1, 999999),
            "groupIds": [],
            "frameId": None,
            "roundness": {"type": 3},
            "boundElements": [{"type": "text", "id": text_id}],
            "updated": 0,
            "link": None,
            "locked": False,
        })

        # 참여자 텍스트
        elements.append({
            "type": "text",
            "version": 1,
            "versionNonce": random.randint(1, 999999),
            "isDeleted": False,
            "id": text_id,
            "fillStyle": "solid",
            "strokeWidth": 1,
            "strokeStyle": "solid",
            "roughness": 0,
            "opacity": 100,
            "angle": 0,
            "x": x,
            "y": _SEQ_START_Y + (_SEQ_PARTICIPANT_HEIGHT - 20) // 2,
            "width": _SEQ_PARTICIPANT_WIDTH,
            "height": 20,
            "strokeColor": "#1e293b",
            "backgroundColor": "transparent",
            "seed": random.randint(1, 999999),
            "groupIds": [],
            "frameId": None,
            "roundness": None,
            "boundElements": [],
            "updated": 0,
            "link": None,
            "locked": False,
            "fontSize": 16,
            "fontFamily": 1,
            "text": p["label"],
            "textAlign": "center",
            "verticalAlign": "middle",
            "containerId": rect_id,
            "originalText": p["label"],
            "autoResize": True,
            "lineHeight": 1.25,
        })

    # 생명선 (lifeline): 참여자별 수직 점선
    lifeline_y_start = _SEQ_START_Y + _SEQ_PARTICIPANT_HEIGHT
    lifeline_length = len(messages) * _SEQ_MESSAGE_V_SPACING + _SEQ_MESSAGE_V_SPACING

    for p in participants:
        pid = p["id"]
        if pid not in participant_x:
            continue
        cx = participant_x[pid] + _SEQ_PARTICIPANT_WIDTH // 2
        color = _NODE_COLORS[participants.index(p) % len(_NODE_COLORS)]
        lifeline_id = _new_id()

        elements.append({
            "type": "arrow",
            "version": 1,
            "versionNonce": random.randint(1, 999999),
            "isDeleted": False,
            "id": lifeline_id,
            "fillStyle": "solid",
            "strokeWidth": 1,
            "strokeStyle": "dashed",
            "roughness": 0,
            "opacity": 50,
            "angle": 0,
            "x": cx,
            "y": lifeline_y_start,
            "width": 0,
            "height": lifeline_length,
            "strokeColor": color["stroke"],
            "backgroundColor": "transparent",
            "seed": random.randint(1, 999999),
            "groupIds": [],
            "frameId": None,
            "roundness": None,
            "boundElements": [],
            "updated": 0,
            "link": None,
            "locked": False,
            "points": [[0, 0], [0, lifeline_length]],
            "lastCommittedPoint": None,
            "startBinding": None,
            "endBinding": None,
            "startArrowhead": None,
            "endArrowhead": None,
        })

    # 메시지 화살표
    msg_y_base = lifeline_y_start + _SEQ_MESSAGE_V_SPACING

    for i, msg in enumerate(messages):
        src = msg["source"]
        dst = msg["target"]
        label = msg["label"]
        style = msg["style"]

        if src not in participant_x or dst not in participant_x:
            continue

        x1 = participant_x[src] + _SEQ_PARTICIPANT_WIDTH // 2
        x2 = participant_x[dst] + _SEQ_PARTICIPANT_WIDTH // 2
        y = msg_y_base + i * _SEQ_MESSAGE_V_SPACING

        arrow_id = _new_id()
        dx = x2 - x1

        arrow_el: dict = {
            "type": "arrow",
            "version": 1,
            "versionNonce": random.randint(1, 999999),
            "isDeleted": False,
            "id": arrow_id,
            "fillStyle": "solid",
            "strokeWidth": 1,
            "strokeStyle": style,
            "roughness": 0,
            "opacity": 100,
            "angle": 0,
            "x": x1,
            "y": y,
            "width": abs(dx),
            "height": 0,
            "strokeColor": _LINE_COLOR,
            "backgroundColor": "transparent",
            "seed": random.randint(1, 999999),
            "groupIds": [],
            "frameId": None,
            "roundness": {"type": 2},
            "boundElements": [],
            "updated": 0,
            "link": None,
            "locked": False,
            "points": [[0, 0], [dx, 0]],
            "lastCommittedPoint": None,
            "startBinding": {
                "elementId": participant_rect_id[src],
                "focus": 0,
                "gap": 8,
            },
            "endBinding": {
                "elementId": participant_rect_id[dst],
                "focus": 0,
                "gap": 8,
            },
            "startArrowhead": None,
            "endArrowhead": "arrow",
        }

        if label:
            label_id = _new_id()
            arrow_el["boundElements"] = [{"type": "text", "id": label_id}]
            elements.append(arrow_el)

            # 라벨 화살표 위에 배치
            mid_x = min(x1, x2) + abs(dx) // 2 - 40
            elements.append({
                "type": "text",
                "version": 1,
                "versionNonce": random.randint(1, 999999),
                "isDeleted": False,
                "id": label_id,
                "fillStyle": "solid",
                "strokeWidth": 1,
                "strokeStyle": "solid",
                "roughness": 0,
                "opacity": 100,
                "angle": 0,
                "x": mid_x,
                "y": y - 18,
                "width": 80,
                "height": 16,
                "strokeColor": _LINE_COLOR,
                "backgroundColor": "transparent",
                "seed": random.randint(1, 999999),
                "groupIds": [],
                "frameId": None,
                "roundness": None,
                "boundElements": [],
                "updated": 0,
                "link": None,
                "locked": False,
                "fontSize": 13,
                "fontFamily": 1,
                "text": label,
                "textAlign": "center",
                "verticalAlign": "middle",
                "containerId": arrow_id,
                "originalText": label,
                "autoResize": True,
                "lineHeight": 1.25,
            })
        else:
            elements.append(arrow_el)

    return elements


def _parse_direction(mermaid_code: str) -> str:
    """Mermaid 코드에서 그래프 방향을 파싱한다.

    Returns:
        'LR', 'RL', 'TB', 'TD', 'BT' 중 하나. 기본값은 'TB'.
    """
    m = re.search(
        r'^(?:graph|flowchart)\s+(LR|RL|TB|TD|BT)',
        mermaid_code,
        re.IGNORECASE | re.MULTILINE,
    )
    if m:
        return m.group(1).upper()
    return 'TB'


def _parse_mermaid(mermaid_code: str) -> tuple[dict, list, dict]:
    """Mermaid flowchart/graph 코드를 파싱하여 노드·엣지·서브그래프를 반환한다.

    Args:
        mermaid_code: 파싱할 Mermaid 코드 문자열.

    Returns:
        (nodes, edges, subgraphs) 튜플.
        - nodes: {node_id: label} 딕셔너리
        - edges: [{"from": str, "to": str, "label": str}] 리스트
        - subgraphs: {subgraph_id: {"label": str, "nodes": [node_id]}} 딕셔너리
    """
    nodes: dict[str, str] = {}
    edges: list[dict] = []
    subgraphs: dict[str, dict] = {}

    current_subgraph: Optional[str] = None
    subgraph_stack: list[str] = []

    # 여러 방향 지시어 제거 (graph LR, flowchart TD 등)
    lines = mermaid_code.strip().splitlines()

    # 노드 라벨 패턴: ID["Label"], ID[Label], ID("Label"), ID{Label}, ID>Label]
    node_patterns = [
        r'(\w[\w\s]*?)\["([^"]+)"\]',     # ID["Label"]
        r"(\w[\w\s]*?)\['([^']+)'\]",     # ID['Label']
        r'(\w[\w\s]*?)\[([^\[\]"\']+)\]', # ID[Label]
        r'(\w[\w\s]*?)\("([^"]+)"\)',     # ID("Label")
        r"(\w[\w\s]*?)\('([^']+)'\)",     # ID('Label')
        r'(\w[\w\s]*?)\(([^()]+)\)',      # ID(Label)
        r'(\w[\w\s]*?)\{"([^"]+)"\}',    # ID{"Label"}
        r'(\w[\w\s]*?)\{([^{}]+)\}',     # ID{Label}
        r'(\w[\w\s]*?)>\s*"([^"]+)"\]',  # ID>"Label"]
        r'(\w[\w\s]*?)>\s*([^\]]+)\]',   # ID>Label]
    ]

    # 엣지 패턴: -->, -.->>, ==>, --텍스트--> 등
    edge_pattern = re.compile(
        r'([\w][\w\s]*?)\s*'           # 출발 노드
        r'(?:--(?:>|>>|o|x)|'          # --> -->> --o --x
        r'-\.->|==>|~~~|'              # -.-> ==> ~~~
        r'--[^-\n]*?-->|'              # --텍스트-->
        r'-\.-[^-\n]*?->)'             # -.-텍스트->
        r'\s*([\w][\w\s]*?)(?=\s*$|'  # 도착 노드
        r'\s*[\[({>]|\s*--|\s*-\.|\s*==)',
        re.MULTILINE,
    )

    # 엣지 라벨 포함 패턴
    edge_label_pattern = re.compile(
        r'([\w][\w\s]*?)\s*'
        r'--([^->\n]*?)-->\s*'
        r'([\w][\w\s]*?)(?:\s|$)',
    )

    # 보다 단순한 엣지 파싱: 한 줄씩 처리
    arrow_re = re.compile(
        r'^([\w][\w\s]*?)\s*'
        r'(-->|-.->|==>|--[^>\n]*-->|-\.-[^>\n]*->|~~~)\s*'
        r'([\w][\w\s]*?)'
        r'(?:\s*$|\s*[\[({>]|\s*%%)',
    )

    for raw_line in lines:
        line = raw_line.strip()

        # 주석 제거
        if line.startswith('%%') or not line:
            continue

        # 그래프 방향 지시어 건너뜀
        if re.match(r'^(?:graph|flowchart)\s+(?:LR|RL|TB|TD|BT)', line, re.IGNORECASE):
            continue

        # 서브그래프 시작
        subgraph_start = re.match(r'^subgraph\s+(\w[\w\s]*?)(?:\s*\[.*\])?\s*$', line, re.IGNORECASE)
        if subgraph_start:
            sg_id = subgraph_start.group(1).strip()
            # 라벨이 대괄호 안에 있는 경우 추출
            sg_label_match = re.match(r'^subgraph\s+\S+\s*\["?([^"\]]+)"?\]', line, re.IGNORECASE)
            sg_label = sg_label_match.group(1) if sg_label_match else sg_id
            subgraphs[sg_id] = {"label": sg_label, "nodes": []}
            subgraph_stack.append(sg_id)
            current_subgraph = sg_id
            continue

        # 서브그래프 종료
        if re.match(r'^end\s*$', line, re.IGNORECASE):
            if subgraph_stack:
                subgraph_stack.pop()
                current_subgraph = subgraph_stack[-1] if subgraph_stack else None
            continue

        # 엣지 파싱 (노드 정의 포함 가능)
        # 복합 엣지: A --> B --> C 형태
        edge_parts = re.split(r'\s*(-->|-.->|==>|~~~)\s*', line)
        if len(edge_parts) >= 3 and '--' in line:
            for i in range(0, len(edge_parts) - 2, 2):
                src_raw = edge_parts[i].strip()
                dst_raw = edge_parts[i + 2].strip() if i + 2 < len(edge_parts) else ""

                if not src_raw or not dst_raw:
                    continue

                # 노드 ID와 라벨 분리
                src_id, src_label = _extract_node_id_label(src_raw)
                dst_id, dst_label = _extract_node_id_label(dst_raw)

                if src_id:
                    nodes.setdefault(src_id, src_label or src_id)
                    if current_subgraph and src_id not in subgraphs[current_subgraph]["nodes"]:
                        subgraphs[current_subgraph]["nodes"].append(src_id)

                if dst_id:
                    nodes.setdefault(dst_id, dst_label or dst_id)
                    if current_subgraph and dst_id not in subgraphs[current_subgraph]["nodes"]:
                        subgraphs[current_subgraph]["nodes"].append(dst_id)

                if src_id and dst_id:
                    # 엣지 라벨 추출
                    edge_label = ""
                    label_match = re.search(r'--([^->\n]+)-->', line)
                    if label_match:
                        edge_label = label_match.group(1).strip().strip('"\'')
                    edges.append({"from": src_id, "to": dst_id, "label": edge_label})
            continue

        # 단독 노드 정의 (엣지 없음)
        for pattern in node_patterns:
            m = re.match(r'^\s*' + pattern + r'\s*$', line)
            if m:
                nid = m.group(1).strip()
                label = m.group(2).strip()
                nodes[nid] = label
                if current_subgraph and nid not in subgraphs[current_subgraph]["nodes"]:
                    subgraphs[current_subgraph]["nodes"].append(nid)
                break

    # 노드가 하나도 없으면 엣지에서 추출
    if not nodes and edges:
        for edge in edges:
            nodes.setdefault(edge["from"], edge["from"])
            nodes.setdefault(edge["to"], edge["to"])

    return nodes, edges, subgraphs


def _extract_node_id_label(raw: str) -> tuple[str, str]:
    """노드 정의 문자열에서 ID와 라벨을 분리한다.

    예: 'A["Hello"]' → ('A', 'Hello'), 'B' → ('B', '')
    """
    raw = raw.strip()

    patterns = [
        (r'^(\w[\w\s]*?)\["([^"]+)"\]\s*$', 1, 2),
        (r"^(\w[\w\s]*?)\['([^']+)'\]\s*$", 1, 2),
        (r'^(\w[\w\s]*?)\[([^\[\]"\']+)\]\s*$', 1, 2),
        (r'^(\w[\w\s]*?)\("([^"]+)"\)\s*$', 1, 2),
        (r"^(\w[\w\s]*?)\('([^']+)'\)\s*$", 1, 2),
        (r'^(\w[\w\s]*?)\(([^()]+)\)\s*$', 1, 2),
        (r'^(\w[\w\s]*?)\{"([^"]+)"\}\s*$', 1, 2),
        (r'^(\w[\w\s]*?)\{([^{}]+)\}\s*$', 1, 2),
        (r'^(\w[\w\s]*?)>\s*"([^"]+)"\]\s*$', 1, 2),
    ]

    for pattern, id_group, label_group in patterns:
        m = re.match(pattern, raw)
        if m:
            return m.group(id_group).strip(), m.group(label_group).strip()

    # 단순 ID (라벨 없음)
    m = re.match(r'^(\w[\w\s]*?)\s*$', raw)
    if m:
        return m.group(1).strip(), ""

    return "", ""


def _topo_levels(node_ids: list[str], edges: list[dict]) -> list[list[str]]:
    """주어진 노드 집합에 대해 위상 정렬(레벨 단위)을 수행한다.

    Args:
        node_ids: 정렬할 노드 ID 목록.
        edges: 전체 엣지 리스트.

    Returns:
        레벨별 노드 ID 리스트 (level[0]이 루트).
    """
    node_set = set(node_ids)
    out_edges: dict[str, list[str]] = {nid: [] for nid in node_ids}
    in_degree: dict[str, int] = {nid: 0 for nid in node_ids}

    for edge in edges:
        src, dst = edge["from"], edge["to"]
        if src in node_set and dst in node_set:
            out_edges[src].append(dst)
            in_degree[dst] += 1

    levels: list[list[str]] = []
    queue = [nid for nid in node_ids if in_degree[nid] == 0]
    visited: set[str] = set()

    while queue:
        levels.append(list(queue))
        visited.update(queue)
        next_queue: list[str] = []
        for nid in queue:
            for neighbor in out_edges.get(nid, []):
                if neighbor not in visited:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        next_queue.append(neighbor)
        queue = next_queue

    remaining = [nid for nid in node_ids if nid not in visited]
    if remaining:
        levels.append(remaining)

    return levels


def _compute_layout(
    nodes: dict[str, str],
    edges: list[dict],
    subgraphs: dict[str, dict],
    direction: str = 'TB',
) -> dict[str, tuple[int, int]]:
    """노드를 방향(direction)에 맞게 배치한다.

    - LR/RL: 서브그래프를 가로로 나란히, 서브그래프 내부 노드도 가로 배치
    - TB/TD/BT: 서브그래프를 가로로 나란히, 서브그래프 내부 노드는 세로 배치
    - 독립 노드(서브그래프 미소속)는 모든 서브그래프 오른쪽에 배치

    Returns:
        {node_id: (x, y)} 좌표 딕셔너리.
    """
    if not nodes:
        return {}

    is_lr = direction in ('LR', 'RL')
    positions: dict[str, tuple[int, int]] = {}

    # 서브그래프에 속한 노드 집합
    sg_node_set: set[str] = set()
    for sg_info in subgraphs.values():
        sg_node_set.update(sg_info["nodes"])

    # 독립 노드 (서브그래프 미소속)
    standalone_nodes = [nid for nid in nodes if nid not in sg_node_set]

    # ── 서브그래프 배치 ──────────────────────────────────────────
    # 각 서브그래프 내부 노드 배치 후, 서브그래프를 가로로 나란히 놓는다.

    # 서브그래프별 로컬 좌표 계산 (원점 기준)
    sg_local: dict[str, dict[str, tuple[int, int]]] = {}
    sg_sizes: dict[str, tuple[int, int]] = {}  # (width, height)

    for sg_id, sg_info in subgraphs.items():
        sg_nodes = [n for n in sg_info["nodes"] if n in nodes]
        if not sg_nodes:
            sg_local[sg_id] = {}
            sg_sizes[sg_id] = (0, 0)
            continue

        levels = _topo_levels(sg_nodes, edges)
        local_pos: dict[str, tuple[int, int]] = {}

        if is_lr:
            # LR: 레벨 = 열(column), 같은 레벨 노드는 세로로 나열
            col_x = _SUBGRAPH_PADDING
            for level_nodes in levels:
                col_y = _SUBGRAPH_PADDING + 24  # 서브그래프 라벨 공간
                for nid in level_nodes:
                    local_pos[nid] = (col_x, col_y)
                    col_y += _V_SPACING
                col_x += _H_SPACING
            # 크기: 열 수 × H_SPACING, 행 수(최대 레벨 크기) × V_SPACING
            max_level_size = max(len(lv) for lv in levels) if levels else 1
            w = len(levels) * _H_SPACING + _SUBGRAPH_PADDING
            h = max_level_size * _V_SPACING + _SUBGRAPH_PADDING + 24
        else:
            # TB: 레벨 = 행(row), 같은 레벨 노드는 가로로 나열
            row_y = _SUBGRAPH_PADDING + 24  # 서브그래프 라벨 공간
            for level_nodes in levels:
                row_x = _SUBGRAPH_PADDING
                for nid in level_nodes:
                    local_pos[nid] = (row_x, row_y)
                    row_x += _H_SPACING
                row_y += _V_SPACING
            max_level_size = max(len(lv) for lv in levels) if levels else 1
            w = max_level_size * _H_SPACING + _SUBGRAPH_PADDING
            h = len(levels) * _V_SPACING + _SUBGRAPH_PADDING + 24

        sg_local[sg_id] = local_pos
        sg_sizes[sg_id] = (w, h)

    # 서브그래프를 가로로 나란히 배치 (서브그래프 간격: _H_SPACING)
    SG_GAP = _H_SPACING  # 서브그래프 간 수평 간격
    cursor_x = 60  # 전체 시작 x
    sg_offsets: dict[str, tuple[int, int]] = {}  # 서브그래프별 절대 오프셋

    for sg_id, sg_info in subgraphs.items():
        sg_nodes = [n for n in sg_info["nodes"] if n in nodes]
        if not sg_nodes:
            continue
        sg_offsets[sg_id] = (cursor_x, 60)
        w, _ = sg_sizes[sg_id]
        cursor_x += w + SG_GAP

    # 로컬 좌표 → 절대 좌표로 변환
    for sg_id, local_pos in sg_local.items():
        if sg_id not in sg_offsets:
            continue
        ox, oy = sg_offsets[sg_id]
        for nid, (lx, ly) in local_pos.items():
            positions[nid] = (ox + lx, oy + ly)

    # ── 독립 노드 배치 ──────────────────────────────────────────
    if standalone_nodes:
        levels = _topo_levels(standalone_nodes, edges)
        if is_lr:
            col_x = cursor_x
            for level_nodes in levels:
                col_y = 60
                for nid in level_nodes:
                    positions[nid] = (col_x, col_y)
                    col_y += _V_SPACING
                col_x += _H_SPACING
        else:
            row_y = 60
            for level_nodes in levels:
                row_x = cursor_x
                for nid in level_nodes:
                    positions[nid] = (row_x, row_y)
                    row_x += _H_SPACING
                row_y += _V_SPACING

    # 서브그래프가 없는 경우 (모든 노드가 독립 노드) 기존 방식으로 폴백
    if not subgraphs and standalone_nodes:
        positions = {}
        all_nodes = list(nodes.keys())
        levels = _topo_levels(all_nodes, edges)
        if is_lr:
            col_x = 60
            for level_nodes in levels:
                col_y = 60
                for nid in level_nodes:
                    positions[nid] = (col_x, col_y)
                    col_y += _V_SPACING
                col_x += _H_SPACING
        else:
            row_y = 60
            for level_nodes in levels:
                row_x = 60
                for nid in level_nodes:
                    positions[nid] = (row_x, row_y)
                    row_x += _H_SPACING
                row_y += _V_SPACING

    return positions


def _make_rectangle(
    eid: str,
    x: int,
    y: int,
    width: int,
    height: int,
    stroke_color: str,
    bg_color: str,
    dashed: bool = False,
    roundness: Optional[dict] = None,
) -> dict:
    """Excalidraw rectangle 요소를 생성한다."""
    return {
        "type": "rectangle",
        "version": 1,
        "versionNonce": 0,
        "isDeleted": False,
        "id": eid,
        "fillStyle": "solid",
        "strokeWidth": 1,
        "strokeStyle": "dashed" if dashed else "solid",
        "roughness": 1,
        "opacity": 100,
        "angle": 0,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "strokeColor": stroke_color,
        "backgroundColor": bg_color,
        "seed": 0,
        "groupIds": [],
        "frameId": None,
        "roundness": roundness or {"type": 3},
        "boundElements": [],
        "updated": 0,
        "link": None,
        "locked": False,
    }


def _make_text(
    eid: str,
    x: int,
    y: int,
    text: str,
    container_id: str,
    font_size: int = 16,
) -> dict:
    """Excalidraw text 요소를 생성한다."""
    return {
        "type": "text",
        "version": 1,
        "versionNonce": 0,
        "isDeleted": False,
        "id": eid,
        "fillStyle": "solid",
        "strokeWidth": 1,
        "strokeStyle": "solid",
        "roughness": 1,
        "opacity": 100,
        "angle": 0,
        "x": x,
        "y": y,
        "width": _NODE_WIDTH,
        "height": font_size + 4,
        "strokeColor": "#1e293b",
        "backgroundColor": "transparent",
        "seed": 0,
        "groupIds": [],
        "frameId": None,
        "roundness": None,
        "boundElements": [],
        "updated": 0,
        "link": None,
        "locked": False,
        "fontSize": font_size,
        "fontFamily": 1,
        "text": text,
        "textAlign": "center",
        "verticalAlign": "middle",
        "containerId": container_id,
        "originalText": text,
        "autoResize": True,
        "lineHeight": 1.25,
    }


def _make_arrow(
    eid: str,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    start_id: str,
    end_id: str,
    label: str = "",
) -> list[dict]:
    """Excalidraw arrow 요소(+ 선택적 라벨)를 생성한다.

    Returns:
        [arrow_element] 또는 [arrow_element, label_element] 리스트.
    """
    dx = x2 - x1
    dy = y2 - y1

    arrow_id = eid
    elements = []

    arrow: dict = {
        "type": "arrow",
        "version": 1,
        "versionNonce": 0,
        "isDeleted": False,
        "id": arrow_id,
        "fillStyle": "solid",
        "strokeWidth": 1,
        "strokeStyle": "solid",
        "roughness": 1,
        "opacity": 100,
        "angle": 0,
        "x": x1,
        "y": y1,
        "width": abs(dx),
        "height": abs(dy),
        "strokeColor": "#475569",
        "backgroundColor": "transparent",
        "seed": 0,
        "groupIds": [],
        "frameId": None,
        "roundness": {"type": 2},
        "boundElements": [],
        "updated": 0,
        "link": None,
        "locked": False,
        "points": [[0, 0], [dx, dy]],
        "lastCommittedPoint": None,
        "startBinding": {
            "elementId": start_id,
            "focus": 0,
            "gap": 8,
        },
        "endBinding": {
            "elementId": end_id,
            "focus": 0,
            "gap": 8,
        },
        "startArrowhead": None,
        "endArrowhead": "arrow",
    }

    if label:
        label_id = _new_id()
        arrow["boundElements"] = [{"type": "text", "id": label_id}]
        elements.append(arrow)

        # 화살표 중간 위치에 라벨 배치
        mid_x = x1 + dx // 2 - 40
        mid_y = y1 + dy // 2 - 10
        label_el: dict = {
            "type": "text",
            "version": 1,
            "versionNonce": 0,
            "isDeleted": False,
            "id": label_id,
            "fillStyle": "solid",
            "strokeWidth": 1,
            "strokeStyle": "solid",
            "roughness": 1,
            "opacity": 100,
            "angle": 0,
            "x": mid_x,
            "y": mid_y,
            "width": 80,
            "height": 20,
            "strokeColor": "#475569",
            "backgroundColor": "#ffffff",
            "seed": 0,
            "groupIds": [],
            "frameId": None,
            "roundness": None,
            "boundElements": [],
            "updated": 0,
            "link": None,
            "locked": False,
            "fontSize": 13,
            "fontFamily": 1,
            "text": label,
            "textAlign": "center",
            "verticalAlign": "middle",
            "containerId": arrow_id,
            "originalText": label,
            "autoResize": True,
            "lineHeight": 1.25,
        }
        elements.append(label_el)
    else:
        elements.append(arrow)

    return elements


def mermaid_to_excalidraw(mermaid_code: str, title: str = "") -> dict:
    """Mermaid 코드를 Excalidraw JSON 형식으로 변환한다.

    Args:
        mermaid_code: 변환할 Mermaid flowchart/graph 또는 sequenceDiagram 코드.
        title: 다이어그램 제목 (현재 미사용, 확장용).

    Returns:
        Excalidraw에서 직접 열 수 있는 JSON 딕셔너리.

    Raises:
        ValueError: 파싱 결과 노드/참여자가 없는 경우.
    """
    # 시퀀스 다이어그램 감지
    first_line = mermaid_code.strip().split('\n')[0].strip().lower()
    if 'sequencediagram' in first_line.replace(' ', ''):
        participants, messages = _parse_sequence(mermaid_code)
        elements = _build_sequence_elements(participants, messages)
        return {
            "type": "excalidraw",
            "version": 2,
            "source": "mermaid-web-converter",
            "elements": elements,
            "appState": {
                "viewBackgroundColor": "#ffffff",
                "gridSize": 20,
            },
            "files": {},
        }

    nodes, edges, subgraphs = _parse_mermaid(mermaid_code)
    direction = _parse_direction(mermaid_code)

    if not nodes:
        raise ValueError(
            "Mermaid 코드에서 노드를 찾을 수 없습니다. "
            "flowchart 또는 graph 형식인지 확인하세요."
        )

    positions = _compute_layout(nodes, edges, subgraphs, direction=direction)

    elements: list[dict] = []

    # 노드 ID → Excalidraw 사각형 ID 매핑 (화살표 바인딩에 사용)
    node_rect_ids: dict[str, str] = {}

    # ── 서브그래프 배경 사각형 생성 ──────────────────────────────
    for sg_id, sg_info in subgraphs.items():
        sg_nodes = sg_info["nodes"]
        if not sg_nodes:
            continue

        # 서브그래프 내 노드들의 bounding box 계산
        xs = [positions[n][0] for n in sg_nodes if n in positions]
        ys = [positions[n][1] for n in sg_nodes if n in positions]
        if not xs:
            continue

        sg_x = min(xs) - _SUBGRAPH_PADDING
        sg_y = min(ys) - _SUBGRAPH_PADDING - 24  # 라벨 공간
        sg_w = max(xs) - min(xs) + _NODE_WIDTH + _SUBGRAPH_PADDING * 2
        sg_h = max(ys) - min(ys) + _NODE_HEIGHT + _SUBGRAPH_PADDING * 2 + 24

        sg_rect_id = _new_id()
        sg_text_id = _new_id()

        elements.append(
            _make_rectangle(
                sg_rect_id,
                sg_x, sg_y, sg_w, sg_h,
                stroke_color=_SUBGRAPH_COLOR["stroke"],
                bg_color=_SUBGRAPH_COLOR["fill"],
                dashed=True,
                roundness={"type": 3},
            )
        )
        # 서브그래프 라벨 (상단)
        elements.append(
            _make_text(
                sg_text_id,
                sg_x, sg_y + 4,
                sg_info["label"],
                container_id=sg_rect_id,
                font_size=13,
            )
        )

    # ── 노드 사각형 + 텍스트 생성 ────────────────────────────────
    for color_idx, (node_id, label) in enumerate(nodes.items()):
        if node_id not in positions:
            continue

        x, y = positions[node_id]
        color = _NODE_COLORS[color_idx % len(_NODE_COLORS)]

        rect_id = _new_id()
        text_id = _new_id()
        node_rect_ids[node_id] = rect_id

        elements.append(
            _make_rectangle(
                rect_id,
                x, y,
                _NODE_WIDTH, _NODE_HEIGHT,
                stroke_color=color["stroke"],
                bg_color=color["fill"],
            )
        )
        # 텍스트 중앙 정렬: 사각형 중앙에 배치
        elements.append(
            _make_text(
                text_id,
                x, y + (_NODE_HEIGHT - 20) // 2,
                label,
                container_id=rect_id,
            )
        )

    # ── 화살표 생성 ────────────────────────────────────────────
    for edge in edges:
        src_id = edge["from"]
        dst_id = edge["to"]
        edge_label = edge.get("label", "")

        if src_id not in node_rect_ids or dst_id not in node_rect_ids:
            continue

        if src_id not in positions or dst_id not in positions:
            continue

        src_x, src_y = positions[src_id]
        dst_x, dst_y = positions[dst_id]

        # 사각형 중앙에서 출발/도착
        x1 = src_x + _NODE_WIDTH // 2
        y1 = src_y + _NODE_HEIGHT // 2
        x2 = dst_x + _NODE_WIDTH // 2
        y2 = dst_y + _NODE_HEIGHT // 2

        arrow_elements = _make_arrow(
            _new_id(),
            x1, y1, x2, y2,
            start_id=node_rect_ids[src_id],
            end_id=node_rect_ids[dst_id],
            label=edge_label,
        )
        elements.extend(arrow_elements)

    return {
        "type": "excalidraw",
        "version": 2,
        "source": "mermaid-web-converter",
        "elements": elements,
        "appState": {
            "viewBackgroundColor": "#ffffff",
            "gridSize": 20,
        },
        "files": {},
    }
