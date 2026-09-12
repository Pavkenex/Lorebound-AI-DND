# File ownership for parallel work

Each workstream owns its directories EXCLUSIVELY. Do not edit files outside your scope
except `backend/app/main.py` router registration (append-only, one line per router).

| Stream | Owns |
|---|---|
| A core-state | `backend/app/core/**`, `backend/app/modules/auth/**`, `backend/app/modules/campaign/**`, `backend/app/modules/character/**`, `backend/app/modules/inventory/**`, `backend/alembic/**`, `backend/tests/test_auth*`, `test_campaign*`, `test_character*`, `test_inventory*`, `test_save*` |
| B actions | `backend/app/modules/rules/**`, `backend/app/modules/actions/**`, `backend/app/modules/narrator/**`, `backend/app/modules/ai/**`, `backend/tests/test_check*`, `test_action*`, `test_narrator*`, `test_authority*`, `test_validator*` |
| C narrative | `backend/app/modules/memory/**`, `backend/app/modules/world/**`, `backend/app/modules/npc/**`, `backend/app/modules/story/**`, `backend/app/modules/journal/**`, `backend/tests/test_memory*`, `test_world*`, `test_npc*`, `test_lead*`, `test_journal*`, `test_chronicle*` |
| D systems | `backend/app/modules/progression/**`, `backend/app/modules/exploration/**`, `backend/app/modules/combat/**`, `backend/app/modules/economy/**`, `backend/app/modules/history/**`, `backend/app/content/**`, `backend/tests/test_skill*`, `test_progression*`, `test_travel*`, `test_combat*`, `test_economy*`, `test_clock*`, `test_rest*` |
| E frontend | `frontend/**` only (+ `backend/tests/test_frontend_contract.py` if needed) |
| F slice/ship | `backend/app/content/ravenford.py`, `backend/app/content/fixtures.py`, `backend/tests/test_slice*`, `test_playtest*`, `docker-compose.yml`, `Dockerfile`, `backend/Dockerfile`, `frontend/Dockerfile`, `docs/**` |
| G live play | `backend/app/modules/play/**` (session/state/engine/router/view/screens/creation), `backend/tests/test_play_*` |

Shared contracts (do not change signatures without updating all callers):
- `EngineProposal` in `backend/app/modules/narrator/authority.py`
- `CheckRequest/CheckResult` in `backend/app/modules/rules/checks.py`
- `WorldFact` in `backend/app/modules/world/facts.py`
- `GameEvent` in `backend/app/core/events.py`
- HTTP play contracts (frontend depends on exact shapes): `POST /act`, `GET /state`,
  the six screen endpoints, campaign lifecycle, and `POST /saves/{id}/load` — all
  served by `backend/app/modules/play/**` and `backend/app/modules/campaign/**`.

