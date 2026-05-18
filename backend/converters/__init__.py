# ============================================================
# __init__.py: 변환 엔진 패키지
# 상세: 변환 파이프라인(PNG/draw.io/PPTX/Excalidraw) 진입점 모듈.
#       smoke_test_er() 는 pytest 미사용 환경에서 ER 변환을 직접 호출해
#       각 포맷의 OK/FAIL 결과 dict를 반환한다.
# 생성일: 2026-04-07 | 수정일: 2026-05-18
# ============================================================


def smoke_test_er() -> dict:
    """ER 다이어그램이 모든 출력 포맷으로 변환되는지 확인하는 간이 smoke test.

    Returns:
        {"png": "OK (123 bytes)", "drawio": "OK (456 chars)", ...}
        실패 시 해당 키 값에 "FAIL: <에러>" 가 들어간다.
    """
    from .png import mermaid_to_png
    from .drawio import mermaid_to_drawio
    from .pptx_shapes import mermaid_to_pptx

    code = (
        "erDiagram\n"
        "    A ||--o{ B : has\n"
        "    A { string name }\n"
        "    B { int count }\n"
    )

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
            results[fmt] = f"OK ({length} {unit})"
        except Exception as exc:  # noqa: BLE001 — smoke test는 모든 예외 표시
            results[fmt] = f"FAIL: {type(exc).__name__}: {exc}"

    # excalidraw 는 dict 반환 → 별도 측정
    try:
        from .excalidraw import mermaid_to_excalidraw

        exc_out = mermaid_to_excalidraw(code)
        if isinstance(exc_out, dict):
            results["excalidraw"] = f"OK ({len(exc_out.get('elements', []))} elements)"
        else:
            results["excalidraw"] = "OK"
    except Exception as exc:  # noqa: BLE001
        results["excalidraw"] = f"FAIL: {type(exc).__name__}: {exc}"

    return results
