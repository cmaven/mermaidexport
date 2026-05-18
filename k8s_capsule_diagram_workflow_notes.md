# Kubernetes 멀티 테넌시 + 스토리지 + AI 다이어그램 작업 노트

> 작성일: 2026-05-11
> 주제: K8s · Capsule · Rook-Ceph · Ceph 아키텍처와 AI로 다이어그램 생성하는 방법 정리

본 문서는 다음 주제들에 대한 대화 내용을 정리한 참고 자료다:

- K8s + Capsule + Rook-Ceph + Ceph 4계층 아키텍처
- Capsule 멀티 테넌시 오퍼레이터 설치 및 운영
- AI 도구로 아키텍처 다이어그램 생성 (도구 비교 + 프롬프트 예시)
- 생성된 다이어그램의 편집 가능성 (draw.io, PowerPoint)
- 실용적 하이브리드 워크플로

---

## 1. 아키텍처 — K8s + Capsule + Rook-Ceph + Ceph 상호작용

### 1.1 4계층 역할 분리

| 계층 | 역할 |
|---|---|
| **Capsule** | 멀티 테넌시 거버넌스 (K8s 위 컨트롤러) |
| **Kubernetes** | 컨테이너 오케스트레이션 플랫폼 |
| **Rook-Ceph** | K8s 내부에서 Ceph를 배포·관리하는 operator + CSI 드라이버 |
| **Ceph** | 분산 스토리지 백엔드 (블록/파일/객체) |

### 1.2 핵심 화살표 (4개 데이터·관리 경로)

1. **Capsule → 테넌트 네임스페이스**: Tenant CRD로 네임스페이스 그룹을 묶고 RBAC·NetworkPolicy·ResourceQuota를 강제 — **control plane**
2. **PVC → CSI Driver**: 테넌트가 만든 PVC가 StorageClass를 거쳐 Rook-Ceph CSI에 바인딩 — **storage request**
3. **Rook Operator → Ceph 데몬**: MON/OSD/MGR Pod을 K8s에 배포하고 라이프사이클 관리 — **management plane**
4. **CSI Driver → 스토리지 백엔드**: 실제 데이터 I/O — RBD(블록), CephFS(파일), RGW(객체) 중 StorageClass에 따라 결정 — **data plane**

### 1.3 한 줄 요약

- **Capsule** = 테넌시 거버넌스
- **Rook-Ceph** = 스토리지 컨트롤 플레인
- **Ceph** = 데이터 플레인

---

## 2. Capsule 설치 및 운영

### 2.1 Helm 차트 설치 (공식 권장 유일 방법)

```bash
helm install capsule \
  oci://ghcr.io/projectcapsule/charts/capsule \
  --version 0.12.4 \
  -n capsule-system --create-namespace
```

**전제조건**:

- Helm 3
- Kubernetes 1.16+ (실질적으로는 최신 마이너 버전 권장)
- ValidatingAdmissionWebhook · MutatingAdmissionWebhook 활성화
- cluster-admin 권한 kubeconfig

### 2.2 cert-manager 연동 (선택)

```bash
helm upgrade --install capsule \
  oci://ghcr.io/projectcapsule/charts/capsule \
  -n capsule-system --create-namespace \
  --set certManager.generateCertificates=true \
  --set tls.create=false \
  --set tls.enableController=false
```

### 2.3 설치되는 K8s 리소스

`capsule-system` 네임스페이스 하나에:

- **Deployment 1개**: `capsule-controller-manager` (기본 1 replica, HA는 2~3)
- **CRDs**: `Tenant`, `CapsuleConfiguration`, `GlobalTenantResource`, `TenantResource`
- **Webhooks**: ValidatingWebhookConfiguration + MutatingWebhookConfiguration
- **ServiceAccount + ClusterRole/Binding**
- **Service** (webhook 엔드포인트)

### 2.4 Kubeflow와 무게 비교

| 항목 | Capsule | Kubeflow |
|---|---|---|
| 네임스페이스 | 1개 | 10+개 |
| Pod 수 | 1~3개 | 50+개 |
| 의존 컴포넌트 | (선택) cert-manager | Istio, Knative, Dex, Cert-Manager, MinIO, MySQL … |
| 설치 도구 | helm 한 줄 | kustomize / kfctl 다단계 |

비유: Capsule은 `kube-rbac-proxy` 또는 Rook-Ceph operator 정도의 풋프린트.

### 2.5 설치 직후 상태

```bash
$ kubectl get pod -n capsule-system
NAME                                          READY   STATUS    RESTARTS   AGE
capsule-controller-manager-7f8c9d6b4-abc12    1/1     Running   0          1m
```

실행 중인 워크로드는 **Pod 1개**. 나머지는 선언적 리소스 (CRD, Webhook, RBAC).

```bash
# 전체 확인
kubectl get all -n capsule-system
kubectl get crd | grep capsule
kubectl get validatingwebhookconfiguration,mutatingwebhookconfiguration | grep capsule
```

### 2.6 첫 사용 — Tenant CR 생성

설치만으로는 아무 일도 안 일어남. `Tenant` 리소스 생성 후 동작 시작:

```yaml
apiVersion: capsule.clastix.io/v1beta2
kind: Tenant
metadata:
  name: oil-team
spec:
  owners:
    - name: jg@example.com
      kind: User
  namespaceOptions:
    quota: 5
  resourceQuotas:
    scope: Tenant
    items:
      - hard:
          requests.cpu: "20"
          requests.memory: 40Gi
          requests.storage: 500Gi
  networkPolicies:
    items:
      - policyTypes: [Ingress, Egress]
        podSelector: {}
```

`oil-team` 오너가 만드는 모든 네임스페이스에 자동으로 RBAC·ResourceQuota·NetworkPolicy 적용.

### 2.7 옵션 컴포넌트

- **Argo CD GitOps**: `Application` 리소스에 `Validate=false,SkipDryRunOnMissingResource=true` 어노테이션 필요 (CRD 검증 타이밍 이슈)
- **`capsule-proxy`**: 테넌트 사용자가 `kubectl get namespaces` 했을 때 자기 테넌트 것만 보이게 해주는 API 프록시 (별도 Helm chart)

---

## 3. AI 다이어그램 도구 비교 (Scaleway 스타일 기준)

### 3.1 솔직한 도구 평가

Scaleway Kapsule 같은 **마케팅용 브랜드 다이어그램**을 만들 수 있는 도구들:

| 도구 | Scaleway 스타일 근접도 | 편집 가능성 | 한계 |
|---|---|---|---|
| **Nano Banana Pro** (Gemini 3 Pro Image) | ★★★★ | ✗ 래스터 | 매번 살짝 다름, 재생성=오타 못 고침 |
| **GPT-4o / DALL-E 3** | ★★★ | ✗ 래스터 | Nano Banana보다 텍스트 정확도 ↓ |
| **Eraser DiagramGPT** | ★★★ | ✓ GUI 편집 | "기술 문서" 룩, 마케팅 아님 |
| **napkin.ai** | ★★★ | ✓ | 인프라 아이콘 부족 |
| **Excalidraw + AI** | ★★ | ✓ | 손그림 스타일 |
| **Mermaid** (Claude/ChatGPT) | ★ | ✓ 코드 | 미감 약함 |
| **Claude (SVG)** | ★★ | ✓ 코드 | 시각 디자인 제약 |
| **Figma + 디자이너** | ★★★★★ | ✓ | 비용/시간 |

### 3.2 핵심 인사이트

- **Scaleway 다이어그램은 AI가 만든 게 아님**. 디자이너가 Figma로 만든 브랜드 자료. 모든 도구가 100% 재현은 불가능.
- **이미지 생성 모델**은 한 번에 보기 좋은 결과를 내지만 **편집 불가**. 오타 하나도 재생성 필요.
- **코드 기반 도구** (Claude, Mermaid)는 편집 자유롭지만 **마케팅 미감** 약함.
- 최고 효율 = **하이브리드**: AI로 구조 잡고, 디자인 툴로 마감.

### 3.3 용도별 추천

| 용도 | 도구 |
|---|---|
| 사내 README, Wiki | Claude Mermaid 또는 Excalidraw |
| 블로그 표지, 발표 자료 | Nano Banana Pro |
| 편집 가능한 클라우드 아키텍처 | Eraser DiagramGPT |
| 브랜드 자료 (외부 공개) | Figma 외주 또는 사내 디자이너 |

---

## 4. 프롬프트 예시 (도구별)

같은 주제(K8s + Capsule + Rook-Ceph)를 같은 비주얼 스타일(Scaleway SaaS)로 그리는 프롬프트.

### 4.1 Nano Banana Pro (블록 구조형)

블록 헤더(`TASK / STYLE / LAYOUT / COMPONENTS / CONSTRAINTS`) 형태가 잘 먹는다.

#### 영어 버전

```
TASK
Generate a SaaS-style technical architecture diagram for a Kubernetes
multi-tenancy platform with Capsule and Rook-Ceph storage.

STYLE
- Aesthetic: modern SaaS marketing (Scaleway / Vercel / Render product pages)
- Colors:
    primary deep purple   #6B46FF
    managed-service fill  #FCE7F3 (soft pink)
    user-data fill        #DBEAFE (light blue)
    background            #FFFFFF
- Shapes: rounded rectangles, corner radius 12px, 1px solid stroke
- No shadows, no gradients, no 3D effects
- Each component has a 24px purple rounded square with filled icon at top
- Font: clean sans-serif (Inter or equivalent), sentence case only

LAYOUT
16:9 horizontal canvas, three columns left-to-right:
  COL 1 (25%): Capsule control plane
  COL 2 (50%): Kubernetes cluster container (dashed purple border)
  COL 3 (25%): floating storage services connected by dashed lines

COMPONENTS

[COL 1 — Capsule]
Header: "Capsule" + shield icon
Subtitle: "Multi-tenancy governance"
Pink inner boxes stacked vertically:
  - Tenant CRD
  - RBAC
  - ResourceQuota
  - NetworkPolicy

[COL 2 — Kubernetes cluster]
Header: "Kubernetes cluster" + Kubernetes wheel icon
Dashed purple border
Two "Tenant namespace" sub-containers (dashed) inside
Each sub-container:
  - 3 blue Pod boxes with container icons
  - 1 blue PVC box with disk icon
  - Pink "System applications" strip at bottom

[COL 3 — Storage services, floating]
Connected to cluster by dashed lines:
  - Top: pink "Rook-Ceph Operator" + gear icon
  - Middle: blue "Ceph Cluster" + cylinder icon
           with text "MON · OSD · MGR" / "RBD · CephFS · RGW"
  - Bottom: blue "Container Registry" + cubes icon

LEGEND (bottom-right)
  Pink swatch    — "Managed by platform"
  Blue swatch    — "Your workloads"
  Dashed line    — "Orchestrated by Kubernetes"

CONSTRAINTS
- Text sharp and correctly spelled at small sizes
- No overlapping labels or icons
- Even spacing between components (min 40px gutter)
- Icons consistent in size and style
- Suitable for product documentation
```

#### 한글 버전

```
TASK
Kubernetes 멀티 테넌시 플랫폼 (Capsule + Rook-Ceph)의
SaaS 스타일 기술 아키텍처 다이어그램 생성

STYLE
- 룩앤필: SaaS 마케팅 자료 (Scaleway / Vercel / Render 제품 페이지)
- 색상:
    primary deep purple   #6B46FF
    managed-service fill  #FCE7F3 (soft pink)
    user-data fill        #DBEAFE (light blue)
    background            #FFFFFF
- 도형: 둥근 사각형, corner radius 12px, 1px solid stroke
- 그림자/그라데이션/3D 효과 없음
- 각 컴포넌트 상단에 24px 보라 둥근 사각형 안에 filled 아이콘
- 폰트: Inter 또는 동급 sans-serif, sentence case

LAYOUT
16:9 가로 캔버스, 좌→우 3 컬럼:
  COL 1 (25%): Capsule control plane
  COL 2 (50%): Kubernetes cluster container (dashed 보라 테두리)
  COL 3 (25%): floating storage services (dashed line으로 cluster와 연결)

COMPONENTS

[COL 1 — Capsule]
헤더: "Capsule" + shield 아이콘
서브: "Multi-tenancy governance"
핑크 박스 4개 수직 stack:
  - Tenant CRD
  - RBAC
  - ResourceQuota
  - NetworkPolicy

[COL 2 — Kubernetes cluster]
헤더: "Kubernetes cluster" + Kubernetes wheel 아이콘
dashed 보라 테두리
내부에 "Tenant namespace" 서브컨테이너 2개 (dashed)
각 서브컨테이너 내용:
  - 파랑 Pod 박스 3개 + 컨테이너 아이콘
  - 파랑 PVC 박스 1개 + 디스크 아이콘
  - 하단 핑크 "System applications" 스트립

[COL 3 — Storage services, floating]
dashed line으로 cluster와 연결:
  - 상단: 핑크 "Rook-Ceph Operator" + gear 아이콘
  - 중간: 파랑 "Ceph Cluster" + cylinder 아이콘
          내부 텍스트: "MON · OSD · MGR" / "RBD · CephFS · RGW"
  - 하단: 파랑 "Container Registry" + cubes 아이콘

LEGEND (우하단)
  Pink swatch    — "Managed by platform"
  Blue swatch    — "Your workloads"
  Dashed line    — "Orchestrated by Kubernetes"

CONSTRAINTS
- 모든 텍스트는 작은 사이즈에서도 sharp하고 정확히 표기
- 라벨/아이콘 겹침 없음
- 컴포넌트 간 일정한 여백 (최소 40px)
- 아이콘 크기/스타일 통일
- product page 또는 기술 문서에 쓸 수 있는 전문적인 마감
```

#### 사용 팁

- **Reference image 첨부 권장**: 원본 Scaleway 다이어그램을 함께 첨부하고 "Generate a similar diagram but for this architecture, keep the same visual style" 추가. 최대 14장 reference 지원.
- **재생성 대신 수정 명령**: "Use the previous image but make the Capsule block taller and move the legend to bottom-left"
- 영어가 한글보다 안정적 (디자인 용어 학습 데이터가 영어 위주)

### 4.2 GPT-4o (서술형)

자연어 문단 + 명시적 색상·치수 제약. 블록 헤더보다 산문형이 잘 먹음.

#### 영어 버전

```
Create a clean, modern technical architecture diagram in the visual style
of Scaleway and Vercel product pages.

Subject: A Kubernetes multi-tenancy platform combining Capsule (tenant
governance) and Rook-Ceph (distributed storage backend).

Visual style:
- 16:9 horizontal layout on a pure white background
- Color palette: deep purple #6B46FF as primary accent, soft pink #FCE7F3
  fills for managed components, light blue #DBEAFE fills for user workloads
- All shapes are rounded rectangles with 12px corner radius and 1px thin stroke
- No drop shadows, no gradients, no 3D effects
- Each component has a small filled icon inside a 24px purple rounded
  square at the top
- Labels in sentence case using a clean sans-serif font like Inter

Layout, left to right in three columns:

LEFT column — "Capsule" control plane.
A vertical pink block titled "Capsule" with a shield icon and subtitle
"Multi-tenancy governance". Inside, stack four smaller pink rounded
boxes labeled: Tenant CRD, RBAC, ResourceQuota, NetworkPolicy.

CENTER column — "Kubernetes cluster".
A large container with a dashed purple border, labeled "Kubernetes cluster"
with a Kubernetes wheel icon. Inside, show two "Tenant namespace"
sub-containers (dashed). Each contains 3 blue Pod boxes with container
icons, 1 blue PVC box with disk icon, and a pink "System applications"
strip at the bottom.

RIGHT column — Storage services floating outside the cluster, connected by
dashed purple lines:
- Top: pink "Rook-Ceph Operator" with gear icon
- Middle: blue "Ceph Cluster" with cylinder icon, listing
  "MON · OSD · MGR" and "RBD · CephFS · RGW"
- Bottom: blue "Container Registry" with stacked cubes icon

Bottom-right legend (small box, 3 rows):
- Pink swatch — "Managed by platform"
- Blue swatch — "Your workloads"
- Dashed line — "Orchestrated by Kubernetes"

Critical: all text spelled correctly, no overlapping elements, even spacing,
polished professional appearance for product documentation.
```

#### 한글 버전

```
Scaleway나 Vercel 제품 페이지 같은 깔끔하고 모던한 기술 아키텍처
다이어그램을 이미지로 생성해줘.

주제: Capsule(테넌트 거버넌스)과 Rook-Ceph(분산 스토리지)를 결합한
Kubernetes 멀티 테넌시 플랫폼.

비주얼 스타일:
- 16:9 가로 레이아웃, 순수 흰색 배경
- 색상 팔레트: primary accent 진보라 #6B46FF,
  managed 컴포넌트 fill 연핑크 #FCE7F3,
  user workload fill 연파랑 #DBEAFE
- 모든 도형은 corner radius 12px의 둥근 사각형, 1px 얇은 stroke
- 그림자, 그라데이션, 3D 효과 없음
- 각 컴포넌트 상단에 24px 보라 둥근 사각형 안에 filled 아이콘
- 모든 라벨 sentence case, Inter 같은 깔끔한 sans-serif 폰트

레이아웃, 좌→우 3 컬럼:

LEFT 컬럼 — "Capsule" control plane.
핑크 fill의 세로 블록, 제목 "Capsule" + shield 아이콘,
서브타이틀 "Multi-tenancy governance".
블록 내부에 핑크 작은 둥근 박스 4개를 위에서 아래로 stack:
Tenant CRD, RBAC, ResourceQuota, NetworkPolicy.

CENTER 컬럼 — "Kubernetes cluster".
dashed 보라 테두리의 큰 컨테이너, 상단에 Kubernetes wheel 아이콘과
"Kubernetes cluster" 라벨. 내부에 "Tenant namespace" 서브컨테이너 2개
(역시 dashed border). 각 서브컨테이너에는 컨테이너 아이콘이 있는 파랑
Pod 박스 3개, 디스크 아이콘이 있는 파랑 PVC 박스 1개, 그리고 하단에
핑크 "System applications" 스트립을 배치.

RIGHT 컬럼 — 외부 스토리지 서비스, 클러스터 밖에 떠있고
각각 dashed 보라 line으로 클러스터와 연결:
- 상단: gear 아이콘이 있는 핑크 카드 "Rook-Ceph Operator"
- 중간: cylinder 아이콘이 있는 파랑 카드 "Ceph Cluster",
  내부에 "MON · OSD · MGR"과 "RBD · CephFS · RGW" 표시
- 하단: stacked cubes 아이콘이 있는 파랑 카드 "Container Registry"

우하단 코너에 작은 범례 박스, 3행:
- 핑크 swatch — "Managed by platform"
- 파랑 swatch — "Your workloads"
- dashed line — "Orchestrated by Kubernetes"

핵심 요구사항:
- 모든 텍스트 정확히 표기, 가독성 확보
- 어떤 요소도 겹치지 않음
- 컴포넌트 간 균등한 여백
- 제품 문서 페이지에 쓸 수 있는 polished, professional 마감
```

#### 사용 팁

- 대화형이라 후속 지시 자연스러움: "조금 더 minimal하게", "icons를 outline 스타일로"
- 첫 프롬프트는 영어로 정밀하게, 후속은 한글로 편하게

### 4.3 Claude (코드 생성)

Claude는 픽셀이 아니라 **SVG/HTML 코드**를 뽑는 도구. 프롬프트 결도 다름.

**핵심 차이**:

- **Artifact 모드 명시**: 인라인 visualizer 말고 standalone HTML로 요청해야 디자인 자유로움
- **hex 코드 명시**: 클래스명 말고 `#6B46FF` 박아달라고
- **viewBox · rx · stroke-width 같은 SVG 속성** 지정 가능
- **아이콘 한계 인정**: 브랜드 로고 못 그림, 단순 SVG path로 대체

#### 한글 프롬프트

```
다음 아키텍처를 standalone HTML artifact로 만들어줘.
(인라인 visualize 말고 Artifact로, SVG 자유롭게 쓸 수 있게)

주제: Kubernetes 멀티 테넌시 플랫폼 — Capsule + Rook-Ceph

전체 스타일:
- 룩앤필: SaaS 마케팅 자료 (Scaleway, Vercel 제품 페이지 느낌)
- 캔버스: 1200×720 (16:9), 흰색 배경
- 색상 팔레트 (정확히 이 hex 써):
    Primary 진보라        #6B46FF
    Managed service 핑크   #FCE7F3
    User data 파랑         #DBEAFE
- 모든 도형: 둥근 사각형 (rx=12), 1px solid 또는 dashed stroke
- 그림자, 그라데이션, 3D 효과 없음
- 폰트: Inter 또는 system-ui sans-serif, sentence case
- 각 컴포넌트 상단에 24px 보라 둥근 사각형 아이콘 박스

레이아웃 — 좌→우 3 컬럼:

LEFT (폭 25%) — Capsule control plane:
  핑크 outer 박스
  헤더: "Capsule" + shield 아이콘
  서브타이틀: "Multi-tenancy governance"
  내부에 핑크 inner 박스 4개 세로 stack:
    Tenant CRD / RBAC / ResourceQuota / NetworkPolicy

CENTER (폭 50%) — Kubernetes cluster:
  dashed 보라 테두리 큰 컨테이너
  헤더: "Kubernetes cluster" + K8s wheel 아이콘
  내부에 "Tenant namespace" 서브컨테이너 2개 (역시 dashed)
  각 서브컨테이너 안:
    파랑 Pod 박스 3개 (컨테이너 아이콘),
    파랑 PVC 박스 1개 (디스크 아이콘),
    하단에 핑크 "System applications" 스트립

RIGHT (폭 25%) — 외부 스토리지 서비스
(클러스터와 dashed line으로 연결, 떠있는 카드):
  상단: 핑크 카드 "Rook-Ceph Operator" + gear 아이콘
  중간: 파랑 카드 "Ceph Cluster" + cylinder 아이콘
        내부 텍스트 2줄:
          "MON · OSD · MGR"
          "RBD · CephFS · RGW"
  하단: 파랑 카드 "Container Registry" + cubes 아이콘

범례 (우하단 작은 박스):
  핑크 swatch — "Managed by platform"
  파랑 swatch — "Your workloads"
  dashed line — "Orchestrated by Kubernetes"

요구사항:
- 모든 텍스트 정확히 표기
- 라벨/요소 겹침 없이 충분한 여백
- viewBox 명시, width 100% responsive
- standalone HTML 파일 하나로 완결 (외부 CDN 의존 OK, 그 외 의존성 없음)
- 다크모드 대응 불필요
- 코드는 한 파일에 인라인 CSS로
```

#### 사용 팁

- Claude한테는 한글 프롬프트 OK (코드 생성이라 언어 영향 적음)
- "draw.io XML로 만들어줘"라고 하면 .drawio 호환 mxGraph XML 생성 가능
- "Mermaid로 만들어줘"하면 GitHub/Notion에서 자동 렌더링되는 코드 생성

---

## 5. Claude로 생성한 실제 결과물

위 한글 프롬프트로 생성한 standalone HTML 파일:

📄 `k8s_capsule_rook_ceph_diagram.html`

- 1200×720 SVG, responsive width
- 색상은 정확히 요청한 hex만 사용 (`#6B46FF`, `#FCE7F3`, `#DBEAFE`)
- 외부 의존성 없는 단일 파일
- 어떤 브라우저에서도 열림

**솔직한 제약**:

- 아이콘은 단순 SVG path (진짜 K8s 7각형 wheel 아닌 6각형, gear는 4-spoke, cubes는 2D 정사각형 3개)
- 디자이너 hand-tune의 미세한 균형감 부족 (grid에 충실한 robotic 느낌)
- 로고/워터마크 부재

---

## 6. 출력 형식별 편집 가능성

### 6.1 비교 테이블

| 결과물 | 형식 | draw.io | PowerPoint |
|---|---|---|---|
| Nano Banana Pro | PNG (래스터) | ✗ 이미지만 | ✗ 이미지만 |
| GPT-4o / DALL-E | PNG (래스터) | ✗ 이미지만 | ✗ 이미지만 |
| Claude (HTML+SVG) | SVG (벡터) | △ 이미지로 import | ✓ shape로 변환 가능 |

### 6.2 PowerPoint에서 SVG 편집 (잘 됨)

PowerPoint 2016+ / Microsoft 365:

1. `Insert` → `Pictures` → `.svg` 파일 선택
2. 삽입된 SVG 우클릭 → **`Convert to Shape`**
3. 모든 박스·텍스트·라인이 PowerPoint 네이티브 도형으로 분리 → 개별 편집 가능

알아둘 점:

- 폰트는 PowerPoint 호환으로 자동 치환 (Inter → Calibri 등)
- 복잡한 SVG path (특히 아이콘)는 변환 약간 깨질 수 있음
- 그룹 해제(Ungroup) 한 번 더 필요한 경우 있음

### 6.3 draw.io에서 SVG (제한적)

draw.io는 SVG를 **단일 이미지로만** import. 박스 개별 편집 불가.

**우회로**:

1. **mxGraph XML 직접 요청**: Claude한테 "draw.io mxGraph XML 형식으로 만들어줘". 네이티브 .drawio 파일 → 완전 편집 가능
2. **Mermaid**: draw.io는 `Extras → Edit Diagram → Insert from text`에서 Mermaid 코드 import 지원

---

## 7. 최종 실용 워크플로

### 7.1 용도별 추천 경로

| 용도 | 추천 경로 |
|---|---|
| 사내 README, GitHub | Mermaid (Claude로 생성, GitHub 자동 렌더링) |
| 발표 슬라이드 (일회성) | Claude SVG → PPT 삽입 → Convert to Shape |
| 팀 공유 + 지속 유지보수 | Claude한테 draw.io XML 요청 → .drawio 파일 |
| 블로그 표지, 마케팅 이미지 | Nano Banana Pro (재생성으로 iterate) |
| 외부 공개 브랜드 자료 | Figma 외주 또는 사내 디자이너 |
| 편집 가능한 클라우드 아키텍처 | Eraser DiagramGPT |

### 7.2 하이브리드 워크플로 (권장)

1. **구조 검증** — Claude(SVG/Mermaid) 또는 Eraser로 컴포넌트·관계를 먼저 정확하게
2. **미감 입히기** — 그 출력을 Nano Banana Pro에 reference로 넣고 "이 다이어그램을 SaaS 마케팅 스타일로 다시 그려줘"
3. **마무리** — 필요시 Figma/PowerPoint에서 손봄

→ 구조는 AI가 검증, 미감도 AI가 입히고, 마무리만 사람.

---

## 참고 링크

- [Project Capsule 공식 사이트](https://projectcapsule.dev/)
- [Capsule Helm Chart (GitHub)](https://github.com/projectcapsule/capsule)
- [Capsule 설치 문서](https://projectcapsule.dev/docs/operating/setup/installation/)
- [Nano Banana Pro (Gemini 3 Pro Image)](https://deepmind.google/models/gemini-image/pro/)
- [Eraser DiagramGPT](https://www.eraser.io/diagramgpt)
- [draw.io (diagrams.net)](https://www.drawio.com/)
- [Mermaid](https://mermaid.js.org/)

---

## 부록 — 핵심 색상 팔레트 (재사용용)

| 용도 | Hex | 비고 |
|---|---|---|
| Primary purple | `#6B46FF` | 모든 stroke, 헤더 텍스트 |
| Managed service fill (pink) | `#FCE7F3` | Capsule, Rook-Ceph Operator, System apps |
| User data fill (blue) | `#DBEAFE` | Pod, PVC, Ceph Cluster, Container Registry |
| Text on blue fill | `#1E3A8A` | 파랑 배경 위 텍스트 |
| Background | `#FFFFFF` | 캔버스 전체 |
| Legend text | `#475569` | 범례 본문 (slate gray) |

이 팔레트는 SaaS 마케팅 미감 + 다크모드 비대응 + 접근성 OK 라인. 다른 색상으로 가고 싶으면 `#6B46FF`만 다른 primary로 바꾸고 나머지는 같은 채도 비율로 조정.
