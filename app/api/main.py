"""FastAPI 애플리케이션 진입점"""

import threading
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse
from contextlib import asynccontextmanager
from collections import defaultdict

from app.api.error_utils import error_response, make_request_id
from app.core.config import settings
from app.core.logging import api_logger


# ---------------------------------------------------------------------------
# 경량 Prometheus 메트릭 수집기 (외부 의존성 없음)
# ---------------------------------------------------------------------------

class _Metrics:
    """스레드 안전한 in-process 메트릭 저장소."""

    def __init__(self):
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._duration_sum: dict[str, float] = defaultdict(float)
        self._duration_count: dict[str, int] = defaultdict(int)

    def inc(self, method: str, path: str, status: int) -> None:
        key = f'{method}|{path}|{status}'
        with self._lock:
            self._counters[key] += 1

    def observe(self, method: str, path: str, duration: float) -> None:
        key = f'{method}|{path}'
        with self._lock:
            self._duration_sum[key] += duration
            self._duration_count[key] += 1

    def render(self) -> str:
        lines: list[str] = []
        lines.append("# HELP http_requests_total Total HTTP requests")
        lines.append("# TYPE http_requests_total counter")
        with self._lock:
            for key, val in sorted(self._counters.items()):
                method, path, status = key.split("|")
                lines.append(
                    f'http_requests_total{{method="{method}",path="{path}",status="{status}"}} {val}'
                )
            lines.append("")
            lines.append("# HELP http_request_duration_seconds HTTP request duration")
            lines.append("# TYPE http_request_duration_seconds summary")
            for key in sorted(self._duration_sum):
                method, path = key.split("|")
                lines.append(
                    f'http_request_duration_seconds_sum{{method="{method}",path="{path}"}} {self._duration_sum[key]:.6f}'
                )
                lines.append(
                    f'http_request_duration_seconds_count{{method="{method}",path="{path}"}} {self._duration_count[key]}'
                )
        lines.append("")
        lines.append("# HELP app_info Application version info")
        lines.append("# TYPE app_info gauge")
        lines.append(f'app_info{{version="{settings.API_VERSION}"}} 1')
        return "\n".join(lines) + "\n"


metrics = _Metrics()
from app.api.routers import (
    admin_router,
    chroma_debug_router,
    complaint_intelligence_router,
    generation_router,
    retrieval_router,
    structuring_router,
    ui_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """애플리케이션 생명주기 관리"""
    # 시작
    api_logger.info("API 서버 시작")
    api_logger.info(f"Ollama: {settings.OLLAMA_BASE_URL}")
    api_logger.info(f"ChromaDB: {settings.CHROMA_DB_PATH}")
    yield
    # 종료
    api_logger.info("API 서버 종료")


# FastAPI 애플리케이션 생성
app = FastAPI(
    title=settings.API_TITLE,
    description=settings.API_DESCRIPTION,
    version=settings.API_VERSION,
    lifespan=lifespan,
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 라우터 등록
app.include_router(retrieval_router)
app.include_router(structuring_router)
app.include_router(generation_router)
app.include_router(chroma_debug_router)
app.include_router(ui_router)
app.include_router(complaint_intelligence_router)
app.include_router(admin_router)


# ---------------------------------------------------------------------------
# 메트릭 수집 미들웨어
# ---------------------------------------------------------------------------

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start
    path = request.url.path
    metrics.inc(request.method, path, response.status_code)
    metrics.observe(request.method, path, duration)
    return response


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    """Prometheus 호환 메트릭 엔드포인트"""
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")



@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request, exc: RequestValidationError):
    """FastAPI 기본 422를 Week2 표준 에러 래퍼로 통일한다."""
    is_search_filter_error = str(request.url.path) == "/api/v1/search" and any(
        isinstance(err.get("loc"), (list, tuple)) and "filters" in err.get("loc", ())
        for err in exc.errors()
    )

    if is_search_filter_error:
        return error_response(
            request_id=make_request_id(),
            error_code="FILTER_INVALID",
            message="검색 필터 형식 또는 값이 올바르지 않습니다.",
            status_code=400,
            retryable=False,
            details={
                "path": str(request.url.path),
                "errors": exc.errors(),
            },
        )

    return error_response(
        request_id=make_request_id(),
        error_code="VALIDATION_ERROR",
        message="요청 본문 형식이 올바르지 않습니다.",
        status_code=422,
        retryable=False,
        details={
            "path": str(request.url.path),
            "errors": exc.errors(),
        },
    )


# 헬스 체크 엔드포인트
@app.get("/health")
async def health_check():
    """API 상태 확인"""
    return {
        "status": "ok",
        "service": settings.API_TITLE,
        "version": settings.API_VERSION,
    }


# 버전 헬스 체크 엔드포인트 (contract alias)
@app.get("/api/v1/health")
async def health_check_v1():
    """API v1 상태 확인"""
    return await health_check()


# 루트 엔드포인트
@app.get("/")
async def root():
    """API 정보"""
    return {
        "title": settings.API_TITLE,
        "description": settings.API_DESCRIPTION,
        "version": settings.API_VERSION,
        "docs_url": "/docs",
        "endpoints": {
            "health": "/api/v1/health",
            "health_legacy": "/health",
            "ingest": "/api/v1/ingest",
            "structure": "/api/v1/structure",
            "index": "/api/v1/index",
            "search": "/api/v1/search",
            "qa": "/api/v1/qa",
            "qa_stream": "/api/v1/qa/stream",
            "ui_cases": "/api/v1/ui/cases",
            "complaint_issue_alerts": "/complaint-intelligence/issue-alerts",
            "complaint_public_insights": "/complaint-intelligence/public-insights",
            "complaint_dashboard": "/complaint-intelligence/dashboard",
            "complaint_run_analysis": "/complaint-intelligence/run-analysis",
            "complaint_dashboard_run_analysis": "/complaint-intelligence/dashboard/run-analysis",
            "complaint_public_insight_run_analysis": "/complaint-intelligence/public-insights/run-analysis",
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=settings.API_HOST,
        port=settings.API_PORT,
        log_level=settings.LOG_LEVEL.lower(),
    )
