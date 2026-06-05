# ============================================================
# excalidraw.py: Mermaid → Excalidraw JSON 범용 변환기
# 상세: Mermaid 코드를 파싱하여 Excalidraw에서 편집 가능한 JSON 생성
#       flowchart/graph, sequenceDiagram(제어 프레임·Note 포함), erDiagram(속성 테이블) 지원
# 생성일: 2026-04-07 | 수정일: 2026-06-05
# ============================================================

import json
import random
import re
import uuid
from typing import Optional


# ── 색상 팔레트 (공통 palette.py에서 가져옴) ──────────────────────
from converters.palette import NODE_COLORS, SUBGRAPH_COLORS
from converters.text_metrics import estimate_text_size_px

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

# ── 시퀀스 제어 프레임 + 노트 상수 ────────────────────────────
_SEQ_FRAME_PADDING = 10     # 프레임이 참여자 범위를 넘는 여백(px)
_SEQ_NOTE_HEIGHT = 44       # Note 박스 높이
_SEQ_NOTE_PADDING = 6       # Note 박스 아래 여백

# ── ER 다이어그램 레이아웃 상수 ─────────────────────────────────
_ER_ENTITY_WIDTH = 200      # 엔티티 박스 폭
_ER_HEADER_HEIGHT = 36      # 헤더(엔티티명) 줄 높이
_ER_ROW_HEIGHT = 22         # 속성 행 높이
_ER_H_GAP = 80              # 엔티티 간 수평 간격
_ER_V_GAP = 60              # 엔티티 간 수직 간격
_ER_MAX_COLS = 4            # 그리드 최대 열 수
_ER_START_X = 60            # 그리드 시작 x
_ER_START_Y = 60            # 그리드 시작 y


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
    events: list[dict],
) -> list[dict]:
    """시퀀스 다이어그램 Excalidraw 요소를 이벤트 스트림으로 생성한다.

    _parse_sequence_events() 반환값을 순회하여 메시지 화살표,
    제어 프레임(alt/opt/loop 등 점선 사각형+라벨), Note 박스를 렌더한다.
    무한 캔버스이므로 클램프 없음.

    Args:
        participants: _parse_sequence() 반환 참여자 목록.
        events: _parse_sequence_events() 반환 이벤트 리스트.

    Returns:
        Excalidraw elements 리스트 (참여자 > 생명선 > 프레임/노트 > 화살표 순).
    """
    participant_elements: list[dict] = []
    lifeline_elements: list[dict] = []
    frame_elements: list[dict] = []   # 그룹 프레임 + 노트 박스 (화살표 뒤 레이어)
    arrow_elements: list[dict] = []   # 메시지 화살표 + 라벨

    if not participants:
        return participant_elements

    # 참여자별 x 좌표 및 박스 요소 생성
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
        participant_elements.append({
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
        participant_elements.append({
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
            "fontSize": 14,
            "fontFamily": 1,
            "text": p["label"],
            "textAlign": "center",
            "verticalAlign": "middle",
            "containerId": rect_id,
            "originalText": p["label"],
            "autoResize": True,
            "lineHeight": 1.25,
        })

    # 그룹 프레임 x 범위: 모든 참여자를 포함 + padding
    if participant_x:
        all_x_min = min(participant_x.values()) - _SEQ_FRAME_PADDING
        all_x_max = (
            max(participant_x.values()) + _SEQ_PARTICIPANT_WIDTH + _SEQ_FRAME_PADDING
        )
    else:
        all_x_min = _SEQ_START_X - _SEQ_FRAME_PADDING
        all_x_max = _SEQ_START_X + _SEQ_PARTICIPANT_WIDTH + _SEQ_FRAME_PADDING
    frame_w = all_x_max - all_x_min

    # 이벤트 순회 — y 커서 기반 렌더
    lifeline_y_start = _SEQ_START_Y + _SEQ_PARTICIPANT_HEIGHT
    y_cursor = lifeline_y_start + _SEQ_MESSAGE_V_SPACING

    # 그룹 스택: [{"type": str, "label": str, "y_top": int, "elses": [int]}]
    group_stack: list[dict] = []

    for event in events:
        kind = event["kind"]

        if kind == "msg":
            src = event["src"]
            dst = event["dst"]
            label = event["label"]
            dashed = event["dashed"]

            if src not in participant_x or dst not in participant_x:
                y_cursor += _SEQ_MESSAGE_V_SPACING
                continue

            x1 = participant_x[src] + _SEQ_PARTICIPANT_WIDTH // 2
            x2 = participant_x[dst] + _SEQ_PARTICIPANT_WIDTH // 2
            y = y_cursor
            dx = x2 - x1

            arrow_id = _new_id()
            arrow_el: dict = {
                "type": "arrow",
                "version": 1,
                "versionNonce": random.randint(1, 999999),
                "isDeleted": False,
                "id": arrow_id,
                "fillStyle": "solid",
                "strokeWidth": 1,
                "strokeStyle": "dashed" if dashed else "solid",
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
                    "elementId": participant_rect_id.get(src, ""),
                    "focus": 0,
                    "gap": 8,
                },
                "endBinding": {
                    "elementId": participant_rect_id.get(dst, ""),
                    "focus": 0,
                    "gap": 8,
                },
                "startArrowhead": None,
                "endArrowhead": "arrow",
            }

            if label:
                label_id = _new_id()
                arrow_el["boundElements"] = [{"type": "text", "id": label_id}]
                arrow_elements.append(arrow_el)
                mid_x = min(x1, x2) + abs(dx) // 2 - 40
                arrow_elements.append({
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
                arrow_elements.append(arrow_el)

            y_cursor += _SEQ_MESSAGE_V_SPACING

        elif kind == "note":
            actors = event["actors"]
            text = event["text"]

            xs = [participant_x[a] for a in actors if a in participant_x]
            if not xs:
                continue

            note_x = min(xs)
            note_w = max(xs) - note_x + _SEQ_PARTICIPANT_WIDTH
            note_rect_id = _new_id()
            note_text_id = _new_id()

            frame_elements.append({
                "type": "rectangle",
                "version": 1,
                "versionNonce": random.randint(1, 999999),
                "isDeleted": False,
                "id": note_rect_id,
                "fillStyle": "solid",
                "strokeWidth": 1,
                "strokeStyle": "solid",
                "roughness": 0,
                "opacity": 100,
                "angle": 0,
                "x": note_x,
                "y": y_cursor,
                "width": note_w,
                "height": _SEQ_NOTE_HEIGHT,
                "strokeColor": "#ca8a04",
                "backgroundColor": "#fefce8",
                "seed": random.randint(1, 999999),
                "groupIds": [],
                "frameId": None,
                "roundness": {"type": 1},
                "boundElements": [{"type": "text", "id": note_text_id}],
                "updated": 0,
                "link": None,
                "locked": False,
            })
            frame_elements.append({
                "type": "text",
                "version": 1,
                "versionNonce": random.randint(1, 999999),
                "isDeleted": False,
                "id": note_text_id,
                "fillStyle": "solid",
                "strokeWidth": 1,
                "strokeStyle": "solid",
                "roughness": 0,
                "opacity": 100,
                "angle": 0,
                "x": note_x,
                "y": y_cursor + (_SEQ_NOTE_HEIGHT - 20) // 2,
                "width": note_w,
                "height": 20,
                "strokeColor": "#92400e",
                "backgroundColor": "transparent",
                "seed": random.randint(1, 999999),
                "groupIds": [],
                "frameId": None,
                "roundness": None,
                "boundElements": [],
                "updated": 0,
                "link": None,
                "locked": False,
                "fontSize": 12,
                "fontFamily": 1,
                "text": text,
                "textAlign": "center",
                "verticalAlign": "middle",
                "containerId": note_rect_id,
                "originalText": text,
                "autoResize": True,
                "lineHeight": 1.25,
            })

            y_cursor += _SEQ_NOTE_HEIGHT + _SEQ_NOTE_PADDING

        elif kind == "group_start":
            group_stack.append({
                "type": event["type"],
                "label": event["label"],
                "y_top": y_cursor,
                "elses": [],
            })

        elif kind == "group_else":
            if group_stack:
                group_stack[-1]["elses"].append(y_cursor)

        elif kind == "group_end":
            if not group_stack:
                continue
            group = group_stack.pop()
            y_top = group["y_top"]
            y_bottom = y_cursor
            if y_bottom <= y_top:
                continue

            frame_label = group["type"]
            if group["label"]:
                frame_label += f" {group['label']}"

            frame_h = y_bottom - y_top
            frame_rect_id = _new_id()
            frame_text_id = _new_id()

            # 그룹 프레임 사각형 (점선, 반투명)
            frame_elements.append({
                "type": "rectangle",
                "version": 1,
                "versionNonce": random.randint(1, 999999),
                "isDeleted": False,
                "id": frame_rect_id,
                "fillStyle": "solid",
                "strokeWidth": 1,
                "strokeStyle": "dashed",
                "roughness": 0,
                "opacity": 25,
                "angle": 0,
                "x": all_x_min,
                "y": y_top,
                "width": frame_w,
                "height": frame_h,
                "strokeColor": "#3b82f6",
                "backgroundColor": "#dbeafe",
                "seed": random.randint(1, 999999),
                "groupIds": [],
                "frameId": None,
                "roundness": {"type": 1},
                "boundElements": [],
                "updated": 0,
                "link": None,
                "locked": False,
            })
            # 프레임 라벨 텍스트 (완전 불투명, 좌상단)
            frame_elements.append({
                "type": "text",
                "version": 1,
                "versionNonce": random.randint(1, 999999),
                "isDeleted": False,
                "id": frame_text_id,
                "fillStyle": "solid",
                "strokeWidth": 1,
                "strokeStyle": "solid",
                "roughness": 0,
                "opacity": 100,
                "angle": 0,
                "x": all_x_min + 4,
                "y": y_top + 2,
                "width": 240,
                "height": 20,
                "strokeColor": "#1d4ed8",
                "backgroundColor": "transparent",
                "seed": random.randint(1, 999999),
                "groupIds": [],
                "frameId": None,
                "roundness": None,
                "boundElements": [],
                "updated": 0,
                "link": None,
                "locked": False,
                "fontSize": 12,
                "fontFamily": 1,
                "text": frame_label,
                "textAlign": "left",
                "verticalAlign": "top",
                "containerId": None,
                "originalText": frame_label,
                "autoResize": True,
                "lineHeight": 1.25,
            })

            # else 구분선 (수평 점선)
            for else_y in group["elses"]:
                else_line_id = _new_id()
                frame_elements.append({
                    "type": "line",
                    "version": 1,
                    "versionNonce": random.randint(1, 999999),
                    "isDeleted": False,
                    "id": else_line_id,
                    "fillStyle": "solid",
                    "strokeWidth": 1,
                    "strokeStyle": "dashed",
                    "roughness": 0,
                    "opacity": 80,
                    "angle": 0,
                    "x": all_x_min,
                    "y": else_y,
                    "width": frame_w,
                    "height": 0,
                    "strokeColor": "#3b82f6",
                    "backgroundColor": "transparent",
                    "seed": random.randint(1, 999999),
                    "groupIds": [],
                    "frameId": None,
                    "roundness": None,
                    "boundElements": [],
                    "updated": 0,
                    "link": None,
                    "locked": False,
                    "points": [[0, 0], [frame_w, 0]],
                    "lastCommittedPoint": None,
                    "startBinding": None,
                    "endBinding": None,
                    "startArrowhead": None,
                    "endArrowhead": None,
                })

    # 생명선 (최종 y 기준으로 높이 결정)
    total_lifeline_h = y_cursor + _SEQ_MESSAGE_V_SPACING - lifeline_y_start

    for p in participants:
        pid = p["id"]
        if pid not in participant_x:
            continue
        cx = participant_x[pid] + _SEQ_PARTICIPANT_WIDTH // 2
        color = _NODE_COLORS[participants.index(p) % len(_NODE_COLORS)]
        lifeline_id = _new_id()

        lifeline_elements.append({
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
            "height": total_lifeline_h,
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
            "points": [[0, 0], [0, total_lifeline_h]],
            "lastCommittedPoint": None,
            "startBinding": None,
            "endBinding": None,
            "startArrowhead": None,
            "endArrowhead": None,
        })

    # 하단 참여자 박스 반복 (생명선 끝, mermaid 스타일)
    bottom_elements: list[dict] = []
    bottom_y = lifeline_y_start + total_lifeline_h
    for i, p in enumerate(participants):
        pid = p["id"]
        if pid not in participant_x:
            continue
        x = participant_x[pid]
        color = _NODE_COLORS[i % len(_NODE_COLORS)]
        rect_id = _new_id()
        text_id = _new_id()
        bottom_elements.append({
            "type": "rectangle", "version": 1,
            "versionNonce": random.randint(1, 999999), "isDeleted": False,
            "id": rect_id, "fillStyle": "solid", "strokeWidth": 1,
            "strokeStyle": "solid", "roughness": 0, "opacity": 100, "angle": 0,
            "x": x, "y": bottom_y,
            "width": _SEQ_PARTICIPANT_WIDTH, "height": _SEQ_PARTICIPANT_HEIGHT,
            "strokeColor": color["stroke"], "backgroundColor": color["fill"],
            "seed": random.randint(1, 999999), "groupIds": [], "frameId": None,
            "roundness": {"type": 3},
            "boundElements": [{"type": "text", "id": text_id}],
            "updated": 0, "link": None, "locked": False,
        })
        bottom_elements.append({
            "type": "text", "version": 1,
            "versionNonce": random.randint(1, 999999), "isDeleted": False,
            "id": text_id, "fillStyle": "solid", "strokeWidth": 1,
            "strokeStyle": "solid", "roughness": 0, "opacity": 100, "angle": 0,
            "x": x, "y": bottom_y + (_SEQ_PARTICIPANT_HEIGHT - 20) // 2,
            "width": _SEQ_PARTICIPANT_WIDTH, "height": 20,
            "strokeColor": "#1e293b", "backgroundColor": "transparent",
            "seed": random.randint(1, 999999), "groupIds": [], "frameId": None,
            "roundness": None, "boundElements": [], "updated": 0,
            "link": None, "locked": False,
            "fontSize": 14, "fontFamily": 1, "text": p["label"],
            "textAlign": "center", "verticalAlign": "middle",
            "containerId": rect_id, "originalText": p["label"],
            "autoResize": True, "lineHeight": 1.25,
        })

    # 요소 순서: 참여자 > 생명선 > 하단박스 > 프레임/노트 > 화살표 (z-order)
    return (participant_elements + lifeline_elements + bottom_elements
            + frame_elements + arrow_elements)


def _build_er_elements(
    entities: list[str],
    relations: list[tuple[str, str, str]],
    entity_attrs: dict[str, list[tuple[str, str]]],
) -> list[dict]:
    """erDiagram Excalidraw 요소를 생성한다.

    각 엔티티 = 사각형 + 멀티라인 텍스트(1행=엔티티명, 이후=type name).
    노드 높이는 속성 수에 비례. 관계 = 카디널리티 라벨 화살표 엣지.

    Args:
        entities: _parse_er() 반환 엔티티명 목록.
        relations: _parse_er() 반환 (src, dst, label) 튜플 목록.
        entity_attrs: _parse_er_attrs() 반환 {name: [(type, name), ...]} 딕셔너리.

    Returns:
        Excalidraw elements 리스트.
    """
    elements: list[dict] = []
    node_rect_ids: dict[str, str] = {}
    entity_positions: dict[str, tuple[int, int]] = {}
    entity_heights: dict[str, int] = {}

    if not entities:
        return elements

    # 엔티티 높이 계산 (헤더 + 속성 행)
    for ent in entities:
        attrs = entity_attrs.get(ent, [])
        entity_heights[ent] = _ER_HEADER_HEIGHT + len(attrs) * _ER_ROW_HEIGHT

    # 그리드 레이아웃 (최대 _ER_MAX_COLS 열, 행별 가변 높이)
    n = len(entities)
    n_cols = min(n, _ER_MAX_COLS)
    n_rows = (n + n_cols - 1) // n_cols

    # 행별 최대 높이 계산
    row_max_h: dict[int, int] = {}
    for i, ent in enumerate(entities):
        row = i // n_cols
        row_max_h[row] = max(row_max_h.get(row, 0), entity_heights[ent])

    # 행별 y 시작점 계산
    row_y: dict[int, int] = {}
    y_acc = _ER_START_Y
    for row in range(n_rows):
        row_y[row] = y_acc
        y_acc += row_max_h.get(row, _ER_HEADER_HEIGHT) + _ER_V_GAP

    # 엔티티별 위치 결정
    for i, ent in enumerate(entities):
        row = i // n_cols
        col = i % n_cols
        x = _ER_START_X + col * (_ER_ENTITY_WIDTH + _ER_H_GAP)
        y = row_y[row]
        entity_positions[ent] = (x, y)

    # 엔티티 렌더 (사각형 + 멀티라인 텍스트)
    for color_idx, ent in enumerate(entities):
        if ent not in entity_positions:
            continue

        x, y = entity_positions[ent]
        h = entity_heights[ent]
        attrs = entity_attrs.get(ent, [])
        color = _NODE_COLORS[color_idx % len(_NODE_COLORS)]

        rect_id = _new_id()
        text_id = _new_id()
        node_rect_ids[ent] = rect_id

        col_split = int(_ER_ENTITY_WIDTH * 0.42)
        line_color = "#cbd5e1"

        def _el(**kw):
            base = {
                "version": 1, "versionNonce": random.randint(1, 999999),
                "isDeleted": False, "fillStyle": "solid", "strokeWidth": 1,
                "strokeStyle": "solid", "roughness": 0, "opacity": 100, "angle": 0,
                "backgroundColor": "transparent",
                "seed": random.randint(1, 999999), "groupIds": [], "frameId": None,
                "roundness": None, "boundElements": [], "updated": 0,
                "link": None, "locked": False,
            }
            base.update(kw)
            return base

        def _hline(lx, ly, lw):
            return _el(type="line", id=_new_id(), x=lx, y=ly, width=lw, height=0,
                       strokeColor=line_color, points=[[0, 0], [lw, 0]],
                       lastCommittedPoint=None, startBinding=None, endBinding=None,
                       startArrowhead=None, endArrowhead=None)

        def _cell_text(t, cx, cy, cw, cc):
            return _el(type="text", id=_new_id(), x=cx, y=cy, width=cw, height=14,
                       strokeColor=cc, fontSize=11, fontFamily=1, text=t,
                       textAlign="left", verticalAlign="middle", containerId=None,
                       originalText=t, autoResize=False, lineHeight=1.25)

        # 외곽 박스 (흰 배경 + 엔티티 stroke)
        elements.append(_el(
            type="rectangle", id=rect_id, x=x, y=y,
            width=_ER_ENTITY_WIDTH, height=h,
            strokeColor=color["stroke"], backgroundColor="#ffffff",
            roundness={"type": 1},
        ))

        # 헤더 밴드 (엔티티명) — 팔레트 fill
        hdr_id = _new_id()
        hdr_txt_id = _new_id()
        elements.append(_el(
            type="rectangle", id=hdr_id, x=x, y=y,
            width=_ER_ENTITY_WIDTH, height=_ER_HEADER_HEIGHT,
            strokeColor=color["stroke"], backgroundColor=color["fill"],
            boundElements=[{"type": "text", "id": hdr_txt_id}],
        ))
        elements.append(_el(
            type="text", id=hdr_txt_id, x=x, y=y + (_ER_HEADER_HEIGHT - 16) // 2,
            width=_ER_ENTITY_WIDTH, height=16, strokeColor="#1e293b",
            fontSize=13, fontFamily=1, text=ent, textAlign="center",
            verticalAlign="middle", containerId=hdr_id, originalText=ent,
            autoResize=False, lineHeight=1.25,
        ))

        # 컬럼/행 구분선 + type|name 셀 텍스트
        if attrs:
            elements.append(_el(
                type="line", id=_new_id(), x=x + col_split, y=y + _ER_HEADER_HEIGHT,
                width=0, height=h - _ER_HEADER_HEIGHT, strokeColor=line_color,
                points=[[0, 0], [0, h - _ER_HEADER_HEIGHT]], lastCommittedPoint=None,
                startBinding=None, endBinding=None, startArrowhead=None, endArrowhead=None,
            ))
            for k, (typ, nm) in enumerate(attrs):
                ry = y + _ER_HEADER_HEIGHT + k * _ER_ROW_HEIGHT
                if k > 0:
                    elements.append(_hline(x, ry, _ER_ENTITY_WIDTH))
                cy = ry + (_ER_ROW_HEIGHT - 14) // 2
                elements.append(_cell_text(typ, x + 6, cy, col_split - 8, "#475569"))
                elements.append(_cell_text(nm, x + col_split + 6, cy,
                                           _ER_ENTITY_WIDTH - col_split - 10, "#1e293b"))

    # 관계 화살표 렌더
    for src, dst, label in relations:
        if src not in node_rect_ids or dst not in node_rect_ids:
            continue
        if src not in entity_positions or dst not in entity_positions:
            continue

        src_x, src_y = entity_positions[src]
        dst_x, dst_y = entity_positions[dst]

        x1 = src_x + _ER_ENTITY_WIDTH // 2
        y1 = src_y + entity_heights[src] // 2
        x2 = dst_x + _ER_ENTITY_WIDTH // 2
        y2 = dst_y + entity_heights[dst] // 2

        arrow_els = _make_arrow(
            _new_id(),
            x1, y1, x2, y2,
            start_id=node_rect_ids[src],
            end_id=node_rect_ids[dst],
            label=label,
        )
        elements.extend(arrow_els)

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
                    nodes.setdefault(src_id, (src_label or src_id).replace('<br/>', '\n').replace('<br>', '\n'))
                    if current_subgraph and src_id not in subgraphs[current_subgraph]["nodes"]:
                        subgraphs[current_subgraph]["nodes"].append(src_id)

                if dst_id:
                    nodes.setdefault(dst_id, (dst_label or dst_id).replace('<br/>', '\n').replace('<br>', '\n'))
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
                nodes[nid] = label.replace('<br/>', '\n').replace('<br>', '\n')
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
    est_w, _ = estimate_text_size_px(text, font_size_px=font_size)
    if est_w > _NODE_WIDTH:
        font_size = max(11, int(font_size * _NODE_WIDTH / est_w))
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
    # 타입 판별: %% 주석·빈 줄을 건너뛴 첫 의미 있는 줄 기준
    from converters.drawio import (
        _first_meaningful_line,
        _parse_er,
        _parse_er_attrs,
        _parse_sequence_events,
    )
    first_line = _first_meaningful_line(mermaid_code)

    # 시퀀스 다이어그램: 이벤트 스트림으로 제어 프레임·Note 포함 렌더
    if 'sequencediagram' in first_line.lower().replace(' ', ''):
        participants, _ = _parse_sequence(mermaid_code)
        events = _parse_sequence_events(mermaid_code)
        elements = _build_sequence_elements(participants, events)
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

    # erDiagram: 엔티티 = 속성 포함 멀티라인 테이블, 관계 = 카디널리티 엣지
    if first_line.startswith('erDiagram'):
        entities, relations = _parse_er(mermaid_code)
        entity_attrs = _parse_er_attrs(mermaid_code)
        elements = _build_er_elements(entities, relations, entity_attrs)
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

    # flowchart/graph 분기 (시퀀스·erDiagram 분기가 위에서 return했으므로 여기까지 오면 flowchart)
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
