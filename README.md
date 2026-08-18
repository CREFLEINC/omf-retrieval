# OMF Retrieval

OMF 설계 문서를 대상으로 하는 정보 조회 플랫폼의 MVP 기반입니다. 현재는
프로젝트 구조와 품질 게이트만 구성하며, 검색·색인 기능은 아직 구현하지 않았습니다.

Python 3.12와 uv 0.12.3이 필요합니다.

```bash
uvx uv@0.12.3 sync --frozen
uvx uv@0.12.3 run pytest
uvx uv@0.12.3 run ruff format --check src tests/unit
uvx uv@0.12.3 run ruff check src tests/unit
```
