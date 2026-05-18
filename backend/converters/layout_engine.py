# ============================================================
# layout_engine.py: mmdc(Mermaid CLI) 기반 SVG 좌표 추출 레이아웃 엔진
# 상세: Mermaid 코드를 mmdc로 SVG 렌더링한 뒤 노드/클러스터/엣지의
#       절대 좌표를 파싱하여 inches 단위 LayoutResult를 반환.
#       PPTX/Draw.io 변환기가 이 결과를 받아 dagre 품질의 레이아웃을
#       그대로 재현한다. 실패 시 None 반환 → 호출자가 기존 그리드 폴백.
# 생성일: 2026-05-18 | 수정일: 2026-05-18
# ============================================================

from __future__ import annotations

import base64
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
    """foreignObject 안의 모든 텍스트 노드를 모아 한 줄로 반환."""
    texts: list[str] = []
    for t in elem.iter():
        if t.text:
            s = t.text.strip()
            if s:
                texts.append(s)
    return " ".join(texts)


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

    # sequenceDiagram 등 비 flowchart는 다른 SVG 구조 → 새 엔진 미적용
    first_line = mermaid_code.strip().split("\n", 1)[0].strip().lower()
    compact = first_line.replace(" ", "")
    if not (compact.startswith("flowchart") or compact.startswith("graph")):
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
