# ============================================================
# main.py: Mermaid Web Converter FastAPI 서버
# 상세: MD 파일 업로드 → Mermaid 블록 추출 → 다중 포맷 변환 API
# 생성일: 2026-04-07 | 수정일: 2026-04-07
# ============================================================

import io
import json
import shutil
import uuid as _uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from parser import parse_mermaid_blocks
from converters.png import mermaid_to_png
from converters.drawio import mermaid_to_drawio
from converters.excalidraw import mermaid_to_excalidraw
from converters.pptx_shapes import mermaid_to_pptx
from converters.pptx_combined import create_combined_pptx

# ---------------------------------------------------------------------------
# 앱 초기화
# ---------------------------------------------------------------------------

app = FastAPI(title="Mermaid Web Converter")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS_DIR = Path("./jobs")
JOBS_DIR.mkdir(exist_ok=True)

FRONTEND_DIR = Path("../frontend")

# 포맷 → 파일 확장자 매핑
FORMAT_EXT: dict[str, str] = {
    "png": "png",
    "drawio": "drawio",
    "excalidraw": "excalidraw",
    "pptx": "pptx",
    "combined-pptx": "pptx",
}

# 포맷 → Content-Type 매핑
FORMAT_CONTENT_TYPE: dict[str, str] = {
    "png": "image/png",
    "drawio": "application/xml",
    "excalidraw": "application/json",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "combined-pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

# ---------------------------------------------------------------------------
# 헬스 체크
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /api/convert
# ---------------------------------------------------------------------------


@app.post("/api/convert")
async def convert(file: UploadFile = File(...)) -> JSONResponse:
    """MD 파일을 업로드받아 Mermaid 블록을 추출하고 다중 포맷으로 변환한다."""

    # 파일 확장자 검증
    filename = file.filename or ""
    if not filename.lower().endswith(".md"):
        raise HTTPException(status_code=400, detail="'.md' 파일만 업로드할 수 있습니다.")

    content_bytes = await file.read()
    if len(content_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="파일 크기가 10MB를 초과합니다.")
    md_text = content_bytes.decode("utf-8", errors="replace")

    # Mermaid 블록 파싱
    blocks = parse_mermaid_blocks(md_text)
    if not blocks:
        raise HTTPException(
            status_code=400,
            detail="업로드된 MD 파일에서 Mermaid 블록을 찾을 수 없습니다.",
        )

    # 작업 디렉토리 생성
    job_id = str(_uuid.uuid4())
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    diagrams = []

    for i, block in enumerate(blocks):
        mermaid_code = block.get("mermaid_code", "")
        title = block.get("title", f"diagram_{i}")

        formats: dict[str, str | None] = {}
        errors: dict[str, str] = {}

        # PNG 변환
        try:
            png_bytes = mermaid_to_png(mermaid_code, title)
            out_path = job_dir / f"diagram_{i}.png"
            out_path.write_bytes(png_bytes)
            formats["png"] = f"/api/download/{job_id}/{i}/png"
        except Exception as exc:
            formats["png"] = None
            errors["png"] = str(exc)

        # Draw.io 변환
        try:
            drawio_content = mermaid_to_drawio(mermaid_code)
            out_path = job_dir / f"diagram_{i}.drawio"
            if isinstance(drawio_content, bytes):
                out_path.write_bytes(drawio_content)
            else:
                out_path.write_text(drawio_content, encoding="utf-8")
            formats["drawio"] = f"/api/download/{job_id}/{i}/drawio"
        except Exception as exc:
            formats["drawio"] = None
            errors["drawio"] = str(exc)

        # Excalidraw 변환
        try:
            excalidraw_data = mermaid_to_excalidraw(mermaid_code)
            out_path = job_dir / f"diagram_{i}.excalidraw"
            out_path.write_text(json.dumps(excalidraw_data, ensure_ascii=False, indent=2), encoding="utf-8")
            formats["excalidraw"] = f"/api/download/{job_id}/{i}/excalidraw"
        except Exception as exc:
            formats["excalidraw"] = None
            errors["excalidraw"] = str(exc)

        # PPTX 변환
        try:
            pptx_bytes = mermaid_to_pptx(mermaid_code)
            out_path = job_dir / f"diagram_{i}.pptx"
            out_path.write_bytes(pptx_bytes)
            formats["pptx"] = f"/api/download/{job_id}/{i}/pptx"
        except Exception as exc:
            formats["pptx"] = None
            errors["pptx"] = str(exc)

        # 미리보기는 PNG 우선; PNG가 없으면 null
        preview = formats.get("png")

        diagram_entry: dict = {
            "index": i,
            "title": title,
            "formats": formats,
            "preview": preview,
        }
        if errors:
            diagram_entry["errors"] = errors

        diagrams.append(diagram_entry)

    # metadata.json 저장 (combined-pptx 엔드포인트에서 사용)
    metadata_blocks = [
        {"title": block.get("title", f"diagram_{i}"), "mermaid_code": block.get("mermaid_code", "")}
        for i, block in enumerate(blocks)
    ]
    (job_dir / "metadata.json").write_text(
        json.dumps({"blocks": metadata_blocks}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 합본 PPTX 생성 (2개 이상 다이어그램일 때)
    if len(blocks) >= 2:
        try:
            combined_bytes = create_combined_pptx(metadata_blocks)
            (job_dir / "combined.pptx").write_bytes(combined_bytes)
        except Exception:
            pass  # 합본 실패해도 다른 포맷은 정상 제공

    return JSONResponse(
        content={
            "job_id": job_id,
            "diagrams": diagrams,
        }
    )


# ---------------------------------------------------------------------------
# GET /api/download/{job_id}/combined-pptx  — 통합 PPTX 다운로드
# (반드시 /{diagram_index}/{format} 라우트보다 먼저 선언해야 올바르게 매칭됨)
# ---------------------------------------------------------------------------


@app.get("/api/download/{job_id}/combined-pptx")
async def download_combined_pptx(job_id: str) -> StreamingResponse:
    """해당 작업의 모든 Mermaid 다이어그램을 슬라이드별로 담은 단일 PPTX를 제공한다."""

    try:
        _uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="잘못된 job_id 형식입니다.")

    job_dir = (JOBS_DIR / job_id).resolve()
    if not str(job_dir).startswith(str(JOBS_DIR.resolve())):
        raise HTTPException(status_code=400, detail="잘못된 요청입니다.")

    metadata_path = job_dir / "metadata.json"
    if not metadata_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"작업 메타데이터를 찾을 수 없습니다: job_id={job_id}",
        )

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    blocks: list[dict] = metadata.get("blocks", [])
    if not blocks:
        raise HTTPException(
            status_code=404,
            detail="변환할 다이어그램 블록이 없습니다.",
        )

    try:
        pptx_bytes = create_combined_pptx(blocks)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PPTX 생성 실패: {exc}")

    return StreamingResponse(
        io.BytesIO(pptx_bytes),
        media_type=FORMAT_CONTENT_TYPE["combined-pptx"],
        headers={
            "Content-Disposition": f'attachment; filename="combined_{job_id}.pptx"'
        },
    )


# ---------------------------------------------------------------------------
# GET /api/download/{job_id}/{diagram_index}/{format}
# ---------------------------------------------------------------------------


@app.get("/api/download/{job_id}/{diagram_index}/{format}")
async def download_file(job_id: str, diagram_index: int, format: str) -> FileResponse:
    """변환된 개별 파일을 제공한다."""

    try:
        _uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="잘못된 job_id 형식입니다.")

    if format not in FORMAT_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 포맷입니다: '{format}'. 지원 포맷: {list(FORMAT_EXT)}",
        )

    ext = FORMAT_EXT[format]
    file_path = (JOBS_DIR / job_id / f"diagram_{diagram_index}.{ext}").resolve()
    if not str(file_path).startswith(str(JOBS_DIR.resolve())):
        raise HTTPException(status_code=400, detail="잘못된 요청입니다.")

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"파일을 찾을 수 없습니다: job_id={job_id}, index={diagram_index}, format={format}",
        )

    filename = f"diagram_{diagram_index}.{ext}"
    media_type = FORMAT_CONTENT_TYPE.get(format, "application/octet-stream")

    return FileResponse(
        path=file_path,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# GET /api/download/{job_id}/all  — ZIP 다운로드
# ---------------------------------------------------------------------------


@app.get("/api/download/{job_id}/all")
async def download_all(job_id: str) -> StreamingResponse:
    """해당 작업의 모든 변환 파일을 ZIP으로 묶어 제공한다."""

    try:
        _uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="잘못된 job_id 형식입니다.")

    job_dir = (JOBS_DIR / job_id).resolve()
    if not str(job_dir).startswith(str(JOBS_DIR.resolve())):
        raise HTTPException(status_code=400, detail="잘못된 요청입니다.")
    if not job_dir.exists():
        raise HTTPException(
            status_code=404,
            detail=f"작업을 찾을 수 없습니다: job_id={job_id}",
        )

    files = list(job_dir.iterdir())
    if not files:
        raise HTTPException(
            status_code=404,
            detail=f"작업 디렉토리가 비어 있습니다: job_id={job_id}",
        )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(files):
            if file_path.is_file() and file_path.name != "metadata.json":
                zf.write(file_path, arcname=file_path.name)

    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="mermaid_export_{job_id}.zip"'
        },
    )


# ---------------------------------------------------------------------------
# 프론트엔드 정적 파일 서빙 (API 라우트 이후에 마운트)
# ---------------------------------------------------------------------------

# TODO: 오래된 jobs/ 디렉토리 자동 정리(cleanup) 기능 추가 필요.
#       예) 24시간 이상 지난 job_id 디렉토리를 주기적으로 삭제하는 백그라운드 태스크.

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
