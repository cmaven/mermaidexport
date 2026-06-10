/**
 * app.js: Mermaid Web Converter 클라이언트 로직
 * 상세: 파일 업로드, 변환 API 호출, 결과 렌더링, 다운로드 처리
 *       Mermaid 확대 모달(openMermaidZoomModal) 포함
 * 생성일: 2026-04-07 | 수정일: 2026-06-10
 */

'use strict';

// ============================================================
// 상수 및 설정
// ============================================================

const API_BASE = '/api';

const ENDPOINTS = {
  convert:         `${API_BASE}/convert`,
  downloadAll:     (jobId) => `${API_BASE}/download/${jobId}/all`,
  downloadOne:     (jobId, index, fmt) => `${API_BASE}/download/${jobId}/${index}/${fmt}`,
  preview:         (jobId, index) => `${API_BASE}/download/${jobId}/${index}/png`,
  combinedPptx:    (jobId) => `${API_BASE}/download/${jobId}/combined-pptx`,
};

const FORMAT_META = {
  png:        { label: 'PNG',       cls: 'btn-png',    ext: 'png'     },
  drawio:     { label: 'Draw.io',   cls: 'btn-drawio', ext: 'drawio'  },
  excalidraw: { label: 'Excalidraw',cls: 'btn-excali', ext: 'excalidraw' },
  pptx:       { label: 'PPTX',      cls: 'btn-pptx',   ext: 'pptx'   },
  svg:        { label: 'SVG',        cls: 'btn-svg',    ext: 'svg'    },
};

// ============================================================
// DOM 참조
// ============================================================

const dropZone       = document.getElementById('dropZone');
const fileInput      = document.getElementById('fileInput');
const btnPick        = document.getElementById('btnPick');
const loadingOverlay = document.getElementById('loadingOverlay');
const errorBanner    = document.getElementById('errorBanner');
const errorMessage   = document.getElementById('errorMessage');
const resultsSection = document.getElementById('resultsSection');
const diagramGrid    = document.getElementById('diagramGrid');
const resultsCount   = document.getElementById('resultsCount');
const btnDownloadAll    = document.getElementById('btnDownloadAll');
const btnCombinedPptx  = document.getElementById('btnCombinedPptx');

// ============================================================
// 앱 상태
// ============================================================

let currentJobId = null;

// ============================================================
// 로딩 상태 관리
// ============================================================

/**
 * 로딩 오버레이를 표시하거나 숨깁니다.
 * @param {boolean} visible
 */
function setLoading(visible) {
  if (visible) {
    loadingOverlay.classList.add('visible');
    loadingOverlay.removeAttribute('aria-hidden');
  } else {
    loadingOverlay.classList.remove('visible');
    loadingOverlay.setAttribute('aria-hidden', 'true');
  }
}

// ============================================================
// 에러 처리
// ============================================================

/**
 * 에러 배너를 표시합니다.
 * @param {string} msg - 표시할 에러 메시지 (한국어)
 */
function showError(msg) {
  errorMessage.textContent = msg;
  errorBanner.hidden = false;
  dropZone.classList.add('has-error');
}

/**
 * 에러 배너를 숨깁니다.
 */
function clearError() {
  errorBanner.hidden = true;
  errorMessage.textContent = '';
  dropZone.classList.remove('has-error');
}

/**
 * HTTP 응답 오류를 한국어 메시지로 변환합니다.
 * @param {Response} response
 * @returns {string}
 */
async function parseApiError(response) {
  try {
    const data = await response.json();
    if (data.detail) return `서버 오류: ${data.detail}`;
    if (data.message) return `서버 오류: ${data.message}`;
  } catch (_) {
    // JSON 파싱 실패 시 상태 코드 기반 메시지
  }

  switch (response.status) {
    case 400: return '잘못된 파일 형식입니다. Markdown(.md) 파일을 업로드해주세요.';
    case 404: return '요청한 리소스를 찾을 수 없습니다. 다시 시도해주세요.';
    case 413: return '파일 크기가 너무 큽니다. 더 작은 파일을 업로드해주세요.';
    case 422: return 'Mermaid 다이어그램을 찾을 수 없습니다. 파일 내용을 확인해주세요.';
    case 500: return '서버 내부 오류가 발생했습니다. 잠시 후 다시 시도해주세요.';
    default:  return `알 수 없는 오류가 발생했습니다. (${response.status})`;
  }
}

// ============================================================
// 파일 업로드 및 변환 API 호출
// ============================================================

/**
 * MD 파일을 서버에 업로드하고 변환을 요청합니다.
 * @param {File} file
 */
async function uploadFile(file) {
  // 파일 유효성 검사
  if (!file) return;

  const isMarkdown = file.name.endsWith('.md') || file.type === 'text/markdown' || file.type === 'text/plain';
  if (!isMarkdown) {
    showError('Markdown(.md) 파일만 업로드할 수 있습니다.');
    return;
  }

  if (file.size > 10 * 1024 * 1024) {
    showError('파일 크기가 10MB를 초과합니다. 더 작은 파일을 선택해주세요.');
    return;
  }

  clearError();
  setLoading(true);

  try {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(ENDPOINTS.convert, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      const errMsg = await parseApiError(response);
      throw new Error(errMsg);
    }

    const data = await response.json();

    if (!data.job_id) {
      throw new Error('서버 응답이 올바르지 않습니다. 다시 시도해주세요.');
    }

    if (!data.diagrams || data.diagrams.length === 0) {
      throw new Error('파일에서 Mermaid 다이어그램을 찾지 못했습니다. 파일 내용을 확인해주세요.');
    }

    currentJobId = data.job_id;
    renderResults(data);

  } catch (err) {
    if (err.name === 'TypeError') {
      // 네트워크 연결 실패
      showError('서버에 연결할 수 없습니다. 네트워크 상태를 확인해주세요.');
    } else {
      showError(err.message);
    }
  } finally {
    setLoading(false);
  }
}

// ============================================================
// 결과 렌더링
// ============================================================

/**
 * 변환 결과를 화면에 렌더링합니다.
 * @param {{ job_id: string, diagrams: Array<{ title?: string, index: number }> }} data
 */
function renderResults(data) {
  const { job_id, diagrams } = data;

  // 기존 카드 초기화
  diagramGrid.innerHTML = '';

  // 개수 뱃지 업데이트
  resultsCount.textContent = `${diagrams.length}개 다이어그램`;

  // 전체 다운로드 버튼 연결
  btnDownloadAll.onclick = () => downloadAll(job_id);

  // 합본 PPTX 다운로드 버튼 (2개 이상 다이어그램일 때)
  if (diagrams.length >= 2) {
    btnCombinedPptx.hidden = false;
    btnCombinedPptx.onclick = () => downloadCombinedPptx(job_id);
  } else {
    btnCombinedPptx.hidden = true;
  }

  // 각 다이어그램 카드 생성
  diagrams.forEach((diagram, i) => {
    const card = createDiagramCard(job_id, diagram, i);
    diagramGrid.appendChild(card);
  });

  // 결과 섹션 표시
  resultsSection.hidden = false;

  // 부드럽게 스크롤
  requestAnimationFrame(() => {
    resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}

/**
 * 개별 다이어그램 카드 DOM 엘리먼트를 생성합니다.
 * @param {string} jobId
 * @param {{ title?: string, index: number }} diagram
 * @param {number} cardIndex - 표시용 순서 인덱스
 * @returns {HTMLElement}
 */
function createDiagramCard(jobId, diagram, cardIndex) {
  const idx   = diagram.index ?? cardIndex;
  const title = diagram.title || `다이어그램 ${cardIndex + 1}`;

  const card = document.createElement('article');
  card.className = 'diagram-card';
  card.setAttribute('role', 'listitem');

  // 헤더
  const header = document.createElement('div');
  header.className = 'diagram-card-header';

  const badge = document.createElement('span');
  badge.className = 'diagram-index';
  badge.textContent = cardIndex + 1;
  badge.setAttribute('aria-label', `다이어그램 ${cardIndex + 1}`);

  const titleEl = document.createElement('h3');
  titleEl.className = 'diagram-title';
  titleEl.textContent = title;
  titleEl.title = title;

  header.appendChild(badge);
  header.appendChild(titleEl);

  // 미리보기
  const preview = document.createElement('div');
  preview.className = 'diagram-preview';

  const img = document.createElement('img');
  img.className = 'diagram-img diagram-img-loading';
  img.alt = `${title} 미리보기`;
  img.src = ENDPOINTS.preview(jobId, idx);

  // 미리보기 확대 버튼 (이미지 로드 성공 시에만 노출)
  const previewZoomBtn = document.createElement('button');
  previewZoomBtn.type = 'button';
  previewZoomBtn.className = 'preview-zoom-btn';
  previewZoomBtn.setAttribute('aria-label', `${title} 미리보기 확대`);
  previewZoomBtn.hidden = true;
  previewZoomBtn.innerHTML =
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">'
    + '<path d="M9.5 2.5H13.5V6.5M6.5 13.5H2.5V9.5"/>'
    + '</svg>확대';
  previewZoomBtn.addEventListener('click', () => openMermaidZoomModal(img, title));

  img.addEventListener('load', () => {
    img.classList.remove('diagram-img-loading');
    previewZoomBtn.hidden = false;
  });

  img.addEventListener('error', () => {
    img.classList.add('diagram-img-error');
    previewZoomBtn.hidden = true;
    const placeholder = createPreviewPlaceholder();
    preview.appendChild(placeholder);
  });

  preview.appendChild(img);
  preview.appendChild(previewZoomBtn);

  // 다운로드 버튼 영역
  const actions = document.createElement('div');
  actions.className = 'diagram-actions';

  Object.entries(FORMAT_META).forEach(([fmt, meta]) => {
    const btn = createDownloadButton(jobId, idx, fmt, meta, diagram.formats?.[fmt]);
    actions.appendChild(btn);
  });

  card.appendChild(header);
  card.appendChild(preview);
  card.appendChild(actions);

  // Mermaid 원본 렌더링 보기 (실제 렌더된 그래프 — SVG 인라인, 지연 로딩)
  card.appendChild(createMermaidRenderBlock(jobId, idx, title));

  if (diagram.mermaid_code) {
    const codeBlock = document.createElement('details');
    codeBlock.className = 'mermaid-source';
    const summary = document.createElement('summary');
    summary.textContent = 'Mermaid 원본 코드 보기';
    codeBlock.appendChild(summary);

    const pre = document.createElement('pre');
    pre.className = 'mermaid-code';
    const code = document.createElement('code');
    code.textContent = diagram.mermaid_code;
    pre.appendChild(code);

    const copyBtn = document.createElement('button');
    copyBtn.className = 'btn-copy-code';
    copyBtn.type = 'button';
    copyBtn.textContent = '복사';
    copyBtn.addEventListener('click', async () => {
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(diagram.mermaid_code);
        } else {
          // HTTP/비보안 컨텍스트 폴백
          const ta = document.createElement('textarea');
          ta.value = diagram.mermaid_code;
          ta.style.position = 'fixed'; ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
        }
        copyBtn.textContent = '복사됨 ✓';
        setTimeout(() => { copyBtn.textContent = '복사'; }, 1500);
      } catch (err) {
        copyBtn.textContent = '복사 실패';
      }
    });

    codeBlock.appendChild(copyBtn);
    codeBlock.appendChild(pre);
    card.appendChild(codeBlock);
  }

  return card;
}

/**
 * "Mermaid 원본 렌더링 보기" 접는 메뉴를 생성합니다.
 * 백엔드가 mmdc로 생성한 SVG(실제 렌더된 그래프)를 인라인하며,
 * 메뉴를 처음 펼칠 때 한 번만 지연 로딩합니다.
 * @param {string} jobId
 * @param {number} index - 다이어그램 인덱스
 * @param {string} title - 접근성 라벨용 제목
 * @returns {HTMLElement} <details> 요소
 */
function createMermaidRenderBlock(jobId, index, title) {
  const block = document.createElement('details');
  block.className = 'mermaid-render';

  const summary = document.createElement('summary');
  summary.textContent = 'Mermaid 원본 렌더링 보기';
  block.appendChild(summary);

  const body = document.createElement('div');
  body.className = 'mermaid-render-body';
  body.innerHTML = '<span class="mermaid-render-status">펼치면 그래프를 불러옵니다…</span>';
  block.appendChild(body);

  let loaded = false;
  block.addEventListener('toggle', async () => {
    if (!block.open || loaded) return;
    loaded = true;
    body.innerHTML = '<span class="mermaid-render-status">불러오는 중…</span>';
    try {
      const res = await fetch(ENDPOINTS.downloadOne(jobId, index, 'svg'));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const svgText = await res.text();
      if (!svgText.includes('<svg')) throw new Error('SVG 형식이 아님');
      body.innerHTML = svgText;
      const svgEl = body.querySelector('svg');
      if (svgEl) {
        svgEl.removeAttribute('width');
        svgEl.removeAttribute('height');
        svgEl.setAttribute('role', 'img');
        svgEl.setAttribute('aria-label', `${title} Mermaid 렌더링`);
      }

      // 확대 버튼 바 삽입 (SVG 로드 성공 시 한 번만)
      const actionsBar = document.createElement('div');
      actionsBar.className = 'mermaid-render-actions';
      const zoomOpenBtn = document.createElement('button');
      zoomOpenBtn.type      = 'button';
      zoomOpenBtn.className = 'mermaid-zoom-open-btn';
      zoomOpenBtn.setAttribute('aria-label', `${title} 전체화면 확대`);
      zoomOpenBtn.innerHTML =
        '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">'
        + '<path d="M9.5 2.5H13.5V6.5M6.5 13.5H2.5V9.5"/>'
        + '</svg>확대';
      zoomOpenBtn.addEventListener('click', () => {
        const svgInBody = body.querySelector('svg');
        if (svgInBody) openMermaidZoomModal(svgInBody, title);
      });
      actionsBar.appendChild(zoomOpenBtn);
      block.insertBefore(actionsBar, body);

    } catch (err) {
      loaded = false; // 실패 시 재시도 허용
      body.innerHTML =
        '<span class="mermaid-render-status mermaid-render-error">'
        + '렌더링 미리보기를 불러올 수 없습니다. (SVG 변환 실패 또는 mmdc 미설치)'
        + '</span>';
    }
  });

  return block;
}

/**
 * 전체화면 확대 모달을 엽니다 (SVG 렌더 / PNG 미리보기 공용).
 * 기존 모달이 열려 있으면 먼저 닫은 후 새로 생성합니다 (싱글톤).
 * @param {SVGElement|HTMLImageElement} sourceSvgEl - 복제 대상 SVG 또는 IMG 요소
 * @param {string} title - 모달 제목 (접근성 라벨)
 */
function openMermaidZoomModal(sourceSvgEl, title) {
  closeMermaidZoomModal(); // 기존 모달 제거 (싱글톤)

  let currentScale = 1;
  const SCALE_STEP = 0.25;
  const SCALE_MIN  = 0.25;
  const SCALE_MAX  = 5;

  // ── 오버레이 (배경 딤처리) ──────────────────────────────────
  const overlay = document.createElement('div');
  overlay.className = 'mermaid-zoom-overlay';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-label', `${title} — 확대 보기`);

  // ── 다이얼로그 패널 ─────────────────────────────────────────
  const dialog = document.createElement('div');
  dialog.className = 'mermaid-zoom-dialog';

  // ── 헤더 ──────────────────────────────────────────────────
  const header = document.createElement('div');
  header.className = 'mermaid-zoom-header';

  const titleEl = document.createElement('span');
  titleEl.className = 'mermaid-zoom-title';
  titleEl.textContent = title;

  // ── 줌 컨트롤 ─────────────────────────────────────────────
  const controls = document.createElement('div');
  controls.className = 'mermaid-zoom-controls';

  const minusBtn = document.createElement('button');
  minusBtn.type = 'button';
  minusBtn.className = 'mermaid-zoom-ctrl-btn';
  minusBtn.textContent = '－';
  minusBtn.setAttribute('aria-label', '축소');

  const resetBtn = document.createElement('button');
  resetBtn.type = 'button';
  resetBtn.className = 'mermaid-zoom-ctrl-btn mermaid-zoom-ctrl-reset';
  resetBtn.textContent = '100%';
  resetBtn.setAttribute('aria-label', '원래 크기로');

  const plusBtn = document.createElement('button');
  plusBtn.type = 'button';
  plusBtn.className = 'mermaid-zoom-ctrl-btn';
  plusBtn.textContent = '＋';
  plusBtn.setAttribute('aria-label', '확대');

  controls.appendChild(minusBtn);
  controls.appendChild(resetBtn);
  controls.appendChild(plusBtn);

  // ── 닫기 버튼 ─────────────────────────────────────────────
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'mermaid-zoom-close';
  closeBtn.setAttribute('aria-label', '모달 닫기');
  closeBtn.textContent = '✕';
  closeBtn.addEventListener('click', closeMermaidZoomModal);

  header.appendChild(titleEl);
  header.appendChild(controls);
  header.appendChild(closeBtn);

  // ── 뷰포트 & SVG 컨테이너 ──────────────────────────────────
  const viewport = document.createElement('div');
  viewport.className = 'mermaid-zoom-viewport';

  const content = document.createElement('div');
  content.className = 'mermaid-zoom-content';

  // 소스 복제 후 삽입 (원본 불변).
  // - SVG: viewBox 기준 명시적 width/height를 부여해야 shrink-to-fit 컨테이너
  //        안에서도 0 크기로 붕괴하지 않는다. (CSS max-width/height가 축소 담당)
  // - IMG(변환 미리보기 PNG 등): 같은 src로 새 img를 만들어 표시.
  let clonedNode;
  if (sourceSvgEl.tagName && sourceSvgEl.tagName.toLowerCase() === 'svg') {
    clonedNode = sourceSvgEl.cloneNode(true);
    const vb = (sourceSvgEl.getAttribute('viewBox') || '')
      .split(/[\s,]+/).map(Number).filter((n) => !Number.isNaN(n));
    if (vb.length === 4 && vb[2] > 0 && vb[3] > 0) {
      clonedNode.setAttribute('width', vb[2]);
      clonedNode.setAttribute('height', vb[3]);
    } else {
      clonedNode.removeAttribute('width');
      clonedNode.removeAttribute('height');
    }
  } else {
    clonedNode = document.createElement('img');
    clonedNode.src = sourceSvgEl.currentSrc || sourceSvgEl.src;
    clonedNode.alt = sourceSvgEl.alt || title;
  }
  content.appendChild(clonedNode);
  viewport.appendChild(content);

  dialog.appendChild(header);
  dialog.appendChild(viewport);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
  document.body.style.overflow = 'hidden'; // 배경 스크롤 잠금

  // ── 줌 적용 함수 ─────────────────────────────────────────
  function applyScale(newScale) {
    currentScale = Math.min(SCALE_MAX, Math.max(SCALE_MIN, newScale));
    content.style.transform = `scale(${currentScale})`;
    resetBtn.textContent = `${Math.round(currentScale * 100)}%`;
  }

  // 버튼 이벤트 연결
  minusBtn.addEventListener('click', () => applyScale(currentScale - SCALE_STEP));
  plusBtn.addEventListener('click',  () => applyScale(currentScale + SCALE_STEP));
  resetBtn.addEventListener('click', () => applyScale(1));

  // 배경 클릭으로 닫기
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) closeMermaidZoomModal();
  });

  // 마우스 휠 줌 (뷰포트 영역)
  viewport.addEventListener('wheel', (e) => {
    e.preventDefault();
    applyScale(currentScale + (e.deltaY < 0 ? SCALE_STEP : -SCALE_STEP));
  }, { passive: false });

  // ESC 키로 닫기
  function onKeydown(e) {
    if (e.key === 'Escape') { e.preventDefault(); closeMermaidZoomModal(); }
  }
  document.addEventListener('keydown', onKeydown);
  overlay._onKeydown = onKeydown; // cleanup 참조 보관

  // 포커스 이동 (키보드 접근성)
  requestAnimationFrame(() => closeBtn.focus());
}

/**
 * 열려 있는 Mermaid 확대 모달을 닫고 DOM에서 제거합니다.
 */
function closeMermaidZoomModal() {
  const existing = document.querySelector('.mermaid-zoom-overlay');
  if (!existing) return;
  if (existing._onKeydown) {
    document.removeEventListener('keydown', existing._onKeydown);
  }
  existing.remove();
  document.body.style.overflow = '';
}

/**
 * 이미지 로딩 실패 시 표시할 플레이스홀더를 생성합니다.
 * @returns {HTMLElement}
 */
function createPreviewPlaceholder() {
  const el = document.createElement('div');
  el.className = 'preview-placeholder';
  el.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
      <rect x="3" y="3" width="18" height="18" rx="2"/>
      <path d="M3 9h18M9 21V9"/>
    </svg>
    <span>미리보기를 불러올 수 없습니다</span>
  `;
  return el;
}

/**
 * 포맷별 다운로드 버튼을 생성합니다.
 * @param {string} jobId
 * @param {number} index
 * @param {string} fmt
 * @param {{ label: string, cls: string, ext: string }} meta
 * @param {string|null|undefined} formatUrl - 변환 결과 URL (null이면 비활성 처리)
 * @returns {HTMLAnchorElement}
 */
function createDownloadButton(jobId, index, fmt, meta, formatUrl) {
  const a = document.createElement('a');
  a.className = `btn-download ${meta.cls}`;

  if (formatUrl === null || formatUrl === undefined) {
    a.classList.add('btn-disabled');
    a.removeAttribute('href');
    a.title = `${meta.label} 변환 실패 — 사용할 수 없음`;
    a.setAttribute('aria-disabled', 'true');
    a.addEventListener('click', (e) => e.preventDefault());
  } else {
    a.href = ENDPOINTS.downloadOne(jobId, index, fmt);
    a.download = `diagram_${index + 1}.${meta.ext}`;
  }

  a.setAttribute('aria-label', `다이어그램 ${index + 1} ${meta.label} 다운로드`);

  // 아이콘 (다운로드 화살표)
  a.innerHTML = `
    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M8 12l-4.5-4.5 1.06-1.06L7 8.88V2h2v6.88l2.44-2.44L12.5 7.5 8 12z"/>
      <path d="M2 13h12v1.5H2z"/>
    </svg>
    ${escapeHtml(meta.label)}
  `;

  return a;
}

// ============================================================
// 전체 다운로드
// ============================================================

/**
 * 전체 결과물 ZIP 파일 다운로드를 트리거합니다.
 * @param {string} jobId
 */
function downloadAll(jobId) {
  if (!jobId) return;

  const url = ENDPOINTS.downloadAll(jobId);

  // 임시 <a> 태그로 다운로드 트리거
  const a = document.createElement('a');
  a.href = url;
  a.download = `mermaid_diagrams_${jobId}.zip`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

/**
 * 전체 다이어그램을 하나의 PPTX 파일로 묶어 다운로드합니다.
 * @param {string} jobId
 */
function downloadCombinedPptx(jobId) {
  if (!jobId) return;

  const url = ENDPOINTS.combinedPptx(jobId);

  const a = document.createElement('a');
  a.href = url;
  a.download = `mermaid_diagrams_${jobId}.pptx`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

// ============================================================
// 유틸리티
// ============================================================

/**
 * HTML 특수문자를 이스케이프합니다 (XSS 방지).
 * @param {string} str
 * @returns {string}
 */
function escapeHtml(str) {
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(str).replace(/[&<>"']/g, (c) => map[c]);
}

// ============================================================
// 드래그 앤 드롭 이벤트 핸들러
// ============================================================

/**
 * 드래그 오버 시 시각적 피드백을 제공합니다.
 * @param {DragEvent} e
 */
function handleDragOver(e) {
  e.preventDefault();
  e.stopPropagation();
  e.dataTransfer.dropEffect = 'copy';
  dropZone.classList.add('drag-over');
}

/**
 * 드래그가 드롭존을 벗어날 때 스타일을 초기화합니다.
 * @param {DragEvent} e
 */
function handleDragLeave(e) {
  e.preventDefault();
  e.stopPropagation();
  // 자식 요소로 이동 시 불필요한 leave 이벤트 방지
  if (!dropZone.contains(e.relatedTarget)) {
    dropZone.classList.remove('drag-over');
  }
}

/**
 * 파일 드롭 처리.
 * @param {DragEvent} e
 */
function handleDrop(e) {
  e.preventDefault();
  e.stopPropagation();
  dropZone.classList.remove('drag-over');

  const files = e.dataTransfer.files;
  if (!files || files.length === 0) return;

  if (files.length > 1) {
    showError('한 번에 하나의 파일만 업로드할 수 있습니다.');
    return;
  }

  uploadFile(files[0]);
}

// ============================================================
// 파일 인풋 이벤트 핸들러
// ============================================================

/**
 * 파일 선택 창을 통한 파일 선택 처리.
 * @param {Event} e
 */
function handleFileInputChange(e) {
  const file = e.target.files[0];
  if (file) {
    uploadFile(file);
    // 같은 파일 재선택 허용을 위해 값 초기화
    e.target.value = '';
  }
}

// ============================================================
// 키보드 접근성
// ============================================================

/**
 * 드롭존 키보드 활성화 (Enter / Space).
 * @param {KeyboardEvent} e
 */
function handleDropZoneKeydown(e) {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    fileInput.click();
  }
}

// ============================================================
// 이벤트 리스너 등록
// ============================================================

// 드래그 앤 드롭
dropZone.addEventListener('dragover',   handleDragOver);
dropZone.addEventListener('dragleave',  handleDragLeave);
dropZone.addEventListener('drop',       handleDrop);

// 키보드 접근성
dropZone.addEventListener('keydown',    handleDropZoneKeydown);

// 파일 선택 버튼 — 숨겨진 input 클릭
btnPick.addEventListener('click', (e) => {
  e.stopPropagation();
  fileInput.click();
});

// 드롭존 클릭 시 파일 선택창 (버튼 외 영역)
dropZone.addEventListener('click', (e) => {
  if (e.target !== btnPick && !btnPick.contains(e.target)) {
    fileInput.click();
  }
});

// 파일 인풋 변경
fileInput.addEventListener('change', handleFileInputChange);

// 전체 다운로드 버튼 초기 상태
btnDownloadAll.addEventListener('click', () => {
  if (currentJobId) downloadAll(currentJobId);
});

// ============================================================
// 전역 드래그 차단 (드롭존 외부에서 브라우저 기본 동작 방지)
// ============================================================

document.addEventListener('dragover', (e) => e.preventDefault());
document.addEventListener('drop',     (e) => e.preventDefault());
