# ShopFinder

조건 기반 멀티-쇼핑몰 검색 어시스턴트 — 라즈베리파이 4 (4GB)에서 Docker Compose로 동작하는 셀프호스팅 웹 도구.

자연어 한 줄로 색상·사이즈·소재·핏·가격 조건을 입력하면, 사전 설정된 5개 쇼핑몰(쿠팡, 네이버 쇼핑, 11번가, G마켓, 무신사)을 병렬로 검색해 SSE 스트리밍으로 결과를 보여준다. OpenAI 호환 LLM 엔드포인트(기본 가정: LM Studio 로컬)를 연결하면 조건 파싱·검색어 최적화·매칭 점수·추천 코멘트가 활성화된다.

전체 사양은 [PRD.md](PRD.md) 참고.

## Run with Docker

```bash
# Build and start (one command)
docker compose up -d

# Health check
curl http://localhost:8080/healthz

# Logs
docker compose logs -f app

# Stop
docker compose down
```

The SQLite database lives under `./data/` (bind-mounted) so it survives container restarts. Override env vars via a `.env` file (see `.env.example`).

### Multi-arch build (for pushing a Pi-friendly image)

```bash
docker buildx build \
    --platform linux/amd64,linux/arm64 \
    -t shopfinder:latest \
    -f docker/Dockerfile \
    .
```

## Status

Phase 1 in progress. 구현 작업은 GitHub Issues로 추적.

## License

Personal use.
