# ============================================================
# layout_graph.py: Graphviz dot 기반 공유 레이아웃 엔진
# 상세: 노드/엣지/클러스터를 DOT로 변환 → `dot -Tjson` 호출 →
#       좌표(좌상단 px)·클러스터 bbox·엣지 경로점을 추출해 LayoutResult 반환.
#       flowchart/erDiagram 의 박스 겹침·엣지 교차를 줄이기 위해 사용.
#       dot 미설치·실패 시 None 반환 → 호출부에서 기존 grid 레이아웃 폴백.
# 생성일: 2026-06-05
# ============================================================

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional

from converters.text_metrics import estimate_text_size_px


# ──────────────────────────────────────────────
# 입력/출력 데이터 모델 (중립 IR)
# ──────────────────────────────────────────────

@dataclass
class LNode:
    id: str
    label: str
    shape: str = "rect"               # rect | round | diamond | circle
    w_in: Optional[float] = None      # 명시 크기(inch) — ER 테이블 등. None이면 라벨로 추정
    h_in: Optional[float] = None


@dataclass
class LEdge:
    source: str
    target: str
    label: str = ""


@dataclass
class LCluster:
    id: str
    label: str
    node_ids: list[str] = field(default_factory=list)


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float


@dataclass
class EdgePath:
    source: str
    target: str
    label: str
    points: list[tuple[float, float]] = field(default_factory=list)
    label_pos: Optional[tuple[float, float]] = None


@dataclass
class LayoutResult:
    nodes: dict[str, Box] = field(default_factory=dict)
    clusters: dict[str, Box] = field(default_factory=dict)
    cluster_labels: dict[str, str] = field(default_factory=dict)
    edges: list[EdgePath] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0


# ──────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────

_PT_PER_INCH = 72.0
_PX_SCALE = 96.0 / 72.0           # point → CSS px(96dpi)
_DOT_SHAPE = {
    "rect": "box", "round": "box", "diamond": "diamond", "circle": "ellipse",
}


# ──────────────────────────────────────────────
# DOT 생성
# ──────────────────────────────────────────────

def _esc(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.IGNORECASE)
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    return text.replace("\n", "\\n")


def _node_size_in(node: LNode) -> tuple[float, float]:
    if node.w_in is not None and node.h_in is not None:
        return max(0.4, node.w_in), max(0.3, node.h_in)
    w_px, h_px = estimate_text_size_px(node.label, font_size_px=14)
    w_in = max(1.4, w_px / 96.0)
    h_in = max(0.55, h_px / 96.0)
    if node.shape == "diamond":
        w_in, h_in = w_in * 1.2, h_in * 1.3
    return w_in, h_in


def build_dot(nodes, edges, clusters, direction="TB", splines="ortho") -> str:
    rankdir = "LR" if direction.upper() == "LR" else "TB"
    in_cluster: set[str] = set()
    lines = [
        "digraph G {",
        f"  rankdir={rankdir};",
        f"  splines={splines};",
        "  nodesep=0.7;",
        "  ranksep=0.95;",
        "  pad=0.3;",
        "  node [fixedsize=true, fontsize=14];",
        "  edge [fontsize=11];",
    ]
    node_map = {n.id: n for n in nodes}

    for ci, cl in enumerate(clusters):
        lines.append(f"  subgraph cluster_{ci} {{")
        lines.append(f'    label="{_esc(cl.label)}";')
        for nid in cl.node_ids:
            n = node_map.get(nid)
            if n is None:
                continue
            in_cluster.add(nid)
            w, h = _node_size_in(n)
            lines.append(
                f'    "{nid}" [label="{_esc(n.label)}", '
                f'shape={_DOT_SHAPE.get(n.shape, "box")}, width={w:.3f}, height={h:.3f}];'
            )
        lines.append("  }")

    for n in nodes:
        if n.id in in_cluster:
            continue
        w, h = _node_size_in(n)
        lines.append(
            f'  "{n.id}" [label="{_esc(n.label)}", '
            f'shape={_DOT_SHAPE.get(n.shape, "box")}, width={w:.3f}, height={h:.3f}];'
        )

    for e in edges:
        attr = f' [label="{_esc(e.label)}"]' if e.label else ""
        lines.append(f'  "{e.source}" -> "{e.target}"{attr};')

    lines.append("}")
    return "\n".join(lines)


# ──────────────────────────────────────────────
# dot 실행 + JSON 파싱
# ──────────────────────────────────────────────

def is_available() -> bool:
    return shutil.which("dot") is not None


def _run_dot(dot_src: str) -> Optional[dict]:
    if not is_available():
        return None
    try:
        proc = subprocess.run(
            ["dot", "-Tjson"], input=dot_src.encode("utf-8"),
            capture_output=True, timeout=20,
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        return json.loads(proc.stdout.decode("utf-8"))
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def _parse_bb(bb: str) -> tuple[float, float, float, float]:
    p = [float(v) for v in bb.split(",")]
    return p[0], p[1], p[2], p[3]


def _collect_points(draw_ops) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for op in draw_ops or []:
        if isinstance(op, dict) and "points" in op:
            for p in op["points"]:
                pts.append((float(p[0]), float(p[1])))
    return pts


def _ldraw_pos(ldraw_ops) -> Optional[tuple[float, float]]:
    for op in ldraw_ops or []:
        if isinstance(op, dict) and op.get("op") in ("t", "T") and "pt" in op:
            return float(op["pt"][0]), float(op["pt"][1])
    return None


def _layout_once(nodes, edges, clusters, direction="TB",
                 scale=_PX_SCALE) -> Optional[LayoutResult]:
    """주어진 방향으로 dot 레이아웃을 1회 계산한다.

    ortho → polyline → spline 순으로 splines를 낮춰 재시도. 모두 실패 시 None.
    """
    if not nodes:
        return None

    data = None
    for spl in ("ortho", "polyline", "spline"):
        data = _run_dot(build_dot(nodes, edges, clusters, direction, splines=spl))
        if data is not None:
            break
    if data is None:
        return None

    try:
        _, _, bb_x1, bb_y1 = _parse_bb(data["bb"])
    except (KeyError, ValueError, IndexError):
        return None

    page_h = bb_y1

    def _flip_y(gv_y: float) -> float:
        return (page_h - gv_y) * scale

    result = LayoutResult(width=bb_x1 * scale, height=bb_y1 * scale)
    gvid_to_name: dict[int, str] = {}

    for obj in data.get("objects", []):
        name = obj.get("name", "")
        gvid = obj.get("_gvid")
        if gvid is not None:
            gvid_to_name[gvid] = name

        if name.startswith("cluster_") and "bb" in obj:
            cx0, cy0, cx1, cy1 = _parse_bb(obj["bb"])
            try:
                ci = int(name.split("_", 1)[1])
                orig = clusters[ci]
            except (ValueError, IndexError):
                continue
            result.clusters[orig.id] = Box(
                x=cx0 * scale, y=_flip_y(cy1),
                w=(cx1 - cx0) * scale, h=(cy1 - cy0) * scale,
            )
            result.cluster_labels[orig.id] = orig.label
            continue

        if "pos" in obj and obj.get("width") and obj.get("height"):
            px_str, py_str = obj["pos"].split(",")
            gx, gy = float(px_str), float(py_str)
            w_pt = float(obj["width"]) * _PT_PER_INCH
            h_pt = float(obj["height"]) * _PT_PER_INCH
            result.nodes[name] = Box(
                x=(gx - w_pt / 2.0) * scale,
                y=_flip_y(gy) - (h_pt / 2.0) * scale,
                w=w_pt * scale, h=h_pt * scale,
            )

    for e in data.get("edges", []):
        tail = gvid_to_name.get(e.get("tail"), "")
        head = gvid_to_name.get(e.get("head"), "")
        raw = _collect_points(e.get("_draw_"))
        pts = [(x * scale, _flip_y(y)) for (x, y) in raw]
        lp = _ldraw_pos(e.get("_ldraw_"))
        lpos = (lp[0] * scale, _flip_y(lp[1])) if lp else None
        result.edges.append(EdgePath(source=tail, target=head,
                                     label=e.get("label", ""),
                                     points=pts, label_pos=lpos))
    return result


# ──────────────────────────────────────────────
# 공개 래퍼: 종횡비 기반 방향(TB↔LR) 자동 선택
# ──────────────────────────────────────────────

def compute_graphviz_layout(nodes, edges, clusters, direction="TB",
                            scale=_PX_SCALE, target_aspect=None):
    base = _layout_once(nodes, edges, clusters, direction, scale)
    if base is None or not target_aspect or target_aspect <= 0:
        return base
    flipped = "LR" if direction.upper() != "LR" else "TB"
    alt = _layout_once(nodes, edges, clusters, flipped, scale)
    def _fit(r):                      # 목표 박스(폭=target_aspect, 높이=1) 기준 상대 배율
        cw, ch = max(r.width, 1.0), max(r.height, 1.0)
        return min(target_aspect / cw, 1.0 / ch)
    cands = [base] + ([alt] if (alt and alt.nodes) else [])
    return max(cands, key=_fit)       # 블록이 가장 커지는 방향 선택
