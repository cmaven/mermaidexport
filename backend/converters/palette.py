# ============================================================
# palette.py: 모든 변환기가 공유하는 통합 색상 팔레트
# 상세: PNG, PPTX, draw.io, Excalidraw 모두 동일한 색상 사용
# 생성일: 2026-04-07 | 수정일: 2026-04-07
# ============================================================

# 노드 색상 팔레트: (fill_hex, stroke_hex)
# 기존 generate_pptx_shapes.py / generate_drawio.py와 동일
NODE_COLORS = [
    ("#dbeafe", "#1e40af"),   # blue
    ("#d1fae5", "#047857"),   # green
    ("#ddd6fe", "#6d28d9"),   # purple
    ("#fed7aa", "#c2410c"),   # orange
    ("#fee2e2", "#dc2626"),   # red
    ("#fef3c7", "#b45309"),   # yellow
    ("#f0fdf4", "#166534"),   # light-green
    ("#ede9fe", "#6d28d9"),   # light-purple
]

# 서브그래프 색상: (fill_hex, stroke_hex)
SUBGRAPH_COLORS = [
    ("#f5f3ff", "#7c3aed"),   # purple-tint
    ("#f0fdf4", "#16a34a"),   # green-tint
    ("#eff6ff", "#3b82f6"),   # blue-tint
    ("#fff7ed", "#ea580c"),   # orange-tint
    ("#fffbeb", "#d97706"),   # yellow-tint
    ("#fafaf9", "#94a3b8"),   # grey-tint
]

# 텍스트/라인 공통 색상
TEXT_COLOR = "#1e293b"
LINE_COLOR = "#475569"
EDGE_LABEL_BG = "#ffffff"
SUBGRAPH_BORDER_FALLBACK = "#94a3b8"


def get_node_color(index: int) -> tuple[str, str]:
    """인덱스에 맞는 노드 색상 (fill, stroke) 반환."""
    return NODE_COLORS[index % len(NODE_COLORS)]


def get_subgraph_color(index: int) -> tuple[str, str]:
    """인덱스에 맞는 서브그래프 색상 (fill, stroke) 반환."""
    return SUBGRAPH_COLORS[index % len(SUBGRAPH_COLORS)]
