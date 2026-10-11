# Specification tests

Run with the existing virtual environment; no extra dependencies are needed:

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s tests -p 'test_spec_*.py' -v
```

| Layer | File | Specification contracts |
| --- | --- | --- |
| Unit | `test_spec_unit.py` | PBKDF2/SHA256, random salts, constant-time comparison, opaque tokens; reconciliation; investment profit/loss and missing history |
| Integration | `test_spec_integration.py` | SQLite member seeding, password/session lifecycle, configurable TTL/FY, combined searches, ownership, natural keys, manual overrides, investment identity |
| Unified app | `test_unified.py` | Auto-rules.csv syntax and precedence, SQL/text filter, staged import preview vs commit, export numeric suffix, creatable kinds, seeded categories, Import / Export / Home views |
| End to end | `test_spec_e2e.py` | Authenticated CLI import/export, family/member filters, CSV round trips, failed-block handling and logging, investment imports, backup/restore, deploy archives, dashboard login/navigation/logout and session expiry/revocation |

Expected behavior comes from `specs/auth-specs.md`, `specs/backend-specs.md`,
`specs/importer-exporter-specs.md`, `specs/dashboard-specs.md`, and
`specs/deploy-backup-specs.md`. The current importer specification supersedes
retired rule/LLM classification behavior. Distribution tests check the runtime
source list specified in `specs/deploy-backup-specs.md`.

New tests use synthetic credentials/CSV fixtures and temporary databases.
CLI end-to-end tests copy the runtime sources into a temporary project and
invoke the real CLI in subprocesses. Distribution tests build and unpack there.
The existing backup/restore test now also isolates `FINANCE_DB`.

Dashboard workflows use Streamlit AppTest with an actual sign-in and SQLite
authentication. AppTest does not execute browser JavaScript or cookie transport;
these tests do not claim browser-level coverage. Existing tests cover editor
callbacks directly because AppTest cannot drive `st.data_editor` cells.

Spec violations are ordinary failing assertions, not skipped or expected-failure
tests. Application code remains unchanged. A failing acceptance assertion should
be investigated against its cited contract before changing the expectation.

The detailed acceptance-criterion map and enabled findings are in
[`SPEC_COVERAGE.md`](SPEC_COVERAGE.md). Specification violations remain normal
failing assertions; they are not skipped, softened, or marked expected failures.

Latest complete validation (2026-10-10, Docker enabled): **192 tests run; 186
passed; 5 failed; 1 errored**. The six non-passing cases are BL-001 through
BL-006 in the coverage matrix. All 8 container tests passed.

Container-level checks are opt-in and require a reachable Docker daemon:

```sh
RUN_DOCKER_TESTS=1 .venv/bin/python -m unittest tests.test_docker -v
```

End-to-end validation executes a temporary copy of the code and tests, copying only
runtime sources and tests. Personal member files, data directories, and databases
are not copied. Neither a personal database nor the workspace database is used.
The member configuration unit test also uses a temporary YAML fixture rather
than requiring personal `members.yaml` or data directories.
