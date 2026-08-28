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
> `2026-08-28 10:07 KST` · 버전 `v2.0` · 열람 대상 `프로젝트 관련자`

Ubuntu 22.04 서버의 `/opt/omf-retrieval`에서 이미 clone한 이 저장소를 서버에서
직접 빌드합니다. API는 `192.168.1.185:9090`에만 publish하며 PostgreSQL, 모델
cache와 OMF 원본은 `/home/storage_disk3/omf-retrieval-disk` 아래에 영구
보관합니다. NVIDIA Container Toolkit, 내부망 UFW 규칙과 두 private 저장소의 Git
인증은 다음 서버 적용 단계에서 확인합니다.

### 패키지와 비밀 준비

1. 서버 전용 환경 파일과 비밀 파일을 준비합니다.

   ```bash
   cd /opt/omf-retrieval
   cp .env.example .env
   chmod 600 .env
   install -d -m 700 secrets
   install -m 600 /dev/null secrets/postgres_password
   umask 077
   openssl rand 64 > secrets/audit_hmac_key
   chmod 600 secrets/postgres_password secrets/audit_hmac_key
   ```

   `.env`의 UID·GID는 배포 계정의 `id -u`, `id -g` 값으로 바꿉니다. URL-safe
   PostgreSQL 비밀번호는 터미널에 출력하지 않는 편집기나 비밀번호 관리자를 통해
   `secrets/postgres_password`에 기록합니다. `.env`의
   `OMF_RETRIEVAL_DATABASE_URL` 비밀번호도 이 파일 내용과 같아야 합니다. 실제
   `.env`, 비밀 파일과 이후 발급되는 client token은 저장소에 커밋하지 않습니다.

2. 영구 디렉터리를 준비하고 서버에서 애플리케이션 이미지를 빌드합니다.

   ```bash
   install -d /home/storage_disk3/omf-retrieval-disk/postgres
   install -d /home/storage_disk3/omf-retrieval-disk/model-cache
   install -d /home/storage_disk3/omf-retrieval-disk/source
   docker compose --env-file .env -f compose.production.yaml build api
   ```

### 소스 사전 준비

PostgreSQL과 API를 시작하기 전에 OMF private 저장소를 clone하고 고정 commit을
깨끗한 detached HEAD로 둡니다.

```bash
git clone '<OMF_PRIVATE_GIT_URL>' /home/storage_disk3/omf-retrieval-disk/source/omf
export OMF_SOURCE_REPO_DIR='/home/storage_disk3/omf-retrieval-disk/source/omf'
git -C "$OMF_SOURCE_REPO_DIR" checkout --detach a8f46f23cd3fb9c5f7042e987dff8103d23f0fa2
test -z "$(git -C "$OMF_SOURCE_REPO_DIR" status --porcelain)"
```

### 런타임 초기화 및 기동

1. PostgreSQL을 시작한 뒤 migration을 적용합니다.

   ```bash
   docker compose --env-file .env -f compose.production.yaml up -d postgres
   docker compose --env-file .env -f compose.production.yaml run --rm api alembic upgrade head
   ```

2. 고정 모델을 준비합니다.

   ```bash
   docker compose --env-file .env -f compose.production.yaml run --rm api omf-retrieval model prepare
   ```

3. 사전 준비한 OMF 현재본을 색인·활성화합니다.

   ```bash
   docker compose --env-file .env -f compose.production.yaml run --rm api omf-retrieval index
   ```

4. 인증 client를 만들고 출력된 token은 즉시 비밀번호 관리자에 보관합니다.

   ```bash
   docker compose --env-file .env -f compose.production.yaml run --rm api omf-retrieval client create deployment-agent
   ```

5. API를 시작하고 token을 명령 인자가 아닌 환경변수로 전달해 검색합니다.

   ```bash
   docker compose --env-file .env -f compose.production.yaml up -d api
   export OMF_RETRIEVAL_API_TOKEN='<one-time token from client create>'
   docker compose --env-file .env -f compose.production.yaml run --rm --no-deps \
     -e OMF_RETRIEVAL_API_TOKEN -e OMF_RETRIEVAL_API_URL=http://api:8000 \
     api omf-retrieval search '긴급 W/O 운영 정책 원문을 찾아줘' --limit 5
   unset OMF_RETRIEVAL_API_TOKEN
   ```
