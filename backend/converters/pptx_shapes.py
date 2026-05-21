# ============================================================
# pptx_shapes.py: Mermaid → 편집 가능한 PowerPoint 도형 변환기
# 상세: Mermaid 코드를 파싱하여 네이티브 도형으로 구성된 PPTX 생성
#       flowchart/graph 및 sequenceDiagram 지원
#       엣지는 mmdc SVG polyline + 8방향 corner detour v2 + 다단계 우회
#       서브그래프 타이틀바도 회피 대상에 포함
# 생성일: 2026-04-07 | 수정일: 2026-05-20 (sequenceDiagram: alt/Note/br decode/동적 슬라이드 보강)
# ============================================================

import html as _html_std
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from io import BytesIO
from typing import Optional

logger = logging.getLogger(__name__)

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_AUTO_SIZE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.util import Inches, Pt
from pptx.oxml.ns import qn
from pptx.oxml import parse_xml
from lxml import etree


# ──────────────────────────────────────────────
# 색상 팔레트 (공통 palette.py에서 가져옴)
# ──────────────────────────────────────────────
from converters.palette import NODE_COLORS, SUBGRAPH_COLORS, TEXT_COLOR
from converters.svg_path import parse_svg_path, path_bounding_box, PathCmd as _SvgPathCmd


def _hex_to_rgb(hex_str: str) -> RGBColor:
    """'#dbeafe' 형식의 hex 문자열을 RGBColor로 변환."""
    h = hex_str.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


_TEXT_RGB = _hex_to_rgb(TEXT_COLOR)

_PALETTE = [
    (_hex_to_rgb(fill), _hex_to_rgb(stroke), _TEXT_RGB)
    for fill, stroke in NODE_COLORS
]

_SUBGRAPH_FILLS = [_hex_to_rgb(fill) for fill, _ in SUBGRAPH_COLORS]
_SUBGRAPH_STROKES = [_hex_to_rgb(stroke) for _, stroke in SUBGRAPH_COLORS]
_SUBGRAPH_BORDER = RGBColor(0x94, 0xA3, 0xB8)


# ──────────────────────────────────────────────
# OOXML 보정 헬퍼
# ──────────────────────────────────────────────

def remove_style_element(element):
    """python-pptx가 자동 생성하는 p:style 요소를 제거한다.
    p:style은 테마 색상을 참조하는데, 직접 포매팅과 동시에 존재하면
    PowerPoint가 파일 손상으로 인식한다."""
    style = element.find(qn("p:style"))
    if style is not None:
        element.remove(style)


def _remove_shadow(shape):
    """도형에서 그림자 효과 제거."""
    spPr = shape._element.find(qn("p:spPr"))
    if spPr is None:
        return
    for eff in spPr.findall(qn("a:effectLst")):
        spPr.remove(eff)
    etree.SubElement(spPr, qn("a:effectLst"))


def _calc_adj(radius_in, w_in, h_in):
    """원하는 절대 커브 반경(인치)을 adj 값(0~50000)으로 변환."""
    min_dim = min(w_in, h_in)
    if min_dim <= 0:
        return 0
    return int(min(radius_in / min_dim * 50000, 50000))


def _set_corner_radius(shape, adj_val, adj2_val=None):
    """도형의 둥근 모서리 반경을 설정 (OOXML avLst 조정)."""
    prstGeom = shape._element.find('.//' + qn('a:prstGeom'))
    if prstGeom is None:
        return
    avLst = prstGeom.find(qn('a:avLst'))
    if avLst is None:
        avLst = etree.SubElement(prstGeom, qn('a:avLst'))
    for old in avLst.findall(qn('a:gd')):
        avLst.remove(old)
    prst = prstGeom.get('prst', '')
    if prst == 'roundRect':
        gd = etree.SubElement(avLst, qn('a:gd'))
        gd.set('name', 'adj')
        gd.set('fmla', f'val {adj_val}')
    elif prst == 'round2SameRect':
        gd1 = etree.SubElement(avLst, qn('a:gd'))
        gd1.set('name', 'adj1')
        gd1.set('fmla', f'val {adj_val}')
        gd2 = etree.SubElement(avLst, qn('a:gd'))
        gd2.set('name', 'adj2')
        gd2.set('fmla', f'val {adj2_val if adj2_val is not None else 0}')


# ──────────────────────────────────────────────
# 데이터 모델
# ──────────────────────────────────────────────

@dataclass
class Node:
    """Mermaid 노드 정보."""
    id: str
    label: str
    shape: str = "rect"          # rect | round | diamond | circle
    subgraph_id: Optional[str] = None

    # 레이아웃 시 채워짐
    x: float = 0.0               # inches
    y: float = 0.0               # inches
    w: float = 2.0               # inches
    h: float = 0.8               # inches


@dataclass
class Edge:
    """Mermaid 엣지 정보."""
    source: str
    target: str
    label: str = ""
    arrow: str = "-->"           # --> | --- | -.-


@dataclass
class Subgraph:
    """Mermaid 서브그래프(클러스터) 정보."""
    id: str
    label: str
    node_ids: list[str] = field(default_factory=list)

    # 레이아웃 시 채워짐
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0


@dataclass
class ParsedDiagram:
    """파싱된 Mermaid 다이어그램 전체 구조."""
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    subgraphs: dict[str, Subgraph] = field(default_factory=dict)
    direction: str = "TB"        # TB | LR | RL | BT


# ──────────────────────────────────────────────
# Mermaid 파서
# ──────────────────────────────────────────────

# 노드 정의 패턴 (엣지 선언이 아닌 단독 노드 정의)
_NODE_ALONE_RE = re.compile(
    r"^\s*(?P<id>[A-Za-z0-9_\-]+)"
    r"(?P<shape_open>[\[\(\{\|]+)"
    r"(?P<label>[^\]\)\}\|]*)"
    r"(?P<shape_close>[\]\)\}\|]+)"
    r"\s*$"
)

# 엣지 패턴: A --> B, A -->|label| B, A --- B, A -.-> B 등
_EDGE_RE = re.compile(
    r"^\s*(?P<src>[A-Za-z0-9_\-]+)"
    r"\s*(?P<arrow>--?>|---?\.?-?>?|==+>?|-\.-?>?)"
    r"(?:\|(?P<label>[^\|]*)\|)?"
    r"\s*(?P<dst>[A-Za-z0-9_\-]+)"
    r"\s*$"
)

# 엣지 + 인라인 노드 정의 패턴 (A[label] --> B[label])
_EDGE_WITH_NODES_RE = re.compile(
    r"^\s*(?P<src_id>[A-Za-z0-9_\-]+)"
    r"(?P<src_open>[\[\(\{]+)?"
    r"(?P<src_label>[^\]\)\}]*)?"
    r"(?P<src_close>[\]\)\}]+)?"
    r"\s*(?P<arrow>--?>|---?\.?-?>?|==+>?|-\.-?>?)"
    r"(?:\|(?P<edge_label>[^\|]*)\|)?"
    r"\s*(?P<dst_id>[A-Za-z0-9_\-]+)"
    r"(?P<dst_open>[\[\(\{]+)?"
    r"(?P<dst_label>[^\]\)\}]*)?"
    r"(?P<dst_close>[\]\)\}]+)?"
    r"\s*$"
)


def _shape_from_tokens(open_tok: str, close_tok: str) -> str:
    """괄호 토큰으로 도형 종류를 결정한다."""
    if not open_tok:
        return "rect"
    o = open_tok.strip()
    c = close_tok.strip() if close_tok else ""
    if o == "((":
        return "circle"
    if o == "{":
        return "diamond"
    if o in ("(", "(["):
        return "round"
    return "rect"


def _clean_label(raw: str) -> str:
    """HTML 태그 및 따옴표를 제거하여 순수 텍스트를 반환한다."""
    text = re.sub(r"<[^>]+>", "", raw or "")
    text = text.strip('"').strip("'").strip()
    return text


def parse_mermaid(code: str) -> ParsedDiagram:
    """Mermaid flowchart/graph 코드를 파싱하여 ParsedDiagram을 반환한다."""
    diagram = ParsedDiagram()
    current_subgraph: Optional[Subgraph] = None

    lines = code.splitlines()
    for raw_line in lines:
        line = raw_line.strip()

        # 주석 제거
        line = re.sub(r"%%.*$", "", line).strip()
        if not line:
            continue

        # 방향 선언: graph LR, flowchart TB 등
        dir_match = re.match(
            r"^(?:graph|flowchart)\s+(TB|TD|LR|RL|BT)\s*$", line, re.I
        )
        if dir_match:
            direction = dir_match.group(1).upper()
            diagram.direction = "LR" if direction == "LR" else "TB"
            continue

        # graph/flowchart 선언만 있는 경우 (방향 없음)
        if re.match(r"^(?:graph|flowchart)\s*$", line, re.I):
            continue

        # 서브그래프 시작
        sg_start = re.match(r"^subgraph\s+(?P<id>[A-Za-z0-9_\-]+)\s*(?:\[(?P<label>[^\]]*)\])?\s*$", line, re.I)
        if sg_start:
            sg_id = sg_start.group("id")
            sg_label = _clean_label(sg_start.group("label") or sg_id)
            current_subgraph = Subgraph(id=sg_id, label=sg_label)
            diagram.subgraphs[sg_id] = current_subgraph
            continue

        # 서브그래프 끝
        if re.match(r"^end\s*$", line, re.I):
            current_subgraph = None
            continue

        # 서브그래프 내 direction 무시
        if re.match(r"^direction\s+", line, re.I):
            continue

        # 엣지 + 인라인 노드 파싱 시도
        edge_match = _EDGE_WITH_NODES_RE.match(line)
        if edge_match:
            g = edge_match.groupdict()
            src_id = g["src_id"]
            dst_id = g["dst_id"]

            # 소스 노드 등록
            if src_id not in diagram.nodes:
                shape = _shape_from_tokens(g.get("src_open") or "", g.get("src_close") or "")
                label = _clean_label(g.get("src_label") or src_id)
                diagram.nodes[src_id] = Node(id=src_id, label=label, shape=shape)
            if current_subgraph and src_id not in current_subgraph.node_ids:
                current_subgraph.node_ids.append(src_id)
                diagram.nodes[src_id].subgraph_id = current_subgraph.id

            # 목적지 노드 등록
            if dst_id not in diagram.nodes:
                shape = _shape_from_tokens(g.get("dst_open") or "", g.get("dst_close") or "")
                label = _clean_label(g.get("dst_label") or dst_id)
                diagram.nodes[dst_id] = Node(id=dst_id, label=label, shape=shape)
            if current_subgraph and dst_id not in current_subgraph.node_ids:
                current_subgraph.node_ids.append(dst_id)
                diagram.nodes[dst_id].subgraph_id = current_subgraph.id

            arrow = g.get("arrow") or "-->"
            edge_label = _clean_label(g.get("edge_label") or "")
            diagram.edges.append(Edge(source=src_id, target=dst_id, label=edge_label, arrow=arrow))
            continue

        # 단독 노드 정의
        node_match = _NODE_ALONE_RE.match(line)
        if node_match:
            nid = node_match.group("id")
            shape = _shape_from_tokens(
                node_match.group("shape_open"), node_match.group("shape_close")
            )
            label = _clean_label(node_match.group("label") or nid)
            if nid not in diagram.nodes:
                diagram.nodes[nid] = Node(id=nid, label=label, shape=shape)
            else:
                # 레이블만 업데이트
                diagram.nodes[nid].label = label
                diagram.nodes[nid].shape = shape
            if current_subgraph and nid not in current_subgraph.node_ids:
                current_subgraph.node_ids.append(nid)
                diagram.nodes[nid].subgraph_id = current_subgraph.id

    return diagram


# ──────────────────────────────────────────────
# 레이아웃 엔진
# ──────────────────────────────────────────────

# 슬라이드 치수 (16:9 와이드스크린)
SLIDE_W = 13.333   # inches
SLIDE_H = 7.5      # inches

# 레이아웃 상수
TITLE_H = 0.6      # 제목 영역 높이
MARGIN = 0.3       # 슬라이드 여백
NODE_W = 2.0       # 노드 기본 너비
NODE_H = 0.75      # 노드 기본 높이
H_GAP = 0.45       # 노드 가로 간격
V_GAP = 0.45       # 노드 세로 간격
SG_PAD = 0.3       # 서브그래프 내부 패딩
SG_TITLE_H = 0.35  # 서브그래프 제목 높이


def _layout_nodes_in_grid(
    node_ids: list[str],
    nodes: dict[str, Node],
    start_x: float,
    start_y: float,
    max_width: float,
) -> tuple[float, float]:
    """노드 목록을 그리드로 배치하고, 점유된 (width, height)를 반환한다."""
    if not node_ids:
        return 0.0, 0.0

    cols = max(1, int((max_width + H_GAP) / (NODE_W + H_GAP)))
    rows = (len(node_ids) + cols - 1) // cols

    for i, nid in enumerate(node_ids):
        col = i % cols
        row = i // cols
        nodes[nid].x = start_x + col * (NODE_W + H_GAP)
        nodes[nid].y = start_y + row * (NODE_H + V_GAP)
        nodes[nid].w = NODE_W
        nodes[nid].h = NODE_H

    total_w = cols * NODE_W + (cols - 1) * H_GAP
    total_h = rows * NODE_H + (rows - 1) * V_GAP
    return total_w, total_h


def compute_layout(diagram: ParsedDiagram) -> None:
    """파싱된 다이어그램에 좌표를 할당한다 (인플레이스 수정)."""
    avail_w = SLIDE_W - 2 * MARGIN
    avail_h = SLIDE_H - TITLE_H - 2 * MARGIN
    content_x = MARGIN
    content_y = TITLE_H + MARGIN

    # 서브그래프가 있는 경우: 서브그래프 단위로 배치
    if diagram.subgraphs:
        # 서브그래프당 열 수 결정 (최대 3열)
        sg_list = list(diagram.subgraphs.values())
        sg_cols = min(len(sg_list), 3)
        sg_col_w = (avail_w - (sg_cols - 1) * H_GAP) / sg_cols

        cur_x = content_x
        cur_y = content_y
        row_h = 0.0
        col_idx = 0

        for sg in sg_list:
            inner_x = cur_x + SG_PAD
            inner_y = cur_y + SG_PAD + SG_TITLE_H
            inner_w = sg_col_w - 2 * SG_PAD

            node_w, node_h = _layout_nodes_in_grid(
                sg.node_ids, diagram.nodes, inner_x, inner_y, inner_w
            )

            sg.x = cur_x
            sg.y = cur_y
            sg.w = sg_col_w
            sg.h = max(node_h + 2 * SG_PAD + SG_TITLE_H, NODE_H + 2 * SG_PAD + SG_TITLE_H)

            row_h = max(row_h, sg.h)
            col_idx += 1

            if col_idx >= sg_cols:
                cur_x = content_x
                cur_y += row_h + V_GAP
                row_h = 0.0
                col_idx = 0
            else:
                cur_x += sg_col_w + H_GAP

        # 서브그래프에 속하지 않는 노드는 하단에 배치
        orphan_ids = [nid for nid in diagram.nodes if not diagram.nodes[nid].subgraph_id]
        if orphan_ids:
            orphan_y = cur_y + row_h + V_GAP if col_idx > 0 else cur_y
            _layout_nodes_in_grid(orphan_ids, diagram.nodes, content_x, orphan_y, avail_w)

    else:
        # 서브그래프 없음: 전체 노드를 그리드로 배치
        _layout_nodes_in_grid(
            list(diagram.nodes.keys()), diagram.nodes, content_x, content_y, avail_w
        )

    # 오버플로우 감지 → 자동 축소
    _scale_to_fit(diagram, content_x, content_y, avail_w, avail_h)


def _scale_to_fit(
    diagram: "ParsedDiagram",
    content_x: float,
    content_y: float,
    avail_w: float,
    avail_h: float,
) -> None:
    """배치된 다이어그램이 슬라이드를 벗어나면 전체를 축소한다."""
    if not diagram.nodes:
        return

    max_x = max(n.x + n.w for n in diagram.nodes.values())
    max_y = max(n.y + n.h for n in diagram.nodes.values())

    # 서브그래프 영역도 포함
    for sg in diagram.subgraphs.values():
        max_x = max(max_x, sg.x + sg.w)
        max_y = max(max_y, sg.y + sg.h)

    bound_r = content_x + avail_w
    bound_b = content_y + avail_h

    if max_x <= bound_r and max_y <= bound_b:
        return  # 축소 불필요

    scale_x = avail_w / (max_x - content_x) if max_x > bound_r else 1.0
    scale_y = avail_h / (max_y - content_y) if max_y > bound_b else 1.0
    scale = min(scale_x, scale_y)
    scale = max(scale, 0.4)  # 최소 40% — 가독성 보장

    for node in diagram.nodes.values():
        node.x = content_x + (node.x - content_x) * scale
        node.y = content_y + (node.y - content_y) * scale
        node.w *= scale
        node.h *= scale

    for sg in diagram.subgraphs.values():
        sg.x = content_x + (sg.x - content_x) * scale
        sg.y = content_y + (sg.y - content_y) * scale
        sg.w *= scale
        sg.h *= scale


# ──────────────────────────────────────────────
# PPTX 렌더러
# ──────────────────────────────────────────────

def _set_shape_fill(shape, fill_color: RGBColor, stroke_color: RGBColor) -> None:
    """도형의 채우기 색상과 테두리 색상을 설정한다."""
    fill = shape.fill
    fill.solid()
    fill.fore_color.rgb = fill_color

    line = shape.line
    line.color.rgb = stroke_color
    line.width = Pt(0.75)


def _wrap_label_smart(text: str, max_chars: int = 18) -> str:
    """CamelCase/단어 경계에서 줄바꿈을 삽입해 가독성을 높인다.

    이미 \\n이 있으면 그대로 반환. CamelCase('getUserName') 경계를
    공백으로 변환한 뒤 max_chars 기준으로 줄을 분할한다.
    """
    import re
    if "\n" in text:
        return text
    # CamelCase 경계를 공백으로 분리
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    words = spaced.split()
    lines: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for w in words:
        extra = 1 if cur else 0  # 단어 사이 공백
        if cur_len + extra + len(w) > max_chars and cur:
            lines.append(" ".join(cur))
            cur, cur_len = [w], len(w)
        else:
            cur.append(w)
            cur_len += extra + len(w)
    if cur:
        lines.append(" ".join(cur))
    return "\n".join(lines)


def _set_text(shape, text: str, font_size: int = 9, bold: bool = False,
              color: RGBColor = RGBColor(0x1E, 0x29, 0x3B)) -> None:
    """도형의 텍스트 프레임을 설정한다. word_wrap=True로 줄 바꿈 허용."""
    tf = shape.text_frame
    tf.word_wrap = True
    tf.auto_size = None  # 폰트 자동 축소 비활성화 — 박스를 확장해 가독성 보장
    tf.margin_top = Pt(2)
    tf.margin_bottom = Pt(2)
    tf.margin_left = Pt(4)
    tf.margin_right = Pt(4)

    # 기존 단락 초기화
    tf.clear()
    para = tf.paragraphs[0]
    para.alignment = PP_ALIGN.CENTER

    run = para.add_run()
    run.text = _wrap_label_smart(text)
    run.font.size = Pt(font_size)
    run.font.bold = bold
    run.font.color.rgb = color

    # 한글 폰트 우선: 맑은 고딕
    try:
        rPr = run._r.get_or_add_rPr()
        # 동아시아 폰트 설정
        ea = etree.SubElement(rPr, qn("a:ea"))
        ea.set("typeface", "맑은 고딕")
        # 라틴 폰트 설정
        latin = rPr.find(qn("a:latin"))
        if latin is None:
            latin = etree.SubElement(rPr, qn("a:latin"))
        latin.set("typeface", "맑은 고딕")
    except Exception:
        pass  # 폰트 설정 실패 시 기본값 사용


def _vertical_center_text(shape) -> None:
    """텍스트를 도형 안에서 수직 가운데 정렬한다."""
    from pptx.enum.text import MSO_ANCHOR
    shape.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE


def _set_text_multiline(
    shape,
    text: str,
    font_size: int = 9,
    color: RGBColor = RGBColor(0x1E, 0x29, 0x3B),
) -> None:
    """ER 엔티티 등 '\\n' 포함 라벨을 multi-paragraph text frame으로 설정.

    첫 줄: bold + center + (font_size+1)pt  — 엔티티 이름
    이후 줄: normal + left + (font_size-1)pt — 속성 행 (type name keys comment)
    """
    from pptx.enum.text import MSO_ANCHOR

    lines = [ln for ln in text.split("\n") if ln.strip()]
    tf = shape.text_frame
    tf.word_wrap = True
    tf.auto_size = None  # 폰트 자동 축소 비활성화 — 박스를 확장해 가독성 보장
    tf.margin_top = Pt(3)
    tf.margin_bottom = Pt(2)
    tf.margin_left = Pt(4)
    tf.margin_right = Pt(4)
    tf.vertical_anchor = MSO_ANCHOR.TOP

    tf.clear()
    for i, line in enumerate(lines):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = PP_ALIGN.CENTER if i == 0 else PP_ALIGN.LEFT
        run = para.add_run()
        run.text = line
        run.font.size = Pt(font_size + 1) if i == 0 else Pt(max(9, font_size - 1))  # F.2: 9pt 하한
        run.font.bold = (i == 0)
        run.font.color.rgb = color
        try:
            rPr = run._r.get_or_add_rPr()
            ea = etree.SubElement(rPr, qn("a:ea"))
            ea.set("typeface", "맑은 고딕")
            latin = rPr.find(qn("a:latin"))
            if latin is None:
                latin = etree.SubElement(rPr, qn("a:latin"))
            latin.set("typeface", "맑은 고딕")
        except Exception:
            pass


def _add_rounded_rect(slide, x: float, y: float, w: float, h: float,
                      fill: RGBColor, stroke: RGBColor,
                      text: str, font_size: int = 9,
                      text_color: RGBColor = RGBColor(0x1E, 0x29, 0x3B)) -> object:
    """모서리가 둥근 직사각형 도형을 슬라이드에 추가한다."""
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Inches(x), Inches(y), Inches(w), Inches(h)
    )

    _set_shape_fill(shape, fill, stroke)
    if "\n" in text:
        # ER 엔티티 등 멀티라인 라벨: multi-paragraph 렌더링
        _set_text_multiline(shape, text, font_size=font_size, color=text_color)
    else:
        _set_text(shape, text, font_size=font_size, color=text_color)
        _vertical_center_text(shape)
    remove_style_element(shape._element)
    _remove_shadow(shape)
    return shape


def _add_diamond(slide, x: float, y: float, w: float, h: float,
                 fill: RGBColor, stroke: RGBColor,
                 text: str, font_size: int = 9,
                 text_color: RGBColor = RGBColor(0x1E, 0x29, 0x3B)) -> object:
    """마름모 도형을 슬라이드에 추가한다."""
    shape = slide.shapes.add_shape(
        MSO_SHAPE.DIAMOND,
        Inches(x), Inches(y), Inches(w), Inches(h)
    )
    _set_shape_fill(shape, fill, stroke)
    _set_text(shape, text, font_size=font_size, color=text_color)
    _vertical_center_text(shape)
    remove_style_element(shape._element)
    _remove_shadow(shape)
    return shape


def _add_oval(slide, x: float, y: float, w: float, h: float,
              fill: RGBColor, stroke: RGBColor,
              text: str, font_size: int = 9,
              text_color: RGBColor = RGBColor(0x1E, 0x29, 0x3B)) -> object:
    """타원 도형을 슬라이드에 추가한다."""
    shape = slide.shapes.add_shape(
        MSO_SHAPE.OVAL,
        Inches(x), Inches(y), Inches(w), Inches(h)
    )
    _set_shape_fill(shape, fill, stroke)
    _set_text(shape, text, font_size=font_size, color=text_color)
    _vertical_center_text(shape)
    remove_style_element(shape._element)
    _remove_shadow(shape)
    return shape


def _add_connector_elbow(slide, src_shape, dst_shape,
                         label: str = "", dashed: bool = False) -> None:
    """두 도형을 잇는 ELBOW(꺾임선) 커넥터 + 화살표 머리를 추가한다.

    스마트 연결점: 두 도형의 상대 위치에 따라 상/하/좌/우 자동 선택.
    """
    from pptx.enum.shapes import MSO_CONNECTOR

    # 스마트 연결점 선택
    src_cx = src_shape.left + src_shape.width // 2
    src_cy = src_shape.top + src_shape.height // 2
    dst_cx = dst_shape.left + dst_shape.width // 2
    dst_cy = dst_shape.top + dst_shape.height // 2

    dx_abs = abs(dst_cx - src_cx)
    dy_abs = abs(dst_cy - src_cy)

    if dx_abs > dy_abs * 1.5:
        if dst_cx > src_cx:
            sx = src_shape.left + src_shape.width
            sy = src_cy
            ex = dst_shape.left
            ey = dst_cy
        else:
            sx = src_shape.left
            sy = src_cy
            ex = dst_shape.left + dst_shape.width
            ey = dst_cy
    else:
        if dst_cy >= src_cy:
            sx = src_cx
            sy = src_shape.top + src_shape.height
            ex = dst_cx
            ey = dst_shape.top
        else:
            sx = src_cx
            sy = src_shape.top
            ex = dst_cx
            ey = dst_shape.top + dst_shape.height

    # zero extent 방지
    if sx == ex:
        ex += 1
    if sy == ey:
        ey += 1

    connector = slide.shapes.add_connector(
        MSO_CONNECTOR.ELBOW, sx, sy, ex, ey
    )
    connector.line.color.rgb = RGBColor(0x47, 0x55, 0x69)
    connector.line.width = Pt(1.2)

    if dashed:
        from pptx.enum.dml import MSO_LINE_DASH_STYLE
        connector.line.dash_style = MSO_LINE_DASH_STYLE.DASH

    # 화살표 머리 추가 (a:tailEnd)
    ln = connector._element.find(".//" + qn("a:ln"))
    if ln is not None:
        tail = etree.SubElement(ln, qn("a:tailEnd"))
        tail.set("type", "triangle")
        tail.set("w", "med")
        tail.set("len", "med")

    remove_style_element(connector._element)

    # 엣지 레이블
    if label:
        mx = (sx + ex) // 2
        my = (sy + ey) // 2
        label_box = slide.shapes.add_textbox(
            mx - Inches(0.6), my - Inches(0.15),
            Inches(1.2), Inches(0.3)
        )
        tf = label_box.text_frame
        tf.word_wrap = False
        para = tf.paragraphs[0]
        para.alignment = PP_ALIGN.CENTER
        run = para.add_run()
        run.text = label.replace("\n", " ")
        run.font.size = Pt(7)
        run.font.color.rgb = RGBColor(0x47, 0x55, 0x69)


def _add_node_shape(slide, node: Node, palette_idx: int) -> object:
    """노드의 shape 속성에 맞는 도형을 슬라이드에 추가하고 반환한다."""
    fill_c, stroke_c, text_c = _PALETTE[palette_idx % len(_PALETTE)]

    if node.shape == "diamond":
        return _add_diamond(
            slide, node.x, node.y, node.w, node.h,
            fill_c, stroke_c, node.label, text_color=text_c
        )
    elif node.shape == "circle":
        return _add_oval(
            slide, node.x, node.y, node.w, node.h,
            fill_c, stroke_c, node.label, text_color=text_c
        )
    else:
        # rect, round 모두 rounded rectangle로 렌더링
        return _add_rounded_rect(
            slide, node.x, node.y, node.w, node.h,
            fill_c, stroke_c, node.label, text_color=text_c
        )


def _node_center(node: Node) -> tuple[float, float]:
    """노드의 중심 좌표를 반환한다."""
    return node.x + node.w / 2, node.y + node.h / 2


def _add_subgraph_box(slide, sg: Subgraph, fill_color: RGBColor, idx: int) -> None:
    """서브그래프를 draw.io 스타일 2개 도형으로 추가한다.
    1) 컨테이너: ROUNDED_RECTANGLE, 흰색 배경 + 얇은 윤곽선, 그림자 없음
    2) 제목: ROUND_2_SAME_RECTANGLE, 연한 색상 배경 + 텍스트 직접 포함
    """
    stroke_color = _SUBGRAPH_STROKES[idx % len(_SUBGRAPH_STROKES)]
    title_h = 0.26
    CORNER_RADIUS_IN = 0.10
    line_w = Pt(0.75)

    adj_container = _calc_adj(CORNER_RADIUS_IN, sg.w, sg.h)
    adj_title = _calc_adj(CORNER_RADIUS_IN, sg.w, title_h)

    # 1) 컨테이너 — 흰색 배경 + 얇은 윤곽선
    container = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Inches(sg.x), Inches(sg.y), Inches(sg.w), Inches(sg.h)
    )
    container.fill.solid()
    container.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    container.line.color.rgb = stroke_color
    container.line.width = line_w
    _set_corner_radius(container, adj_container)
    _remove_shadow(container)
    remove_style_element(container._element)
    tf = container.text_frame
    tf.margin_top = Pt(0)
    tf.margin_bottom = Pt(0)

    # 2) 제목 도형 — 둥근 위쪽 모서리 + 텍스트 직접 포함
    title_shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUND_2_SAME_RECTANGLE,
        Inches(sg.x), Inches(sg.y), Inches(sg.w), Inches(title_h)
    )
    title_shape.fill.solid()
    title_shape.fill.fore_color.rgb = fill_color
    title_shape.line.color.rgb = stroke_color
    title_shape.line.width = line_w
    _set_corner_radius(title_shape, adj_title, adj2_val=0)
    _remove_shadow(title_shape)
    remove_style_element(title_shape._element)

    # 제목 텍스트를 도형 안에 직접 배치
    tf2 = title_shape.text_frame
    tf2.auto_size = None
    tf2.word_wrap = True
    tf2.margin_top = Pt(3)
    tf2.margin_bottom = Pt(2)
    tf2.margin_left = Pt(6)
    tf2.margin_right = Pt(6)
    txBody = title_shape._element.find(qn("p:txBody"))
    if txBody is not None:
        bodyPr = txBody.find(qn("a:bodyPr"))
        if bodyPr is not None:
            bodyPr.set("anchor", "ctr")
    p = tf2.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = sg.label
    run.font.size = Pt(9)
    run.font.bold = True
    run.font.color.rgb = stroke_color

    try:
        rPr = run._r.get_or_add_rPr()
        ea = etree.SubElement(rPr, qn("a:ea"))
        ea.set("typeface", "맑은 고딕")
        latin = rPr.find(qn("a:latin"))
        if latin is None:
            latin = etree.SubElement(rPr, qn("a:latin"))
        latin.set("typeface", "맑은 고딕")
    except Exception:
        pass


# ──────────────────────────────────────────────
# 시퀀스 다이어그램 파서 / 렌더러
# ──────────────────────────────────────────────

# 시퀀스 다이어그램 레이아웃 상수
_SEQ_PARTICIPANT_W = 2.0    # 참여자 박스 너비 (inches)
_SEQ_PARTICIPANT_H = 0.6    # 참여자 박스 높이 (inches)
_SEQ_PARTICIPANT_GAP = 0.5  # 참여자 간격 (inches)
_SEQ_MSG_GAP = 0.5          # 메시지 세로 간격 (inches)
_SEQ_TOP_MARGIN = 1.0       # 상단 여백 (제목 포함, inches)

# 시퀀스 메시지 화살표 패턴
_SEQ_ARROW_RE = re.compile(
    r"^\s*(?P<src>[A-Za-z0-9_]+)\s*"
    r"(?P<arrow>-->>|--?>|-->|->|->>)"
    r"\s*(?P<dst>[A-Za-z0-9_]+)\s*"
    r":\s*(?P<label>.+)$"
)

# participant/actor 선언 패턴
_SEQ_PARTICIPANT_RE = re.compile(
    r"^\s*(?:participant|actor)\s+(?P<id>[A-Za-z0-9_]+)"
    r"(?:\s+as\s+(?P<label>.+))?\s*$",
    re.I
)

# <br/> 태그 패턴 (Y.2)
_SEQ_BR_RE = re.compile(r'<br\s*/?>', re.I)


def _br_decode(text: str) -> str:
    """HTML <br/> → \\n 변환 + HTML entity decode (Y.2)."""
    return _html_std.unescape(_SEQ_BR_RE.sub('\n', text))


# Note over / Note left of / Note right of 파싱 패턴 (Y.1)
_SEQ_NOTE_RE = re.compile(
    r"^\s*[Nn]ote\s+(?:over|left\s+of|right\s+of)\s+"
    r"(?P<actors>[A-Za-z0-9_]+(?:\s*,\s*[A-Za-z0-9_]+)*)"
    r"\s*:\s*(?P<label>.+)$"
)

# alt / else / loop / opt / par / end 블록 패턴 (Y.1)
_SEQ_BLOCK_RE = re.compile(
    r"^\s*(?P<kind>alt|else|loop|opt|par|end)\b(?:\s+(?P<label>.+))?$",
    re.I
)


def _parse_sequence(mermaid_code: str) -> tuple[list[tuple[str, str]], list[dict]]:
    """시퀀스 다이어그램 Mermaid 코드를 파싱한다.

    Returns:
        (participants, events) 튜플.
        participants: [(id, label), ...] 순서 보장 리스트. label은 <br/> decode 완료.
        events: 이벤트 딕셔너리 리스트.
            {"type": "msg",       "src", "dst", "label", "dashed"}
            {"type": "note",      "actors": [str], "label"}
            {"type": "blk_start", "kind": "alt"|"loop"|"opt"|"par", "label"}
            {"type": "blk_else",  "label"}
            {"type": "blk_end"}
    """
    participants: list[tuple[str, str]] = []
    participant_ids: list[str] = []
    events: list[dict] = []

    for raw_line in mermaid_code.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("sequencediagram") or line.startswith("%%"):
            continue

        # participant/actor 선언 (Y.4: alias <br/> decode)
        p_match = _SEQ_PARTICIPANT_RE.match(line)
        if p_match:
            pid = p_match.group("id")
            plabel = _br_decode((p_match.group("label") or pid).strip())
            if pid not in participant_ids:
                participant_ids.append(pid)
                participants.append((pid, plabel))
            continue

        # Note over / Note left of / Note right of (Y.1)
        n_match = _SEQ_NOTE_RE.match(line)
        if n_match:
            actors = [a.strip() for a in n_match.group("actors").split(",")]
            label = _br_decode(n_match.group("label").strip())
            events.append({"type": "note", "actors": actors, "label": label})
            continue

        # alt / else / loop / opt / par / end 블록 (Y.1)
        b_match = _SEQ_BLOCK_RE.match(line)
        if b_match:
            kind = b_match.group("kind").lower()
            label = _br_decode((b_match.group("label") or "").strip())
            if kind == "end":
                events.append({"type": "blk_end"})
            elif kind == "else":
                events.append({"type": "blk_else", "label": label})
            else:
                events.append({"type": "blk_start", "kind": kind, "label": label})
            continue

        # 메시지 화살표 (Y.2: label <br/> decode)
        m_match = _SEQ_ARROW_RE.match(line)
        if m_match:
            src = m_match.group("src")
            dst = m_match.group("dst")
            label = _br_decode(m_match.group("label").strip())
            arrow = m_match.group("arrow")
            dashed = "--" in arrow
            events.append({"type": "msg", "src": src, "dst": dst,
                           "label": label, "dashed": dashed})

            # 암묵적 참여자 등록
            for actor_id in (src, dst):
                if actor_id not in participant_ids:
                    participant_ids.append(actor_id)
                    participants.append((actor_id, actor_id))

    return participants, events


def _seq_add_label(slide, text: str,
                   x_emu: int, y_emu: int, w_emu: int, h_emu: int,
                   align=PP_ALIGN.CENTER) -> None:
    """시퀀스 메시지/Note 라벨 텍스트박스를 추가한다 (\\n 멀티라인 지원)."""
    txb = slide.shapes.add_textbox(x_emu, y_emu, w_emu, h_emu)
    tf = txb.text_frame
    tf.word_wrap = True
    tf.margin_top = Pt(1)
    tf.margin_bottom = Pt(1)
    tf.margin_left = Pt(2)
    tf.margin_right = Pt(2)
    lines = text.split('\n')
    for li, line_text in enumerate(lines):
        para = tf.paragraphs[0] if li == 0 else tf.add_paragraph()
        para.alignment = align
        run = para.add_run()
        run.text = line_text
        run.font.size = Pt(7)
        run.font.color.rgb = RGBColor(0x47, 0x55, 0x69)


# alt/loop 블록 헤더 색상 맵 (P.1: WCAG 4.5:1 통과 — 라이트 톤 + 어두운 텍스트)
_SEQ_BLK_COLORS: dict[str, RGBColor] = {
    "alt":  RGBColor(0x93, 0xC5, 0xFD),  # blue-300   (#93c5fd)
    "loop": RGBColor(0x86, 0xEF, 0xAC),  # green-300  (#86efac)
    "opt":  RGBColor(0xFC, 0xD3, 0x4D),  # amber-300  (#fcd34d)
    "par":  RGBColor(0xC4, 0xB5, 0xFD),  # violet-300 (#c4b5fd)
}

# alt/loop 블록 헤더 텍스트 색상 (P.1: 라이트 배경에 맞는 어두운 텍스트)
_SEQ_BLK_TEXT_COLORS: dict[str, RGBColor] = {
    "alt":  RGBColor(0x1E, 0x29, 0x3B),  # slate-900
    "loop": RGBColor(0x06, 0x4E, 0x3B),  # emerald-900
    "opt":  RGBColor(0x78, 0x35, 0x0F),  # amber-900
    "par":  RGBColor(0x4C, 0x1D, 0x95),  # violet-900
}


def _render_sequence(mermaid_code: str, title: str = "") -> bytes:
    """시퀀스 다이어그램 Mermaid 코드를 네이티브 도형 PPTX로 변환한다.

    Args:
        mermaid_code: sequenceDiagram Mermaid 코드 문자열.
        title: 슬라이드 상단 제목.

    Returns:
        생성된 PPTX 파일의 바이트 데이터.
    """
    from pptx.enum.dml import MSO_LINE_DASH_STYLE

    participants, events = _parse_sequence(mermaid_code)

    if not participants:
        raise ValueError("시퀀스 다이어그램에서 참여자를 찾을 수 없습니다.")

    n_part = len(participants)

    # Y.3: 동적 슬라이드 폭 — participant 수에 맞게 폭·박스 크기 조정
    _PART_GAP = _SEQ_PARTICIPANT_GAP  # 0.5"
    avail_w = SLIDE_W - 2 * MARGIN   # 12.733"
    total_gaps = (n_part - 1) * _PART_GAP
    # participant 박스 폭: 슬라이드에 맞추되 최소 1.0"
    part_w = (
        min(_SEQ_PARTICIPANT_W, (avail_w - total_gaps) / n_part)
        if n_part > 1 else _SEQ_PARTICIPANT_W
    )
    part_w = max(part_w, 1.0)
    total_part_w = n_part * part_w + (n_part - 1) * _PART_GAP
    slide_w = max(SLIDE_W, total_part_w + 2 * MARGIN)

    # ── Pass 1: 이벤트별 y 위치 계산 ─────────────────────
    part_y = _SEQ_TOP_MARGIN
    msg_y_start = part_y + _SEQ_PARTICIPANT_H + 0.3
    cur_y = msg_y_start
    y_positions: list[float] = []

    for event in events:
        y_positions.append(cur_y)
        etype = event["type"]
        if etype == "msg":
            cur_y += _SEQ_MSG_GAP
        elif etype == "note":
            n_lines = len(event["label"].split('\n'))
            cur_y += max(0.35, n_lines * 0.22 + 0.15)
        elif etype in ("blk_start", "blk_else"):
            cur_y += 0.28
        else:  # blk_end
            cur_y += 0.05

    msg_y_end = cur_y + 0.3

    # Y.3: 동적 슬라이드 높이
    slide_h = max(SLIDE_H, msg_y_end + 0.3)

    # ── alt/loop 블록 스팬 계산 ─────────────────────────
    alt_blocks: list[dict] = []
    blk_stack: list[dict] = []
    for i, event in enumerate(events):
        etype = event["type"]
        y = y_positions[i]
        if etype == "blk_start":
            blk_stack.append({
                "kind": event.get("kind", "alt"),
                "label": event.get("label", ""),
                "start_y": y,
                "else_ys": [],
            })
        elif etype == "blk_else" and blk_stack:
            blk_stack[-1]["else_ys"].append(y)
        elif etype == "blk_end" and blk_stack:
            blk = blk_stack.pop()
            blk["end_y"] = y + 0.05
            alt_blocks.append(blk)
    for blk in blk_stack:  # 닫히지 않은 블록
        blk["end_y"] = msg_y_end - 0.1
        alt_blocks.append(blk)

    # ── PPTX 생성 ────────────────────────────────────────
    prs = Presentation()
    prs.slide_width = Inches(slide_w)
    prs.slide_height = Inches(slide_h)

    blank_layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank_layout)

    # 배경: 흰색
    background = slide.background
    background.fill.solid()
    background.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    # 제목
    if title:
        title_box = slide.shapes.add_textbox(
            Inches(MARGIN), Inches(0.1),
            Inches(slide_w - 2 * MARGIN), Inches(TITLE_H)
        )
        tf = title_box.text_frame
        para = tf.paragraphs[0]
        para.alignment = PP_ALIGN.LEFT
        run = para.add_run()
        run.text = title
        run.font.size = Pt(22)
        run.font.bold = True
        run.font.color.rgb = RGBColor(0x1E, 0x29, 0x3B)
        try:
            rPr = run._r.get_or_add_rPr()
            ea = etree.SubElement(rPr, qn("a:ea"))
            ea.set("typeface", "맑은 고딕")
            latin = rPr.find(qn("a:latin"))
            if latin is None:
                latin = etree.SubElement(rPr, qn("a:latin"))
            latin.set("typeface", "맑은 고딕")
        except Exception:
            pass

        # 제목 하단 구분선
        line_bar = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Inches(MARGIN), Inches(TITLE_H + 0.05),
            Inches(slide_w - 2 * MARGIN), Inches(0.03)
        )
        line_bar.fill.solid()
        line_bar.fill.fore_color.rgb = RGBColor(0x3B, 0x82, 0xF6)
        line_bar.line.fill.background()
        remove_style_element(line_bar._element)
        _remove_shadow(line_bar)

    # 참여자 배치 계산: 슬라이드 중앙 정렬
    start_x = (slide_w - total_part_w) / 2.0

    # ── Y.1: alt/loop 블록 박스 (배경 레이어로 먼저 그림) ─
    _BLK_BOX_PAD = 0.12   # 블록 박스가 참여자 영역보다 넓어지는 패딩
    _BLK_HEADER_H = 0.24
    blk_box_x = start_x - _BLK_BOX_PAD
    blk_box_w = total_part_w + 2 * _BLK_BOX_PAD

    for blk in alt_blocks:
        kind = blk["kind"]
        label = blk.get("label", "")
        by = blk["start_y"]
        bh = max(blk["end_y"] - by, _BLK_HEADER_H + 0.1)

        # 외곽선 박스 (투명 배경 + 점선 테두리)
        box_shape = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Inches(blk_box_x), Inches(by), Inches(blk_box_w), Inches(bh)
        )
        box_shape.fill.background()
        box_shape.line.color.rgb = RGBColor(0x64, 0x74, 0x8B)
        box_shape.line.width = Pt(1.0)
        box_shape.line.dash_style = MSO_LINE_DASH_STYLE.DASH
        remove_style_element(box_shape._element)
        _remove_shadow(box_shape)

        # 헤더 색상 탭 ([alt] / [loop] 등) — P.1: 라이트 배경 + 어두운 텍스트
        hdr_color = _SEQ_BLK_COLORS.get(kind, RGBColor(0xE2, 0xE8, 0xF0))
        hdr_text_color = _SEQ_BLK_TEXT_COLORS.get(kind, RGBColor(0x1E, 0x29, 0x3B))
        hdr_shape = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Inches(blk_box_x), Inches(by), Inches(1.0), Inches(_BLK_HEADER_H)
        )
        hdr_shape.fill.solid()
        hdr_shape.fill.fore_color.rgb = hdr_color
        hdr_shape.line.fill.background()
        remove_style_element(hdr_shape._element)
        _remove_shadow(hdr_shape)

        tf = hdr_shape.text_frame
        tf.word_wrap = False
        tf.margin_top = Pt(1)
        tf.margin_left = Pt(3)
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.LEFT
        run = p.add_run()
        run.text = f"[{kind}]"
        run.font.size = Pt(7)
        run.font.bold = True
        run.font.color.rgb = hdr_text_color

        # 헤더 옆 라벨 (조건 텍스트)
        if label:
            lbl_box = slide.shapes.add_textbox(
                Inches(blk_box_x + 1.05), Inches(by + 0.02),
                Inches(blk_box_w - 1.1), Inches(_BLK_HEADER_H - 0.04)
            )
            tf2 = lbl_box.text_frame
            tf2.word_wrap = False
            tf2.margin_top = Pt(1)
            p2 = tf2.paragraphs[0]
            p2.alignment = PP_ALIGN.LEFT
            run2 = p2.add_run()
            run2.text = label
            run2.font.size = Pt(7)
            run2.font.italic = True
            run2.font.color.rgb = RGBColor(0x47, 0x55, 0x69)

        # else 구분선 + "[else]" 라벨
        for else_y in blk["else_ys"]:
            else_line = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE,
                Inches(blk_box_x), Inches(else_y),
                Inches(blk_box_w), Inches(0.02)
            )
            else_line.fill.solid()
            else_line.fill.fore_color.rgb = RGBColor(0x94, 0xA3, 0xB8)
            else_line.line.fill.background()
            remove_style_element(else_line._element)
            _remove_shadow(else_line)

            else_lbl = slide.shapes.add_textbox(
                Inches(blk_box_x + 0.05), Inches(else_y + 0.02),
                Inches(0.6), Inches(0.2)
            )
            tf3 = else_lbl.text_frame
            p3 = tf3.paragraphs[0]
            run3 = p3.add_run()
            run3.text = "[else]"
            run3.font.size = Pt(6)
            run3.font.italic = True
            run3.font.color.rgb = RGBColor(0x64, 0x74, 0x8B)

    # ── 참여자 박스 ──────────────────────────────────────
    part_centers: dict[str, float] = {}

    for i, (pid, plabel) in enumerate(participants):
        px = start_x + i * (part_w + _PART_GAP)
        cx = px + part_w / 2.0
        fill_c, stroke_c, text_c = _PALETTE[i % len(_PALETTE)]
        shape = _add_rounded_rect(
            slide, px, part_y, part_w, _SEQ_PARTICIPANT_H,
            fill_c, stroke_c, plabel, font_size=9, text_color=text_c
        )
        tf = shape.text_frame
        for para in tf.paragraphs:
            for run in para.runs:
                run.font.bold = True
        part_centers[pid] = cx

    # ── 생명선 (수직 점선) ───────────────────────────────
    for pid, cx in part_centers.items():
        lifeline_top_emu = Inches(part_y + _SEQ_PARTICIPANT_H)
        lifeline_bot_emu = Inches(msg_y_end)
        cx_emu = Inches(cx)
        connector = slide.shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT,
            cx_emu, lifeline_top_emu,
            cx_emu + 1, lifeline_bot_emu  # +1 EMU: zero extent 방지
        )
        connector.line.color.rgb = RGBColor(0x94, 0xA3, 0xB8)
        connector.line.width = Pt(0.75)
        connector.line.dash_style = MSO_LINE_DASH_STYLE.DASH
        remove_style_element(connector._element)
        _remove_shadow(connector)

    # ── 이벤트 렌더링 ────────────────────────────────────
    for idx, event in enumerate(events):
        etype = event["type"]
        y = y_positions[idx]
        msg_y_emu = Inches(y)

        if etype == "note":
            # Y.1: Note 박스 (노란색 메모 스타일)
            actors = event["actors"]
            label = event["label"]
            valid = [a for a in actors if a in part_centers]
            if valid:
                min_cx = min(part_centers[a] for a in valid)
                max_cx = max(part_centers[a] for a in valid)
                nx = min_cx - part_w / 2.0
                nw = max(max_cx - min_cx + part_w, 1.5)
            else:
                nx = start_x
                nw = 2.0
            n_lines = len(label.split('\n'))
            nh = max(0.3, n_lines * 0.22 + 0.12)

            note_shape = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE,
                Inches(nx), Inches(y - 0.02), Inches(nw), Inches(nh)
            )
            note_shape.fill.solid()
            note_shape.fill.fore_color.rgb = RGBColor(0xFF, 0xF3, 0x9C)
            note_shape.line.color.rgb = RGBColor(0xD9, 0x7D, 0x06)
            note_shape.line.width = Pt(0.75)
            remove_style_element(note_shape._element)
            _remove_shadow(note_shape)

            tf = note_shape.text_frame
            tf.word_wrap = True
            tf.margin_top = Pt(2)
            tf.margin_bottom = Pt(2)
            tf.margin_left = Pt(4)
            tf.margin_right = Pt(4)
            note_lines = label.split('\n')
            for li, line_text in enumerate(note_lines):
                np_ = tf.paragraphs[0] if li == 0 else tf.add_paragraph()
                np_.alignment = PP_ALIGN.CENTER
                nr = np_.add_run()
                nr.text = line_text
                nr.font.size = Pt(7)
                nr.font.color.rgb = RGBColor(0x78, 0x35, 0x00)

        elif etype in ("blk_start", "blk_else", "blk_end"):
            pass  # 이미 블록 박스로 처리됨

        elif etype == "msg":
            src = event["src"]
            dst = event["dst"]
            label = event["label"]
            dashed = event["dashed"]

            if src not in part_centers or dst not in part_centers:
                continue

            sx_emu = Inches(part_centers[src])
            dx_emu = Inches(part_centers[dst])

            if src == dst:
                # 자기 자신 메시지: 오른쪽 루프
                loop_offset = Inches(0.3)
                loop_h = Inches(_SEQ_MSG_GAP * 0.4)

                # 수평 → 수직 → 수평(화살표) 세그먼트
                segs = [
                    (sx_emu, msg_y_emu, sx_emu + loop_offset, msg_y_emu, False),
                    (sx_emu + loop_offset, msg_y_emu,
                     sx_emu + loop_offset, msg_y_emu + loop_h, False),
                    (sx_emu + loop_offset, msg_y_emu + loop_h,
                     sx_emu, msg_y_emu + loop_h, True),
                ]
                for x1, y1, x2, y2, add_arrow in segs:
                    c = slide.shapes.add_connector(
                        MSO_CONNECTOR.STRAIGHT, x1, y1, x2, y2)
                    c.line.color.rgb = RGBColor(0x47, 0x55, 0x69)
                    c.line.width = Pt(1.2)
                    if dashed:
                        c.line.dash_style = MSO_LINE_DASH_STYLE.DASH
                    if add_arrow:
                        ln = c._element.find(".//" + qn("a:ln"))
                        if ln is not None:
                            tail = etree.SubElement(ln, qn("a:tailEnd"))
                            tail.set("type", "triangle")
                            tail.set("w", "med")
                            tail.set("len", "med")
                    remove_style_element(c._element)
                    _remove_shadow(c)

                if label:
                    msg_lines = label.split('\n')
                    lh = max(0.25, len(msg_lines) * 0.18)
                    _seq_add_label(slide, label,
                                   sx_emu + loop_offset,
                                   msg_y_emu - Inches(lh),
                                   Inches(1.4), Inches(lh),
                                   align=PP_ALIGN.LEFT)
            else:
                if sx_emu == dx_emu:
                    dx_emu += 1

                connector = slide.shapes.add_connector(
                    MSO_CONNECTOR.STRAIGHT,
                    sx_emu, msg_y_emu, dx_emu, msg_y_emu
                )
                connector.line.color.rgb = RGBColor(0x47, 0x55, 0x69)
                connector.line.width = Pt(1.2)
                if dashed:
                    connector.line.dash_style = MSO_LINE_DASH_STYLE.DASH

                # 화살표 머리 (tailEnd)
                ln = connector._element.find(".//" + qn("a:ln"))
                if ln is not None:
                    tail = etree.SubElement(ln, qn("a:tailEnd"))
                    tail.set("type", "triangle")
                    tail.set("w", "med")
                    tail.set("len", "med")

                remove_style_element(connector._element)
                _remove_shadow(connector)

                # 라벨: 화살표 위 중앙 텍스트박스 (멀티라인 지원)
                if label:
                    mx_emu = (sx_emu + dx_emu) // 2
                    msg_lines = label.split('\n')
                    lh = max(0.22, len(msg_lines) * 0.18)
                    _seq_add_label(slide, label,
                                   mx_emu - Inches(0.9),
                                   msg_y_emu - Inches(lh + 0.02),
                                   Inches(1.8), Inches(lh),
                                   align=PP_ALIGN.CENTER)

    # BytesIO로 저장 후 바이트 반환
    output = BytesIO()
    prs.save(output)
    output.seek(0)
    return output.read()


# ──────────────────────────────────────────────
# ER 다이어그램 PNG 폴백 렌더러
# ──────────────────────────────────────────────

def _render_er_png_fallback(mermaid_code: str, title: str = "") -> bytes:
    """ER 다이어그램 레이아웃 엔진 실패 시 mmdc PNG를 PPTX 슬라이드에 임베드한다.

    mmdc가 없거나 PNG 생성에 실패하면 에러 메시지 텍스트 박스를 표시한다.
    """
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    prs = Presentation()
    prs.slide_width = Inches(SLIDE_W)
    prs.slide_height = Inches(SLIDE_H)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_title_and_bg(slide, title)

    avail_w = SLIDE_W - 2 * MARGIN
    avail_h = SLIDE_H - TITLE_H - 2 * MARGIN

    png_bytes: Optional[bytes] = None
    if shutil.which("mmdc"):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                workdir = Path(tmp)
                in_path = workdir / "input.mmd"
                out_path = workdir / "out.png"
                in_path.write_text(mermaid_code, encoding="utf-8")
                cmd = ["mmdc", "-i", str(in_path), "-o", str(out_path), "-b", "white"]
                pc = "/app/backend/puppeteer-config.json"
                if Path(pc).exists():
                    cmd += ["-p", pc]
                result = subprocess.run(cmd, capture_output=True, timeout=30)
                if result.returncode == 0 and out_path.exists():
                    png_bytes = out_path.read_bytes()
        except Exception as exc:
            logger.warning("ER PNG 폴백 mmdc 실패: %s", exc)

    if png_bytes:
        # 폭 기준으로 삽입 (aspect ratio는 python-pptx가 자동 유지)
        slide.shapes.add_picture(
            BytesIO(png_bytes),
            Inches(MARGIN),
            Inches(TITLE_H + MARGIN),
            width=Inches(avail_w),
        )
    else:
        # mmdc 실패 시 에러 안내 텍스트 박스
        txb = slide.shapes.add_textbox(
            Inches(MARGIN), Inches(TITLE_H + MARGIN),
            Inches(avail_w), Inches(1.0),
        )
        tf = txb.text_frame
        para = tf.paragraphs[0]
        run = para.add_run()
        run.text = "ER 다이어그램 변환 실패: mmdc 미설치 또는 렌더링 오류입니다."
        run.font.size = Pt(12)
        run.font.color.rgb = RGBColor(0xEF, 0x44, 0x44)

    output = BytesIO()
    prs.save(output)
    output.seek(0)
    return output.read()


# ──────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────

def mermaid_to_pptx(mermaid_code: str, title: str = "") -> bytes:
    """Mermaid flowchart/graph 코드를 네이티브 도형 PPTX로 변환한다.

    모든 도형은 PowerPoint에서 개별 선택·이동·편집이 가능하다.

    Args:
        mermaid_code: 변환할 Mermaid 다이어그램 코드 문자열.
        title: 슬라이드 상단에 표시할 제목. 빈 문자열이면 제목 영역을 생략한다.

    Returns:
        생성된 PPTX 파일의 바이트 데이터 (BytesIO 기반).

    Raises:
        ValueError: Mermaid 코드가 비어 있거나 노드를 찾을 수 없는 경우.
    """
    if not mermaid_code or not mermaid_code.strip():
        raise ValueError("Mermaid 코드가 비어 있습니다.")

    # 시퀀스 다이어그램 감지: 첫 비-주석 라인이 sequenceDiagram이면 시퀀스 렌더링 분기
    # `%% source: ...` 같은 주석으로 시작하는 mermaid 도 처리
    from converters.palette import first_diagram_directive
    compact_first = first_diagram_directive(mermaid_code)
    if 'sequencediagram' in compact_first:
        return _render_sequence(mermaid_code, title)

    # ER 다이어그램 감지 (레이아웃 실패 시 PNG 폴백 경로 결정에 사용)
    is_er = compact_first.startswith('erdiagram')

    # 새 레이아웃 엔진(mmdc SVG) 시도 — erDiagram 포함 (layout_engine v2)
    try:
        from converters.layout_engine import compute_layout_via_mmdc
        layout = compute_layout_via_mmdc(
            mermaid_code,
            target_w_in=SLIDE_W - 2 * MARGIN,
            target_h_in=SLIDE_H - TITLE_H - 2 * MARGIN,
            puppeteer_config="/app/backend/puppeteer-config.json",
        )
    except Exception:
        layout = None

    if layout is not None and layout.nodes:
        # ER: 손상 레이아웃 감지 — 엣지가 있는데 어느 엣지도 노드 목록에 매칭 안 되면 PNG 폴백
        if is_er and layout.edges:
            node_ids = set(layout.nodes.keys())
            matched = sum(
                1 for e in layout.edges
                if e.source in node_ids and e.target in node_ids
            )
            if matched == 0:
                logger.warning(
                    "ER 레이아웃 손상 감지 (엣지 %d개 중 노드 매칭 0개) → PNG 폴백",
                    len(layout.edges),
                )
                return _render_er_png_fallback(mermaid_code, title)
        # B.7: classDef + class 파싱 → fill_override 적용
        if not is_er:
            _parse_class_overrides(mermaid_code, layout.nodes)
        return _render_pptx_from_layout(layout, title)

    # ER 다이어그램: parse_mermaid는 ER 구문 미지원 → PNG 폴백
    if is_er:
        logger.info("ER 다이어그램 레이아웃 엔진 실패 → PNG 폴백 사용")
        return _render_er_png_fallback(mermaid_code, title)

    # 1. 파싱 (폴백 — flowchart 전용)
    diagram = parse_mermaid(mermaid_code)

    if not diagram.nodes:
        raise ValueError("Mermaid 코드에서 노드를 찾을 수 없습니다.")

    # 2. 레이아웃 계산
    compute_layout(diagram)

    # 3. PPTX 생성
    prs = Presentation()

    # 16:9 슬라이드 크기 설정
    prs.slide_width = Inches(SLIDE_W)
    prs.slide_height = Inches(SLIDE_H)

    # 빈 레이아웃 사용 (인덱스 6 = blank layout)
    blank_layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank_layout)

    # 4. 슬라이드 배경: 흰색
    background = slide.background
    background.fill.solid()
    background.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    # 5. 제목 추가
    if title:
        title_box = slide.shapes.add_textbox(
            Inches(MARGIN),
            Inches(0.1),
            Inches(SLIDE_W - 2 * MARGIN),
            Inches(TITLE_H)
        )
        tf = title_box.text_frame
        para = tf.paragraphs[0]
        para.alignment = PP_ALIGN.LEFT
        run = para.add_run()
        run.text = title
        run.font.size = Pt(22)
        run.font.bold = True
        run.font.color.rgb = RGBColor(0x1E, 0x29, 0x3B)

        try:
            rPr = run._r.get_or_add_rPr()
            ea = etree.SubElement(rPr, qn("a:ea"))
            ea.set("typeface", "맑은 고딕")
            latin = rPr.find(qn("a:latin"))
            if latin is None:
                latin = etree.SubElement(rPr, qn("a:latin"))
            latin.set("typeface", "맑은 고딕")
        except Exception:
            pass

        # 제목 하단 구분선 (얇은 직사각형)
        line_bar = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Inches(MARGIN), Inches(TITLE_H + 0.05),
            Inches(SLIDE_W - 2 * MARGIN), Inches(0.03)
        )
        line_bar.fill.solid()
        line_bar.fill.fore_color.rgb = RGBColor(0x3B, 0x82, 0xF6)
        line_bar.line.fill.background()
        remove_style_element(line_bar._element)
        _remove_shadow(line_bar)

    # 6. 서브그래프 배경 박스 추가 (노드보다 먼저 → 뒤에 위치)
    for sg_idx, sg in enumerate(diagram.subgraphs.values()):
        fill_color = _SUBGRAPH_FILLS[sg_idx % len(_SUBGRAPH_FILLS)]
        _add_subgraph_box(slide, sg, fill_color, sg_idx)

    # 7. 노드 도형 추가 + 인덱스 맵 구성
    shape_map: dict[str, object] = {}
    for node_idx, (nid, node) in enumerate(diagram.nodes.items()):
        # 서브그래프 인덱스를 팔레트 선택에 사용
        if node.subgraph_id and node.subgraph_id in diagram.subgraphs:
            sg_keys = list(diagram.subgraphs.keys())
            palette_idx = sg_keys.index(node.subgraph_id)
        else:
            palette_idx = node_idx

        shape_map[nid] = _add_node_shape(slide, node, palette_idx)

    # 8. 엣지(ELBOW 커넥터 + 화살표) 추가
    for edge in diagram.edges:
        src_shape = shape_map.get(edge.source)
        dst_shape = shape_map.get(edge.target)
        if not src_shape or not dst_shape:
            continue

        _add_connector_elbow(slide, src_shape, dst_shape, label=edge.label)

    # 9. BytesIO로 저장 후 바이트 반환
    output = BytesIO()
    prs.save(output)
    output.seek(0)
    return output.read()


# ──────────────────────────────────────────────
# mmdc SVG 기반 레이아웃 렌더러 (신규)
# ──────────────────────────────────────────────

def _add_title_and_bg(slide, title: str, slide_w: float = SLIDE_W) -> None:
    """슬라이드 배경(흰색) + 제목 + 하단 구분선을 추가한다."""
    background = slide.background
    background.fill.solid()
    background.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    if not title:
        return

    title_box = slide.shapes.add_textbox(
        Inches(MARGIN), Inches(0.1),
        Inches(slide_w - 2 * MARGIN), Inches(TITLE_H),
    )
    tf = title_box.text_frame
    para = tf.paragraphs[0]
    para.alignment = PP_ALIGN.LEFT
    run = para.add_run()
    run.text = title
    run.font.size = Pt(22)
    run.font.bold = True
    run.font.color.rgb = RGBColor(0x1E, 0x29, 0x3B)
    try:
        rPr = run._r.get_or_add_rPr()
        ea = etree.SubElement(rPr, qn("a:ea"))
        ea.set("typeface", "맑은 고딕")
        latin = rPr.find(qn("a:latin"))
        if latin is None:
            latin = etree.SubElement(rPr, qn("a:latin"))
        latin.set("typeface", "맑은 고딕")
    except Exception:
        pass

    line_bar = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Inches(MARGIN), Inches(TITLE_H + 0.05),
        Inches(SLIDE_W - 2 * MARGIN), Inches(0.03),
    )
    line_bar.fill.solid()
    line_bar.fill.fore_color.rgb = RGBColor(0x3B, 0x82, 0xF6)
    line_bar.line.fill.background()
    remove_style_element(line_bar._element)
    _remove_shadow(line_bar)


def _add_subgraph_box_at(
    slide, x: float, y: float, w: float, h: float,
    label: str, fill_color: RGBColor, idx: int,
) -> None:
    """layout 좌표(inches)를 받아 draw.io 스타일 서브그래프 박스를 그린다."""
    stroke_color = _SUBGRAPH_STROKES[idx % len(_SUBGRAPH_STROKES)]
    title_h = 0.26
    CORNER_RADIUS_IN = 0.10
    line_w = Pt(0.75)

    adj_container = _calc_adj(CORNER_RADIUS_IN, w, h)
    adj_title = _calc_adj(CORNER_RADIUS_IN, w, title_h)

    container = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Inches(x), Inches(y), Inches(w), Inches(h),
    )
    container.fill.solid()
    container.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    container.line.color.rgb = stroke_color
    container.line.width = line_w
    _set_corner_radius(container, adj_container)
    _remove_shadow(container)
    remove_style_element(container._element)
    tf = container.text_frame
    tf.margin_top = Pt(0)
    tf.margin_bottom = Pt(0)

    title_shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUND_2_SAME_RECTANGLE,
        Inches(x), Inches(y), Inches(w), Inches(title_h),
    )
    title_shape.fill.solid()
    title_shape.fill.fore_color.rgb = fill_color
    title_shape.line.color.rgb = stroke_color
    title_shape.line.width = line_w
    _set_corner_radius(title_shape, adj_title, adj2_val=0)
    _remove_shadow(title_shape)
    remove_style_element(title_shape._element)

    tf2 = title_shape.text_frame
    tf2.auto_size = None
    tf2.word_wrap = True
    tf2.margin_top = Pt(3)
    tf2.margin_bottom = Pt(2)
    tf2.margin_left = Pt(6)
    tf2.margin_right = Pt(6)
    txBody = title_shape._element.find(qn("p:txBody"))
    if txBody is not None:
        bodyPr = txBody.find(qn("a:bodyPr"))
        if bodyPr is not None:
            bodyPr.set("anchor", "ctr")
    p = tf2.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = label
    run.font.size = Pt(9)
    run.font.bold = True
    run.font.color.rgb = stroke_color

    try:
        rPr = run._r.get_or_add_rPr()
        ea = etree.SubElement(rPr, qn("a:ea"))
        ea.set("typeface", "맑은 고딕")
        latin = rPr.find(qn("a:latin"))
        if latin is None:
            latin = etree.SubElement(rPr, qn("a:latin"))
        latin.set("typeface", "맑은 고딕")
    except Exception:
        pass


# ──────────────────────────────────────────────
# B.1/B.2/B.3/B.7 보조 함수
# ──────────────────────────────────────────────

def _parse_class_overrides(mermaid_code: str, nodes: dict) -> None:
    """B.7: mermaid classDef + class 문을 파싱해 LaidNode.fill_override 적용.

    지원 문법:
      classDef <name> fill:#xxx[,color:#yyy][,stroke:#zzz]
      class <nodeId>[,<nodeId>...] <name>
      <nodeId>:::<name>   (inline class 선언)
    """
    class_styles: dict[str, dict[str, str]] = {}

    for line in mermaid_code.split('\n'):
        line = line.strip()
        # classDef 선언
        m = re.match(r'classDef\s+(\w+)\s+(.*)', line)
        if m:
            cname, style_str = m.group(1), m.group(2)
            styles: dict[str, str] = {}
            for part in style_str.split(','):
                part = part.strip()
                if ':' in part:
                    k, v = part.split(':', 1)
                    styles[k.strip()] = v.strip()
            # 트랙 P: 어두운 fill 자동 라이트 톤 치환
            from converters.palette import is_color_too_dark, lighten_dark_fill
            fill_hex = styles.get('fill', '')
            if fill_hex and is_color_too_dark(fill_hex):
                light_fill, dark_text = lighten_dark_fill(fill_hex)
                styles['fill'] = light_fill
                if 'color' not in styles:
                    styles['color'] = dark_text
            class_styles[cname] = styles

    for line in mermaid_code.split('\n'):
        line = line.strip()
        # class 할당: class A,B,C name
        m = re.match(r'^class\s+([\w,\s\-]+)\s+(\w+)\s*$', line)
        if m:
            node_ids = [n.strip() for n in m.group(1).split(',')]
            cname = m.group(2)
            styles = class_styles.get(cname, {})
            for nid in node_ids:
                if nid in nodes and styles:
                    nodes[nid].fill_override = styles.get('fill')
                    nodes[nid].text_color_override = styles.get('color')

    # inline class: nodeId:::className
    for line in mermaid_code.split('\n'):
        m = re.findall(r'(\w+):::(\w+)', line)
        for nid, cname in m:
            if nid in nodes and cname in class_styles:
                styles = class_styles[cname]
                nodes[nid].fill_override = styles.get('fill')
                nodes[nid].text_color_override = styles.get('color')


def _br_to_newline(text: str) -> str:
    """<br/>, <br>, &lt;br/&gt; 패턴을 \\n으로 변환 (B.1).
    HTML entity 형태도 처리하여 PPTX 렌더 전 사전 정규화.
    """
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'&lt;br\s*/?&gt;', '\n', text, flags=re.IGNORECASE)
    return text


def _estimate_required_size(
    label: str,
    font_pt: int = 9,
    min_w: float = 0.7,
    max_chars_per_line: int = 20,
) -> tuple[float, float]:
    """라벨 텍스트 기반 최소 필요 (width_in, height_in) 추정 (B.2).

    Args:
        label: 라벨 텍스트 (\\n 분리 가능, <br/> 미포함 가정)
        font_pt: 폰트 크기 (포인트)
        min_w: 최소 너비 (inches)
        max_chars_per_line: 자동 줄바꿈 기준 글자 수

    Returns:
        (required_w, required_h) inches — layout 값과 max() 적용 필요
    """
    # <br/> → \\n 정규화
    label = _br_to_newline(label)
    raw_lines = label.split('\n') if '\n' in label else [label]

    # 긴 줄 자동 줄바꿈 (단어 단위, 18자 이상)
    lines: list[str] = []
    for line in raw_lines:
        if len(line) <= max_chars_per_line:
            lines.append(line)
        else:
            # 단어 단위 줄바꿈 시도
            words = line.split(' ')
            cur = ''
            for word in words:
                if not cur:
                    cur = word
                elif len(cur) + 1 + len(word) <= max_chars_per_line:
                    cur += ' ' + word
                else:
                    lines.append(cur)
                    cur = word
            if cur:
                lines.append(cur)

    if not lines:
        lines = ['']

    # 폰트 단위 (inches per point)
    font_in = font_pt / 72.0

    # 각 줄의 추정 너비 (유니코드 동아시아 폭 기반 휴리스틱)
    max_line_w = 0.0
    for line in lines:
        lw = 0.0
        for ch in line:
            try:
                eaw = unicodedata.east_asian_width(ch)
            except Exception:
                eaw = 'N'
            if eaw in ('W', 'F'):        # 한글, 한자 등 full-width
                lw += font_in * 1.05
            elif eaw == 'A':             # Ambiguous (일부 특수문자)
                lw += font_in * 0.75
            else:                        # ASCII, narrow
                lw += font_in * 0.60
        max_line_w = max(max_line_w, lw)

    H_PAD = 0.20   # 수평 패딩 (양쪽 합계)
    V_PAD = 0.16   # 수직 패딩 (양쪽 합계)
    LINE_H = font_in * 1.40   # 줄 간격 배율

    req_w = max(min_w, max_line_w + H_PAD)
    req_h = max(0.30, len(lines) * LINE_H + V_PAD)
    return req_w, req_h


def _fit_label_to_box(
    text: str,
    box_w_in: float,
    box_h_in: float,
    font_size_pt: float = 9.0,
    pad_h: float = 0.12,
    pad_v: float = 0.07,
    min_font_pt: float = 9.0,  # 예약 — 현재 버전에서 폰트 축소 없음
) -> tuple[float, float, float]:
    """박스 폭(inches) 기반 실제 word-wrap 줄 수를 추정하고 필요 높이를 반환 (B.2 개선).

    기존 ``label.count('\\n') + 1`` 방식은 명시적 개행만 카운트하므로,
    박스 폭에 비해 긴 줄이 PPTX word-wrap될 때 줄 수가 과소 추정된다.
    유니코드 east_asian_width 기반 문자 폭 휴리스틱으로 실제 줄 수를 계산한다.

    PPTX 텍스트 프레임 기준:
    - 좌/우 margin: 4 pt 각각 → pad_h ≈ 0.12 in
    - 상/하 margin: 2 pt 각각 → pad_v ≈ 0.07 in
    - 기본 폰트: 9 pt

    Args:
        text: 노드 라벨 (``\\n`` 분리 가능, ``<br/>`` 포함 가능).
        box_w_in: 노드 박스 폭 (inches).
        box_h_in: 노드 박스 높이 (inches, 참조용).
        font_size_pt: 폰트 크기 (포인트). 기본 9.
        pad_h: 수평 패딩 합계 (inches). 기본 0.12.
        pad_v: 수직 패딩 합계 (inches). 기본 0.07.
        min_font_pt: 최소 폰트 크기 (예약 파라미터, 현재 미사용).

    Returns:
        ``(required_h_in, box_w_in, font_size_pt)`` — B.2에서는 첫 번째 값만 사용.
    """
    # <br/> → \n 정규화
    text = _br_to_newline(text)
    if not text.strip():
        return (max(box_h_in, 0.30), box_w_in, font_size_pt)

    font_in = font_size_pt / 72.0
    avail_w = max(0.05, box_w_in - pad_h)  # 패딩 제거 후 텍스트 가용 폭
    LINE_H = font_in * 1.40  # 줄 간격 배율 — _estimate_required_size 와 동일

    def _cw(ch: str) -> float:
        """문자 폭 추정 (east_asian_width 기반)."""
        try:
            eaw = unicodedata.east_asian_width(ch)
        except Exception:
            eaw = 'N'
        if eaw in ('W', 'F'):   # 한글·한자 full-width
            return font_in * 1.05
        if eaw == 'A':          # Ambiguous (일부 특수문자)
            return font_in * 0.75
        return font_in * 0.60   # ASCII narrow

    total_lines = 0
    for raw_line in text.split('\n'):
        if not raw_line:
            total_lines += 1
            continue
        words = raw_line.split(' ')
        cur_w = 0.0
        line_count = 1
        for i, word in enumerate(words):
            word_w = sum(_cw(ch) for ch in word)
            if i == 0:
                cur_w = word_w
            else:
                space_w = _cw(' ')
                if cur_w + space_w + word_w <= avail_w:
                    cur_w += space_w + word_w
                else:
                    line_count += 1
                    cur_w = word_w
        total_lines += line_count

    if total_lines == 0:
        total_lines = 1

    required_h = max(0.30, total_lines * LINE_H + pad_v)
    return (required_h, box_w_in, font_size_pt)


def _recompute_cluster_bboxes(
    nodes: dict,
    clusters: list,
    header_h: float = 0.28,
    padding: float = 0.14,
) -> None:
    """노드 크기 조정 후 cluster bbox를 자식 노드 합집합으로 재계산 (B.3).

    1) 각 cluster의 (x, y, w, h)를 자식 노드 bbox 합집합 + 헤더 + padding으로 갱신.
    2) cluster 간 겹침을 y축 push-down + 자식 노드 함께 이동으로 해소.

    Args:
        nodes: LaidNode dict (cluster_id 필드 포함).
        clusters: LaidCluster list (in-place 수정).
        header_h: 서브그래프 타이틀 헤더 높이 (inches).
        padding: 노드 외곽 여유 (inches).
    """
    # cluster id → child nodes 매핑
    cluster_children: dict[str, list] = {cl.id: [] for cl in clusters}
    for node in nodes.values():
        if node.cluster_id and node.cluster_id in cluster_children:
            cluster_children[node.cluster_id].append(node)

    # 1단계: cluster bbox를 자식 노드 합집합으로 갱신
    for cl in clusters:
        children = cluster_children.get(cl.id, [])
        if not children:
            continue
        min_x = min(n.x for n in children)
        min_y = min(n.y for n in children)
        max_x = max(n.x + n.w for n in children)
        max_y = max(n.y + n.h for n in children)

        cl.x = min_x - padding
        cl.y = min_y - header_h - padding
        cl.w = (max_x - min_x) + padding * 2
        cl.h = (max_y - min_y) + header_h + padding * 2

    # 2단계: cluster 간 겹침 해소 (반복 수렴)
    max_iter = 15
    changed = True
    while changed and max_iter > 0:
        changed = False
        max_iter -= 1
        n_cl = len(clusters)
        for i in range(n_cl):
            for j in range(i + 1, n_cl):
                a, b = clusters[i], clusters[j]
                ax2, ay2 = a.x + a.w, a.y + a.h
                bx2, by2 = b.x + b.w, b.y + b.h
                ov_x = min(ax2, bx2) - max(a.x, b.x)
                ov_y = min(ay2, by2) - max(a.y, b.y)
                if ov_x <= 0 or ov_y <= 0:
                    continue
                # 겹침 발생: 더 아래에 있는 cluster를 push-down
                if a.y <= b.y:
                    push_cl = b
                    push_dy = ay2 - b.y + padding
                    push_children = cluster_children.get(b.id, [])
                else:
                    push_cl = a
                    push_dy = by2 - a.y + padding
                    push_children = cluster_children.get(a.id, [])
                if push_dy > 0:
                    push_cl.y += push_dy
                    for n in push_children:
                        n.y += push_dy
                    changed = True


def _add_node_at(
    slide, x: float, y: float, w: float, h: float,
    label: str, shape: str, palette_idx: int,
    fill_override: str | None = None,
    text_color_override: str | None = None,
) -> object:
    """layout 좌표(inches)에 노드 도형을 추가하고 도형 객체 반환.
    B.1: label의 <br/> → \\n 변환 후 렌더.
    B.7: fill_override / text_color_override 가 있으면 classDef 색 우선 적용.
    """
    # B.1: <br/> → \\n 정규화 (PPTX multi-paragraph 렌더 활성화)
    label = _br_to_newline(label)
    fill_c, stroke_c, text_c = _PALETTE[palette_idx % len(_PALETTE)]
    # B.7: classDef 색 우선 적용
    if fill_override:
        try:
            fill_c = _hex_to_rgb(fill_override)
        except Exception:
            pass
    if text_color_override:
        try:
            text_c = _hex_to_rgb(text_color_override)
        except Exception:
            pass
    if shape == "diamond":
        return _add_diamond(slide, x, y, w, h, fill_c, stroke_c, label, text_color=text_c)
    if shape == "circle":
        return _add_oval(slide, x, y, w, h, fill_c, stroke_c, label, text_color=text_c)
    return _add_rounded_rect(slide, x, y, w, h, fill_c, stroke_c, label, text_color=text_c)


# ──────────────────────────────────────────────
# 엣지 노드 회피 라우팅 (ELBOW + bbox 우회)
# ──────────────────────────────────────────────

def _lines_intersect(a, b) -> bool:
    """두 선분 a, b 교차 검사. 표준 ccw 알고리즘."""
    def ccw(p1, p2, p3):
        return (p3[1] - p1[1]) * (p2[0] - p1[0]) > (p2[1] - p1[1]) * (p3[0] - p1[0])
    (p1, p2), (p3, p4) = a, b
    return ccw(p1, p3, p4) != ccw(p2, p3, p4) and ccw(p1, p2, p3) != ccw(p1, p2, p4)


def _segment_crosses_bbox(seg, bbox, padding: float = 0.0) -> bool:
    """선분이 bbox(padding 확장)와 교차하는지.
    양 끝점이 bbox 안이거나 4변 중 하나와 교차하면 True."""
    (x1, y1), (x2, y2) = seg
    bx1, by1, bx2, by2 = bbox
    bx1 -= padding
    by1 -= padding
    bx2 += padding
    by2 += padding
    # AABB 1차 필터링: 선분의 bbox가 노드 bbox와 분리되면 비교차
    if max(x1, x2) < bx1 or min(x1, x2) > bx2:
        return False
    if max(y1, y2) < by1 or min(y1, y2) > by2:
        return False
    # 양 끝점 중 하나가 bbox 내부 → 교차 (경계 위 점은 통과로 인정 — strict)
    inside1 = bx1 < x1 < bx2 and by1 < y1 < by2
    inside2 = bx1 < x2 < bx2 and by1 < y2 < by2
    if inside1 or inside2:
        return True
    # 4변과 교차 검사
    edges = [
        ((bx1, by1), (bx2, by1)),
        ((bx2, by1), (bx2, by2)),
        ((bx2, by2), (bx1, by2)),
        ((bx1, by2), (bx1, by1)),
    ]
    return any(_lines_intersect(seg, edge) for edge in edges)


def _segment_intersects_any(seg, bboxes, padding: float = 0.0) -> bool:
    """선분이 bboxes 중 하나라도 교차하면 True."""
    for bx in bboxes:
        if _segment_crosses_bbox(seg, bx, padding):
            return True
    return False


def _path_intersects_any(path, bboxes, padding: float = 0.0) -> bool:
    """polyline path의 segment 중 하나라도 bboxes와 교차하면 True."""
    for i in range(len(path) - 1):
        seg = (path[i], path[i + 1])
        if _segment_intersects_any(seg, bboxes, padding):
            return True
    return False


def _make_elbow_candidates(s, e) -> list[list[tuple[float, float]]]:
    """H→V→H, V→H→V 두 ELBOW 후보를 반환."""
    sx, sy = s
    ex, ey = e
    mx, my = (sx + ex) / 2.0, (sy + ey) / 2.0
    return [
        [s, (mx, sy), (mx, ey), e],     # H→V→H
        [s, (sx, my), (ex, my), e],     # V→H→V
    ]


def _path_length(path) -> float:
    """경로 총 길이 (Manhattan distance). 폴리라인 segment 길이의 합."""
    return sum(
        abs(path[i + 1][0] - path[i][0]) + abs(path[i + 1][1] - path[i][1])
        for i in range(len(path) - 1)
    )


def _try_corner_detour(s, e, bboxes, padding: float):
    """8방향 후보 + 다단계 우회로 회피 경로 탐색 (v2).

    1단계: 각 bbox마다 외곽 padding 적용 후 8개 후보점 수집
           (4모서리 + 4변 중간점).
    2단계: 후보당 3가지 ELBOW 변형(H→V→H→V, V→H→V→H, 단순 H→V→H) 시도,
           bbox 교차 통과 + 최단 Manhattan 거리 후보를 best로 추적.
    3단계: candidates[:16]을 c1/c2 후보로 사용해 s→c1→c2→e 두 단계 우회.
    4단계: 모든 통과 경로 중 가장 짧은 경로 반환, 없으면 None.
    """
    detour_offset = padding * 2.0

    # 1단계: 8방향 후보 점 수집
    candidates: list[tuple[float, float]] = []
    for bx in bboxes:
        x1, y1, x2, y2 = bx
        px1 = x1 - detour_offset
        py1 = y1 - detour_offset
        px2 = x2 + detour_offset
        py2 = y2 + detour_offset
        mid_x = (px1 + px2) / 2.0
        mid_y = (py1 + py2) / 2.0
        candidates.extend([
            (px1, py1), (px2, py1), (px2, py2), (px1, py2),     # 4 모서리
            (mid_x, py1), (px2, mid_y), (mid_x, py2), (px1, mid_y),  # 4 변 중간점
        ])

    best_path = None
    best_length = float("inf")

    # 2단계: 단일 후보 경로 변형 시도
    for cx, cy in candidates:
        path_variants = [
            [s, (cx, s[1]), (cx, cy), (e[0], cy), e],   # H→V→H→V
            [s, (s[0], cy), (cx, cy), (cx, e[1]), e],   # V→H→V→H
            [s, (cx, s[1]), (cx, e[1]), e],             # 단순 H→V→H
        ]
        for path in path_variants:
            if not _path_intersects_any(path, bboxes, padding):
                length = _path_length(path)
                if length < best_length:
                    best_length = length
                    best_path = path

    # 3단계: 두 단계 우회 (조합 폭발 방지 위해 candidates[:16])
    limited = candidates[:16]
    for c1 in limited:
        for c2 in limited:
            if c1 == c2:
                continue
            path = [s, (c1[0], s[1]), c1, c2, (e[0], c2[1]), e]
            if not _path_intersects_any(path, bboxes, padding):
                length = _path_length(path)
                if length < best_length:
                    best_length = length
                    best_path = path

    # 4단계: 최단 경로 반환 (없으면 None)
    return best_path


def _route_around_nodes(start, end, avoid_bboxes, padding: float = 0.1):
    """직선 start→end가 다른 노드 bbox(padding 포함)와 교차하면 ELBOW 회피 경로 반환.

    Args:
        start, end: (x, y) inches
        avoid_bboxes: [(x1, y1, x2, y2), ...] inches, source/target 제외
        padding: bbox 외곽 여유 (inches)

    Returns:
        polyline list[(x, y)] — 회피 필요 없으면 [start, end] 그대로
    """
    # 직선이 bbox 통과 없으면 그대로
    if not _segment_intersects_any((start, end), avoid_bboxes, padding):
        return [start, end]
    # 1단계: ELBOW 두 후보
    for path in _make_elbow_candidates(start, end):
        if not _path_intersects_any(path, avoid_bboxes, padding):
            return path
    # 2단계: corner detour
    detour = _try_corner_detour(start, end, avoid_bboxes, padding)
    if detour:
        return detour
    # 3단계: fallback
    logger.warning(f"화살표 회피 실패: start={start} end={end}")
    return [start, end]


def _add_polyline_edge(
    slide, points_in: list[tuple[float, float]],
    dashed: bool = False,
) -> None:
    """polyline(inches 좌표 시퀀스)을 STRAIGHT connector 다중으로 그리고,
    마지막 segment에만 화살표 머리를 부여한다."""
    if len(points_in) < 2:
        return
    from pptx.enum.dml import MSO_LINE_DASH_STYLE

    line_color = RGBColor(0x47, 0x55, 0x69)
    line_w = Pt(2.0)

    for i in range(len(points_in) - 1):
        x1, y1 = points_in[i]
        x2, y2 = points_in[i + 1]
        sx_emu = Inches(x1)
        sy_emu = Inches(y1)
        ex_emu = Inches(x2)
        ey_emu = Inches(y2)
        if sx_emu == ex_emu:
            ex_emu += 9144  # 1px(96dpi) = 9144 EMU — LibreOffice 수직선 렌더링 보장
        if sy_emu == ey_emu:
            ey_emu += 9144  # 1px(96dpi) = 9144 EMU — LibreOffice 수평선 렌더링 보장

        conn = slide.shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT, sx_emu, sy_emu, ex_emu, ey_emu
        )
        conn.line.color.rgb = line_color
        conn.line.width = line_w
        if dashed:
            conn.line.dash_style = MSO_LINE_DASH_STYLE.DASH

        is_last = (i == len(points_in) - 2)
        if is_last:
            ln = conn._element.find(".//" + qn("a:ln"))
            if ln is not None:
                tail = etree.SubElement(ln, qn("a:tailEnd"))
                tail.set("type", "triangle")
                tail.set("w", "med")
                tail.set("len", "med")

        remove_style_element(conn._element)
        _remove_shadow(conn)


def _add_freeform_edge(
    slide,
    path_d: Optional[str],
    scale: float,
    off_x: float,
    off_y: float,
    dashed: bool = False,
) -> bool:
    """SVG path d 속성 → OOXML custGeom freeform shape 으로 edge 를 그린다 (트랙 D).

    dagre 의 bezier 제어점을 그대로 보존해 부드러운 곡선을 렌더링.
    path_d 가 None 이거나 파싱 실패 시 False 반환 → 호출자가 polyline 폴백 사용.

    Args:
        path_d: SVG path d 원본 속성 (SVG pixel 좌표계). None 이면 즉시 False.
        scale:  px → inches 변환 계수 (layout.scale).
        off_x, off_y: 슬라이드 오프셋 (inches).
        dashed: dash 스타일 여부.

    Returns:
        True = custGeom shape 추가 성공. False = 폴백 필요.
    """
    if not path_d:
        return False

    try:
        cmds = parse_svg_path(path_d)
    except Exception:
        return False

    if not cmds:
        return False

    # SVG px → 절대 slide inches 좌표로 변환된 PathCmd 목록
    abs_cmds: list[_SvgPathCmd] = []
    for cmd in cmds:
        abs_pts = [(px * scale + off_x, py * scale + off_y) for px, py in cmd.pts]
        abs_cmds.append(_SvgPathCmd(cmd.op, abs_pts))

    bb = path_bounding_box(abs_cmds)
    if bb is None:
        return False

    min_x, min_y, max_x, max_y = bb
    PAD = 0.05  # inches — 제어점이 bbox 경계 밖으로 나가지 않도록 여유
    min_x -= PAD; min_y -= PAD
    max_x += PAD; max_y += PAD

    cx_in = max_x - min_x
    cy_in = max_y - min_y
    if cx_in < 0.001 or cy_in < 0.001:
        return False

    shape_x_emu = int(Inches(min_x))
    shape_y_emu = int(Inches(min_y))
    shape_cx_emu = int(Inches(cx_in))
    shape_cy_emu = int(Inches(cy_in))

    def to_local(ax_in: float, ay_in: float) -> tuple[int, int]:
        """절대 slide inches → shape 로컬 EMU 좌표."""
        return (
            int(Inches(ax_in)) - shape_x_emu,
            int(Inches(ay_in)) - shape_y_emu,
        )

    # ── XML 구조 직접 빌드 ──────────────────────────────────────────
    sp = etree.SubElement(slide.shapes._spTree, qn("p:sp"))

    # nvSpPr (이름/id)
    nvSpPr = etree.SubElement(sp, qn("p:nvSpPr"))
    cNvPr = etree.SubElement(nvSpPr, qn("p:cNvPr"))
    sp_id = len(slide.shapes._spTree)
    cNvPr.set("id", str(sp_id))
    cNvPr.set("name", f"edge_freeform_{sp_id}")
    cNvSpPr = etree.SubElement(nvSpPr, qn("p:cNvSpPr"))
    spLocks = etree.SubElement(cNvSpPr, qn("a:spLocks"))
    spLocks.set("noGrp", "1")
    etree.SubElement(nvSpPr, qn("p:nvPr"))

    # spPr
    spPr = etree.SubElement(sp, qn("p:spPr"))

    # xfrm — 위치 + 크기
    xfrm = etree.SubElement(spPr, qn("a:xfrm"))
    off_el = etree.SubElement(xfrm, qn("a:off"))
    off_el.set("x", str(shape_x_emu)); off_el.set("y", str(shape_y_emu))
    ext_el = etree.SubElement(xfrm, qn("a:ext"))
    ext_el.set("cx", str(shape_cx_emu)); ext_el.set("cy", str(shape_cy_emu))

    # custGeom
    custGeom = etree.SubElement(spPr, qn("a:custGeom"))
    etree.SubElement(custGeom, qn("a:avLst"))
    etree.SubElement(custGeom, qn("a:gdLst"))
    etree.SubElement(custGeom, qn("a:ahLst"))
    etree.SubElement(custGeom, qn("a:cxnLst"))
    rect_el = etree.SubElement(custGeom, qn("a:rect"))
    rect_el.set("l", "0"); rect_el.set("t", "0")
    rect_el.set("r", str(shape_cx_emu)); rect_el.set("b", str(shape_cy_emu))
    pathLst = etree.SubElement(custGeom, qn("a:pathLst"))
    path_el = etree.SubElement(pathLst, qn("a:path"))
    path_el.set("w", str(shape_cx_emu))
    path_el.set("h", str(shape_cy_emu))
    path_el.set("fill", "none")   # 선 도형 — 채움 없음

    for cmd in abs_cmds:
        if cmd.op == "M":
            mv = etree.SubElement(path_el, qn("a:moveTo"))
            lx, ly = to_local(*cmd.pts[0])
            pt = etree.SubElement(mv, qn("a:pt"))
            pt.set("x", str(lx)); pt.set("y", str(ly))
        elif cmd.op == "L":
            ln_to = etree.SubElement(path_el, qn("a:lnTo"))
            lx, ly = to_local(*cmd.pts[0])
            pt = etree.SubElement(ln_to, qn("a:pt"))
            pt.set("x", str(lx)); pt.set("y", str(ly))
        elif cmd.op == "C":
            cub = etree.SubElement(path_el, qn("a:cubicBezTo"))
            for ax, ay in cmd.pts:
                lx, ly = to_local(ax, ay)
                pt = etree.SubElement(cub, qn("a:pt"))
                pt.set("x", str(lx)); pt.set("y", str(ly))
        elif cmd.op == "Q":
            quad = etree.SubElement(path_el, qn("a:quadBezTo"))
            for ax, ay in cmd.pts:
                lx, ly = to_local(ax, ay)
                pt = etree.SubElement(quad, qn("a:pt"))
                pt.set("x", str(lx)); pt.set("y", str(ly))
        elif cmd.op == "Z":
            etree.SubElement(path_el, qn("a:close"))

    # noFill (도형 채움 없음 — 선 전용)
    etree.SubElement(spPr, qn("a:noFill"))

    # 선 스타일
    ln_el = etree.SubElement(spPr, qn("a:ln"))
    ln_el.set("w", str(int(Pt(2.0))))   # 2pt = 25400 EMU
    solidFill = etree.SubElement(ln_el, qn("a:solidFill"))
    srgbClr = etree.SubElement(solidFill, qn("a:srgbClr"))
    srgbClr.set("val", "475569")         # slate-600
    if dashed:
        prstDash = etree.SubElement(ln_el, qn("a:prstDash"))
        prstDash.set("val", "dash")
    tail_end = etree.SubElement(ln_el, qn("a:tailEnd"))
    tail_end.set("type", "triangle")
    tail_end.set("w", "med")
    tail_end.set("len", "med")

    # txBody (p:sp 필수 자식 요소)
    txBody = etree.SubElement(sp, qn("p:txBody"))
    etree.SubElement(txBody, qn("a:bodyPr"))
    etree.SubElement(txBody, qn("a:lstStyle"))
    etree.SubElement(txBody, qn("a:p"))

    return True


def _add_edge_label_at(
    slide,
    cx: float,
    cy: float,
    label: str,
    avoid_bboxes: list | None = None,
) -> None:
    """엣지 라벨을 (cx, cy) 중심으로 배치 (흰색 배경 + 슬레이트 텍스트).

    B.5: avoid_bboxes가 제공되면 라벨-노드 충돌 시 수직축으로 최대 4회 nudge.
         여전히 겹치면 외곽선을 추가하여 가독성 확보.
    """
    if not label:
        return
    # iter-7: 스마트 래핑 — 16자 초과 라벨은 CamelCase/공백 경계에서 줄 분할
    wrapped_label = _wrap_label_smart(label, max_chars=16)
    n_lines = wrapped_label.count("\n") + 1

    # 텍스트 길이에 따른 폭/높이 추정 (한 줄 기준 0.08in/char, 다중 줄은 높이 추가)
    longest_line = max(wrapped_label.split("\n"), key=len)
    est_w = max(0.5, 0.08 * len(longest_line) + 0.2)
    est_h = 0.24 * n_lines  # iter-7: 줄 수 × 0.24in

    # B.5: 라벨 위치 충돌 회피 — 양방향(위/아래) nudge, 최대 32회, 0.30in씩
    # F.3: B.2 높이 확장 후 노드가 3~4in 이상 될 수 있어 기존 ±1.5in 범위 부족 → ±4.8in으로 확대
    has_conflict = False
    if avoid_bboxes:
        # nudge 방향 패턴: 0, -0.30, +0.30, -0.60, +0.60, ...
        nudge_seq = [0.0]
        for step in range(1, 17):
            nudge_seq.append(-step * 0.30)
            nudge_seq.append(+step * 0.30)

        best_cy = cy
        resolved = False
        for delta in nudge_seq:
            trial_cy = cy + delta
            lx1 = cx - est_w / 2; ly1 = trial_cy - est_h / 2
            lx2 = cx + est_w / 2; ly2 = trial_cy + est_h / 2
            conflict = any(
                lx1 < bx2 and lx2 > bx1 and ly1 < by2 and ly2 > by1
                for bx1, by1, bx2, by2 in avoid_bboxes
            )
            if not conflict:
                best_cy = trial_cy
                resolved = True
                break
        cy = best_cy
        if not resolved:
            has_conflict = True  # 모든 시도 실패 → 외곽선 부여

    txb = slide.shapes.add_textbox(
        Inches(cx - est_w / 2),
        Inches(cy - est_h / 2),
        Inches(est_w),
        Inches(est_h),
    )
    # 배경 흰색 사각형 효과
    txb.fill.solid()
    txb.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    # iter-7: 항상 외곽선 추가 (가독성) — 충돌 시 더 진한 색
    if has_conflict:
        txb.line.color.rgb = RGBColor(0x64, 0x74, 0x88)
        txb.line.width = Pt(1.0)
    else:
        txb.line.color.rgb = RGBColor(0x94, 0xA3, 0xB8)
        txb.line.width = Pt(0.75)
    tf = txb.text_frame
    tf.margin_left = Pt(2)
    tf.margin_right = Pt(2)
    tf.margin_top = Pt(1)
    tf.margin_bottom = Pt(1)
    tf.word_wrap = (n_lines > 1)  # 다중 줄이면 word_wrap 활성화
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = wrapped_label
    run.font.size = Pt(11)  # iter-7: 최소 11pt (가독성)
    run.font.color.rgb = RGBColor(0x47, 0x55, 0x69)
    try:
        rPr = run._r.get_or_add_rPr()
        ea = etree.SubElement(rPr, qn("a:ea"))
        ea.set("typeface", "맑은 고딕")
        latin = rPr.find(qn("a:latin"))
        if latin is None:
            latin = etree.SubElement(rPr, qn("a:latin"))
        latin.set("typeface", "맑은 고딕")
    except Exception:
        pass


def _render_pptx_from_layout(layout, title: str = "") -> bytes:
    """layout_engine이 만든 LayoutResult를 PPTX 슬라이드로 렌더링한다."""
    from converters.layout_engine import _suggest_slide_dims, _apply_layout_rescale

    # ── B.2: 노드 최소 높이 보장 — dagre 폰트 크기 차이 보정 ──────────────────
    # [설계 결정] auto_size=None + word_wrap=True = OOXML <a:noAutofit/>
    #   → PowerPoint: 텍스트가 박스 밖으로 overflow (가시적). 잘림 없음.
    #   → LibreOffice: 박스 경계에서 clip (LibreOffice 렌더링 한계).
    #
    # dagre 레이아웃은 mmdc 브라우저 9px 폰트(char_w≈0.052in) 기준으로 노드를
    # 배치한다. PPTX는 맑은 고딕 9pt(char_w≈0.065in)를 사용하므로 실제 wrapping이
    # dagre 예상보다 많아 박스 높이가 부족하다.
    #
    # _fit_label_to_box 는 PPTX 실제 인치 단위(9pt 폰트)로 높이를 계산하므로,
    # dagre pre-scale 좌표와 단위가 다르다. 예상 rescale factor(rs)를 미리 계산하여
    # 박스 폭/높이를 PPTX 공간으로 변환한 뒤 _fit_label_to_box를 호출하고,
    # 결과를 다시 dagre 좌표로 역변환하여 node.h를 보정한다.
    # 이렇게 하면 rs가 매우 클 때(예: 7x) 과도한 높이 팽창을 방지한다.
    _MAX_EXPAND  = 2.2   # 원본 높이 대비 최대 확장 비율 (겹침 방지)
    _FIT_FONT_PT = 9.0   # PPTX 노드 기본 폰트 크기 (_add_node_at 기본값)

    # 예상 rs 계산 (rescale 전이므로 현재 canvas 기준)
    _b2_ratio = (layout.canvas_w / layout.canvas_h) if layout.canvas_h > 0 else None
    _b2_sw, _b2_sh = _suggest_slide_dims(
        len(layout.nodes), len(layout.clusters), canvas_ratio=_b2_ratio
    )
    _b2_aw = _b2_sw - 2 * MARGIN
    _b2_ah = _b2_sh - TITLE_H - 2 * MARGIN
    if layout.canvas_w > 0 and layout.canvas_h > 0:
        _b2_rs = min(_b2_aw / layout.canvas_w, _b2_ah / layout.canvas_h)
        _b2_rs = max(_b2_rs, 1.0)   # downscale 방지
    else:
        _b2_rs = 1.0

    # F.1: 다이아몬드 내접 텍스트 영역 보정 인수
    # 다이아몬드(rhombus) 바운딩 박스 대비 실제 텍스트 가용 폭/높이 ≈ 60%
    _DIAMOND_EFF = 0.60

    for node in layout.nodes.values():
        # 박스 폭/높이를 PPTX 공간으로 변환 → _fit_label_to_box 호출 → dagre로 역변환
        scaled_w = node.w * _b2_rs
        scaled_h = node.h * _b2_rs
        if node.shape == "diamond":
            # 다이아몬드: 내접 가용 폭/높이로 줄 수 추정 후 외접 박스 크기로 역산
            eff_w = scaled_w * _DIAMOND_EFF
            eff_h = scaled_h * _DIAMOND_EFF
            fit_h_pptx, _, _ = _fit_label_to_box(
                node.label, eff_w, eff_h, font_size_pt=_FIT_FONT_PT
            )
            fit_h_pptx = fit_h_pptx / _DIAMOND_EFF  # 내접 → 외접 박스 높이로 변환
        else:
            fit_h_pptx, _, _ = _fit_label_to_box(
                node.label, scaled_w, scaled_h, font_size_pt=_FIT_FONT_PT
            )
        fit_h_dagre = fit_h_pptx / _b2_rs  # PPTX 인치 → dagre 좌표
        cap_h = node.h * _MAX_EXPAND
        node.h = min(max(node.h, fit_h_dagre), cap_h)

    # ── iter-6: 노드 간 최소 간격 보장 — B.2 height expansion 후 겹침 보정 ─────
    # dense 프리셋(40/40)으로 발생하는 0.05" 이내 미세 겹침을 y 축 push-down으로 해소.
    _MIN_NODE_GAP = 0.08   # 최소 노드 간 수직 간격 (inches)
    _sep_nodes = sorted(layout.nodes.values(), key=lambda n: (n.y, n.x))
    for _si in range(len(_sep_nodes)):
        _ni = _sep_nodes[_si]
        for _sj in range(_si + 1, len(_sep_nodes)):
            _nj = _sep_nodes[_sj]
            if _nj.y >= _ni.y + _ni.h + _MIN_NODE_GAP:
                break   # 이후 노드는 모두 충분히 아래에 있음
            # x 겹침 확인
            if not (_ni.x + _ni.w <= _nj.x or _nj.x + _nj.w <= _ni.x):
                _nj.y = _ni.y + _ni.h + _MIN_NODE_GAP  # y 축 push-down

    # ── B.3: cluster bbox를 자식 노드 합집합으로 재계산 + 겹침 해소 ─────────
    if layout.clusters:
        _recompute_cluster_bboxes(layout.nodes, layout.clusters)
        # canvas 크기를 갱신된 bbox에 맞게 확장
        all_x2 = [n.x + n.w for n in layout.nodes.values()]
        all_y2 = [n.y + n.h for n in layout.nodes.values()]
        all_x2 += [cl.x + cl.w for cl in layout.clusters]
        all_y2 += [cl.y + cl.h for cl in layout.clusters]
        if all_x2:
            layout.canvas_w = max(layout.canvas_w, max(all_x2))
        if all_y2:
            layout.canvas_h = max(layout.canvas_h, max(all_y2))

    # ── iter-3/4: 동적 슬라이드 크기 + viewBox 비율 적응 + 캔버스 업스케일 ───
    canvas_ratio = (layout.canvas_w / layout.canvas_h) if layout.canvas_h > 0 else None
    slide_w, slide_h = _suggest_slide_dims(len(layout.nodes), len(layout.clusters), canvas_ratio=canvas_ratio)
    avail_w = slide_w - 2 * MARGIN
    avail_h = slide_h - TITLE_H - 2 * MARGIN
    # canvas가 avail 영역보다 작을 때 업스케일 (더 큰 슬라이드에 맞춰 확대)
    if layout.canvas_w > 0 and layout.canvas_h > 0:
        rs_w = avail_w / layout.canvas_w
        rs_h = avail_h / layout.canvas_h
        rs = min(rs_w, rs_h)
        if rs > 1.001:  # 0.1% 이상 확대 시에만 적용
            _apply_layout_rescale(layout, rs)

    # ── D.1 (type-profile): ER entity 박스 클램프 — profile.pptx_box_clamp_strategy 분기
    # "fixed_max"            (ER_PROFILE 기본): 고정 상한 2.5"×4.0" + pad (iter-6 베스트)
    # "content_proportional" (fallback):        content × 1.6/2.0 (iter-9 스타일)
    if getattr(layout, 'is_er', False):
        _ER_MIN_H, _ER_MAX_H = 0.8, 2.5
        _ER_MIN_W, _ER_MAX_W = 1.5, 4.0
        _profile = getattr(layout, 'profile', None)
        _clamp_strategy = (_profile.pptx_box_clamp_strategy if _profile else "fixed_max")

        if _clamp_strategy == "fixed_max":
            # iter-6 스타일: 고정 상한 + 소량 패딩 — ER 기본 전략 (사용자 평가 최고)
            _ER_PAD = 0.15
            for node in layout.nodes.values():
                node.h = max(_ER_MIN_H, min(node.h + _ER_PAD, _ER_MAX_H))
                node.w = max(_ER_MIN_W, min(node.w + _ER_PAD, _ER_MAX_W))
        else:
            # content 비례 (iter-9 스타일): 라벨 줄 수 기반 동적 상한
            _ER_LINE_H = 0.25; _ER_V_PAD = 0.15
            _ER_CHAR_W = 0.075; _ER_H_PAD = 0.20
            for node in layout.nodes.values():
                lbl = node.label or ""
                lines = [ln for ln in lbl.split("\n") if ln.strip()] or [""]
                n_lines   = max(1, len(lines))
                max_chars = max(len(ln) for ln in lines)
                content_h = n_lines * _ER_LINE_H + _ER_V_PAD
                content_w = max_chars * _ER_CHAR_W + _ER_H_PAD
                max_h = min(_ER_MAX_H, max(_ER_MIN_H, content_h * 1.6))
                max_w = min(_ER_MAX_W, max(_ER_MIN_W, content_w * 2.0))
                node.h = max(_ER_MIN_H, min(node.h, max_h))
                node.w = max(_ER_MIN_W, min(node.w, max_w))

    # PPTX 슬라이드 생성 (동적 크기)
    prs = Presentation()
    prs.slide_width = Inches(slide_w)
    prs.slide_height = Inches(slide_h)
    blank_layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank_layout)

    _add_title_and_bg(slide, title, slide_w=slide_w)

    # 콘텐츠 영역에 캔버스를 가운데 정렬 (오프셋)
    off_x = MARGIN + max(0.0, (avail_w - layout.canvas_w) / 2.0)
    off_y = TITLE_H + MARGIN + max(0.0, (avail_h - layout.canvas_h) / 2.0)

    def _clamp_pos(x: float, y: float, w: float, h: float) -> tuple[float, float]:
        """shape 좌상단 좌표를 슬라이드 경계 안으로 클램프."""
        x = max(0.0, min(x, slide_w - w))
        y = max(TITLE_H, min(y, slide_h - h))
        return x, y

    # 1) 서브그래프 박스 (노드 뒤로)
    cluster_idx_map: dict[str, int] = {}
    for idx, cl in enumerate(layout.clusters):
        cluster_idx_map[cl.id] = idx
        fill_color = _SUBGRAPH_FILLS[idx % len(_SUBGRAPH_FILLS)]
        cx, cy = _clamp_pos(off_x + cl.x, off_y + cl.y, cl.w, cl.h)
        _add_subgraph_box_at(
            slide,
            cx, cy, cl.w, cl.h,
            cl.label, fill_color, idx,
        )

    # 2) 노드 (서브그래프 인덱스 기반 팔레트)
    shape_map: dict[str, object] = {}
    for node_idx, (nid, node) in enumerate(layout.nodes.items()):
        if node.cluster_id and node.cluster_id in cluster_idx_map:
            palette_idx = cluster_idx_map[node.cluster_id]
        else:
            palette_idx = node_idx
        nx, ny = _clamp_pos(off_x + node.x, off_y + node.y, node.w, node.h)
        shape_map[nid] = _add_node_at(
            slide,
            nx, ny, node.w, node.h,
            node.label, node.shape, palette_idx,
            fill_override=node.fill_override,
            text_color_override=node.text_color_override,
        )

    # 3) 엣지 (polyline 다중 connector) — 노드 회피 라우팅 적용
    AVOID_PADDING = 0.08
    SUBGRAPH_TITLE_H = 0.26  # _add_subgraph_box_at의 title_h와 동일

    # B.4: 전체 노드 bbox 사전 구성 (B.2 조정 크기 반영 + B.5 label nudge 재사용)
    all_node_bboxes: dict[str, tuple[float, float, float, float]] = {
        nid: (off_x + n.x, off_y + n.y, off_x + n.x + n.w, off_y + n.y + n.h)
        for nid, n in layout.nodes.items()
    }

    # B.4: 이미 배치된 edge label bbox 목록 (상호 회피용)
    placed_label_bboxes: list[tuple[float, float, float, float]] = []

    for edge in layout.edges:
        if edge.source not in shape_map or edge.target not in shape_map:
            continue
        pts = [(off_x + x, off_y + y) for (x, y) in edge.points]
        if len(pts) < 2:
            continue

        # B.4: src/dst 노드의 cluster 파악
        src_node = layout.nodes.get(edge.source)
        dst_node = layout.nodes.get(edge.target)
        src_cl_id = src_node.cluster_id if src_node else None
        dst_cl_id = dst_node.cluster_id if dst_node else None

        # B.4: 회피 박스 구성
        #  - 노드: source/target 제외 모든 노드 (B.2 조정 크기 반영)
        #  - cluster: src/dst와 무관한 cluster → 전체 박스 회피
        #             src/dst와 관련된 cluster → 타이틀 바만 회피
        avoid: list[tuple[float, float, float, float]] = [
            bbox for nid, bbox in all_node_bboxes.items()
            if nid not in (edge.source, edge.target)
        ]
        for cl in layout.clusters:
            if cl.id not in (src_cl_id, dst_cl_id):
                avoid.append(
                    (off_x + cl.x, off_y + cl.y,
                     off_x + cl.x + cl.w, off_y + cl.y + cl.h)
                )
            else:
                avoid.append(
                    (off_x + cl.x, off_y + cl.y,
                     off_x + cl.x + cl.w, off_y + cl.y + SUBGRAPH_TITLE_H)
                )

        # ── 트랙 D: custGeom freeform 우선 시도 ──────────────────────────
        # dagre 의 bezier 경로(path_d)를 OOXML cubicBezTo 로 직접 변환.
        # dagre 가 이미 노드 회피를 처리하므로 avoidance routing 불필요.
        # 파싱 실패 시 polyline + 회피 라우팅 폴백.
        if not _add_freeform_edge(slide, edge.path_d, layout.scale, off_x, off_y, edge.dashed):
            # polyline 폴백: 회피 라우팅 적용
            if len(pts) == 2:
                # 단순 직선 → 전체 회피 라우팅
                pts = _route_around_nodes(pts[0], pts[1], avoid, padding=AVOID_PADDING)
            else:
                # mmdc/dagre가 라우팅한 polyline → 교차하는 segment만 우회
                new_pts = [pts[0]]
                for i in range(len(pts) - 1):
                    seg = (pts[i], pts[i + 1])
                    if _segment_intersects_any(seg, avoid, AVOID_PADDING):
                        routed = _route_around_nodes(pts[i], pts[i + 1], avoid, padding=AVOID_PADDING)
                        new_pts.extend(routed[1:])  # 첫 점은 중복이므로 제외
                    else:
                        new_pts.append(pts[i + 1])
                pts = new_pts
            _add_polyline_edge(slide, pts, dashed=edge.dashed)
        if edge.label and edge.label_pos:
            lx, ly = edge.label_pos
            cx_label = off_x + lx
            cy_label = off_y + ly
            # iter-4: edge label 위치 클램프 (음수 또는 슬라이드 초과 좌표 방지)
            _cl_est_w = max(0.5, 0.08 * len(edge.label) + 0.2)
            _cl_est_h = 0.22
            _cl_tl_x, _cl_tl_y = _clamp_pos(
                cx_label - _cl_est_w / 2, cy_label - _cl_est_h / 2,
                _cl_est_w, _cl_est_h,
            )
            cx_label = _cl_tl_x + _cl_est_w / 2
            cy_label = _cl_tl_y + _cl_est_h / 2
            # B.4/B.5: 회피 대상 = 모든 노드(src/dst 포함) + 비관련 cluster + 이미 배치된 라벨
            #           edge label은 어떤 노드와도 겹치면 안 됨
            label_avoid: list[tuple[float, float, float, float]] = list(all_node_bboxes.values())
            for cl in layout.clusters:
                if cl.id not in (src_cl_id, dst_cl_id):
                    label_avoid.append(
                        (off_x + cl.x, off_y + cl.y,
                         off_x + cl.x + cl.w, off_y + cl.y + cl.h)
                    )
            label_avoid.extend(placed_label_bboxes)  # edge label 상호 회피

            _add_edge_label_at(
                slide, cx_label, cy_label, edge.label,
                avoid_bboxes=label_avoid,
            )
            # 배치된 라벨 bbox 누적 (est_w/h와 동일 계산)
            est_w_l = max(0.5, 0.08 * len(edge.label) + 0.2)
            placed_label_bboxes.append((
                cx_label - est_w_l / 2, cy_label - 0.11,
                cx_label + est_w_l / 2, cy_label + 0.11,
            ))

    output = BytesIO()
    prs.save(output)
    output.seek(0)
    return output.read()
