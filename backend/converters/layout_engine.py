# ============================================================
# layout_engine.py: mmdc(Mermaid CLI) 기반 SVG 좌표 추출 레이아웃 엔진
# 상세: Mermaid 코드를 mmdc로 SVG 렌더링한 뒤 노드/클러스터/엣지의
#       절대 좌표를 파싱하여 inches 단위 LayoutResult를 반환.
#       PPTX/Draw.io 변환기가 이 결과를 받아 dagre 품질의 레이아웃을
#       그대로 재현한다. 실패 시 None 반환 → 호출자가 기존 그리드 폴백.
#       erDiagram도 지원 — 엔티티 박스/Attribute 행/관계 cardinality 추출.
# 생성일: 2026-05-18 | 수정일: 2026-05-19
# ============================================================

from __future__ import annotations

import base64
import html as _html_mod
import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lxml import etree

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 데이터 모델
# ──────────────────────────────────────────────

@dataclass
class LaidNode:
    """SVG에서 추출한 노드 (좌표 단위: inches, 좌상단 기준)."""
    id: str
    label: str
    x: float
    y: float
    w: float
    h: float
    cluster_id: Optional[str] = None
    shape: str = "rect"


@dataclass
class LaidCluster:
    """SVG에서 추출한 클러스터(서브그래프). 좌상단 기준 좌표."""
    id: str
    label: str
    x: float
    y: float
    w: float
    h: float


@dataclass
class LaidEdge:
    """엣지: polyline 좌표 시퀀스(inches). 첫 점이 source 출구, 마지막이 target 입구."""
    source: str
    target: str
    label: str
    points: list[tuple[float, float]] = field(default_factory=list)
    label_pos: Optional[tuple[float, float]] = None   # 라벨 중심 (inches)
    dashed: bool = False


@dataclass
class LayoutResult:
    """mmdc 렌더링 결과 좌표 집합. 원점은 (0,0)."""
    nodes: dict[str, LaidNode] = field(default_factory=dict)
    clusters: list[LaidCluster] = field(default_factory=list)
    edges: list[LaidEdge] = field(default_factory=list)
    canvas_w: float = 0.0   # inches
    canvas_h: float = 0.0
    scale: float = 1.0      # px → inches 변환 계수


# SVG 네임스페이스
_SVG_NS = "http://www.w3.org/2000/svg"
_SVG = f"{{{_SVG_NS}}}"

# 노드 ID 패턴: my-svg-flowchart-{NODEID}-{seq}
_NODE_ID_RE = re.compile(r"flowchart-(.+?)-\d+$")
# 엣지 ID 패턴: my-svg-L_{SRC}_{DST}_{seq}
_EDGE_ID_RE = re.compile(r"L_(.+?)_(.+?)_\d+$")
# SVG prefix (mmdc는 첫 g 안에 모든 노드 id를 'my-svg-' 접두사로 생성)
_CLUSTER_ID_PREFIX_RE = re.compile(r"^[A-Za-z][\w-]*?-(.+)$")

# ER 다이어그램: 엔티티 노드 id 패턴 — entity-{ENTITY_NAME}-{idx}
# mmdc 버전에 따라 trailing -\d+ 가 없을 수도 있으므로 선택적으로 처리
_ER_NODE_ID_RE = re.compile(r"^entity-(.+?)(?:-\d+)?$")
# ER 다이어그램: 엣지 id 패턴 — id_entity-{SRC}-X_entity-{DST}-Y_{seq}
_ER_EDGE_ID_RE = re.compile(r"^id_entity-(.+?)-\d+_entity-(.+?)-\d+_\d+$")
# ER 노드 외곽 박스를 그리는 path의 첫 M 절대좌표 추출용 (entityBox path)
_ER_PATH_BBOX_RE = re.compile(
    r"^\s*M\s*([-\d.]+)\s+([-\d.]+)\s+L\s*([-\d.]+)\s+([-\d.]+)\s+"
    r"L\s*([-\d.]+)\s+([-\d.]+)\s+L\s*([-\d.]+)\s+([-\d.]+)"
)
# ER marker URL → cardinality 문자열 매핑
# (mermaid ER 문법: ||--o{ 처럼 source/target 각각 두 글자)
_ER_MARKER_TO_CARDINALITY = {
    "er-onlyOneStart": "||",
    "er-onlyOneEnd": "||",
    "er-zeroOrMoreStart": "}o",
    "er-zeroOrMoreEnd": "o{",
    "er-oneOrMoreStart": "}|",
    "er-oneOrMoreEnd": "|{",
    "er-zeroOrOneStart": "|o",
    "er-zeroOrOneEnd": "o|",
}
_ER_MARKER_URL_RE = re.compile(r"#[^_]*_?(er-\w+)\)")


# ──────────────────────────────────────────────
# mmdc 호출
# ──────────────────────────────────────────────

def _check_mmdc() -> bool:
    """mmdc CLI가 PATH에 있는지 확인."""
    return shutil.which("mmdc") is not None


def _run_mmdc_to_svg(
    mermaid_code: str,
    workdir: Path,
    puppeteer_config: Optional[str],
    timeout: int = 30,
) -> Optional[Path]:
    """mmdc로 Mermaid를 SVG로 렌더링하고 경로 반환. 실패 시 None."""
    in_path = workdir / "input.mmd"
    out_path = workdir / "out.svg"
    in_path.write_text(mermaid_code, encoding="utf-8")

    cmd = ["mmdc", "-i", str(in_path), "-o", str(out_path), "-b", "transparent"]
    if puppeteer_config and Path(puppeteer_config).exists():
        cmd += ["-p", puppeteer_config]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.warning("mmdc 호출 시간 초과(%ds)", timeout)
        return None

    if result.returncode != 0 or not out_path.exists():
        logger.warning("mmdc 실패: rc=%s stderr=%s", result.returncode, result.stderr[:300])
        return None

    return out_path


# ──────────────────────────────────────────────
# SVG 파싱 헬퍼
# ──────────────────────────────────────────────

def _parse_viewbox(root: etree._Element) -> tuple[float, float]:
    """viewBox 속성에서 (width, height) 추출."""
    vb = root.get("viewBox") or ""
    parts = vb.split()
    if len(parts) == 4:
        try:
            return float(parts[2]), float(parts[3])
        except ValueError:
            pass
    # 폴백: width/height 직접
    try:
        w = float((root.get("width") or "0").rstrip("px"))
        h = float((root.get("height") or "0").rstrip("px"))
        return w, h
    except ValueError:
        return 0.0, 0.0


_TRANSLATE_RE = re.compile(r"translate\(\s*([-\d.]+)[,\s]+([-\d.]+)\s*\)")


def _parse_translate(transform_attr: str) -> tuple[float, float]:
    """transform='translate(x, y)' 에서 (x, y) 반환. 실패 시 (0, 0)."""
    if not transform_attr:
        return 0.0, 0.0
    m = _TRANSLATE_RE.search(transform_attr)
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except ValueError:
            return 0.0, 0.0
    return 0.0, 0.0


def _extract_text_from_foreign(elem: etree._Element) -> str:
    """foreignObject 안의 텍스트를 수집: html.unescape + <br/> → \\n 처리.

    lxml은 <br/>를 요소로 파싱하고, <br/>뒤 텍스트를 element.tail에 저장한다.
    기존 code가 .text만 수집해 <br/>뒤 텍스트가 소실되는 버그를 수정한다.

    처리 규칙:
     - t.text → 일반 텍스트 (공백 제거 후 추가)
     - t.tag가 br (네임스페이스 무시) → t.tail을 \\n 접두어로 추가
     - 기타 t.tail → 공백으로 이어 붙임
    """
    parts: list[str] = []
    for t in elem.iter():
        # element 자체 텍스트
        if t.text:
            s = t.text.strip()
            if s:
                parts.append(s)
        # element tail (element 닫힘 태그 뒤 텍스트)
        if t.tail:
            s = t.tail.strip()
            if s:
                tag_local = t.tag.split("}")[-1].lower() if "}" in str(t.tag) else str(t.tag).lower()
                if tag_local == "br":
                    parts.append("\n" + s)
                else:
                    parts.append(" " + s)

    raw = "".join(parts).strip()
    return _html_mod.unescape(raw)


def _strip_svg_id_prefix(svg_id: str, root_prefix: str = "") -> str:
    """SVG root id 접두사(예: 'my-svg-')를 제거해 원래 Mermaid ID를 복원."""
    if root_prefix and svg_id.startswith(root_prefix):
        return svg_id[len(root_prefix):]
    # 폴백: 'my-svg-' 또는 단일 prefix 형태 제거
    m = _CLUSTER_ID_PREFIX_RE.match(svg_id)
    if m:
        return m.group(1)
    return svg_id


def _decode_data_points(b64: str) -> Optional[list[tuple[float, float]]]:
    """data-points 속성(base64 JSON)을 디코드하여 [(x, y), ...] 반환."""
    if not b64:
        return None
    try:
        raw = base64.b64decode(b64).decode("utf-8")
        pts = json.loads(raw)
        out: list[tuple[float, float]] = []
        for p in pts:
            if isinstance(p, dict) and "x" in p and "y" in p:
                out.append((float(p["x"]), float(p["y"])))
        return out if out else None
    except Exception:
        return None


# 'M x,y L x,y C x1,y1 x2,y2 x,y ...' 패턴에서 절대 좌표 추출 (대문자 명령만)
_PATH_NUM_RE = re.compile(r"[-+]?\d*\.?\d+(?:e[-+]?\d+)?", re.I)


def _parse_path_d_fallback(d: str) -> list[tuple[float, float]]:
    """data-points 부재 시 path d 속성에서 절대좌표 시퀀스를 근사 추출.
    M, L, C(시작·끝점만)을 처리. m, l, c(상대좌표)는 단순화하여 누적."""
    pts: list[tuple[float, float]] = []
    if not d:
        return pts
    # 명령 단위로 토큰화
    tokens = re.findall(r"([MmLlCcZz])|([-+]?\d*\.?\d+(?:e[-+]?\d+)?)", d)
    nums: list[float] = []
    cur_cmd = None
    cur_x = cur_y = 0.0
    i = 0

    flat = re.findall(r"[MmLlCcZz]|[-+]?\d*\.?\d+(?:e[-+]?\d+)?", d)
    for tok in flat:
        if tok in "MmLlCcZz":
            cur_cmd = tok
            continue
        try:
            num = float(tok)
        except ValueError:
            continue
        nums.append(num)

        if cur_cmd in ("M", "L"):
            if len(nums) == 2:
                cur_x, cur_y = nums[0], nums[1]
                pts.append((cur_x, cur_y))
                nums = []
        elif cur_cmd in ("m", "l"):
            if len(nums) == 2:
                cur_x += nums[0]
                cur_y += nums[1]
                pts.append((cur_x, cur_y))
                nums = []
        elif cur_cmd == "C":
            if len(nums) == 6:
                cur_x, cur_y = nums[4], nums[5]
                pts.append((cur_x, cur_y))
                nums = []
        elif cur_cmd == "c":
            if len(nums) == 6:
                cur_x += nums[4]
                cur_y += nums[5]
                pts.append((cur_x, cur_y))
                nums = []
        elif cur_cmd in ("Z", "z"):
            nums = []
    return pts


def _simplify_polyline(
    pts: list[tuple[float, float]], min_segment: float
) -> list[tuple[float, float]]:
    """짧은 segment를 제거해 polyline을 매끄럽게 한다 (inches 단위)."""
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    for x, y in pts[1:-1]:
        lx, ly = out[-1]
        if abs(x - lx) + abs(y - ly) >= min_segment:
            out.append((x, y))
    out.append(pts[-1])
    return out


# ──────────────────────────────────────────────
# 핵심 파싱 로직
# ──────────────────────────────────────────────

def _parse_clusters(
    root: etree._Element,
    scale: float,
    root_prefix: str = "",
) -> dict[str, LaidCluster]:
    """SVG에서 cluster 좌표를 추출. 키: cluster_id, 값: LaidCluster (inches)."""
    clusters: dict[str, LaidCluster] = {}
    for g in root.iter(f"{_SVG}g"):
        if (g.get("class") or "") != "cluster":
            continue
        svg_id = g.get("id") or ""
        cid = _strip_svg_id_prefix(svg_id, root_prefix)
        rect = g.find(f"{_SVG}rect")
        if rect is None:
            continue
        try:
            x = float(rect.get("x", "0"))
            y = float(rect.get("y", "0"))
            w = float(rect.get("width", "0"))
            h = float(rect.get("height", "0"))
        except ValueError:
            continue

        # cluster-label foreignObject의 텍스트
        label = ""
        label_g = None
        for child in g.iter(f"{_SVG}g"):
            if (child.get("class") or "").startswith("cluster-label"):
                label_g = child
                break
        if label_g is not None:
            fo = label_g.find(f"{_SVG}foreignObject")
            if fo is not None:
                label = _extract_text_from_foreign(fo)

        clusters[cid] = LaidCluster(
            id=cid,
            label=label or cid,
            x=x * scale,
            y=y * scale,
            w=w * scale,
            h=h * scale,
        )
    return clusters


def _parse_nodes(
    root: etree._Element,
    scale: float,
) -> dict[str, LaidNode]:
    """SVG에서 노드 좌표를 추출. 키: 원래 Mermaid 노드 ID."""
    nodes: dict[str, LaidNode] = {}
    for g in root.iter(f"{_SVG}g"):
        cls = g.get("class") or ""
        if "node" not in cls.split():
            continue
        svg_id = g.get("id") or ""
        m = _NODE_ID_RE.search(svg_id)
        if not m:
            continue
        node_id = m.group(1)

        # transform translate(cx, cy)
        cx, cy = _parse_translate(g.get("transform") or "")

        # 내부 rect/polygon/circle의 bbox
        w = h = 0.0
        shape = "rect"
        rect = g.find(f"{_SVG}rect")
        if rect is not None:
            try:
                w = float(rect.get("width", "0"))
                h = float(rect.get("height", "0"))
            except ValueError:
                pass
            shape = "rect"
        else:
            poly = g.find(f"{_SVG}polygon")
            if poly is not None:
                pts_attr = poly.get("points") or ""
                xs, ys = [], []
                for token in pts_attr.replace(",", " ").split():
                    try:
                        v = float(token)
                    except ValueError:
                        continue
                    # 짝/홀로 분배
                    if len(xs) == len(ys):
                        xs.append(v)
                    else:
                        ys.append(v)
                if xs and ys:
                    w = max(xs) - min(xs)
                    h = max(ys) - min(ys)
                shape = "diamond"
            else:
                circ = g.find(f"{_SVG}circle")
                if circ is not None:
                    try:
                        r = float(circ.get("r", "0"))
                        w = h = 2 * r
                    except ValueError:
                        pass
                    shape = "circle"

        if w <= 0 or h <= 0:
            continue

        # 라벨
        label = ""
        fo = g.find(f".//{_SVG}foreignObject")
        if fo is not None:
            label = _extract_text_from_foreign(fo)

        # 좌상단 좌표 (cx, cy는 중심)
        x_tl = (cx - w / 2) * scale
        y_tl = (cy - h / 2) * scale

        nodes[node_id] = LaidNode(
            id=node_id,
            label=label or node_id,
            x=x_tl,
            y=y_tl,
            w=w * scale,
            h=h * scale,
            shape=shape,
        )
    return nodes


def _parse_edges(
    root: etree._Element,
    scale: float,
    min_seg_in: float,
    root_prefix: str = "",
) -> list[LaidEdge]:
    """edgePaths의 path들에서 polyline 좌표를 추출."""
    edges: list[LaidEdge] = []

    # edgeLabel 좌표 매핑 (data-id → (cx, cy))
    label_centers: dict[str, tuple[float, float]] = {}
    label_texts: dict[str, str] = {}
    for elg in root.iter(f"{_SVG}g"):
        if (elg.get("class") or "") != "edgeLabel":
            continue
        cx, cy = _parse_translate(elg.get("transform") or "")
        # label g 안의 data-id가 엣지와 매칭
        inner = elg.find(f"{_SVG}g")
        data_id = inner.get("data-id") if inner is not None else None
        if not data_id:
            continue
        label_centers[data_id] = (cx * scale, cy * scale)
        fo = elg.find(f".//{_SVG}foreignObject")
        if fo is not None:
            label_texts[data_id] = _extract_text_from_foreign(fo)

    for path in root.iter(f"{_SVG}path"):
        cls = path.get("class") or ""
        if "flowchart-link" not in cls:
            continue
        svg_id = path.get("id") or ""
        edge_data_id = path.get("data-id") or _strip_svg_id_prefix(svg_id, root_prefix)
        m = _EDGE_ID_RE.search(svg_id)
        if not m:
            continue
        src, dst = m.group(1), m.group(2)

        # 우선 data-points (base64 JSON) 시도
        b64 = path.get("data-points") or ""
        pts_px = _decode_data_points(b64)
        if not pts_px:
            pts_px = _parse_path_d_fallback(path.get("d") or "")

        if not pts_px:
            continue

        pts_in = [(x * scale, y * scale) for x, y in pts_px]
        pts_in = _simplify_polyline(pts_in, min_seg_in)

        dashed = ("edge-pattern-dashed" in cls) or ("edge-pattern-dotted" in cls)

        label = label_texts.get(edge_data_id, "")
        label_pos = label_centers.get(edge_data_id)

        edges.append(
            LaidEdge(
                source=src,
                target=dst,
                label=label,
                points=pts_in,
                label_pos=label_pos,
                dashed=dashed,
            )
        )

    return edges


# ──────────────────────────────────────────────
# ER 다이어그램 전용 파싱 로직 (Phase 2)
# ──────────────────────────────────────────────

def _is_er_svg(root: etree._Element, mermaid_code: str) -> bool:
    """SVG root class 또는 mermaid 코드 첫 줄로 ER 여부 판정."""
    cls = (root.get("class") or "").lower()
    if "erdiagram" in cls.replace(" ", ""):
        return True
    if mermaid_code:
        first = mermaid_code.strip().split("\n", 1)[0].strip().lower().replace(" ", "")
        if first.startswith("erdiagram"):
            return True
    return False


def _marker_url_to_cardinality(marker_url: str) -> str:
    """marker-start/marker-end 의 url() 문자열에서 cardinality 토큰 추출.

    예: 'url(#my-svg_er-onlyOneStart)' → '||'
    매칭 실패 또는 빈 입력이면 ''.
    """
    if not marker_url:
        return ""
    m = _ER_MARKER_URL_RE.search(marker_url)
    if not m:
        return ""
    return _ER_MARKER_TO_CARDINALITY.get(m.group(1), "")


def _er_node_text_from_label(label_g: etree._Element) -> str:
    """label g 안의 foreignObject 텍스트만 추출 (속성 행/이름 모두 공통)."""
    fo = label_g.find(f"{_SVG}foreignObject")
    if fo is None:
        return ""
    return _extract_text_from_foreign(fo)


def _er_node_bbox(g: etree._Element) -> tuple[float, float]:
    """엔티티 노드의 (width, height)를 SVG-local 좌표로 반환.

    두 가지 케이스:
    1) 속성 없는 엔티티: <rect class="basic label-container" width=.. height=..>
    2) 속성 있는 엔티티: 외곽 path의 M -W -H L W -H L W H L -W H 좌표에서 추출
    """
    # case 1: rect 기반
    rect = g.find(f"{_SVG}rect")
    if rect is not None:
        try:
            w = float(rect.get("width", "0"))
            h = float(rect.get("height", "0"))
            if w > 0 and h > 0:
                return w, h
        except ValueError:
            pass

    # case 2: 첫 자식 g의 첫 path의 d 속성에서 4-corner 추출
    for child in g.iter(f"{_SVG}path"):
        d = child.get("d") or ""
        m = _ER_PATH_BBOX_RE.match(d)
        if not m:
            continue
        try:
            xs = [float(m.group(i)) for i in (1, 3, 5, 7)]
            ys = [float(m.group(i)) for i in (2, 4, 6, 8)]
        except ValueError:
            continue
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        if w > 0 and h > 0:
            return w, h
    return 0.0, 0.0


def _parse_er_nodes(
    root: etree._Element,
    scale: float,
) -> dict[str, LaidNode]:
    """ER 엔티티 노드를 파싱해 {엔티티명: LaidNode} 반환.

    엔티티명을 ID로 사용한다 (mermaid ER에서는 엔티티명이 곧 식별자).
    label은 '엔티티이름\\n타입 이름\\n타입 이름...' 형태의 멀티라인 문자열.

    1차: _ER_NODE_ID_RE (entity-{NAME}-{idx} 또는 entity-{NAME}) 로 매칭.
    2차 fallback: class="node" + <g class="label name"> 안 entityLabel / nodeLabel 텍스트로
               엔티티 이름을 추출 (mmdc 버전 변경 대응).
    """
    nodes: dict[str, LaidNode] = {}

    # 1차: ID 정규식 매칭
    candidate_gs: list[tuple[str, etree._Element]] = []
    for g in root.iter(f"{_SVG}g"):
        cls = g.get("class") or ""
        if "node" not in cls.split():
            continue
        svg_id = g.get("id") or ""
        m = _ER_NODE_ID_RE.match(svg_id)
        if not m:
            continue
        entity_name = m.group(1)
        candidate_gs.append((entity_name, g))

    # 2차 fallback: 정규식 매칭 실패 시 entityLabel / nodeLabel 텍스트로 추출
    if not candidate_gs:
        logger.debug("ER 노드 ID 정규식 매칭 없음 → entityLabel 텍스트 fallback 시도")
        for g in root.iter(f"{_SVG}g"):
            cls = g.get("class") or ""
            if "node" not in cls.split():
                continue
            # label name g 에서 텍스트 추출
            for child in g.iter(f"{_SVG}g"):
                child_cls = (child.get("class") or "").strip()
                if child_cls in ("label name", "label"):
                    name_text = _er_node_text_from_label(child)
                    if name_text and len(name_text) > 1:
                        candidate_gs.append((name_text, g))
                        break

    for entity_name, g in candidate_gs:
        # 이미 처리된 엔티티 스킵
        if entity_name in nodes:
            continue

        # transform translate(cx, cy) — 중심 좌표
        cx, cy = _parse_translate(g.get("transform") or "")

        # 박스 크기 (rect 또는 path 기반)
        w_px, h_px = _er_node_bbox(g)
        if w_px <= 0 or h_px <= 0:
            continue

        # label name g — 엔티티 이름. 없으면 자식 g.label 의 foreignObject 사용.
        name_text = ""
        for child in g.iter(f"{_SVG}g"):
            child_cls = (child.get("class") or "").strip()
            if child_cls == "label name":
                name_text = _er_node_text_from_label(child)
                break
        if not name_text:
            # 속성 없는 엔티티: <g class="label"> 안의 foreignObject
            for child in g.iter(f"{_SVG}g"):
                child_cls = (child.get("class") or "").strip()
                if child_cls == "label":
                    name_text = _er_node_text_from_label(child)
                    if name_text:
                        break

        # Attribute 행 수집: 동일 transform y 값별로 (type, name, keys, comment) 4-튜플
        # 키: y(소수점 1자리 라운드) — mmdc가 동일 행에서 소수점 미세 차이를 줄 수 있어 3에서 1로 완화
        rows: dict[float, dict[str, str]] = {}
        for child in g.iter(f"{_SVG}g"):
            child_cls = (child.get("class") or "").strip()
            if not child_cls.startswith("label attribute-"):
                continue
            kind = child_cls.split("attribute-", 1)[1].strip()
            _, ty = _parse_translate(child.get("transform") or "")
            key = round(ty, 1)
            text = _er_node_text_from_label(child)
            row = rows.setdefault(key, {})
            row[kind] = text

        # y 오름차순으로 정렬해 한 줄씩 합치기 (빈 문자열 제외)
        attr_lines: list[str] = []
        for y_key in sorted(rows.keys()):
            row = rows[y_key]
            cells = [
                row.get("type", "").strip(),
                row.get("name", "").strip(),
                row.get("keys", "").strip(),
                row.get("comment", "").strip(),
            ]
            cells = [c for c in cells if c]
            if cells:
                attr_lines.append(" ".join(cells))

        label_parts: list[str] = []
        if name_text:
            label_parts.append(name_text)
        else:
            label_parts.append(entity_name)
        label_parts.extend(attr_lines)
        label = "\n".join(label_parts)

        # 좌상단 좌표 (cx, cy는 중심)
        x_tl = (cx - w_px / 2) * scale
        y_tl = (cy - h_px / 2) * scale

        nodes[entity_name] = LaidNode(
            id=entity_name,
            label=label,
            x=x_tl,
            y=y_tl,
            w=w_px * scale,
            h=h_px * scale,
            shape="rect",
        )
    return nodes


def _parse_er_edges(
    root: etree._Element,
    scale: float,
    min_seg_in: float,
) -> list[LaidEdge]:
    """ER 관계(relationshipLine) 파싱.

    cardinality는 marker-start/marker-end 의 url id에서 추출해
    label 앞에 'src..tgt ' 형태로 부착.
    edge label은 flowchart와 동일한 edgeLabel g 구조를 재사용.
    """
    edges: list[LaidEdge] = []

    # edgeLabel data-id → (cx, cy) / 텍스트 매핑 (flowchart 와 동일 구조)
    label_centers: dict[str, tuple[float, float]] = {}
    label_texts: dict[str, str] = {}
    for elg in root.iter(f"{_SVG}g"):
        if (elg.get("class") or "") != "edgeLabel":
            continue
        cx, cy = _parse_translate(elg.get("transform") or "")
        inner = elg.find(f"{_SVG}g")
        data_id = inner.get("data-id") if inner is not None else None
        if not data_id:
            continue
        label_centers[data_id] = (cx * scale, cy * scale)
        fo = elg.find(f".//{_SVG}foreignObject")
        if fo is not None:
            label_texts[data_id] = _extract_text_from_foreign(fo)

    for path in root.iter(f"{_SVG}path"):
        cls = path.get("class") or ""
        if "relationshipLine" not in cls:
            continue
        svg_id = path.get("id") or ""
        m = _ER_EDGE_ID_RE.match(svg_id)
        if not m:
            continue
        src, dst = m.group(1), m.group(2)

        edge_data_id = path.get("data-id") or svg_id

        # 좌표 시퀀스
        b64 = path.get("data-points") or ""
        pts_px = _decode_data_points(b64)
        if not pts_px:
            pts_px = _parse_path_d_fallback(path.get("d") or "")
        if not pts_px:
            continue

        pts_in = [(x * scale, y * scale) for x, y in pts_px]
        pts_in = _simplify_polyline(pts_in, min_seg_in)

        # cardinality 토큰
        src_card = _marker_url_to_cardinality(path.get("marker-start") or "")
        tgt_card = _marker_url_to_cardinality(path.get("marker-end") or "")
        body_text = label_texts.get(edge_data_id, "")

        # label 조립: 'src_card..tgt_card body' (양쪽 모두 있을 때) 또는 부분만
        card_token = ""
        if src_card or tgt_card:
            card_token = f"{src_card}..{tgt_card}".strip(".")
        label_parts = [p for p in (card_token, body_text) if p]
        label = " ".join(label_parts)

        label_pos = label_centers.get(edge_data_id)
        dashed = ("edge-pattern-dashed" in cls) or ("edge-pattern-dotted" in cls)

        edges.append(
            LaidEdge(
                source=src,
                target=dst,
                label=label,
                points=pts_in,
                label_pos=label_pos,
                dashed=dashed,
            )
        )
    return edges


def _assign_clusters_to_nodes(
    nodes: dict[str, LaidNode],
    clusters: list[LaidCluster],
) -> None:
    """노드 중심이 어느 클러스터 안에 있는지로 cluster_id를 할당.
    여러 클러스터에 포함되면 면적이 가장 작은(가장 안쪽) 클러스터를 선택."""
    if not clusters:
        return
    for node in nodes.values():
        cx = node.x + node.w / 2
        cy = node.y + node.h / 2
        best: Optional[LaidCluster] = None
        best_area = float("inf")
        for cl in clusters:
            if cl.x <= cx <= cl.x + cl.w and cl.y <= cy <= cl.y + cl.h:
                area = cl.w * cl.h
                if area < best_area:
                    best_area = area
                    best = cl
        if best is not None:
            node.cluster_id = best.id


# ──────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────

def compute_layout_via_mmdc(
    mermaid_code: str,
    target_w_in: float = 12.5,
    target_h_in: float = 6.3,
    puppeteer_config: Optional[str] = None,
    timeout: int = 30,
    min_segment_in: float = 0.05,
) -> Optional[LayoutResult]:
    """Mermaid 코드를 mmdc로 SVG 렌더링하고 좌표를 추출하여 LayoutResult를 반환.

    Args:
        mermaid_code: Mermaid 다이어그램 코드 (flowchart/graph 전용).
        target_w_in: 결과를 맞출 최대 폭 (inches).
        target_h_in: 결과를 맞출 최대 높이 (inches).
        puppeteer_config: puppeteer-config.json 경로 (Docker 환경 --no-sandbox용).
        timeout: mmdc 호출 타임아웃 (초).
        min_segment_in: polyline 단순화 최소 세그먼트 길이 (inches).

    Returns:
        성공 시 LayoutResult, 실패 시 None.
    """
    if not mermaid_code or not mermaid_code.strip():
        return None

    # sequenceDiagram 등 비 flowchart/ER는 다른 SVG 구조 → 새 엔진 미적용
    first_line = mermaid_code.strip().split("\n", 1)[0].strip().lower()
    compact = first_line.replace(" ", "")
    if not (
        compact.startswith("flowchart")
        or compact.startswith("graph")
        or compact.startswith("erdiagram")
    ):
        return None

    if not _check_mmdc():
        logger.info("mmdc 미설치 → layout_engine 비활성")
        return None

    try:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            svg_path = _run_mmdc_to_svg(
                mermaid_code, workdir, puppeteer_config, timeout=timeout
            )
            if svg_path is None:
                return None

            try:
                tree = etree.parse(str(svg_path))
            except etree.XMLSyntaxError as exc:
                logger.warning("SVG 파싱 실패: %s", exc)
                return None

            root = tree.getroot()
            svg_w_px, svg_h_px = _parse_viewbox(root)
            if svg_w_px <= 0 or svg_h_px <= 0:
                return None

            # px → inches 스케일: 타겟 박스 안에 맞춤 (종횡비 유지)
            scale_w = target_w_in / svg_w_px
            scale_h = target_h_in / svg_h_px
            scale = min(scale_w, scale_h)

            # SVG root id (예: 'my-svg') → 자식 id 접두사는 'my-svg-'
            root_id = root.get("id") or ""
            root_prefix = f"{root_id}-" if root_id else ""

            # ER 다이어그램 분기: 전용 파서 호출 (cluster 개념 없음)
            if _is_er_svg(root, mermaid_code):
                nodes_map = _parse_er_nodes(root, scale)
                edges = _parse_er_edges(root, scale, min_seg_in=min_segment_in)
                if not nodes_map:
                    logger.warning("ER SVG에서 엔티티를 찾지 못함")
                    return None
                return LayoutResult(
                    nodes=nodes_map,
                    clusters=[],
                    edges=edges,
                    canvas_w=svg_w_px * scale,
                    canvas_h=svg_h_px * scale,
                    scale=scale,
                )

            # flowchart / graph: 기존 경로
            clusters_map = _parse_clusters(root, scale, root_prefix=root_prefix)
            nodes_map = _parse_nodes(root, scale)
            edges = _parse_edges(
                root, scale, min_seg_in=min_segment_in, root_prefix=root_prefix
            )

            if not nodes_map:
                logger.warning("SVG에서 노드를 찾지 못함")
                return None

            clusters_list = list(clusters_map.values())
            _assign_clusters_to_nodes(nodes_map, clusters_list)

            return LayoutResult(
                nodes=nodes_map,
                clusters=clusters_list,
                edges=edges,
                canvas_w=svg_w_px * scale,
                canvas_h=svg_h_px * scale,
                scale=scale,
            )
    except Exception as exc:
        logger.warning("layout_engine 예외: %s", exc, exc_info=True)
        return None
