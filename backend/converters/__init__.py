# ============================================================
# __init__.py: 변환 엔진 패키지
# 상세: 변환 파이프라인(PNG/draw.io/PPTX/Excalidraw) 진입점 모듈.
#       smoke_test_er() 는 pytest 미사용 환경에서 ER 변환을 직접 호출해
#       각 포맷의 OK/FAIL 결과 dict를 반환한다.
# 생성일: 2026-04-07 | 수정일: 2026-05-19
# ============================================================

# test_er.md 1번 블록 (CRD 관계도) — 실전 케이스, 8 엔티티 포함
_ER_CODE_CRD = (
    "erDiagram\n"
    '    NPUClusterPolicy ||--o{ DaemonSet : "creates (npu.ai/owner annotation)"\n'
    '    NPUClusterPolicy ||--o{ ConfigMap : "creates (npu.ai/owner annotation)"\n'
    "    NPUClusterPolicy {\n"
    "        string phase\n"
    "        array conditions\n"
    "        object detector\n"
    "        object nvidia\n"
    "        object furiosa\n"
    "    }\n"
    '    NodeDeviceReport ||--|| Node : "1:1 per node"\n'
    "    NodeDeviceReport {\n"
    "        string nodeName\n"
    "        array devices\n"
    "        array conditions\n"
    "    }\n"
    '    DriverInstallPolicy ||--o{ Job : "triggers (mode=job)"\n'
    "    DriverInstallPolicy {\n"
    "        string vendor\n"
    "        string model\n"
    "        object driver_DriverSpec\n"
    "    }\n"
    "    NodeDeviceReport ||--|{ DeviceEntry : contains\n"
)

# 단순 ER — 기본 동작 확인용
_ER_CODE_SIMPLE = (
    "erDiagram\n"
    "    A ||--o{ B : has\n"
    "    A { string name }\n"
    "    B { int count }\n"
)


def _run_pipeline(code: str, label: str) -> dict[str, str]:
    """주어진 ER 코드를 4개 포맷으로 변환해 {fmt: 상태문자열} 반환."""
    from .png import mermaid_to_png
    from .drawio import mermaid_to_drawio
    from .pptx_shapes import mermaid_to_pptx

    results: dict[str, str] = {}
    pipeline = (
        ("png", mermaid_to_png),
        ("drawio", mermaid_to_drawio),
        ("pptx", mermaid_to_pptx),
    )
    for fmt, fn in pipeline:
        try:
            out = fn(code)
            length = len(out) if hasattr(out, "__len__") else "n/a"
            unit = "chars" if isinstance(out, str) else "bytes"
            results[f"{label}/{fmt}"] = f"OK ({length} {unit})"
        except Exception as exc:  # noqa: BLE001
            results[f"{label}/{fmt}"] = f"FAIL: {type(exc).__name__}: {exc}"

    try:
        from .excalidraw import mermaid_to_excalidraw
        exc_out = mermaid_to_excalidraw(code)
        if isinstance(exc_out, dict):
            results[f"{label}/excalidraw"] = f"OK ({len(exc_out.get('elements', []))} elements)"
        else:
            results[f"{label}/excalidraw"] = "OK"
    except Exception as exc:  # noqa: BLE001
        results[f"{label}/excalidraw"] = f"FAIL: {type(exc).__name__}: {exc}"

    return results


def smoke_test_er() -> dict:
    """ER 다이어그램이 모든 출력 포맷으로 변환되는지 확인하는 간이 smoke test.

    두 케이스 모두 검사:
    - simple: 기본 2-엔티티 ER (회귀 기저선)
    - crd:    test_er.md 1번 블록 — 8엔티티 실전 CRD 관계도

    Returns:
        {"simple/png": "OK (...)", ..., "crd/pptx": "OK (...)"}
        실패 시 해당 키 값에 "FAIL: <에러>" 가 들어간다.
    """
    results: dict[str, str] = {}
    results.update(_run_pipeline(_ER_CODE_SIMPLE, "simple"))
    results.update(_run_pipeline(_ER_CODE_CRD, "crd"))

    # 빠른 확인: 단순 포맷명으로 alias (하위 호환)
    for fmt in ("png", "drawio", "pptx", "excalidraw"):
        simple_key = f"simple/{fmt}"
        if simple_key in results:
            results[fmt] = results[simple_key]

    return results
