# Contributing

## Report an issue

Use [GitHub Issues](https://github.com/felpzw/bot_moodle/issues) for bugs and feature requests. Include the expected behavior, actual behavior, reproduction steps, Python version, and relevant sanitized logs. Do not include credentials, session keys, or private message content.

## Set up development

Follow the [README](README.md) to create a virtual environment, install dependencies, and configure a local database. Keep `.env` local; use `.env.example` to document configuration changes.

Keep each change focused and describe any effect on authentication, message delivery, queue state, or AI behavior. Write project documentation in English.

## Validate a change

From the repository root, check Python syntax without starting the bot or creating bytecode files:

```sh
python - <<'PY'
import ast
from pathlib import Path

for filename in ('bot.py', 'AI.py'):
    ast.parse(Path(filename).read_text(encoding='utf-8'), filename=filename)
    print(f'{filename}: syntax OK')
PY
```

Validate the database service configuration and review whitespace errors:

```sh
docker compose config --quiet
git diff --check
```

These checks do not verify live authentication, delivery, or AI generation. The repository currently has no automated test suite or CI workflow.

For runtime changes, use a designated test conversation and describe the scenarios checked. Relevant scenarios include a successful queued send, session renewal, a failed delivery with `error_log`, and a newly received message producing a single AI reply. Inspect both queue state and the Moodle conversation. Clear `TEST_CONTENT` after startup-seed checks because it runs on every restart.

For documentation changes, verify commands, environment names, defaults, and relative links against the current source. Avoid claiming delivery guarantees the implementation does not provide.

## Submit a change

Use a descriptive commit or pull request title. Explain the problem, the resulting behavior, and the validation performed, including any checks you could not run. Update related documentation and configuration examples in the same change.

## License

Unless explicitly stated otherwise, contributions intentionally submitted for inclusion in this project are covered by its [Apache License 2.0](LICENSE), as described in Section 5.
