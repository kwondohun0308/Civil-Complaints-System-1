# 운영 런북 (RUNBOOK)

> **시스템**: AI-Civil-Affairs-Systems  
> **버전**: v1.0.0  
> **최종 수정**: 2026-06-19

---

## 1. 서비스 개요

온디바이스(로컬) 환경에서 운영되는 민원 검색/QA 시스템이다.  
주요 구성 요소:

| 구성 요소 | 역할 | 기본 포트 |
|-----------|------|-----------|
| FastAPI (Uvicorn) | REST API 서버 | 8000 |
| Ollama | 로컬 LLM 서빙 | 11434 |
| ChromaDB | 벡터 DB (임베딩 저장) | 내장 (파일 기반) |
| Next.js Workbench | 프론트엔드 UI | 3000 |

---

## 2. 서비스 시작

### 2.1 Ollama 시작

```bash
ollama serve
# 모델이 없으면 pull 필요:
# ollama pull exaone3.5:7.8b
```

### 2.2 FastAPI 시작

```bash
cd AI-Civil-Affairs-Systems
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
python scripts/run_api.py
```

### 2.3 Next.js 프론트엔드 시작

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

---

## 3. 헬스체크

서비스 정상 동작 여부를 확인하는 엔드포인트:

| 엔드포인트 | 기대 응답 | 용도 |
|-----------|-----------|------|
| `GET /api/v1/health` | `{"status": "ok", ...}` | API 서버 헬스체크 |
| `GET /health` | `{"status": "ok", ...}` | 레거시 호환 헬스체크 |
| `GET /metrics` | Prometheus 포맷 텍스트 | 관측성 메트릭 수집 |

### 자동 헬스체크 스크립트

```bash
# 단순 확인
curl -sf http://localhost:8000/api/v1/health || echo "API 서버 비정상"

# Ollama 확인
curl -sf http://localhost:11434/api/tags || echo "Ollama 서버 비정상"
```

---

## 4. 배포 프로세스

### 4.1 Main 브랜치 배포 흐름

```
Feature 브랜치 개발
  → PR 생성 (CI 게이트: 린트 + 단위 테스트 통과 필수)
  → 코드 리뷰 및 승인
  → main 머지
  → deploy-production.yml 자동 실행 (Smoke Check)
  → Discord 알림 (discord-notify.yml)
```

### 4.2 릴리스 태그 생성

```bash
# 최신 main에서 태그 생성
git checkout main
git pull origin main
git tag -a v1.0.0 -m "v1.0.0: 초기 릴리스 - CI/CD, 관측성, 런북 추가"
git push origin v1.0.0
```

GitHub 웹에서 릴리스 노트를 작성하여 변경 사항을 기록한다.

---

## 5. 롤백 계획

### 5.1 즉시 롤백 (Git 기반)

배포 후 장애가 발생하면 이전 안정 태그로 롤백한다:

```bash
# 1. 현재 문제가 되는 커밋 확인
git log --oneline -5

# 2. 이전 안정 릴리스 태그로 롤백
git checkout v1.0.0    # 안정 버전 태그
# 또는 특정 커밋으로 롤백
git revert HEAD --no-edit

# 3. 서비스 재시작
# FastAPI
taskkill /F /IM python.exe  # Windows
python scripts/run_api.py

# 4. 헬스체크 확인
curl -sf http://localhost:8000/api/v1/health
```

### 5.2 롤백 의사결정 기준

| 상황 | 행동 |
|------|------|
| API `/health` 실패 | 즉시 이전 태그로 롤백 |
| 단위 테스트 실패율 > 10% | 머지 전 차단 (CI 게이트) |
| LLM 응답 품질 급격 저하 | Ollama 모델 버전 확인 후 이전 모델로 교체 |
| ChromaDB 인덱스 손상 | `data/chroma_db` 백업으로 교체 후 재시작 |

### 5.3 ChromaDB 데이터 롤백

```bash
# 1. 서비스 정지
# 2. 손상된 DB 백업
mv data/chroma_db data/chroma_db_broken_$(date +%Y%m%d)

# 3. 이전 백업 복원
cp -r data/chroma_db_backup data/chroma_db

# 4. 서비스 재시작
python scripts/run_api.py
```

---

## 6. 관측성 (Observability)

### 6.1 로그

로그 파일 위치:

| 로거 | 파일 경로 | 내용 |
|------|-----------|------|
| API | `logs/api/app.log` | HTTP 요청/응답, 에러 |
| Pipeline | `logs/pipeline/pipeline.log` | 구조화/검색/생성 파이프라인 |
| Evaluation | `logs/evaluation/evaluation.log` | 평가 실행 결과 |

로그 파일은 RotatingFileHandler로 관리되며, 파일당 최대 10MB, 백업 5개까지 보관한다.

### 6.2 메트릭

`/metrics` 엔드포인트에서 Prometheus 포맷으로 다음 메트릭을 노출한다:

| 메트릭 | 타입 | 설명 |
|--------|------|------|
| `http_requests_total` | Counter | HTTP 요청 총 수 (method, path, status 라벨) |
| `http_request_duration_seconds` | Summary | HTTP 요청 처리 시간 |
| `app_info` | Gauge | 애플리케이션 버전 정보 |

### 6.3 대시보드

`configs/grafana_dashboard.json` 파일을 Grafana에 Import하여 다음 패널을 확인할 수 있다:

1. **HTTP Request Rate (QPS)**: 초당 요청 수 추이
2. **Average Latency**: 평균 응답 시간
3. **Error Rate**: 4xx/5xx 에러 비율

---

## 7. 장애 대응 체크리스트

```
□ 1. 헬스체크 실패 확인: curl http://localhost:8000/api/v1/health
□ 2. 로그 확인: logs/api/app.log 마지막 100줄
□ 3. Ollama 상태 확인: curl http://localhost:11434/api/tags
□ 4. 메트릭 확인: curl http://localhost:8000/metrics
□ 5. 문제 원인 판별:
     - API 서버 크래시 → 재시작 또는 롤백
     - Ollama 모델 미로딩 → ollama pull 재실행
     - ChromaDB 오류 → 인덱스 재빌드 또는 백업 복원
□ 6. 롤백 실행 (필요 시): 섹션 5 참조
□ 7. 헬스체크 재확인
□ 8. 팀 Discord 채널에 장애 보고
```

---

## 8. 연락처

| 역할 | 담당 |
|------|------|
| BE1 (팀장) | 데이터 파이프라인, 구조화, 평가, 발표 총괄 |
| FE | Next.js Workbench UI/UX |
| BE2 | 임베딩/벡터DB/검색 |
| BE3 | API/LLM/RAG/성능 안정화 |

---

*이 런북은 프로젝트 변경에 따라 지속적으로 업데이트되어야 합니다.*
