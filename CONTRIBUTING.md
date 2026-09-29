# Contributing

1. Read `ARCHITECTURE.md` (spec), `design.md` (how) and `PROGRESS.md` (status).
2. Pick or add a task in `PROGRESS.md`, mark it 🔄, and make sure its design exists in `design.md`.
3. Keep changes consistent with the invariants in design DS-02 (no sending from request handlers,
   suppression before send, limits never evaded, idempotent jobs, no silent job loss).
4. Schema changes: edit `backend/app/models`, then `alembic revision --autogenerate`, review the
   migration, and make sure `alembic check` passes.
5. Run all suites (see `docs/TESTING.md`) and `ruff check` / `npm run typecheck` before pushing.
6. Update `PROGRESS.md` (status, date, changelog) and `design.md` if the design changed.
