# OMF Retrieval MVP 상세 구현 계획

> **문서 정본:** 이 Markdown 파일이 실행 계획의 정본이다. 같은 경로의 HTML은 프로젝트 관련자를 위한 파생 열람본이며, 내용이 충돌하면 이 파일을 우선한다.
>
> **실행자 필수:** 구현을 시작할 때 <code>superpowers:executing-plans</code>, 각 기능을 작성할 때 <code>superpowers:test-driven-development</code>, 완료를 주장하기 전에 <code>superpowers:verification-before-completion</code>을 사용한다. Python 코드는 <code>crefle-agent-skills:coding-rules</code>를 따른다.

| 문서 항목 | 값 |
|---|---|
| 작성자 | CREFLE Inc. CTO 김정규 |
| 작성일시 | 2026-08-13 15:50 KST |
| 문서 버전 | 1.0 |
| 열람 대상 | 프로젝트 관련자 |
| 기준 설계 | <code>docs/design/2026-08-13-omf-retrieval-mvp-system-design.html</code> v1.0 |
| 상태 | 최종 승인 · 개발 착수 가능 |

## 1. 목표

OMF 문서의 고정 Git commit에서 승인된 Markdown만 색인하고, 자연어 질의에 대해 관련 원문을 다음 재현 정보와 함께 반환하는 공유 검색 서비스를 구현한다.

- 직접 인용과 1-based inclusive 원문 행 범위
- 저장소 기준 source path와 제목 계층
- 색인 대상 commit SHA와 UTF-8 원문 SHA-256
- 문서 날짜·버전·결정 상태·소유 영역
- 키워드·벡터 개별 순위와 RRF 점수
- 같은 내용의 모든 원본 경로와 명시적으로 등록된 잠재 충돌

MVP는 근거 검색까지만 담당한다. LLM 요약·분석, MCP, Codex function tool, 리랭커, ANN, 자동 백업은 구현하지 않는다.

## 2. 구현 원칙과 정지 조건

1. 모든 동작 변경은 실패하는 테스트로 시작한다.
2. 한 번에 하나의 논리적 기능만 구현하고 테스트가 통과한 상태에서 Conventional Commit을 만든다.
3. OMF 저장소는 읽기 전용이다. 테스트는 임시 Git 저장소나 fixture를 사용한다.
4. unit test는 모델 다운로드·GPU·외부 네트워크를 요구하지 않는다.
5. PostgreSQL 동작은 실제 <code>pgvector/pgvector</code> Compose 컨테이너에서 검증한다.
6. 서버 GPU E2E와 운영 배포는 로컬·통합 테스트가 모두 통과하고 사용자가 해당 단계를 승인한 뒤 실행한다.
7. 골드 질문과 정답 근거는 사람이 검토해야 한다. 30개 골드셋 승인 전에는 품질 게이트를 통과했다고 주장하지 않는다.
8. 서비스 FQDN, Gateway 사설 IP, 검색 서버 bind IP·port, OMF Git remote·인증 방식, 초기 API client가 없으면 운영 배포 직전에 정지한다.
9. 새로운 런타임 의존성, API endpoint, DB table, 검색 가중치, 권한 범위를 이 계획 밖에서 추가해야 하면 구현을 멈추고 사용자 승인을 받는다.
10. 아래 번호가 붙은 실행 항목을 체크 단위로 사용한다. 한 항목의 red/green 확인이 5분을 넘길 것으로 보이면 구현 전에 더 작은 test case와 함수 단위로 나눈다.

## 3. 확정 구현 기준

### 3.1 Python과 의존성

- Python 3.12
- <code>pyproject.toml</code> + <code>uv.lock</code>, uv 0.12.3
- PyTorch 2.11.0을 직접 의존성으로 선언하고, Linux x86_64만 공식 <code>cu128</code> index를 명시적으로 사용한다. macOS는 PyPI의 CPU/MPS build를 사용한다.
- 운영 직접 의존성 기준:
  - FastAPI 0.141.1
  - Uvicorn 0.52.2 최소 패키지
  - Typer 0.27.1
  - Pydantic Settings 2.15.0
  - SQLAlchemy 2.0.52 동기 API
  - Alembic 1.19.1
  - Psycopg 3.3.4 binary extra
  - pgvector Python adapter 0.5.0
  - Sentence Transformers 5.7.0
  - Transformers 5.15.0
  - markdown-it-py 4.2.0
  - HTTPX 0.28.1
- 개발 직접 의존성 기준:
  - pytest 9.1.1
  - pytest-cov 7.1.0
  - Ruff 0.16.2
- <code>pyproject.toml</code>에는 호환 가능한 minor 상한을 기록하고, 실제 설치 버전과 transitive dependency는 <code>uv.lock</code>으로 고정한다. PyTorch index는 <code>explicit=true</code>로 두어 다른 package가 CUDA index에서 해결되지 않게 한다.
- JSON 평가셋, 표준 <code>logging</code>, <code>hashlib</code>, <code>hmac</code>, <code>secrets</code>를 사용한다. PyYAML, structlog, 비동기 DB driver, Testcontainers, Flash Attention은 추가하지 않는다.

### 3.2 불변 외부 artifact

| Artifact | 고정값 |
|---|---|
| Application base | <code>pytorch/pytorch:2.11.0-cuda12.8-cudnn9-runtime@sha256:eee11b3b3872a8c838e35ef48f08b2d5def2080902c7f666831310ca1a0ef2be</code> |
| PostgreSQL | <code>pgvector/pgvector:0.8.6-pg18-trixie@sha256:1963bc48febf543433baa1ce3edcc6cc08154de722e22495f86681cc9a849026</code> |
| Embedding model | <code>Qwen/Qwen3-Embedding-0.6B</code> |
| Model revision | <code>97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3</code> |
| Embedding dimension | 1024 |
| Query instruction | <code>Instruct: Retrieve passages from Korean internal software design documents that provide the requirements, policies, API definitions, data models, or decisions needed to answer the query.\nQuery: {query}</code> |

### 3.3 보수적 문서 메타데이터 판정

문서 전체의 의미를 추론하지 않는다. 다음 명시적 신호만 사용한다.

- 날짜: 상단 40행의 <code>작성일:</code>, 없으면 파일명 선두 <code>YYYY-MM-DD</code>, 없으면 <code>null</code>
- 버전: 상단 40행의 <code>vN[.N...]</code>, 없으면 파일명 버전, 없으면 <code>null</code>
- 과거본: source path에 <code>/versions/</code>가 있는 문서만 <code>historical</code>; 나머지는 <code>current</code>
- 결정 상태:
- <code>confirmed</code>: 파일명 <code>확정기록</code>·<code>결정서</code>, 상단 메타데이터의 <code>[확정]</code>·<code>확정</code>, 또는 식별 표의 <code>신뢰도=확정</code>
  - <code>draft</code>: 파일명·상단 제목의 <code>초안</code>·<code>제안안</code>·<code>가설</code>·<code>진행메모</code>
  - 그 외 <code>unknown</code>
- 소유 영역: <code>uiux/</code>는 <code>uiux</code>, <code>docs/</code>는 <code>docs</code>
- <code>supersedes</code>와 <code>potential_conflict</code>는 사람이 관리하는 <code>config/source_profiles/omf-relations.json</code>에 원문 path·행 근거가 있을 때만 저장한다.
- 같은 문서 일부에 등장하는 “철회”, “폐기”, “가설”이라는 단어만으로 문서 전체 상태를 변경하지 않는다.
- 상태 marker는 구조화된 metadata line·표 cell·파일명에서만 읽고, <code>미확정</code>·<code>불확정</code>·<code>확정 전</code>처럼 부정된 표현은 <code>confirmed</code>로 판정하지 않는다.

## 4. 목표 저장소 구조

~~~text
.
├── pyproject.toml
├── uv.lock
├── .python-version
├── README.md
├── alembic.ini
├── compose.yaml
├── compose.test.yaml
├── Dockerfile
├── config/
│   └── source_profiles/
│       ├── omf.json
│       └── omf-relations.json
├── migrations/
│   ├── env.py
│   └── versions/0001_initial_schema.py
├── src/omf_retrieval/
│   ├── settings.py
│   ├── domain/
│   │   ├── enums.py
│   │   ├── errors.py
│   │   ├── models.py
│   │   └── policies.py
│   ├── application/
│   │   ├── search/{ports.py,rrf.py,evidence.py,service.py}
│   │   ├── indexing/{ports.py,hashing.py,metadata.py,service.py,activation.py}
│   │   ├── evaluation/{dataset.py,metrics.py,runner.py}
│   │   └── admin/{tokens.py,service.py}
│   ├── interfaces/
│   │   ├── api/{app.py,dependencies.py,errors.py,schemas.py,routes/}
│   │   └── cli/{main.py,search.py,indexing.py,evaluation.py,admin.py,model.py}
│   └── infrastructure/
│       ├── database/{base.py,models.py,session.py,repositories.py,search.py}
│       ├── embedding/{provider.py,sentence_transformer.py}
│       ├── source/{profiles.py,git_archive.py,markdown.py,chunker.py}
│       └── observability/{logging.py,timing.py}
├── evaluations/
│   ├── gold/{schema.json,omf-retrieval-v1.json}
│   └── results/.gitkeep
├── ops/
│   ├── build-and-push.sh
│   ├── deploy.sh
│   ├── prepare-host.sh
│   └── smoke-test.sh
└── tests/
    ├── fixtures/
    ├── unit/
    ├── integration/
    ├── contract/
    ├── performance/
    └── server/
~~~

## 5. 단계와 사용자 확인 지점

| 단계 | 범위 | 완료 조건 | 사용자 확인 |
|---|---|---|---|
| A · 기반 | 작업 1~6 | 프로젝트·도메인·DB·source·parser·chunk unit/integration 통과 | 결과 공유 후 확인 |
| B · 검색 수직 절편 | 작업 7~12 | fake embedding 색인부터 인증된 API·CLI 검색까지 통과 | 결과 공유 후 확인 |
| C · 평가·컨테이너 | 작업 13~15 | 평가기·로그·Docker/Compose와 로컬 계약 통과 | 결과 공유 후 확인 |
| D · 운영 검증 | 작업 16~17 | 30개 골드 승인, server GPU E2E, 품질·성능 기준 통과 | 각 외부 변경 전 확인 |
| E · 인수 | 작업 18 | 전체 검증 증거와 운영 인계 완료 | 최종 승인 |

---

## 작업 1. Python 프로젝트와 품질 게이트 구성

**파일**

- 생성: <code>pyproject.toml</code>
- 생성: <code>uv.lock</code>
- 생성: <code>.python-version</code>
- 생성: <code>README.md</code>
- 생성: <code>src/omf_retrieval/__init__.py</code>
- 생성: 설계의 목표 package별 <code>__init__.py</code>
- 테스트: <code>tests/unit/test_package.py</code>

**1.1 실패하는 import test 작성**

~~~python
def test_package_exposes_version() -> None:
    from omf_retrieval import __version__

    assert __version__ == "0.1.0"
~~~

**1.2 실패 확인**

실행: <code>uv run pytest tests/unit/test_package.py -q</code>

예상: package가 없어 <code>ModuleNotFoundError</code>.

**1.3 최소 package와 project metadata 작성**

- build backend는 표준 wheel을 만들 수 있는 uv build backend를 사용한다.
- script entry point는 <code>omf-retrieval = omf_retrieval.interfaces.cli.main:app</code> 하나만 둔다.
- dependency group <code>dev</code>에 pytest, pytest-cov, Ruff를 둔다.
- Python 범위는 <code>&gt;=3.12,&lt;3.13</code>으로 고정한다.
- <code>torch==2.11.0</code>을 직접 선언하고 Linux x86_64 marker에는 explicit <code>https://download.pytorch.org/whl/cu128</code> source를 연결한다.

**1.4 잠금 파일 생성 및 재현 확인**

실행:

~~~bash
uv lock
uv sync --frozen
uv run pytest tests/unit/test_package.py -q
~~~

예상: 1 passed.

**1.5 Ruff gate 구성**

- line length 88
- import sorting, unused import, common bug rules 활성화
- <code>ruff format --check</code>와 <code>ruff check</code> 모두 통과

**1.6 commit**

~~~bash
git add pyproject.toml uv.lock .python-version README.md src tests/unit/test_package.py
git commit -m "build(project): Python 애플리케이션 기반 구성"
~~~

---

## 작업 2. 설정, domain model, hash 규약 정의

**파일**

- 생성: <code>src/omf_retrieval/settings.py</code>
- 생성: <code>src/omf_retrieval/domain/enums.py</code>
- 생성: <code>src/omf_retrieval/domain/errors.py</code>
- 생성: <code>src/omf_retrieval/domain/models.py</code>
- 생성: <code>src/omf_retrieval/domain/policies.py</code>
- 생성: <code>src/omf_retrieval/application/indexing/hashing.py</code>
- 테스트: <code>tests/unit/domain/test_policies.py</code>
- 테스트: <code>tests/unit/indexing/test_hashing.py</code>
- 테스트: <code>tests/unit/test_settings.py</code>

**2.1 config hash 실패 test 작성**

~~~python
def test_config_hash_is_stable_across_key_order() -> None:
    assert config_hash({"b": 2, "a": 1}) == config_hash({"a": 1, "b": 2})


def test_content_hash_preserves_exact_utf8_bytes() -> None:
    assert content_hash("문서\n".encode()) != content_hash("문서".encode())
~~~

**2.2 실패 확인**

실행: <code>uv run pytest tests/unit/indexing/test_hashing.py -q</code>

**2.3 canonical JSON과 SHA-256 구현**

~~~python
def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def config_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()
~~~

- content hash는 Git archive에서 읽은 원본 UTF-8 bytes에 직접 적용한다.
- config hash는 canonical JSON bytes에 적용한다.
- chunk hash 입력에는 parser version, chunk config hash, heading path, 1-based line range, raw text, search text를 포함한다.

**2.4 domain enum과 value object test 작성**

- <code>VersionScope</code>: current, historical, all
- <code>DecisionState</code>: confirmed, draft, unknown
- <code>OwnerDomain</code>: docs, uiux
- <code>IndexRunStatus</code>: building, ready, active, previous, failed
- <code>RelationType</code>: supersedes, potential_conflict
- <code>SearchStatus</code>: ok, no_evidence

공개 model은 frozen dataclass 또는 Pydantic과 무관한 typed dataclass로 두어 domain이 framework에 의존하지 않게 한다.

**2.5 production 설정 test 작성**

~~~python
def test_production_requires_gpu_zero_and_secret_files(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Settings(
            environment="production",
            embedding_device="cpu",
            postgres_password_file=tmp_path / "missing",
        )
~~~

- 설정은 environment variable과 <code>*_FILE</code> secret path를 검증한다.
- API token 원문, DB password, audit HMAC key는 settings <code>repr</code>에 나오지 않는다.
- model name, revision, dimension, query instruction과 검색 기본값은 설정에서 읽는다.

**2.6 전체 unit test 및 commit**

~~~bash
uv run pytest tests/unit/domain tests/unit/indexing/test_hashing.py tests/unit/test_settings.py -q
uv run ruff check .
git add src/omf_retrieval tests
git commit -m "feat(domain): 검색과 색인 핵심 계약 정의"
~~~

---

## 작업 3. PostgreSQL test 환경과 초기 schema

**파일**

- 생성: <code>compose.test.yaml</code>
- 생성: <code>alembic.ini</code>
- 생성: <code>migrations/env.py</code>
- 생성: <code>migrations/script.py.mako</code>
- 생성: <code>migrations/versions/0001_initial_schema.py</code>
- 생성: <code>src/omf_retrieval/infrastructure/database/base.py</code>
- 생성: <code>src/omf_retrieval/infrastructure/database/models.py</code>
- 생성: <code>src/omf_retrieval/infrastructure/database/session.py</code>
- 테스트: <code>tests/integration/database/test_migrations.py</code>
- 테스트: <code>tests/integration/database/test_constraints.py</code>

**3.1 실제 extension을 요구하는 실패 test 작성**

~~~python
def test_required_extensions_are_installed(connection: Connection) -> None:
    versions = dict(
        connection.execute(
            text(
                "select extname, extversion from pg_extension "
                "where extname in ('vector', 'pg_trgm')"
            )
        )
    )
    assert set(versions) == {"vector", "pg_trgm"}
~~~

**3.2 test DB 기동과 실패 확인**

~~~bash
docker compose -f compose.test.yaml up -d db
uv run pytest tests/integration/database/test_migrations.py -q
~~~

예상: migration과 table이 없어 실패.

**3.3 초기 migration 작성**

다음 13개 table을 한 migration에 만든다.

| Table | 필수 column·제약 |
|---|---|
| <code>source_profiles</code> | UUID PK, unique source_key, include/exclude JSONB, nullable active_index_run_id |
| <code>index_configs</code> | UUID PK, unique config_hash, parser/chunk/tokenizer/embedding/RRF JSONB snapshot |
| <code>index_runs</code> | source/config FK, commit_sha, status check, timestamps, stats JSONB, sanitized failure |
| <code>document_contents</code> | unique content_hash, UTF-8 content, byte_size |
| <code>document_occurrences</code> | run/content FK, source_path, version scope, date/version/state/owner, unique run+path |
| <code>document_parses</code> | content FK, parser version, chunk config hash, unique content+config |
| <code>sections</code> | parse FK, self parent FK, ordinal/level/heading/path, body, inclusive lines |
| <code>chunks</code> | section FK, ordinal, raw/search text, token count, inclusive lines, chunk hash |
| <code>chunk_embeddings</code> | chunk FK, embedding config hash, model/revision/dimension, unbounded vector, status |
| <code>document_relations</code> | run/from/to occurrence FK, relation type, explicit evidence path+lines |
| <code>api_clients</code>, <code>client_source_grants</code>, <code>search_audit_events</code> | token 원문과 query 원문을 저장하지 않는 인증·감사 column |

인증 관련 3개 table은 물리적으로 같은 migration에 포함하되 application 기능은 작업 10에서 작성한다.

**3.4 index와 DB invariant 작성**

~~~sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX ix_chunks_search_text_trgm
    ON chunks USING gin (search_text gin_trgm_ops);
~~~

- ANN index는 만들지 않는다.
- <code>vector_dims(embedding) = dimension</code> check를 둔다.
- <code>(chunk_id, embedding_config_hash)</code>, <code>(run_id, source_path)</code>, grant 조합을 unique로 둔다.
- circular active run FK는 table 생성 후 추가하고 삭제 정책은 restrict로 둔다.

**3.5 migration·constraint test 통과**

~~~bash
uv run alembic upgrade head
uv run pytest tests/integration/database -q
uv run alembic downgrade base
uv run alembic upgrade head
~~~

**3.6 commit**

~~~bash
git add compose.test.yaml alembic.ini migrations src/omf_retrieval/infrastructure/database tests/integration/database
git commit -m "feat(database): 검색 데이터 모델과 마이그레이션 추가"
~~~

---

## 작업 4. OMF source profile과 안전한 Git snapshot

**파일**

- 생성: <code>config/source_profiles/omf.json</code>
- 생성: <code>config/source_profiles/omf-relations.json</code>
- 생성: <code>src/omf_retrieval/application/indexing/ports.py</code>
- 생성: <code>src/omf_retrieval/infrastructure/source/profiles.py</code>
- 생성: <code>src/omf_retrieval/infrastructure/source/git_archive.py</code>
- 테스트: <code>tests/unit/source/test_profiles.py</code>
- 테스트: <code>tests/integration/source/test_git_archive.py</code>

**4.1 include/exclude 실패 test 작성**

~~~python
@pytest.mark.parametrize(
    ("path", "included"),
    [
        ("docs/research/a.md", True),
        ("docs/planning/versions/v1.md", True),
        ("uiux/spec.md", True),
        ("docs/raw/secret.md", False),
        ("docs/_workspace/note.md", False),
        ("uiux/CLAUDE.md", False),
        ("uiux/image.png", False),
    ],
)
def test_omf_profile_filters_paths(path: str, included: bool) -> None:
    assert omf_profile().includes(path) is included
~~~

**4.2 profile 구현**

- include: <code>docs/research/**/*.md</code>, <code>docs/planning/**/*.md</code>, <code>uiux/**/*.md</code>
- exclude: <code>docs/raw/**</code>, <code>docs/_workspace/**</code>, <code>**/AGENTS.md</code>, <code>**/CLAUDE.md</code>, <code>**/.agents/**</code>, <code>**/.claude/**</code>, <code>**/_workspace/**</code>, 생성물·임시 file pattern
- path는 POSIX 상대 경로로 정규화하고 <code>..</code>, 절대 경로, symlink 탈출을 거부한다.

**4.3 임시 Git 저장소 기반 실패 test 작성**

- dirty worktree는 거부
- 존재하지 않는 commit은 거부
- 지정 commit 내용만 archive
- source repository에 쓰기 없음

**4.4 GitArchiveSnapshotProvider 구현**

~~~python
class SourceSnapshotProvider(Protocol):
    def snapshot(self, repo: Path, commit_sha: str) -> SourceSnapshot: ...
~~~

- <code>git status --porcelain</code>가 비어 있어야 한다.
- <code>git rev-parse --verify commit^{commit}</code>으로 full SHA를 얻는다.
- <code>git archive</code> 출력은 private temporary directory에서만 푼다.
- archive member의 absolute path, <code>..</code>, symlink/hardlink를 거부한다.
- temporary directory는 성공·실패 모두 정리한다.

**4.5 test와 commit**

~~~bash
uv run pytest tests/unit/source tests/integration/source -q
git add config src/omf_retrieval/application/indexing/ports.py src/omf_retrieval/infrastructure/source tests
git commit -m "feat(source): OMF Git 스냅샷과 파일 선별 구현"
~~~

---

## 작업 5. Markdown 계층·행 범위·메타데이터 parser

**파일**

- 생성: <code>src/omf_retrieval/application/indexing/metadata.py</code>
- 생성: <code>src/omf_retrieval/infrastructure/source/markdown.py</code>
- 생성: <code>tests/fixtures/markdown/</code>의 표·목록·인용·중복 제목 fixture
- 테스트: <code>tests/unit/source/test_markdown_parser.py</code>
- 테스트: <code>tests/unit/indexing/test_metadata.py</code>

**5.1 1-based inclusive line map 실패 test**

~~~python
def test_parser_preserves_heading_hierarchy_and_lines() -> None:
    parsed = parser.parse("# A\nintro\n\n## B\nbody\n")

    assert parsed.sections[1].heading_path == ("A", "B")
    assert (parsed.sections[1].line_start, parsed.sections[1].line_end) == (4, 5)
~~~

**5.2 block 구조 실패 test**

- fenced code 안의 <code>#</code>를 heading으로 보지 않음
- table row, list item, block quote의 source map 보존
- heading이 없는 preamble을 synthetic root section에 포함
- 같은 heading text가 반복되어도 ordinal과 line으로 구분

**5.3 markdown-it-py token map 기반 parser 구현**

- block token의 0-based half-open map을 1-based inclusive로 변환한다.
- section tree와 heading path를 stack으로 만든다.
- body raw text는 원본 line slice로 얻어 표·공백·인용 표현을 바꾸지 않는다.
- parser version 상수를 index config에 기록한다.

**5.4 보수적 metadata extractor 실패 test**

실제 OMF 표기를 축약한 fixture로 날짜, 버전, 확정 상태, owner, versions path를 검증한다.

~~~python
metadata = extract_metadata(
    "docs/research/2026-07-14-긴급WO-운영방식-확정기록.md",
    first_lines,
)
assert metadata.date == date(2026, 7, 14)
assert metadata.version == "1.0"
assert metadata.decision_state is DecisionState.CONFIRMED
assert metadata.version_scope is VersionScope.CURRENT
~~~

**5.5 relation sidecar validation**

- relation 양쪽 path가 같은 snapshot에 있어야 함
- relation type은 두 enum만 허용
- evidence line은 실제 문서 범위 안이어야 함
- 자동 relation 추론은 구현하지 않음

**5.6 test와 commit**

~~~bash
uv run pytest tests/unit/source/test_markdown_parser.py tests/unit/indexing/test_metadata.py -q
git add src/omf_retrieval tests
git commit -m "feat(parser): Markdown 계층과 명시적 메타데이터 파싱"
~~~

---

## 작업 6. 결정론적 Parent–Child chunker

**파일**

- 생성: <code>src/omf_retrieval/infrastructure/source/chunker.py</code>
- 테스트: <code>tests/unit/source/test_chunker.py</code>
- 테스트: <code>tests/fixtures/markdown/long-section.md</code>

**6.1 fake token counter로 실패 test 작성**

~~~python
def test_small_section_remains_one_child(fake_counter: TokenCounter) -> None:
    chunks = chunker(fake_counter).split(section(tokens=600))
    assert len(chunks) == 1


def test_search_text_always_contains_heading_path() -> None:
    chunk = chunker().split(section(path=("A", "B"), body="내용"))[0]
    assert chunk.search_text.startswith("A\nB\n")
~~~

**6.2 경계 test 추가**

- 일반 text 목표 400, soft max 600
- 다음 child에 64 token overlap
- table/list/quote는 하나의 atomic block으로 유지
- atomic block이 800을 넘으면 row/item 경계에서 분할
- single row/item 자체가 800을 넘으면 paragraph/token boundary로 분할하고 warning metadata 기록
- child line range는 excerpt의 실제 최소·최대 line
- 동일 입력·설정은 동일 chunk hash와 ordinal 생성

**6.3 token abstraction과 chunker 구현**

~~~python
class TokenCounter(Protocol):
    def encode(self, text: str) -> Sequence[int]: ...


@dataclass(frozen=True)
class ChunkConfig:
    target_tokens: int = 400
    soft_max_tokens: int = 600
    overlap_tokens: int = 64
    atomic_max_tokens: int = 800
    parent_context_max_tokens: int = 1200
~~~

- unit test는 deterministic fake counter를 사용한다.
- 운영 token counter는 작업 7의 고정 Qwen tokenizer adapter를 주입한다.
- parent context 생성은 section body에서 match 주변 block을 1,200 token 이하로 자른다.

**6.4 test와 단계 A 전체 gate 실행**

~~~bash
uv run pytest tests/unit tests/integration/database tests/integration/source -q
uv run pytest tests/unit --cov=omf_retrieval.domain --cov=omf_retrieval.application --cov-fail-under=80
uv run ruff format --check .
uv run ruff check .
~~~

**6.5 commit과 사용자 확인**

~~~bash
git add src/omf_retrieval/infrastructure/source/chunker.py tests
git commit -m "feat(indexing): 결정론적 parent-child 청킹 구현"
~~~

단계 A 결과, migration revision, test 수, coverage를 사용자에게 공유하고 단계 B 진행 확인을 받는다.

---

## 작업 7. EmbeddingProvider와 Qwen GPU adapter

**파일**

- 생성: <code>src/omf_retrieval/infrastructure/embedding/provider.py</code>
- 생성: <code>src/omf_retrieval/infrastructure/embedding/sentence_transformer.py</code>
- 생성: <code>src/omf_retrieval/interfaces/cli/model.py</code>
- 테스트: <code>tests/unit/embedding/test_provider.py</code>
- 테스트: <code>tests/server/test_gpu_embedding.py</code>

**7.1 query/document 차이를 검증하는 실패 test**

~~~python
def test_query_instruction_is_applied_only_to_queries() -> None:
    provider = RecordingEmbeddingProvider()
    service = EmbeddingService(provider, QUERY_INSTRUCTION)

    service.embed_query("승인 정책")
    service.embed_documents(["승인 정책"])

    assert provider.inputs == [
        EXPECTED_INSTRUCTED_QUERY,
        "승인 정책",
    ]
~~~

**7.2 protocol과 fake 구현**

~~~python
class EmbeddingProvider(Protocol):
    @property
    def descriptor(self) -> EmbeddingDescriptor: ...
    def embed_query(self, query: str) -> list[float]: ...
    def embed_documents(self, documents: Sequence[str]) -> list[list[float]]: ...
    def is_ready(self) -> bool: ...
~~~

- unit/integration test는 1024차원 deterministic fake를 사용한다.
- dimension mismatch와 non-finite value를 domain error로 변환한다.

**7.3 SentenceTransformer adapter 구현**

- model과 tokenizer는 process당 한 번 lazy load한다.
- revision을 반드시 전달하고 <code>trust_remote_code=False</code>를 유지한다.
- output을 normalize하여 cosine distance와 일치시킨다.
- production은 <code>cuda:0</code> 외 device를 거부한다.
- dev/test에서만 명시적 CPU 설정을 허용한다.
- inference는 bounded semaphore 1로 보호하고 batch size를 설정으로 둔다.
- Flash Attention은 사용하지 않는다.

**7.4 model prepare CLI 구현**

<code>omf-retrieval model prepare</code>는 고정 revision을 model cache에 내려받고 file hash·revision을 출력한다. API container는 <code>HF_HUB_OFFLINE=1</code>로 실행하며 cache가 없으면 readiness 실패한다.

**7.5 local test와 server test 분리**

~~~bash
uv run pytest tests/unit/embedding -q
uv run pytest -m "not gpu" -q
~~~

<code>tests/server/test_gpu_embedding.py</code>는 <code>@pytest.mark.gpu</code>로 분리하며 phoebe server에서만 실행한다.

**7.6 commit**

~~~bash
git add src/omf_retrieval/infrastructure/embedding src/omf_retrieval/interfaces/cli/model.py tests
git commit -m "feat(embedding): Qwen 임베딩 공급자 추가"
~~~

---

## 작업 8. 증분 색인 pipeline과 content reuse

**파일**

- 생성: <code>src/omf_retrieval/infrastructure/database/repositories.py</code>
- 생성: <code>src/omf_retrieval/application/indexing/service.py</code>
- 테스트: <code>tests/unit/indexing/test_index_service.py</code>
- 테스트: <code>tests/integration/indexing/test_incremental_index.py</code>

**8.1 fake port 기반 실패 test**

~~~python
def test_duplicate_content_is_parsed_and_embedded_once() -> None:
    result = index_service.index(snapshot_with_two_paths_same_bytes())

    assert result.occurrence_count == 2
    assert result.unique_content_count == 1
    assert fake_parser.calls == 1
    assert fake_embeddings.document_calls == 1
~~~

**8.2 단계별 repository port 구현**

- create building run
- upsert content by hash
- create occurrence per path
- find/reuse parse by content+chunk config
- find/reuse embedding by chunk+embedding config
- store explicit relations
- record counters and sanitized failure

**8.3 indexing orchestrator 구현**

~~~text
advisory lock → commit snapshot → full path scan → content hash
→ metadata → parse/chunk reuse or create → embedding reuse or create
→ invariant validation → ready
~~~

- 전체 경로를 매 run 기록하되 unchanged content artifact는 재사용한다.
- UTF-8 decode 실패, excluded file, empty document, parse failure를 구분해 count한다.
- 한 document 실패가 전체 run을 실패시키며 active run은 건드리지 않는다.
- failure detail에는 원문, token, host absolute path를 저장하지 않는다.

**8.4 actual PostgreSQL integration test**

- first run에서 모든 artifact 생성
- second run에서 한 file만 변경했을 때 changed content만 parse/embed
- exact duplicate가 한 content를 공유하고 두 origins로 보존
- model revision 또는 dimension 변경 시 embedding만 재생성
- chunk config 변경 시 parse/chunk 재생성
- RRF 설정만 변경하면 재색인 불필요

**8.5 test와 commit**

~~~bash
uv run pytest tests/unit/indexing tests/integration/indexing/test_incremental_index.py -q
git add src/omf_retrieval/application/indexing src/omf_retrieval/infrastructure/database/repositories.py tests
git commit -m "feat(indexing): 증분 색인 파이프라인 구현"
~~~

---

## 작업 9. 원자 활성 전환·2세대 보존·rollback

**파일**

- 생성: <code>src/omf_retrieval/application/indexing/activation.py</code>
- 확장: <code>src/omf_retrieval/infrastructure/database/repositories.py</code>
- 테스트: <code>tests/integration/indexing/test_activation.py</code>
- 테스트: <code>tests/integration/indexing/test_rollback.py</code>

**9.1 동시 색인과 실패 격리 test 작성**

- 같은 source의 두 transaction 중 하나만 advisory lock 획득
- building/failed run은 active pointer 대상이 될 수 없음
- 새 run 실패 후 기존 active ID 불변

**9.2 단일 transaction activation 구현**

~~~python
with repository.transaction():
    repository.assert_status(run_id, IndexRunStatus.READY)
    old_active = repository.lock_source_profile(source_key)
    repository.mark_previous(old_active)
    repository.mark_active(run_id)
    repository.set_active_pointer(source_key, run_id)
    repository.prune_search_artifacts_except_active_and_previous(source_key)
~~~

- transaction 중 오류가 나면 status와 pointer가 모두 rollback된다.
- 오래된 run의 commit/config/stats/failure metadata는 남기고 occurrence 이하 검색 artifact만 정리한다.

**9.3 rollback 구현**

- previous가 없으면 domain error
- current active와 previous를 한 transaction에서 교환
- rollback 대상 config/model/cache readiness를 먼저 검사
- 실행 actor와 시각을 audit-safe log에 남김

**9.4 integration test와 commit**

~~~bash
uv run pytest tests/integration/indexing/test_activation.py tests/integration/indexing/test_rollback.py -q
git add src/omf_retrieval/application/indexing src/omf_retrieval/infrastructure/database tests
git commit -m "feat(indexing): 원자 활성 전환과 롤백 구현"
~~~

---

## 작업 10. API client token, source grant, 안전한 query hash

**파일**

- 생성: <code>src/omf_retrieval/application/admin/tokens.py</code>
- 생성: <code>src/omf_retrieval/application/admin/service.py</code>
- 확장: <code>src/omf_retrieval/infrastructure/database/repositories.py</code>
- 테스트: <code>tests/unit/admin/test_tokens.py</code>
- 테스트: <code>tests/integration/auth/test_grants.py</code>

**10.1 token 원문 미저장 실패 test**

~~~python
def test_issued_token_is_shown_once_and_only_hash_is_persisted() -> None:
    issued = service.create_client("agent-a", {"omf"})

    assert issued.token.startswith("omfr_")
    assert repository.saved.token_hash == sha256(issued.secret)
    assert issued.secret not in repr(repository.saved)
~~~

**10.2 token 형식과 검증 구현**

- 형식: <code>omfr_&lt;16-char-key-id&gt;.&lt;base64url-32-byte-secret&gt;</code>
- 32 random bytes는 256-bit entropy
- key ID로 row를 조회하고 SHA-256 결과를 <code>hmac.compare_digest</code>로 비교
- disabled, revoked, expired를 모두 401로 처리해 상태 차이를 노출하지 않음
- token은 create 시 stdout에 한 번만 표시

고entropy API secret은 offline dictionary 대상이 아니므로 password KDF dependency를 추가하지 않는다.

**10.3 source grant 선검증**

- search repository를 부르기 전에 client와 requested source grant를 검사한다.
- 없는 source와 무권한 source의 내부 차이를 외부 message로 노출하지 않는다.
- create/revoke/list는 application admin service만 제공하며 HTTP admin route는 만들지 않는다.

**10.4 query HMAC 규약**

- audit query hash는 unkeyed SHA-256이 아니라 <code>HMAC-SHA256(audit_hmac_key, exact_utf8_query)</code>
- 운영 key file: <code>/opt/omf-retrieval/secrets/audit_hmac_key</code>, mode 600
- key가 없으면 production readiness 실패
- query normalization을 하지 않아 audit hash가 사용자 입력을 임의 병합하지 않게 한다.

**10.5 test와 commit**

~~~bash
uv run pytest tests/unit/admin tests/integration/auth -q
git add src/omf_retrieval/application/admin src/omf_retrieval/infrastructure/database tests
git commit -m "feat(auth): 검색 토큰과 source 권한 구현"
~~~

---

## 작업 11. pg_trgm·vector 후보와 RRF evidence package

**파일**

- 생성: <code>src/omf_retrieval/application/search/ports.py</code>
- 생성: <code>src/omf_retrieval/application/search/rrf.py</code>
- 생성: <code>src/omf_retrieval/application/search/evidence.py</code>
- 생성: <code>src/omf_retrieval/application/search/service.py</code>
- 생성: <code>src/omf_retrieval/infrastructure/database/search.py</code>
- 테스트: <code>tests/unit/search/test_rrf.py</code>
- 테스트: <code>tests/unit/search/test_evidence.py</code>
- 테스트: <code>tests/integration/search/test_hybrid_search.py</code>
- 테스트: <code>tests/integration/search/test_authorization_filter.py</code>

**11.1 RRF 실패 test**

~~~python
def test_rrf_uses_one_based_rank_and_equal_weights() -> None:
    fused = reciprocal_rank_fusion(
        keyword=[candidate("a"), candidate("b")],
        vector=[candidate("b"), candidate("a")],
        k=60,
        keyword_weight=1.0,
        vector_weight=1.0,
    )
    assert fused["a"].score == pytest.approx(1 / 61 + 1 / 62)
~~~

**11.2 parent grouping 실패 test**

- 같은 parent의 두 child를 한 evidence item으로 묶음
- evidence rank는 child 최고 RRF, 추가 match score를 더하지 않음
- 각 match의 keyword/vector rank, RRF, discontiguous lines 보존
- exact duplicate는 evidence 한 개와 origins 여러 개
- explicit potential conflict는 양쪽 path와 line 근거 반환

**11.3 authorized candidate CTE 구현**

검색 SQL의 첫 CTE에서 다음을 모두 고정한다.

- authenticated client grant
- source profile active run
- version scope
- path prefixes
- decision states

그 결과에 포함된 occurrence와 연결된 unique chunk만 keyword/vector 후보가 될 수 있다. 권한·filter 밖 row는 similarity나 distance 계산에 들어가지 않는다.

**11.4 후보 SQL 구현**

~~~sql
-- keyword: authorized unique chunks, top 50
ORDER BY similarity(search_text, :query) DESC, chunk_id
LIMIT 50

-- vector: authorized unique chunks, exact cosine, top 50
ORDER BY embedding <=> CAST(:query_vector AS vector), chunk_id
LIMIT 50
~~~

- ANN index와 database-side weighted fusion은 사용하지 않는다.
- stable tie breaker는 chunk UUID다.
- active config의 model revision과 dimension이 query descriptor와 다르면 503 domain error다.

**11.5 search service 구현**

~~~text
principal/source 확인 → active run 고정 → query instruction embedding
→ keyword/vector 각 top 50 → application RRF → parent grouping
→ 최고 child score 정렬 → limit → provenance 검증 → audit event
~~~

- default limit 5, max 20
- no candidate는 <code>no_evidence</code>와 empty list
- <code>include_context=true</code>일 때만 1,200 token 이하 parent context
- query raw text는 audit repository나 log에 전달하지 않는다.

**11.6 integration test와 commit**

~~~bash
uv run pytest tests/unit/search tests/integration/search -q
git add src/omf_retrieval/application/search src/omf_retrieval/infrastructure/database/search.py tests
git commit -m "feat(search): 하이브리드 검색과 근거 그룹화 구현"
~~~

---

## 작업 12. FastAPI 검색·health 계약과 Typer CLI

**파일**

- 생성: <code>src/omf_retrieval/interfaces/api/app.py</code>
- 생성: <code>src/omf_retrieval/interfaces/api/dependencies.py</code>
- 생성: <code>src/omf_retrieval/interfaces/api/errors.py</code>
- 생성: <code>src/omf_retrieval/interfaces/api/schemas.py</code>
- 생성: <code>src/omf_retrieval/interfaces/api/routes/search.py</code>
- 생성: <code>src/omf_retrieval/interfaces/api/routes/health.py</code>
- 생성: <code>src/omf_retrieval/interfaces/cli/main.py</code>
- 생성: <code>src/omf_retrieval/interfaces/cli/search.py</code>
- 생성: <code>src/omf_retrieval/interfaces/cli/indexing.py</code>
- 생성: <code>src/omf_retrieval/interfaces/cli/evaluation.py</code>
- 생성: <code>src/omf_retrieval/interfaces/cli/admin.py</code>
- 테스트: <code>tests/contract/api/test_search.py</code>
- 테스트: <code>tests/contract/api/test_health.py</code>
- 테스트: <code>tests/contract/cli/test_commands.py</code>

**12.1 API schema 실패 test**

정상, <code>no_evidence</code>, 401, 403, 409, 422, 503 fixture를 설계서 JSON 계약과 비교한다.

~~~python
def test_no_evidence_is_successful_empty_response(client: TestClient) -> None:
    response = client.post("/v1/search", headers=auth(), json=VALID_REQUEST)

    assert response.status_code == 200
    assert response.json()["status"] == "no_evidence"
    assert response.json()["evidence_items"] == []
~~~

**12.2 endpoint 구현**

- <code>POST /v1/search</code>
- <code>GET /health/live</code>
- <code>GET /health/ready</code>
- 전체 문서·admin endpoint는 만들지 않음

오류 body는 <code>request_id</code>, stable <code>code</code>, safe <code>message</code>만 포함한다. validation error에도 raw body, token, host path, stack trace가 나오지 않게 custom handler를 둔다.

**12.3 health semantics**

- live: process event loop가 응답하면 200, build/version 최소 정보
- ready: DB query, active run, model cache/revision, provider ready, production CUDA, visible GPU count 1, logical <code>cuda:0</code> 검증
- ready endpoint는 active Bearer token을 요구한다. Docker 내부 healthcheck는 live만 사용하고, 배포·운영 readiness 확인은 발급된 검색 token으로 호출한다.
- ready 결과는 최대 120초 startup 내 성공해야 하지만 endpoint result 자체는 짧은 TTL로 cache해 동시 probe가 model을 다시 load하지 않게 함

**12.4 CLI 실패 test와 구현**

~~~text
omf-retrieval search
omf-retrieval index
omf-retrieval evaluate
omf-retrieval index-status
omf-retrieval rollback
omf-retrieval client create|revoke|list
omf-retrieval model prepare
~~~

- search CLI만 HTTPX로 REST API를 호출한다.
- 나머지는 same application service를 직접 호출한다.
- secret/token은 verbose mode에도 재출력하지 않는다.
- exit code: success 0, validation 2, auth 3, unavailable 4, quality gate fail 5.

**12.5 전체 단계 B gate**

~~~bash
uv run pytest tests/unit tests/integration tests/contract -m "not gpu and not server" -q
uv run pytest tests/unit tests/contract --cov=omf_retrieval.domain --cov=omf_retrieval.application --cov-fail-under=80
uv run ruff format --check .
uv run ruff check .
~~~

**12.6 commit과 사용자 확인**

~~~bash
git add src/omf_retrieval/interfaces tests/contract
git commit -m "feat(api): 검색·헬스체크·운영 CLI 계약 구현"
~~~

단계 B 결과와 sample fake evidence response를 사용자에게 공유하고 단계 C 진행 확인을 받는다.

---

## 작업 13. 검색 전용 평가 dataset과 metric

**파일**

- 생성: <code>evaluations/gold/schema.json</code>
- 생성: <code>evaluations/gold/omf-retrieval-v1.json</code>
- 생성: <code>evaluations/results/.gitkeep</code>
- 생성: <code>src/omf_retrieval/application/evaluation/dataset.py</code>
- 생성: <code>src/omf_retrieval/application/evaluation/metrics.py</code>
- 생성: <code>src/omf_retrieval/application/evaluation/runner.py</code>
- 테스트: <code>tests/unit/evaluation/test_dataset.py</code>
- 테스트: <code>tests/unit/evaluation/test_metrics.py</code>
- 테스트: <code>tests/integration/evaluation/test_runner.py</code>

**13.1 dataset validation 실패 test**

각 질문은 다음을 포함해야 한다.

~~~json
{
  "id": "omf-001",
  "split": "tuning",
  "category": "single_evidence",
  "query": "긴급 W/O의 승인 정책 원문을 찾아줘",
  "version_scope": "current",
  "required_evidence": [
    {
      "source_path": "docs/research/example.md",
      "heading_path": ["확정 사항", "발행 권한"],
      "line_start": 10,
      "line_end": 15,
      "commit_sha": "40-hex",
      "content_hash": "64-hex",
      "relevance": 3
    }
  ],
  "expect_no_evidence": false
}
~~~

- chunk ID를 gold coordinate로 사용하지 않는다.
- 20 tuning, 10 hold-out을 강제한다.
- category 분포 8/6/4/4/4/4를 강제한다.
- path/line/hash가 고정 commit 원문과 일치하는지 validator가 검사한다.

**13.2 metric 실패 test**

손으로 계산 가능한 작은 ranking fixture로 다음 metric을 검증한다.

- Evidence Recall@5, @10
- All-required Evidence@10
- nDCG@10: relevance 0~3
- MRR@10: 첫 relevance 3
- Context Precision: 반환 evidence 중 relevance 2 이상 비율
- Duplicate Evidence Ratio: 같은 content+line evidence 반복 비율
- hybrid Recall@10이 keyword/vector best single보다 낮지 않은지

**13.3 100% boolean gate 구현**

- provenance exact match
- unauthorized leakage 0
- historical selection correctness
- registered conflict 양쪽 노출
- no-evidence hallucination 0
- duplicate origin paths 모두 보존

한 건이라도 실패하면 evaluation CLI exit code 5.

**13.4 deterministic report**

<code>evaluations/results/&lt;UTC timestamp&gt;-&lt;app sha&gt;-&lt;source sha&gt;.json</code>에 다음을 기록한다.

- application commit SHA
- source commit SHA
- active run ID
- index config hash
- model/revision/dimension
- dataset content hash
- query별 rank와 판정
- aggregate metric과 threshold pass/fail
- 단계별 latency summary

질의 원문은 gold dataset 자체에 존재하므로 결과 report에는 question ID만 기록한다.

**13.5 30개 candidate 작성 후 사람 검토 checkpoint**

Agent가 고정 OMF commit에서 30개 질문과 line coordinate candidate를 작성한다. 사용자 또는 지정 reviewer가 질문 의도·필수 근거·관련도·no-evidence를 승인하기 전 gold version을 1.0으로 표시하지 않는다.

**13.6 test와 commit**

~~~bash
uv run pytest tests/unit/evaluation tests/integration/evaluation -q
git add evaluations src/omf_retrieval/application/evaluation tests
git commit -m "feat(evaluation): 검색 품질 평가 게이트 구현"
~~~

---

## 작업 14. 안전한 JSON logging, timing, audit event

**파일**

- 생성: <code>src/omf_retrieval/infrastructure/observability/logging.py</code>
- 생성: <code>src/omf_retrieval/infrastructure/observability/timing.py</code>
- 테스트: <code>tests/unit/observability/test_logging.py</code>
- 테스트: <code>tests/integration/observability/test_audit.py</code>

**14.1 금지 field 실패 test**

~~~python
def test_log_record_never_contains_query_token_or_excerpt(caplog) -> None:
    log_search_event(event_with_sensitive_values())
    rendered = caplog.text

    assert RAW_QUERY not in rendered
    assert BEARER_TOKEN not in rendered
    assert EVIDENCE_EXCERPT not in rendered
~~~

**14.2 allowlist JSON formatter 구현**

허용 field만 직렬화한다.

- timestamp, level, event
- request_id, client_id, source_key
- query_hmac, result_count, commit_sha, status/error_code
- embedding_ms, keyword_ms, vector_ms, rrf_ms, total_ms
- index counters와 elapsed

모든 unknown extra field를 그대로 출력하지 않는다.

**14.3 audit event 구현**

- response transaction과 분리하되 search 결과 직후 best effort로 기록
- audit DB failure는 검색 결과를 바꾸지 않지만 error log와 readiness degraded state를 남김
- returned ID는 chunk/evidence UUID만 저장
- request/response raw body 저장 금지

**14.4 test와 commit**

~~~bash
uv run pytest tests/unit/observability tests/integration/observability -q
git add src/omf_retrieval/infrastructure/observability tests
git commit -m "feat(observability): 안전한 검색 감사 로그 구현"
~~~

---

## 작업 15. GPU Docker image와 Docker Compose

**파일**

- 생성: <code>Dockerfile</code>
- 생성: <code>.dockerignore</code>
- 생성: <code>compose.yaml</code>
- 생성: <code>ops/prepare-host.sh</code>
- 생성: <code>tests/contract/deployment/test_compose.py</code>
- 생성: <code>tests/contract/deployment/test_image.py</code>

**15.1 static deployment 실패 test**

- base image tag와 digest exact match
- PostgreSQL tag와 digest exact match
- DB host port 없음
- API worker 1
- GPU device ID 0만 reservation
- source mount read-only
- secret은 <code>_FILE</code>로 전달
- no local Nginx/TLS service
- log rotation 설정

**15.2 Dockerfile 구현**

~~~dockerfile
FROM pytorch/pytorch:2.11.0-cuda12.8-cudnn9-runtime@sha256:eee11b3b3872a8c838e35ef48f08b2d5def2080902c7f666831310ca1a0ef2be

RUN python -m pip install --no-cache-dir uv==0.12.3
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv export --frozen --no-dev --no-emit-project \
      --output-file /tmp/requirements.lock \
    && uv pip install --system --require-hashes \
      --requirement /tmp/requirements.lock \
    && uv build --frozen --wheel --out-dir /tmp/dist \
    && uv pip install --system --no-deps /tmp/dist/*.whl
RUN useradd --uid 10001 --create-home omf-retrieval
USER 10001
ENTRYPOINT ["omf-retrieval"]
~~~

실제 구현에서는 dependency layer cache를 살리되 source tree 없이 editable install을 만들지 않는다. image에는 model과 secret을 넣지 않는다.

**15.3 Compose 구현**

- <code>api</code>: application image, <code>serve</code> command, worker 1, private IP bind
- <code>admin</code>: same image, Compose profile, one-off CLI
- <code>db</code>: 공개 pgvector image, internal network only
- volume:
  - <code>/home/storage_disk3/omf-retrieval-disk/postgres</code>
  - <code>/home/storage_disk3/omf-retrieval-disk/model-cache</code>
  - <code>/home/storage_disk3/omf-retrieval-disk/evaluations</code>
  - host OMF clone read-only
- secret:
  - <code>/opt/omf-retrieval/secrets/postgres_password</code>
  - <code>/opt/omf-retrieval/secrets/audit_hmac_key</code>
- DB는 <code>backend</code> internal network만, API는 <code>edge</code>와 <code>backend</code>
- <code>json-file</code> max-size와 max-file 설정

**15.4 host prepare script**

script는 대상 path를 explicit argument와 상수로 검증하고 다음만 생성한다.

- <code>/opt/omf-retrieval/{config,secrets,sources}</code>
- <code>/home/storage_disk3/omf-retrieval-disk/{postgres,model-cache,evaluations}</code>
- service UID가 필요한 directory를 쓸 수 있게 ownership 설정
- secret file mode 600 검증

기존 data를 삭제하거나 volume을 초기화하지 않는다.

**15.5 linux/amd64 image build test**

~~~bash
docker buildx build --platform linux/amd64 --load -t omf-retrieval:test .
docker run --rm --entrypoint python omf-retrieval:test -c \
  "import torch; assert torch.__version__.startswith('2.11.0')"
~~~

**15.6 단계 C 전체 gate와 commit**

~~~bash
uv run pytest tests/unit tests/integration tests/contract -m "not gpu and not server" -q
uv run ruff format --check .
uv run ruff check .
git add Dockerfile .dockerignore compose.yaml ops/prepare-host.sh tests/contract/deployment
git commit -m "build(container): GPU 이미지와 Compose 배포 구성"
~~~

단계 C 결과, image size, dependency vulnerability 확인 결과, Compose rendered config를 공유하고 단계 D 진행 확인을 받는다.

---

## 작업 16. Buildx push와 불변 배포 script

**파일**

- 생성: <code>ops/build-and-push.sh</code>
- 생성: <code>ops/deploy.sh</code>
- 생성: <code>ops/smoke-test.sh</code>
- 테스트: <code>tests/contract/deployment/test_scripts.py</code>

**16.1 script 계약 실패 test**

- dirty worktree/HEAD mismatch 시 build 거부
- image tag가 정확히 <code>hub.crefle.com/crefle-ai/omf-retrieval:&lt;git-sha&gt;</code>
- <code>--platform linux/amd64</code>와 <code>--push</code> 필수
- deploy는 tag를 pull한 뒤 registry digest를 확인하고 digest reference로 Compose 실행
- SSH alias는 <code>phoebe-onpremise-test</code>, remote root는 <code>/opt/omf-retrieval</code>
- migration 실패 시 기존 API container를 교체하지 않음

**16.2 build-and-push 구현**

~~~text
clean tree/HEAD 검증 → buildx --platform linux/amd64 --push
→ metadata file에서 digest 추출 → tag@digest와 SBOM metadata 출력
~~~

Registry login credential을 읽거나 저장하지 않고 기존 Docker credential store를 사용한다.

**16.3 deploy 구현**

~~~text
remote prerequisite 점검 → compose pull → model/cache readiness
→ DB health → one-off alembic upgrade head
→ api up -d → /health/live → /health/ready
→ authenticated smoke search → deployed digest 기록
~~~

- remote <code>.env</code>에는 image digest와 비secret 운영값만 둔다.
- DB password와 audit key를 전송하거나 출력하지 않는다.
- deploy script는 volume 삭제, image prune, DB downgrade를 하지 않는다.
- failed readiness 시 새 API를 unhealthy로 남기지 않고 이전 image digest로 Compose를 되돌릴 수 있는 명령을 출력한다. 자동 rollback은 하지 않는다.

**16.4 운영 입력 checkpoint**

다음 값이 사용자에게 확인되기 전 <code>ops/deploy.sh</code>를 실행하지 않는다.

- FQDN
- Gateway private IP
- backend private bind IP와 port
- OMF Git remote와 host clone 인증
- initial API client
- server firewall 변경 주체와 승인

**16.5 contract test와 commit**

~~~bash
uv run pytest tests/contract/deployment -q
git add ops tests/contract/deployment
git commit -m "build(deploy): 불변 이미지 배포 절차 추가"
~~~

---

## 작업 17. 골드 평가, GPU server E2E, 성능 gate

**파일**

- 생성: <code>tests/server/test_index_and_search.py</code>
- 생성: <code>tests/server/test_reindex_failure.py</code>
- 생성: <code>tests/server/test_rollback.py</code>
- 생성: <code>tests/performance/run_search_benchmark.py</code>
- 생성: <code>docs/operations/acceptance-checklist.md</code>

**17.1 서버 배포 전 read-only 확인**

~~~bash
ssh phoebe-onpremise-test \
  'docker version; docker compose version; nvidia-smi; df -h / /home/storage_disk3'
~~~

확인값이 설계 전제와 다르면 중지하고 공유한다.

**17.2 image push와 server pull**

사용자 확인 후:

~~~bash
./ops/build-and-push.sh "$(git rev-parse HEAD)"
./ops/deploy.sh "<app-image-digest>"
~~~

push digest, server pull digest, running container digest가 모두 같아야 한다.

**17.3 model과 최초 index**

- model prepare 결과가 고정 revision인지 확인
- container에서 visible device가 1개이고 <code>cuda:0</code>가 physical GPU 0인지 확인
- clean OMF host clone의 full commit SHA를 기록
- <code>index --source omf --commit &lt;sha&gt;</code>
- active run pointer, counts, excluded files, duplicate origins, embedding dimension 검증
- 초기 전체 index 15분 이하

**17.4 기능 E2E**

- 정상 검색과 provenance를 <code>git show &lt;sha&gt;:&lt;path&gt;</code> line과 대조
- no-evidence 200
- current/historical/all filter
- decision/path filter
- unauthorized client 403와 candidate leakage 없음
- changed commit 재색인과 artifact reuse
- 강제 embedding 실패에서 active 유지
- previous rollback
- duplicate path 모두 반환
- registered conflict 양쪽 반환

**17.5 품질 평가**

승인된 20 tuning 질문으로 설정을 조정한다. 순서는 다음으로 제한한다.

1. RRF weight와 k
2. query instruction
3. chunk 설정
4. 그 후에만 별도 사용자 승인으로 reranker·검색 확장 검토

10 hold-out은 최종 한 번 평가하고 tuning에 사용하지 않는다.

출시 기준:

- Recall@5 ≥ 0.85
- Recall@10 ≥ 0.95
- All-required@10 ≥ 0.85
- nDCG@10 ≥ 0.85
- MRR@10 ≥ 0.90
- Context Precision ≥ 0.80
- Duplicate Ratio ≤ 0.10
- 100% boolean gate 전부 통과

**17.6 성능 평가**

<code>run_search_benchmark.py</code>는 HTTPX와 표준 <code>concurrent.futures</code>만 사용한다.

- warm request 최소 100회
- concurrency 1 p95 ≤ 2초
- concurrency 5 p95 ≤ 3초
- server error rate &lt; 1%
- model cache가 있을 때 readiness ≤ 120초
- 단계별 latency 기록

**17.7 결과 문서와 commit**

~~~bash
git add tests/server tests/performance docs/operations/acceptance-checklist.md evaluations
git commit -m "test(acceptance): GPU 검색 품질과 성능 검증 추가"
~~~

실제 평가 결과 JSON은 내부 원문을 포함하지 않는지 확인한 뒤 저장소 포함 여부를 사용자와 확인한다.

---

## 작업 18. 최종 검증과 운영 인계

**파일**

- 확장: <code>README.md</code>
- 생성: <code>docs/operations/runbook.md</code>
- 생성: <code>docs/operations/deployment.md</code>
- 생성: <code>docs/operations/troubleshooting.md</code>

**18.1 문서 내용**

- local CPU test와 PostgreSQL integration 실행법
- model cache 준비, 최초 index, status, evaluate
- client create/revoke/rotation
- active/previous 의미와 rollback
- health endpoint와 stable error code
- central Nginx Gateway upstream 요구사항
- image digest 배포·이전 digest 복귀
- 로그 위치와 secret redaction
- 재색인으로 복구 가능한 범위
- backup/restore 자동화가 2차 범위임을 명시

**18.2 최종 자동 gate**

~~~bash
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run pytest tests/unit tests/integration tests/contract -m "not gpu and not server" \
  --cov=omf_retrieval.domain --cov=omf_retrieval.application --cov-fail-under=80
docker compose -f compose.test.yaml config --quiet
docker compose config --quiet
docker buildx build --platform linux/amd64 --load -t omf-retrieval:acceptance .
~~~

**18.3 최종 server gate**

- deployed image digest와 Git SHA 일치
- ready 200
- approved gold quality pass
- performance pass
- active source commit과 config hash 기록
- token/source grant smoke test
- central Gateway를 통한 HTTPS search 성공
- DB port 외부 미노출

**18.4 범위 누출 검토**

다음이 존재하지 않는지 <code>rg</code>와 route/schema 목록으로 확인한다.

- LLM generation/CrefleAI/Codex 호출
- MCP/function tool
- full document endpoint
- admin HTTP endpoint
- ANN index/reranker
- raw/query/token logging
- local Nginx/TLS
- backup automation

**18.5 documentation commit**

~~~bash
git add README.md docs/operations
git commit -m "docs(operations): 검색 서비스 운영 절차 정리"
~~~

**18.6 인수 보고**

다음 증거를 사용자에게 제공한다.

- commit과 image digest
- test 수·coverage·명령별 exit code
- migration revision과 PostgreSQL/pgvector version
- source commit, index config hash, model revision
- gold metric과 performance percentile
- security negative test 결과
- 남은 운영 입력 또는 2차 개발 항목

사용자 최종 승인 전에는 완료로 표시하지 않는다.

## 6. 예상 commit 순서

1. <code>build(project): Python 애플리케이션 기반 구성</code>
2. <code>feat(domain): 검색과 색인 핵심 계약 정의</code>
3. <code>feat(database): 검색 데이터 모델과 마이그레이션 추가</code>
4. <code>feat(source): OMF Git 스냅샷과 파일 선별 구현</code>
5. <code>feat(parser): Markdown 계층과 명시적 메타데이터 파싱</code>
6. <code>feat(indexing): 결정론적 parent-child 청킹 구현</code>
7. <code>feat(embedding): Qwen 임베딩 공급자 추가</code>
8. <code>feat(indexing): 증분 색인 파이프라인 구현</code>
9. <code>feat(indexing): 원자 활성 전환과 롤백 구현</code>
10. <code>feat(auth): 검색 토큰과 source 권한 구현</code>
11. <code>feat(search): 하이브리드 검색과 근거 그룹화 구현</code>
12. <code>feat(api): 검색·헬스체크·운영 CLI 계약 구현</code>
13. <code>feat(evaluation): 검색 품질 평가 게이트 구현</code>
14. <code>feat(observability): 안전한 검색 감사 로그 구현</code>
15. <code>build(container): GPU 이미지와 Compose 배포 구성</code>
16. <code>build(deploy): 불변 이미지 배포 절차 추가</code>
17. <code>test(acceptance): GPU 검색 품질과 성능 검증 추가</code>
18. <code>docs(operations): 검색 서비스 운영 절차 정리</code>

## 7. 구현 완료 정의

다음 조건이 모두 참일 때만 MVP 구현을 완료로 본다.

- 승인된 REST·CLI·DB·검색·권한 계약이 구현됨
- 전체 자동 test와 80% domain/application coverage 통과
- 실제 PostgreSQL 18 + pgvector 0.8.6 integration 통과
- OMF 고정 commit의 최초·증분·실패·rollback E2E 통과
- 승인된 30개 골드셋의 정량·100% gate 통과
- RTX 4090 GPU 0에서 성능 기준 통과
- 중앙 Gateway HTTPS 경로와 private network boundary 확인
- app/DB/model/source/config의 재현 좌표 기록
- 원문·token·query가 log/audit/image에 노출되지 않음
- 사용자에게 검증 증거를 제시하고 최종 승인을 받음
