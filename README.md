# Moodle Messaging Bot

An asynchronous Python service that delivers queued messages to UFSC Moodle and optionally generates replies using Google Gemini.

The bot authenticates through UFSC CAS, reads an outgoing message queue in PostgreSQL, and uses Moodle's session-based AJAX endpoints to send messages. It is tailored to `presencial.moodle.ufsc.br`; other Moodle installations require changes to the URLs and authentication flow.

## Highlights

- **Persistent outgoing queue:** tracks pending, sent, and failed messages in PostgreSQL.
- **Session recovery:** renews expired cookies and `sesskey` values when sending or reading messages.
- **Optional AI replies:** monitors one configured conversation and queues plain-text Gemini responses.
- **Message splitting:** splits outgoing text into chunks of up to 3,800 characters.
- **Background workers:** runs dispatch, conversation polling, and session keep-alive with `asyncio`.
- **Local database setup:** includes PostgreSQL 16, schema initialization, and a health check through Docker Compose.

## Requirements

- Python 3.11 or later.
- Docker with Docker Compose for the included database setup, or an existing PostgreSQL database initialized with [`db/init.sql`](db/init.sql).
- UFSC credentials with access to the target Moodle conversation.
- A Gemini API key if automatic replies are enabled.

Python dependencies are `httpx`, `asyncpg`, `beautifulsoup4`, `python-dotenv`, and `google-genai`. Install all five even in queue-only mode: the AI module is imported at startup. Dependency versions are not currently pinned.

## Quick start

Run these commands from the repository root on macOS or Linux:

```sh
git clone https://github.com/felpzw/bot_moodle.git
cd bot_moodle
python3 -m venv .venv
source .venv/bin/activate
python -m pip install httpx asyncpg beautifulsoup4 python-dotenv google-genai
cp .env.example .env
```

Edit `.env` and set `MOODLE_USER` and `MOODLE_PASS`. The default database URL matches the included Compose service.

```sh
docker compose up -d db
docker compose ps
```

Wait for the database to report `healthy`, then start the bot:

```sh
python bot.py
```

By default, the bot processes existing queue entries and keeps its Moodle session active. It does not monitor a conversation until `TEST_CONVERSATION_ID` is set. Stop the bot with `Ctrl+C`; use `docker compose stop db` to stop the database while retaining its volume.

### Enable automatic replies

Set `GEMINI_API_KEY` and `TEST_CONVERSATION_ID` in `.env`, then restart the bot. Despite its name, `TEST_CONVERSATION_ID` selects the conversation monitored by the reader. Leave `TEST_CONTENT` unset unless you want to enqueue a test message on **every startup**.

The current AI prompt is written in Portuguese and describes an academic assistant for Transport Phenomena. It requests plain-text replies of at most 2,500 characters; this is a prompt instruction, not an enforced output limit. Edit `_SYSTEM_PROMPT` in [`AI.py`](AI.py) to change that behavior.

### Enqueue a message

Open the database console:

```sh
docker compose exec db psql -U bot_user -d moodle_queue
```

Replace the example IDs with valid values before running:

```sql
INSERT INTO message_queue (moodle_user_id, conversation_id, content)
VALUES ('12345', '67890', 'Hello from the Moodle messaging bot.');

SELECT id, status, sent_at, error_log
FROM message_queue
ORDER BY id DESC
LIMIT 10;
```

`conversation_id` determines the destination. `moodle_user_id` is required metadata; the dispatcher does not use it to select a recipient. Rows without a conversation ID remain pending.

## Operational limits

Run one bot process per queue: rows are not locked or claimed for concurrent dispatchers. Delivery is not exactly-once, and failed rows are not automatically requeued. The reader retrieves only the latest message every five seconds, so it can miss messages in a busy conversation.

When AI replies are enabled, incoming message text is sent to Gemini. Queue contents are stored in PostgreSQL, and logs may include message excerpts and a session-key prefix. The included database credentials are local development defaults.

See the [configuration and operations reference](docs/README.md) for the full settings, architecture, failure behavior, and troubleshooting steps.

## Repository information

| Path | Purpose |
| --- | --- |
| [`bot.py`](bot.py) | Authentication, dispatch, reader, and keep-alive workers |
| [`AI.py`](AI.py) | Gemini client and response instructions |
| [`db/init.sql`](db/init.sql) | Queue schema and pending-message index |
| [`docker-compose.yml`](docker-compose.yml) | Local PostgreSQL service and persistent volume |
| [`.env.example`](.env.example) | Configuration template |
| [`docs/README.md`](docs/README.md) | Configuration, architecture, and operations |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Development and contribution guidelines |
| [`CLAUDE.md`](CLAUDE.md) | Repository guidance for coding assistants |
| [`LICENSE`](LICENSE) | Apache License 2.0 terms |

Source: [felpzw/bot_moodle](https://github.com/felpzw/bot_moodle). Report bugs and request features through [GitHub Issues](https://github.com/felpzw/bot_moodle/issues), including reproduction steps and sanitized logs.

## License

Copyright 2026 Felipe Winsch and contributors.

Licensed under the [Apache License, Version 2.0](LICENSE). The license text is reproduced from the [Apache Software Foundation](https://www.apache.org/licenses/LICENSE-2.0.txt). Third-party dependencies retain their own licenses.
