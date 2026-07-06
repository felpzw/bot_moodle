# Arquitetura do Bot de Mensageria (Moodle)

## 1. Visão Geral
Sistema assíncrono projetado para operar em background, consumindo uma fila de mensagens de um banco PostgreSQL dockerizado e despachando via WebServices do Moodle. A arquitetura garante tolerância a falhas, incluindo reconexão automática e renovação de tokens expirados (`sesskey`) sem perda de dados.

## 2. Stack Tecnológica
* **Linguagem:** Python 3.11+ (utilizando a biblioteca nativa `asyncio` para concorrência).
* **HTTP Client:** `httpx` (cliente assíncrono que gerencia a persistência de cookies automaticamente através da classe `AsyncClient`).
* **Banco de Dados:** PostgreSQL 16 (via Docker).
* **Driver de Banco de Dados:** `asyncpg` (driver assíncrono de altíssima performance para interagir com o Postgres sem bloquear o I/O).
* **Extração DOM:** `BeautifulSoup4` (para parsear o HTML e capturar o `sesskey`).

---

## 3. Topologia de Componentes (Concorrência)

O sistema roda instanciando múltiplos componentes (tasks `asyncio`) que compartilham o estado do bot. O controle de acesso ao cliente HTTP e à autenticação é protegido por um `asyncio.Lock()`.

1. **State Manager:** Uma classe que guarda a instância do `httpx.AsyncClient` (com os cookies) e o token `sesskey`.
2. **Auth Worker:** Acionado no boot ou quando um token expira. Faz o POST de login e extrai o novo `sesskey` usando o `BeautifulSoup4`.
3. **Queue Dispatcher:** Um loop (`while True`) que busca registros `PENDING` no Postgres usando `asyncpg` e tenta o envio.
4. **Keep-Alive Worker:** Uma rotina simples com `asyncio.sleep(900)` (15 minutos) que faz um GET leve na API do Moodle para estender a vida útil do cookie.

---

## 4. O Fluxo de Renovação de Token (Auto-Refresh)

A resiliência do sistema depende de interceptar requisições falhas e renovar o token. A lógica de envio opera com uma estratégia de interceptação e bloqueio:

1. **Tentativa (Try):** O Dispatcher tenta enviar a mensagem usando o `sesskey` atual.
2. **Interceptação (Catch):** O Moodle responde. A resposta é analisada em busca de códigos 403, 303 (redirecionamento para login), ou um JSON contendo erro de sessão expirada.
3. **Bloqueio (Lock):** O Dispatcher invoca o Auth Worker e aciona um bloqueio assíncrono (`async with auth_lock:`). O processamento de novas mensagens é paralisado até a renovação ser concluída.
4. **Renovação (Refresh):** O Auth Worker executa um novo login HTTP, atualiza os cookies no `AsyncClient` compartilhado e extrai o novo `sesskey`.
5. **Retentativa (Retry):** O bloqueio é liberado e a mensagem original é reenviada com o novo token. Caso falhe novamente, a rotina a marca como `FAILED` no banco.

---

## 5. Estrutura do Banco de Dados (PostgreSQL 16)

Como a imagem `postgres:16` já está localmente disponível na máquina, a tabela de fila é estruturada para rastrear o status de cada envio:

```sql
CREATE TYPE message_status AS ENUM ('PENDING', 'SENT', 'FAILED');

CREATE TABLE message_queue (
    id SERIAL PRIMARY KEY,
    moodle_user_id VARCHAR(50) NOT NULL,
    conversation_id VARCHAR(50),
    content TEXT NOT NULL,
    status message_status DEFAULT 'PENDING',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP WITH TIME ZONE,
    error_log TEXT
);

-- Índice para otimizar a busca rápida por mensagens pendentes
CREATE INDEX idx_message_status ON message_queue(status) WHERE status = 'PENDING';