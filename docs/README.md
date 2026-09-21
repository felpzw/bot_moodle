# Configuration and Operations

See the [project README](../README.md) for an overview and installation instructions.

## Configuration

The application calls `load_dotenv()` at startup. Use a local `.env` file or export environment variables; existing environment variables take precedence over values loaded from `.env`.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `MOODLE_USER` | Yes | None | UFSC CAS username |
| `MOODLE_PASS` | Yes | None | UFSC CAS password |
| `DATABASE_URL` | No | `postgresql://bot_user:bot_password@localhost:5432/moodle_queue` | PostgreSQL connection string |
| `GEMINI_API_KEY` | For AI replies | None | API key read when the first AI response is requested |
| `GEMINI_MODEL` | No | `gemini-2.5-flash` | Model identifier passed to the Gemini API |
| `TEST_CONVERSATION_ID` | For AI replies or startup seeding | None | Conversation monitored by the reader and destination for a startup test message |
| `TEST_USER_ID` | No | `0` | User metadata for a startup test message |
| `TEST_CONTENT` | No | None | Message inserted on every startup when a conversation ID is also set |
| `LOG_LEVEL` | No | `INFO` | Python logging level, such as `DEBUG`, `INFO`, or `WARNING` |

The model above is the default in the source code, not a guarantee of availability for your Gemini account. Set `GEMINI_MODEL` if your account requires a different model identifier.

### Operating modes

| Mode | Configuration | Behavior |
| --- | --- | --- |
| Queue only | Leave `TEST_CONVERSATION_ID` unset | Dispatches pending rows and maintains the session |
| Queue and AI reader | Set `TEST_CONVERSATION_ID` and `GEMINI_API_KEY` | Also monitors one conversation and queues generated replies |
| Startup test message | Also set `TEST_CONTENT`; optionally set `TEST_USER_ID` | Inserts one message at startup and enables the reader for that conversation |

Startup seeding has no deduplication. Clear `TEST_CONTENT` after a controlled test to avoid inserting it again on restart.

### Fixed settings

These values are constants in [`bot.py`](../bot.py), not environment settings:

| Setting | Value |
| --- | --- |
| Moodle host | `https://presencial.moodle.ufsc.br` |
| Idle queue polling and reader interval | 5 seconds |
| Keep-alive interval | 900 seconds (15 minutes) |
| HTTP client timeout | 30 seconds |
| Dispatch batch size | Up to 10 rows, ordered by ID |
| Outgoing chunk size | Up to 3,800 characters |
| Database connection pool | 1–4 connections |
| AI reply marker | `🤖` |

The dispatcher immediately fetches another batch after processing rows; it sleeps only when no eligible rows are returned.

## Architecture

```mermaid
flowchart LR
    Producers[Manual SQL or external producers] --> Queue[(PostgreSQL message_queue)]
    Queue --> Dispatcher[Queue dispatcher]
    Dispatcher --> Moodle[UFSC Moodle AJAX endpoints]
    Moodle --> Reader[Optional conversation reader]
    Reader --> Gemini[Google Gemini]
    Gemini --> Reader
    Reader --> Queue
    Auth[CAS authentication and session renewal] --> Session[Shared HTTP client and session state]
    Session --> Dispatcher
    Session --> Reader
    KeepAlive[Keep-alive worker] --> Moodle
```

`BotState` holds the shared `httpx.AsyncClient`, cookies, `sesskey`, Moodle user ID, and authentication lock. The application creates the database pool, authenticates, optionally seeds the queue, starts background workers, and runs the dispatcher in the foreground.

### Authentication and recovery

1. Follow the Moodle login redirect to UFSC CAS and submit credentials with the form's hidden fields.
2. Handle a Collecta survey redirect by selecting the existing **Responder mais tarde** (respond later) action. For other off-Moodle redirects, request the Moodle login page again.
3. Load the dashboard and extract the `sesskey` from the logout link and the user ID from a profile link when available.
4. Detect session expiry during message requests through HTTP 303/403, non-JSON responses, or Moodle errors such as `invalidsesskey`, `servicerequireslogin`, and `sessionexpired`.
5. Acquire `auth_lock` and retry the request in case another worker has already renewed the session. If it still reports expiry, authenticate again.

The sender retries after successful authentication. A reader that recovers its session resumes polling on a later iteration. The lock coordinates recovery; it does not serialize all HTTP requests. The keep-alive worker requests the dashboard periodically but does not independently verify or renew the session.

### Message dispatch

The dispatcher selects up to 10 `PENDING` rows with non-null conversation IDs and processes them sequentially. It splits long text near spaces where possible, then calls `core_message_send_messages_to_conversation`.

After a send returns successfully, the row is marked `SENT` with `sent_at`. Exceptions during sending or the success update trigger an attempt to mark the row `FAILED` and record the exception in `error_log`.

### AI reader

The reader calls `core_message_get_conversation_messages` for one configured conversation. On successful initialization, it records the latest message ID so existing messages are not answered. If initialization fails, it starts from ID zero and may process the latest existing message later.

Each poll fetches only the latest message. The reader removes HTML and skips empty text and messages beginning with `🤖`. It sends eligible text to `AI.responder()` and inserts the reply into the queue with that marker. Filtering uses the marker rather than sender identity, including in conversations with the bot's own account.

Gemini receives the current message and the system instruction in `AI.py`; no conversation history is supplied. The client is created lazily and reused. The current instruction is in Portuguese and asks for concise, plain-text academic assistance for Transport Phenomena.

## Database

[`db/init.sql`](../db/init.sql) defines the `message_status` enum (`PENDING`, `SENT`, `FAILED`) and the following table:

| Column | Type | Purpose |
| --- | --- | --- |
| `id` | `SERIAL PRIMARY KEY` | Queue entry identifier |
| `moodle_user_id` | `VARCHAR(50) NOT NULL` | User metadata; not used for dispatch routing |
| `conversation_id` | `VARCHAR(50)` | Destination conversation; must represent an integer when sent |
| `content` | `TEXT NOT NULL` | Outgoing message body |
| `status` | `message_status` | Delivery state; defaults to `PENDING` |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | Insertion time; defaults to the current timestamp |
| `sent_at` | `TIMESTAMP WITH TIME ZONE` | Time the dispatcher records a successful send |
| `error_log` | `TEXT` | Exception recorded for a failed entry |

A partial index, `idx_message_status`, covers pending rows. The Compose service stores database files in the `pgdata` volume and initializes the schema only when the PostgreSQL data directory is empty. Editing `init.sql` does not migrate an existing database.

### Inspect and retry a failed entry

Connect with `docker compose exec db psql -U bot_user -d moodle_queue` and inspect recent entries:

```sql
SELECT id, conversation_id, status, created_at, sent_at, error_log
FROM message_queue
ORDER BY id DESC
LIMIT 20;
```

Resolve the recorded error and check the Moodle conversation before retrying: a failed database update or ambiguous response can occur after delivery. Replace `42` with the specific failed entry to retry:

```sql
UPDATE message_queue
SET status = 'PENDING', sent_at = NULL, error_log = NULL
WHERE id = 42 AND status = 'FAILED';
```

## Reliability and data handling

- **Single dispatcher:** queue selection has no row claim or locking mechanism. Multiple bot processes can send the same entry.
- **No exactly-once guarantee:** Moodle delivery and database updates are separate operations. An interruption between them can result in duplicate delivery on retry.
- **Limited send validation:** the sender detects selected session errors and top-level Moodle errors; it does not comprehensively validate HTTP status codes or individual chunk results.
- **Manual retries:** failed rows are retained, but there is no automatic requeue or backoff policy. Some database failures can terminate the dispatcher.
- **Latest-message polling:** intermediate messages can be missed between polls. The reader cursor lives in memory and is advanced before response generation, so a failed generation or queue insert is not automatically retried.
- **Marker-based filtering:** user messages beginning with `🤖` are skipped too. Only the beginning of a generated reply is prefixed; later chunks of a long reply may lack the marker and be read as new input.
- **External processing:** AI mode sends incoming message text to Gemini. The database retains queued outgoing content; no automatic retention cleanup is implemented.
- **Local configuration:** `.env` is ignored by Git. Keep credentials out of tracked files and redact logs before sharing them: logs can contain message excerpts, user IDs, and part of a session key. The Compose service exposes PostgreSQL on host port 5432 with fixed development credentials.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Bot exits with a missing-variable error | Set `MOODLE_USER` and `MOODLE_PASS` in `.env` or the process environment. |
| Database connection fails | Check `docker compose ps`, database health, `DATABASE_URL`, and whether another service uses port 5432. |
| `message_queue` does not exist | Confirm that `db/init.sql` ran against the intended database; initialization scripts do not rerun on an existing volume. |
| Login fails or no `sesskey` is found | Verify account access and whether the CAS form, dashboard, or redirect flow has changed. |
| A queue entry stays pending | Ensure `conversation_id` is non-null and the dispatcher is running. |
| An entry becomes failed | Read `error_log`, confirm access to the destination conversation, and verify the ID is numeric. |
| No AI replies appear | Check `TEST_CONVERSATION_ID`, `GEMINI_API_KEY`, model access, and reader logs. Send a new message after reader initialization. |
| Repeated startup test messages | Unset `TEST_CONTENT`; it inserts a new row every time the bot starts. |

The application writes logs to the console. Use `LOG_LEVEL=DEBUG` for local investigation and review the data-handling notes before sharing output. There is no automated test suite or CI workflow in the repository; see [contribution guidelines](../CONTRIBUTING.md) for local checks.
