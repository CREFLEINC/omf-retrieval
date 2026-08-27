# OMF Retrieval

고정 OMF 현재본에서 재현 가능한 근거를 반환하는 인증 검색 MVP입니다. Python
3.12와 현재 lockfile을 사용합니다.

## 로컬 MVP 실행

아래 순서만 사용합니다. 데이터베이스 URL과 모델 cache는 로컬 전용 값으로 바꿀 수
있지만 저장소에 비밀값을 기록하지 않습니다.

1. 로컬 PostgreSQL을 시작하고 애플리케이션 연결을 지정합니다.

   ```bash
   docker compose -f compose.test.yaml up -d db
   export OMF_RETRIEVAL_DATABASE_URL='postgresql+psycopg://omf_retrieval_test:omf_retrieval_test@127.0.0.1:55432/omf_retrieval_test'
   ```

2. 고정 revision의 Qwen 모델을 준비합니다.

   ```bash
   export OMF_RETRIEVAL_EMBEDDING_CACHE_DIR='/absolute/private/model-cache'
   uv run omf-retrieval model prepare
   ```

3. 데이터베이스 migration을 적용합니다.

   ```bash
   uv run alembic upgrade head
   ```

4. OMF 원본의 고정 commit을 detached worktree로 준비한 뒤 보정 전 색인을
   활성화합니다. 이 상태의 공개 search와 ready는 안전하게 실패합니다.

   ```bash
   export OMF_SOURCE_REPOSITORY='/absolute/path/to/omf'
   export OMF_FIXED_SOURCE_DIR='/absolute/private/omf-fixed-source'
   git -C "$OMF_SOURCE_REPOSITORY" worktree add --detach "$OMF_FIXED_SOURCE_DIR" a8f46f23cd3fb9c5f7042e987dff8103d23f0fa2
   export OMF_RETRIEVAL_SOURCE_REPO="$OMF_FIXED_SOURCE_DIR"
   export OMF_RETRIEVAL_KEYWORD_SIMILARITY_FLOOR='1.0'
   export OMF_RETRIEVAL_VECTOR_SIMILARITY_FLOOR='1.0'
   export OMF_RETRIEVAL_EVIDENCE_FLOOR_STATUS='calibration_pending'
   uv run omf-retrieval index
   ```

5. `omf` grant가 있는 client를 만듭니다. Token은 이 출력에서 한 번만 확인할 수
   있으며 파일이나 명령 인자로 저장하지 않습니다.

   ```bash
   uv run omf-retrieval client create local-agent
   export OMF_RETRIEVAL_API_TOKEN='<one-time token from client create>'
   ```

6. 내부 smoke harness로 점수 분리를 확인한 뒤 독립 검증에서 확정한 byte-exact
   하한선을 적용해 다시 색인합니다. 확정 margin은 `0.16857380984674064`입니다.

   ```bash
   uv run python scripts/calibrate_search.py
   export OMF_RETRIEVAL_KEYWORD_SIMILARITY_FLOOR='0.03658536400000001'
   export OMF_RETRIEVAL_VECTOR_SIMILARITY_FLOOR='0.48344050397156374'
   export OMF_RETRIEVAL_EVIDENCE_FLOOR_STATUS='calibrated'
   uv run omf-retrieval index
   ```

7. 로컬 API를 실행합니다.

   ```bash
   uv run omf-retrieval serve --host 127.0.0.1 --port 8000
   ```

8. 같은 환경을 이어받은 다른 shell에서 검색합니다.

   ```bash
   uv run omf-retrieval search '긴급 W/O 운영 정책 원문을 찾아줘' --limit 5
   ```
