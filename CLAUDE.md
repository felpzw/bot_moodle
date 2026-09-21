# Repository Guidance

## Project context

This repository contains an asynchronous Python messaging bot for UFSC Moodle. PostgreSQL stores outgoing messages, Moodle AJAX endpoints handle message delivery and retrieval, and Google Gemini optionally generates replies for one conversation.

Read [README.md](README.md) for setup and [docs/README.md](docs/README.md) for configuration, architecture, and operational limits.

## Code map

- `bot.py`: shared session state, CAS authentication, session recovery, message splitting, queue dispatcher, keep-alive, reader, and startup configuration.
- `AI.py`: lazy Gemini client and the `responder()` coroutine. The academic system prompt is currently in Portuguese.
- `db/init.sql`: queue schema and partial index for pending rows.
- `docker-compose.yml`: PostgreSQL 16 with a persistent volume and health check.
- `.env.example`: documented environment configuration.

## Implementation constraints

- Preserve asynchronous network and database operations.
- Coordinate session renewal with `BotState.auth_lock`.
- Preserve the Collecta **Responder mais tarde** action; changing it to permanent survey dismissal would change account behavior.
- Keep the AI reply marker and reader filtering consistent. Consider message splitting when changing loop prevention.
- Do not imply exactly-once delivery, automatic failed-message retries, or safe concurrent dispatchers; these are not implemented.
- Keep queue schema changes explicit. Container initialization scripts do not migrate existing volumes.
- Update the configuration reference and `.env.example` when changing settings. `TEST_CONVERSATION_ID` enables the reader as well as selecting the startup seed destination.

## Validation

Use the checks in [CONTRIBUTING.md](CONTRIBUTING.md). No automated test suite or CI workflow is currently included. Live runs authenticate to UFSC and may send messages or invoke Gemini; use an explicitly designated test conversation for integration checks.

Keep documentation in English and avoid committing credentials, session data, database exports, or unredacted logs.
