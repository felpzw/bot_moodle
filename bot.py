"""Bot de mensageria do Moodle UFSC.

Consome `message_queue` (Postgres) e envia as mensagens via webservice AJAX
do Moodle. Renova `sesskey`/cookies automaticamente quando a sessão expira.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field
from urllib.parse import urljoin

import asyncpg
import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from AI import responder

URL_LOGIN = "https://presencial.moodle.ufsc.br/login/index.php"
URL_DASHBOARD = "https://presencial.moodle.ufsc.br/my/"
URL_AJAX = "https://presencial.moodle.ufsc.br/lib/ajax/service.php"

POLL_INTERVAL_S = 5
KEEP_ALIVE_S = 900
HTTP_TIMEOUT_S = 30

logger = logging.getLogger("moodle_bot")


# --------------------------- estado compartilhado --------------------------- #

@dataclass
class BotState:
    client: httpx.AsyncClient
    sesskey: str | None = None
    user_id: int = 0
    auth_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# --------------------------------- auth ------------------------------------ #

async def authenticate(state: BotState, username: str, password: str) -> None:
    """Login via CAS da UFSC (sistemas.ufsc.br) e captura sesskey no dashboard.

    O Moodle da UFSC redireciona para o CAS central em vez de usar seu próprio
    formulário. Fluxo: GET URL_LOGIN → 302 → GET CAS → POST CAS → redirect de
    volta para Moodle com ticket → sessão criada → GET dashboard → sesskey.
    """
    # Segue o redirect de URL_LOGIN até o formulário CAS (sistemas.ufsc.br).
    cas_resp = await state.client.get(URL_LOGIN, follow_redirects=True)
    cas_resp.raise_for_status()
    logger.debug("página CAS: %s", cas_resp.url)

    soup = BeautifulSoup(cas_resp.text, "html.parser")
    form = soup.find("form")

    # Determina a URL de POST: action do form ou a URL atual do CAS.
    if form and form.get("action"):
        action = form["action"]
        post_url = action if action.startswith("http") else urljoin(str(cas_resp.url), action)
    else:
        post_url = str(cas_resp.url)

    # Carrega todos os campos ocultos do CAS (lt, execution, _eventId, …).
    payload: dict[str, str] = {"username": username, "password": password}
    if form:
        for inp in form.find_all("input", type="hidden"):
            name = inp.get("name")
            if name:
                payload[name] = inp.get("value", "")

    # POST para o CAS; a cadeia de redirects devolve ao Moodle com o ticket.
    resp = await state.client.post(post_url, data=payload, follow_redirects=True)
    resp.raise_for_status()

    # Vai ao dashboard para capturar o sesskey.
    dash = await state.client.get(URL_DASHBOARD, follow_redirects=True)
    dash.raise_for_status()

    soup = BeautifulSoup(dash.text, "html.parser")
    logout = soup.find("a", href=lambda h: h and "logout.php?sesskey=" in h)
    if not logout:
        raise RuntimeError("login falhou — sesskey não encontrado no dashboard")

    href: str = logout["href"]
    state.sesskey = href.split("sesskey=", 1)[1].split("&", 1)[0]

    # Extrai o user_id do bot a partir do link de perfil no dashboard.
    profile = soup.find("a", href=lambda h: h and "/user/profile.php?id=" in h)
    if profile:
        pid = profile["href"].split("id=")[1].split("&")[0]
        state.user_id = int(pid)

    logger.info("autenticado user_id=%s, sesskey=%s…", state.user_id, state.sesskey[:6])


# ------------------------------ envio -------------------------------------- #

class SessionExpired(Exception):
    """Cookie/sesskey inválidos — exige re-login."""


def _build_payload(conversation_id: str, content: str) -> list[dict]:
    return [
        {
            "index": 0,
            "methodname": "core_message_send_messages_to_conversation",
            "args": {
                "conversationid": int(conversation_id),
                "messages": [{"text": content}],
            },
        }
    ]


async def _send_once(state: BotState, conversation_id: str, content: str) -> None:
    if state.sesskey is None:
        raise SessionExpired("sesskey não inicializado")

    resp = await state.client.post(
        URL_AJAX,
        params={"sesskey": state.sesskey},
        json=_build_payload(conversation_id, content),
        follow_redirects=False,
    )

    if resp.status_code in (303, 403):
        raise SessionExpired(f"HTTP {resp.status_code}")

    try:
        data = resp.json()
    except ValueError:
        raise SessionExpired("resposta não-JSON (provável redirect pro login)")

    if isinstance(data, list) and data and data[0].get("error"):
        exc = data[0].get("exception") or {}
        code = exc.get("errorcode", "")
        if code in {"servicerequireslogin", "invalidsesskey", "sessionexpired"}:
            raise SessionExpired(code)
        raise RuntimeError(f"Moodle retornou erro: {exc or data[0]}")


def _strip_html(text: str) -> str:
    return BeautifulSoup(text, "html.parser").get_text(separator=" ").strip()


async def _fetch_messages(
    state: BotState,
    conversation_id: str,
    bot_user_id: int,
    limitnum: int = 1,
    timefrom: int = 0,
) -> list[dict]:
    if state.sesskey is None:
        raise SessionExpired("sesskey não inicializado")

    payload = [
        {
            "index": 0,
            "methodname": "core_message_get_conversation_messages",
            "args": {
                "convid": int(conversation_id),
                "currentuserid": bot_user_id,
                "limitfrom": 0,
                "limitnum": limitnum,
                "newest": True,
                "timefrom": timefrom,
            },
        }
    ]
    resp = await state.client.post(
        URL_AJAX,
        params={"sesskey": state.sesskey},
        json=payload,
        follow_redirects=False,
    )

    if resp.status_code in (303, 403):
        raise SessionExpired(f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        raise SessionExpired("resposta não-JSON")

    if isinstance(data, list) and data and data[0].get("error"):
        exc = data[0].get("exception") or {}
        code = exc.get("errorcode", "")
        if code in {"servicerequireslogin", "invalidsesskey", "sessionexpired"}:
            raise SessionExpired(code)
        raise RuntimeError(f"fetch_messages erro: {exc}")

    return data[0].get("data", {}).get("messages", [])


async def send_message(
    state: BotState,
    username: str,
    password: str,
    conversation_id: str,
    content: str,
) -> None:
    """Try → on session error: lock + reauth + retry once."""
    try:
        await _send_once(state, conversation_id, content)
        return
    except SessionExpired as e:
        logger.warning("sessão expirou (%s); renovando", e)

    async with state.auth_lock:
        # outro worker pode ter renovado enquanto esperávamos o lock
        try:
            await _send_once(state, conversation_id, content)
            return
        except SessionExpired:
            await authenticate(state, username, password)

    await _send_once(state, conversation_id, content)


# ---------------------------- dispatcher ----------------------------------- #

SELECT_PENDING = """
    SELECT id, conversation_id, content
    FROM message_queue
    WHERE status = 'PENDING' AND conversation_id IS NOT NULL
    ORDER BY id
    LIMIT 10
"""
MARK_SENT = "UPDATE message_queue SET status = 'SENT', sent_at = now() WHERE id = $1"
MARK_FAILED = "UPDATE message_queue SET status = 'FAILED', error_log = $2 WHERE id = $1"


async def dispatcher_loop(
    state: BotState,
    pool: asyncpg.Pool,
    username: str,
    password: str,
) -> None:
    while True:
        async with pool.acquire() as conn:
            rows = await conn.fetch(SELECT_PENDING)

        if not rows:
            await asyncio.sleep(POLL_INTERVAL_S)
            continue

        for row in rows:
            try:
                await send_message(
                    state, username, password, row["conversation_id"], row["content"]
                )
                async with pool.acquire() as conn:
                    await conn.execute(MARK_SENT, row["id"])
                logger.info("msg %s enviada", row["id"])
            except Exception as e:
                logger.exception("falha ao enviar msg %s", row["id"])
                async with pool.acquire() as conn:
                    await conn.execute(MARK_FAILED, row["id"], str(e))


# ---------------------------- keep-alive ----------------------------------- #

async def keep_alive_loop(state: BotState) -> None:
    while True:
        await asyncio.sleep(KEEP_ALIVE_S)
        try:
            await state.client.get(URL_DASHBOARD)
            logger.debug("keep-alive ok")
        except Exception:
            logger.exception("keep-alive falhou (dispatcher renova se necessário)")


# ------------------------------ reader ------------------------------------- #

async def reader_loop(
    state: BotState,
    pool: asyncpg.Pool,
    username: str,
    password: str,
    conversation_id: str,
) -> None:
    # Inicializa last_seen_id com a mensagem mais recente já existente para
    # não responder a mensagens antigas no boot.
    last_seen_id: int = 0
    try:
        msgs = await _fetch_messages(state, conversation_id, state.user_id, limitnum=1)
        if msgs:
            last_seen_id = msgs[0]["id"]
            logger.info("reader: inicializado, last_seen_id=%s", last_seen_id)
    except Exception:
        logger.exception("reader: erro na inicialização (continuando com id=0)")

    while True:
        await asyncio.sleep(POLL_INTERVAL_S)

        try:
            msgs = await _fetch_messages(state, conversation_id, state.user_id, limitnum=1)
        except SessionExpired as e:
            logger.warning("reader: sessão expirou (%s); renovando", e)
            async with state.auth_lock:
                try:
                    await _fetch_messages(state, conversation_id, state.user_id, limitnum=1)
                except SessionExpired:
                    await authenticate(state, username, password)
            continue
        except Exception:
            logger.exception("reader: erro ao buscar mensagens")
            continue

        if not msgs:
            continue

        latest = msgs[0]
        msg_id: int = latest["id"]

        # Ignora mensagem já vista ou enviada pelo próprio bot.
        if msg_id <= last_seen_id or latest.get("useridfrom") == state.user_id:
            last_seen_id = max(last_seen_id, msg_id)
            continue

        last_seen_id = msg_id
        text = _strip_html(latest.get("text", ""))
        if not text:
            continue

        logger.info("reader: nova msg [%s] de userid=%s — %s…",
                    msg_id, latest.get("useridfrom"), text[:50])
        try:
            reply = await responder(text)
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO message_queue (moodle_user_id, conversation_id, content)"
                    " VALUES ($1, $2, $3)",
                    str(latest.get("useridfrom", 0)), conversation_id, reply,
                )
            logger.info("reader: resposta IA enfileirada (convid=%s)", conversation_id)
        except Exception:
            logger.exception("reader: falha ao gerar/enfileirar resposta IA")


# ------------------------------- main -------------------------------------- #

def _env(name: str, default: str | None = None) -> str:
    val = os.getenv(name, default)
    if val is None:
        sys.exit(f"variável de ambiente obrigatória: {name}")
    return val


async def seed_from_env(pool: asyncpg.Pool) -> None:
    """Insere uma mensagem inicial na fila a partir de variáveis de ambiente.

    Ativado apenas quando TEST_CONVERSATION_ID e TEST_CONTENT estiverem
    definidos — útil para testes controlados sem INSERT manual.
    """
    conv_id = os.getenv("TEST_CONVERSATION_ID")
    content = os.getenv("TEST_CONTENT")
    if not conv_id or not content:
        return
    user_id = os.getenv("TEST_USER_ID", "0")
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO message_queue (moodle_user_id, conversation_id, content)"
            " VALUES ($1, $2, $3)",
            user_id, conv_id, content,
        )
    logger.info("seed: mensagem de teste inserida (convid=%s)", conv_id)


async def main() -> None:
    load_dotenv()

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    username = _env("MOODLE_USER")
    password = _env("MOODLE_PASS")
    db_dsn = os.getenv(
        "DATABASE_URL",
        "postgresql://bot_user:bot_password@localhost:5432/moodle_queue",
    )

    pool = await asyncpg.create_pool(db_dsn, min_size=1, max_size=4)
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S, follow_redirects=True) as client:
            state = BotState(client=client)
            await authenticate(state, username, password)
            await seed_from_env(pool)

            conv_id = os.getenv("TEST_CONVERSATION_ID")

            tasks = [
                asyncio.create_task(keep_alive_loop(state), name="keep-alive"),
            ]
            if conv_id:
                tasks.append(asyncio.create_task(
                    reader_loop(state, pool, username, password, conv_id),
                    name="reader",
                ))
                logger.info("reader iniciado (convid=%s, bot_user_id=%s)", conv_id, state.user_id)

            try:
                await dispatcher_loop(state, pool, username, password)
            finally:
                for t in tasks:
                    t.cancel()
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM message_queue")
        logger.info("fila limpa")
        await pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
