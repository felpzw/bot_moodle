# Documentação do Projeto: Bot Moodle

## 1. Informações principais

O **bot_moodle** é um bot assíncrono em Python para envio e resposta de mensagens no Moodle UFSC.
Ele consome mensagens de uma fila no PostgreSQL e envia usando endpoints AJAX do Moodle.

### Objetivo
- Processar mensagens pendentes de forma contínua em background.
- Garantir resiliência de autenticação (cookies + `sesskey`) sem perder mensagens.
- Permitir resposta automática com IA (Gemini) quando novas mensagens chegam em uma conversa monitorada.

### Stack utilizada
- Python 3.11+
- `asyncio` (concorrência)
- `httpx.AsyncClient` (HTTP assíncrono com persistência de cookies)
- `asyncpg` (acesso assíncrono ao PostgreSQL)
- `BeautifulSoup4` (parsing HTML para capturar `sesskey`)
- PostgreSQL 16 via Docker (`docker-compose.yml`)

### Estrutura de arquivos
- `/home/runner/work/bot_moodle/bot_moodle/bot.py`: fluxo principal do bot (auth, dispatcher, reader, keep-alive).
- `/home/runner/work/bot_moodle/bot_moodle/AI.py`: integração com Gemini (`responder`).
- `/home/runner/work/bot_moodle/bot_moodle/db/init.sql`: schema da fila de mensagens.
- `/home/runner/work/bot_moodle/bot_moodle/docker-compose.yml`: serviço local do PostgreSQL.
- `/home/runner/work/bot_moodle/bot_moodle/.env.example`: variáveis de ambiente esperadas.

## 2. Arquitetura

O sistema roda com múltiplas tasks `asyncio` compartilhando um estado comum (`BotState`).

### Componentes principais
1. **State Manager (`BotState`)**
   - Mantém `httpx.AsyncClient`, `sesskey`, `user_id` e `auth_lock`.
2. **Auth Worker (`authenticate`)**
   - Faz login via CAS UFSC, trata redirecionamentos e extrai `sesskey` do dashboard.
3. **Queue Dispatcher (`dispatcher_loop`)**
   - Busca mensagens `PENDING` no banco e tenta enviar.
   - Marca como `SENT` em caso de sucesso ou `FAILED` em falha.
4. **Keep-Alive Worker (`keep_alive_loop`)**
   - Realiza GET periódico no dashboard para manter sessão ativa.
5. **Reader Worker (`reader_loop`)**
   - Monitora novas mensagens na conversa configurada.
   - Gera resposta com IA e enfileira no PostgreSQL com marcador do bot.

### Fluxo de renovação de sessão (auto-refresh)
1. Tenta envio com `sesskey` atual.
2. Se detectar erro de sessão (HTTP 303/403 ou códigos de erro do Moodle), considera sessão expirada.
3. Entra em `auth_lock` para evitar renovação concorrente.
4. Reautentica (`authenticate`) e atualiza estado compartilhado.
5. Reenvia a mensagem original.

## 3. Funcionalidades importantes

- **Envio confiável via fila**
  - Usa `message_queue` com status `PENDING`, `SENT` e `FAILED`.
- **Tolerância a expiração de sessão**
  - Detecta erros de autenticação e renova automaticamente.
- **Controle de concorrência na autenticação**
  - `asyncio.Lock` evita múltiplos logins simultâneos.
- **Chunking de mensagens longas**
  - Divide mensagens grandes em blocos para respeitar limite do Moodle.
- **Proteção contra loop de auto-resposta**
  - Prefixo `🤖` identifica mensagens do bot no reader.
- **Seed opcional para testes**
  - `seed_from_env` permite inserir mensagem inicial por variáveis de ambiente.
- **Integração com IA**
  - `AI.py` gera resposta textual com Gemini usando `GEMINI_API_KEY`.

## 4. Banco de dados

Schema base (em `db/init.sql`):
- Tipo `message_status`: `PENDING`, `SENT`, `FAILED`.
- Tabela `message_queue`:
  - `id`, `moodle_user_id`, `conversation_id`, `content`, `status`, `created_at`, `sent_at`, `error_log`.
- Índice parcial para acelerar leitura de pendências:
  - `idx_message_status` para registros `PENDING`.

## 5. Configuração de ambiente

Variáveis principais:
- `MOODLE_USER`, `MOODLE_PASS`
- `DATABASE_URL` (opcional; há default compatível com docker-compose)
- `GEMINI_API_KEY`
- `GEMINI_MODEL` (opcional)
- `TEST_CONVERSATION_ID`, `TEST_USER_ID`, `TEST_CONTENT` (opcionais para teste)
- `LOG_LEVEL` (opcional)

## 6. Observações operacionais

- O banco local pode ser iniciado com `docker-compose.yml`.
- O bot depende de sessão web do Moodle; por isso o fluxo de refresh é crítico.
- Em falhas de envio, a mensagem não é descartada silenciosamente: o erro fica registrado em `error_log` quando status vira `FAILED`.
