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

## Production Docker Compose

> 문서 정보: 작성자 `Codex — 사용자 승인 반영` · 수정일시
> `2026-08-30 20:50 KST` · 버전 `v2.1` · 열람 대상 `프로젝트 관련자`

Ubuntu 22.04 서버의 `/opt/omf-retrieval`에서 이미 clone한 이 저장소를 서버에서
직접 빌드합니다. PostgreSQL, 기존 model cache와 OMF 원본은
`/home/storage_disk3/omf-retrieval-disk` 아래에 영구 보관합니다. 전체 gate와 중단
조건은 [공유 배포 하네스](.agents/skills/development-workflow/references/deployment-harness.md)를
따릅니다.

현재 calibration code/image packaging과 Compose policy injection은 로컬 독립
검증만 PASS했습니다. `server preflight/build/migration/CUDA calibration/smoke`는
`NOT RUN`이며 외부 listener와 API는 중단 상태입니다. 실제 서버 gate가 검증될
때까지 `192.168.1.185:9090` publish 금지를 유지합니다.

용어상 **기존 재배포(redeploy)**는 active run·embedding·client를 보존한 채 image와
search policy만 갱신하는 경로이고, **최초 bootstrap**은 해당 자산이 없는 환경을
처음 만드는 별도 경로입니다. **내부 검증**은 host port를 열지 않은 one-off API에서
ready와 smoke를 확인하는 단계입니다.

### 기존 공유 환경 재배포

이 경로가 공유 환경의 기본 절차입니다. Active run
`427f2c4a-ab06-486a-9801-4bde3ef17d63`, 문서 158개, section 4,202개, chunk와
embedding 각 5,584개, `local-agent`, 기존 deployment token과 기존 model cache를
보존합니다. `index`와 `client create` 하위 명령은 실행하지 않습니다.

1. 사용자 승인 뒤 read-only preflight와 상태 snapshot을 남깁니다. 서버 SHA, 고정
   OMF commit, GPU 0, 네 count, secret file owner·mode `0600`, PostgreSQL health,
   API와 listener 중단을 확인합니다. 비밀 파일 내용은 읽거나 출력하지 않습니다.
   기존 `.env`에는 `.env.example`의 세 `calibration_pending` search policy placeholder가
   있어야 하며, 실제 보정값은 아래 calibration이 끝나기 전에 넣지 않습니다.

2. 승인 SHA에서 image를 빌드하고 PostgreSQL만 시작합니다. API는 아직 공개하지
   않습니다.

   ```bash
   cd /opt/omf-retrieval
   chmod 600 .env
   docker compose --env-file .env -f compose.production.yaml build api
   docker compose --env-file .env -f compose.production.yaml up -d postgres
   ```

3. Additive migration을 적용한 뒤 active run과 네 count가 snapshot과 같은지 다시
   확인합니다. Downgrade는 하지 않습니다.

   ```bash
   docker compose --env-file .env -f compose.production.yaml run --rm api \
     alembic upgrade head
   ```

4. Preflight에서 확인한 기존 deployment token 파일의 절대경로와 owner UID를 아래
   placeholder에 넣습니다. Token 파일이 regular file이고 symlink가 아니며 mode와
   owner가 preflight 결과와 일치하는지 먼저 검사한 뒤, command-scoped subshell
   안에서만 token을 읽어 보존 embedding의 CUDA raw score를 측정합니다. 다음 명령은
   host port를 열지 않습니다.

   ```bash
   (
     set +x
     set -u
     DEPLOYMENT_TOKEN_FILE='<preflight-confirmed-token-file>'
     DEPLOYMENT_TOKEN_OWNER_UID='<preflight-confirmed-owner-uid>'
     test -n "${DEPLOYMENT_TOKEN_FILE-}" || exit 64
     test -n "${DEPLOYMENT_TOKEN_OWNER_UID-}" || exit 64
     test -f "$DEPLOYMENT_TOKEN_FILE" || exit 64
     test ! -L "$DEPLOYMENT_TOKEN_FILE" || exit 64
     test "$(stat -c '%a' -- "$DEPLOYMENT_TOKEN_FILE")" = '600' || exit 64
     test "$(stat -c '%u' -- "$DEPLOYMENT_TOKEN_FILE")" = "$DEPLOYMENT_TOKEN_OWNER_UID" || exit 64
     OMF_RETRIEVAL_API_TOKEN="$(<"$DEPLOYMENT_TOKEN_FILE")" || exit 64
     test -n "$OMF_RETRIEVAL_API_TOKEN" || exit 64
     export OMF_RETRIEVAL_API_TOKEN
     exec docker compose --env-file .env -f compose.production.yaml run --rm --no-deps \
       -e OMF_RETRIEVAL_API_TOKEN api python scripts/calibrate_search.py
   )
   ```

   Bash의 `set -e` 동작을 보안 보장으로 신뢰하지 않습니다. 각 검사는 명시적
   `|| exit 64` guard를 사용하므로 token 파일 변수가 없거나
   regular-file·symlink·mode·owner·read·빈 값 검사 중 하나라도 실패하면, 호출 문맥과
   무관하게 subshell은 nonzero로 즉시 끝나고 Docker를 호출하지 않습니다. Token 값은
   명령 인자나 parent shell에 들어가지 않습니다. Subshell의 마지막 `exec`가
   `docker compose`이므로 compose exit status가 그대로 반환되며, 정상 종료, 명령 실패
   또는 interrupt 어느 경우에도 token 환경은 subshell 종료와 함께 사라집니다. 이후
   policy 기록·후속 readiness·smoke가 이 token을 암묵적으로 상속하지 않게 하며,
   인증이 필요한 각 호출도 같은 command-scoped subshell 원칙으로 파일을 다시 읽어
   해당 명령에만 전달합니다.

5. Calibration 결과의 byte-exact 값을 `/opt/omf-retrieval/.env`에 기록하고 mode
   `0600`을 유지합니다. 값은 로그나 README에 복사하지 않습니다.

   ```dotenv
   OMF_RETRIEVAL_KEYWORD_SIMILARITY_FLOOR=<calibration keyword_floor>
   OMF_RETRIEVAL_VECTOR_SIMILARITY_FLOOR=<calibration vector_floor>
   OMF_RETRIEVAL_EVIDENCE_FLOOR_STATUS=calibrated
   ```

6. `--service-ports` 없이 one-off API를 시작해 host listener를 닫아 둔 채 인증된
   내부 readiness, search policy 좌표와 6개 smoke를 확인합니다. 정상 5개는 top 5
   직접 근거, 문서에 없는 1개는 `no_evidence`여야 합니다. 401·403·409·422·503과
   로그의 원문·token·DB URL·host path 비노출도 검사하고, 같은 image와 policy로
   내부 restart 후 다시 확인합니다.

7. 위 결과를 별도 검증 Agent가 다시 확인하고 사용자가 공개를 승인한 뒤에만
   publish합니다. 하나라도 실패하면 API-only rollback으로 이전 image와 policy
   환경설정을 복원하며 active run과 embedding은 유지합니다.

   ```bash
   docker compose --env-file .env -f compose.production.yaml up -d api
   ```

### 최초 bootstrap

최초 bootstrap은 기존 재배포가 아니며 별도 계획과 명시적 사용자 승인이 필요합니다.
Active run, model cache와 인증 client의 부재를 read-only로 증명한 경우에만 필요한
항목을 생성합니다. 기존 model cache가 있으면 모델을 다시 준비하지 않고,
기존 deployment token이 있으면 client를 다시 만들거나 token을 재발급하지 않습니다.

별도 승인된 bootstrap 계획에서만 다음 명령을 필요한 항목에 한해 사용합니다.

```bash
omf-retrieval model prepare
omf-retrieval index
omf-retrieval client create deployment-agent
```

Bootstrap으로 초기 자산을 만든 뒤에는 위 기존 공유 환경 재배포 gate로 돌아와 CUDA
policy, 내부 readiness, smoke와 publish 승인을 검증합니다. 실제 `.env`,
`secrets/postgres_password`, `secrets/audit_hmac_key`와 token 값은 저장소나 로그에
기록하지 않습니다.
