# ============================================================
# palette.py: 모든 변환기가 공유하는 통합 색상 팔레트
# 상세: PNG, PPTX, draw.io, Excalidraw 모두 동일한 색상 사용
#       트랙 P: 어두운 classDef fill 자동 라이트 톤 치환 헬퍼 포함
# 생성일: 2026-04-07 | 수정일: 2026-05-21
# ============================================================

# 노드 색상 팔레트: (fill_hex, stroke_hex)
# diagram_1~8 GPT 스타일 참조 톤: Tailwind 100 fill + 700/800 stroke
NODE_COLORS = [
    ("#dbeafe", "#1e40af"),   # blue       (blue-100 + blue-800)
    ("#d1fae5", "#047857"),   # green      (emerald-100 + emerald-700)
    ("#ddd6fe", "#6d28d9"),   # purple     (violet-100 + violet-700)
    ("#fed7aa", "#c2410c"),   # orange     (orange-100 + orange-700)
    ("#fee2e2", "#dc2626"),   # red        (red-100 + red-600)
    ("#fef3c7", "#b45309"),   # yellow     (amber-100 + amber-700)
    ("#cffafe", "#0e7490"),   # cyan       (cyan-100 + cyan-700)
    ("#fae8ff", "#a21caf"),   # fuchsia    (fuchsia-100 + fuchsia-700)
]

# 서브그래프 색상: (fill_hex, stroke_hex)
# diagram_1~8 참조: Tailwind 50 fill + 500/600 stroke (옅은 파스텔 클러스터 배경)
SUBGRAPH_COLORS = [
    ("#f5f3ff", "#7c3aed"),   # purple-tint (violet-50 + violet-600)
    ("#f0fdf4", "#16a34a"),   # green-tint  (green-50 + green-600)
    ("#eff6ff", "#3b82f6"),   # blue-tint   (blue-50 + blue-500)
    ("#fff7ed", "#ea580c"),   # orange-tint (orange-50 + orange-600)
    ("#fffbeb", "#d97706"),   # yellow-tint (amber-50 + amber-600)
    ("#ecfeff", "#06b6d4"),   # cyan-tint   (cyan-50 + cyan-500)
]

# 텍스트/라인 공통 색상
TEXT_COLOR = "#1e293b"
LINE_COLOR = "#475569"
EDGE_LABEL_BG = "#ffffff"
SUBGRAPH_BORDER_FALLBACK = "#94a3b8"


def _hex_luminance(hex_str: str) -> float:
    """WCAG 2.1 상대 휘도(Relative Luminance) 계산. 0=검정, 1=흰색."""
    h = hex_str.lstrip('#')
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    r, g, b = int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0

    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def is_color_too_dark(hex_str: str, threshold: float = 0.35) -> bool:
    """fill 색상의 상대 휘도가 threshold 미만이면 True (배경으로 사용 부적합).

    Args:
        hex_str: '#rrggbb' 형식 hex 색상 문자열.
        threshold: 0.35 기본값 — 이 미만이면 어둡다고 판정.

    Returns:
        어두운 색이면 True.
    """
    try:
        return _hex_luminance(hex_str.strip()) < threshold
    except Exception:
        return False


def lighten_dark_fill(hex_str: str) -> tuple[str, str]:
    """어두운 fill → 파스텔 밝은 버전 + 어두운 텍스트색 반환.

    같은 Hue(색조)를 유지하되 Saturation을 줄이고 Value를 높여
    WCAG 가독성 기준을 만족하는 파스텔 톤을 생성한다.

    Args:
        hex_str: '#rrggbb' 형식의 어두운 fill 색상.

    Returns:
        (light_fill_hex, dark_text_hex) 튜플.
        dark_text_hex는 항상 '#1e293b' (slate-900).
    """
    import colorsys
    h = hex_str.lstrip('#')
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    r, g, b = int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0
    hue, sat, _val = colorsys.rgb_to_hsv(r, g, b)
    # 파스텔 톤: 채도 절반 이하 (max 0.22), 명도 0.93
    lr, lg, lb = colorsys.hsv_to_rgb(hue, min(sat * 0.5, 0.22), 0.93)
    fill_hex = '#{:02x}{:02x}{:02x}'.format(
        round(lr * 255), round(lg * 255), round(lb * 255)
    )
    return fill_hex, TEXT_COLOR  # '#1e293b' slate-900


def get_node_color(index: int) -> tuple[str, str]:
    """인덱스에 맞는 노드 색상 (fill, stroke) 반환."""
    return NODE_COLORS[index % len(NODE_COLORS)]


def get_subgraph_color(index: int) -> tuple[str, str]:
    """인덱스에 맞는 서브그래프 색상 (fill, stroke) 반환."""
    return SUBGRAPH_COLORS[index % len(SUBGRAPH_COLORS)]


def first_diagram_directive(mermaid_code: str) -> str:
    """`%%` 주석/빈 줄을 건너뛰고 첫 다이어그램 지시문을 반환.

    반환: lowercase + 공백 제거된 첫 비-주석 라인 (예: "erdiagram", "sequencediagram", "graphtb").
    type 판정용. 사용자가 `%% source: ...` 같은 주석을 다이어그램 위에 두는 경우 대응.
    """
    for line in mermaid_code.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("%%"):
            continue
        return stripped.lower().replace(" ", "")
    return ""
