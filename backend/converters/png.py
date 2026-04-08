# ============================================================
# png.py: Mermaid 코드를 PNG 이미지로 변환하는 모듈
# 상세: mmdc CLI(mermaid-cli)를 사용하여 .mmd 파일을 PNG로 렌더링.
#       NanumSquare 폰트, base 테마, 파란색/슬레이트 색상 스키마 적용.
# 생성일: 2026-04-07 | 수정일: 2026-04-07
# ============================================================

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


# Mermaid 테마 설정 (generate_diagrams.py 스타일과 동일)
_MERMAID_CONFIG = {
    "theme": "base",
    "themeVariables": {
        "fontFamily": "NanumSquare, sans-serif",
        "fontSize": "19px",
        "primaryColor": "#dbeafe",
        "primaryBorderColor": "#3b82f6",
        "primaryTextColor": "#1e293b",
        "lineColor": "#475569",
        "clusterBkg": "#fafafa",
        "clusterBorder": "#94a3b8",
        "edgeLabelBackground": "#ffffff",
        "nodeTextColor": "#1e293b",
        "arrowheadColor": "#475569",
        # 시퀀스 다이어그램 테마
        "actorBkg": "#dbeafe",
        "actorBorder": "#3b82f6",
        "actorTextColor": "#1e293b",
        "signalColor": "#475569",
        "signalTextColor": "#1e293b",
        "noteBkgColor": "#fef3c7",
        "noteBorderColor": "#d97706",
        "noteTextColor": "#1e293b",
        "activationBkgColor": "#ede9fe",
        "activationBorderColor": "#6d28d9",
        "labelBoxBkgColor": "#f0fdf4",
        "labelBoxBorderColor": "#16a34a",
    },
    "flowchart": {
        "padding": 70,
        "htmlLabels": True,
        "useMaxWidth": False,
    },
    "sequence": {
        "useMaxWidth": False,
    },
}


from converters.palette import NODE_COLORS as _COLOR_PALETTE
from converters.palette import SUBGRAPH_COLORS as _SUBGRAPH_COLORS


# mmdc --cssFile로 주입할 공통 CSS (NanumSquare 폰트, 노드 둥글기, 엣지 레이블 스타일)
_COMMON_CSS = (
    'text,.nodeLabel,span{font-family:"NanumSquare",sans-serif !important;font-size:19px !important}\n'
    ".node rect{rx:8 !important;ry:8 !important}\n"
    ".cluster rect{rx:14 !important;ry:14 !important}\n"
    ".edgeLabel rect{fill:none !important;opacity:0 !important;stroke:none !important}\n"
    ".edgeLabel span{font-size:17px !important;color:#475569 !important;background-color:transparent !important}\n"
    ".edgeLabel .labelBkg{background-color:transparent !important}\n"
    ".edgeLabel p{background-color:transparent !important}\n"
    ".edgeLabel{background-color:transparent !important}\n"
    "foreignObject{overflow:visible !important}\n"
    ".cluster-label text{font-size:19px !important}\n"
    ".actor{rx:8 !important;ry:8 !important}\n"
    '.messageText{font-family:"NanumSquare",sans-serif !important;font-size:16px !important}\n'
)


def _inject_styles(mermaid_code: str) -> str:
    """Mermaid 코드에 노드별/서브그래프별 색상 style 지시문을 자동 주입한다."""
    import re

    lines = mermaid_code.strip().split("\n")

    # 이미 style 지시문이 있으면 사용자 정의 우선 → 주입하지 않음
    if any(line.strip().startswith("style ") for line in lines):
        return mermaid_code

    # sequence diagram은 style 주입하지 않음
    first_line = lines[0].strip().lower()
    if "sequencediagram" in first_line.replace(" ", ""):
        return mermaid_code

    # 노드 ID 추출
    node_ids = []
    node_re = re.compile(r'^\s+([A-Za-z_][A-Za-z0-9_]*)\s*[\[({]')
    for line in lines:
        m = node_re.match(line)
        if m:
            nid = m.group(1)
            if nid not in ("subgraph", "end", "graph", "flowchart", "style", "classDef"):
                if nid not in node_ids:
                    node_ids.append(nid)

    # 서브그래프 ID 추출
    sg_ids = []
    sg_re = re.compile(r'^\s*subgraph\s+([A-Za-z_][A-Za-z0-9_]*)')
    for line in lines:
        m = sg_re.match(line)
        if m:
            sg_ids.append(m.group(1))

    if not node_ids and not sg_ids:
        return mermaid_code

    # style 지시문 생성
    style_lines = []
    for i, nid in enumerate(node_ids):
        fill, stroke = _COLOR_PALETTE[i % len(_COLOR_PALETTE)]
        style_lines.append(
            f"    style {nid} fill:{fill},stroke:{stroke},stroke-width:1px,rx:10"
        )

    # 서브그래프 CSS (frame_css 스타일)
    css_parts = []
    for i, sg_id in enumerate(sg_ids):
        fill, stroke = _SUBGRAPH_COLORS[i % len(_SUBGRAPH_COLORS)]
        css_parts.append(
            f'    [id*="{sg_id}"]>rect{{fill:{fill} !important;stroke:{stroke} !important}}'
        )

    result = mermaid_code.rstrip()
    if style_lines:
        result += "\n" + "\n".join(style_lines)

    return result


def check_mmdc_available() -> bool:
    """mmdc CLI가 PATH에 설치되어 있는지 확인한다."""
    return shutil.which("mmdc") is not None


def mermaid_to_png(mermaid_code: str, title: str = "") -> bytes:
    """Mermaid 코드를 PNG 바이트로 변환한다.

    PPTX 도형을 먼저 생성한 후 LibreOffice로 PNG 변환하여
    PPTX와 동일한 시각적 결과를 보장한다.
    LibreOffice가 없으면 mmdc 폴백.

    Args:
        mermaid_code: 변환할 Mermaid 다이어그램 코드 문자열.
        title: 다이어그램 제목 (선택).

    Returns:
        렌더링된 PNG 이미지의 바이트 데이터.
    """
    # 1차: PPTX → LibreOffice → PNG (PPTX와 동일 결과)
    if shutil.which("libreoffice") is not None:
        try:
            return _png_via_pptx(mermaid_code, title)
        except Exception:
            pass  # LibreOffice 실패 시 mmdc 폴백

    # 2차 폴백: mmdc 직접 PNG 생성
    return _png_via_mmdc(mermaid_code)


def _png_via_pptx(mermaid_code: str, title: str = "") -> bytes:
    """PPTX를 생성하고 LibreOffice로 PNG 변환한다."""
    from converters.pptx_shapes import mermaid_to_pptx

    pptx_bytes = mermaid_to_pptx(mermaid_code, title)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        pptx_path = tmp_path / "slide.pptx"
        pptx_path.write_bytes(pptx_bytes)

        result = subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "png", str(pptx_path)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(tmp_path),
        )

        png_path = tmp_path / "slide.png"
        if result.returncode != 0 or not png_path.exists():
            raise RuntimeError(f"LibreOffice 변환 실패: {result.stderr}")

        return png_path.read_bytes()


def _png_via_mmdc(mermaid_code: str) -> bytes:
    """mmdc CLI로 PNG를 직접 생성한다 (폴백)."""
    if not check_mmdc_available():
        raise RuntimeError(
            "mmdc(mermaid-cli)가 설치되어 있지 않습니다. "
            "설치 방법: npm install -g @mermaid-js/mermaid-cli"
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        input_path = tmp_path / "input.mmd"
        output_path = tmp_path / "output.png"
        config_path = tmp_path / "config.json"

        styled_code = _inject_styles(mermaid_code)
        input_path.write_text(styled_code, encoding="utf-8")

        config_path.write_text(
            json.dumps(_MERMAID_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        css_path = tmp_path / "style.css"
        css_path.write_text(_COMMON_CSS, encoding="utf-8")

        cmd = [
            "mmdc",
            "-i", str(input_path),
            "-o", str(output_path),
            "-b", "transparent",
            "-w", "1920",
            "-s", "2",
            "-c", str(config_path),
            "--cssFile", str(css_path),
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("mmdc 실행 시간이 초과되었습니다 (60초).")

        if result.returncode != 0:
            raise RuntimeError(f"mmdc 렌더링 실패: {result.stderr.strip()}")

        if not output_path.exists():
            raise RuntimeError("출력 PNG 파일이 생성되지 않았습니다.")

        return output_path.read_bytes()
