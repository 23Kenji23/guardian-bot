#!/usr/bin/env python3
"""Guardian — um bot de monitoramento simples. Roda no seu celular, PC ou servidor; avisa quando algo cai e quando volta.
A simple monitoring bot. Runs on your phone, PC or server; tells you when something goes down and when it is back.

  • Verifica sites (HTTP/HTTPS) e portas (TCP) em intervalos.            Checks websites (HTTP/HTTPS) and TCP ports.
  • Avisa por Telegram, webhook (Discord, Slack, ntfy…) e e-mail.         Alerts via Telegram, webhook and e-mail.
  • Telegram: /status, /verificar, /pausar, /retomar. API local (opcional).  Telegram: /status, /check, /pause, /resume. Optional local API.
  • Sinal de vida externo + checks "push" para scripts/cron.               External heartbeat + "push" checks for scripts/cron.
  • Um arquivo, só a biblioteca padrão do Python 3.11+. Nada para instalar. One file, Python 3.11+ stdlib only. Nothing to install.

Licença MIT. Sem garantias. Veja README.md.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hmac
import html
import http.client
import json
import logging
import os
import queue
import re
import signal
import smtplib
import socket
import ssl
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

__version__ = "1.1.0"
USER_AGENT = f"Guardian/{__version__} (monitoring bot)"
MIN_INTERVAL = 5
MAX_PUSH_INTERVAL = 31 * 86400      # um sinal por mês ainda é um cron válido
MAX_PAUSE = 7 * 86400
DEFAULT_PAUSE = 3600
LOOP_ALIVE_SECONDS = 30             # o sinal de vida só sai se o laço principal andou há menos que isso
MAX_EVENTS = 200
CERT_RECHECK_SECONDS = 6 * 3600
log = logging.getLogger("guardian")

# --------------------------------------------------------------------------- textos
STR = {
    "pt": {
        "down": "🔴 {name} FORA DO AR — {detail}",
        "up": "🟢 {name} voltou ao ar — ficou fora {dur}",
        "still": "🔴 {name} continua fora do ar há {dur} — {detail}",
        "cert": "🟠 {name}: o certificado SSL vence em {days} dia(s)",
        "cert_expired": "🟠 {name}: o certificado SSL já venceu",
        "test": "✅ Teste do Guardian: se você leu isto, os avisos funcionam.",
        "started": "🛡️ Guardian {version} iniciado, vigiando {n} item(ns).",
        "status_head": "🛡️ Guardian — {up} no ar, {down} fora, {unknown} aguardando",
        "no_checks_yet": "ainda sem verificação",
        "help": ("Comandos:\n/status — situação atual\n/verificar [item] — verifica agora\n"
                 "/pausar [tempo] [item] — silencia os avisos (ex.: /pausar 2h · /pausar 30m Meu site)\n"
                 "/retomar [item] — volta a avisar\n/ajuda — esta mensagem"),
        "unknown_cmd": "Comando não reconhecido. Use /ajuda.",
        "pause_set_all": "⏸️ Avisos em pausa por {dur}. Continuo verificando e, se algo ainda estiver fora quando acabar, eu aviso. Para encerrar antes: /retomar",
        "pause_set_one": "⏸️ Avisos de {name} em pausa por {dur}. Para encerrar antes: /retomar {name}",
        "pause_end_all": "▶️ A pausa terminou: os avisos voltaram ao normal.",
        "pause_end_one": "▶️ A pausa de {name} terminou: os avisos voltaram ao normal.",
        "resume_none": "Não havia nenhuma pausa ativa.",
        "resume_none_one": "{name} não estava em pausa.",
        "resume_blocked": "Há uma pausa geral ativa. Use /retomar (sem nome) para encerrá-la.",
        "pause_bad_time": "Tempo inválido. Use, por exemplo, /pausar 30m, /pausar 2h ou /pausar 1d (de 1 minuto a 7 dias).",
        "item_unknown": "Não achei o item \"{name}\". Itens: {names}",
        "check_now_head": "🔄 Verificado agora.",
        "check_now_nothing": "Não há o que verificar agora: itens de sinal (push) só mudam quando o sinal chega ou deixa de chegar.",
        "status_paused": "⏸️ Avisos em pausa ({scope}): faltam {dur}",
        "scope_all": "tudo",
        "paused_tag": "em pausa",
        "no_signal": "nenhum sinal há {dur} (esperado a cada {every})",
        "signal_failed": "o script avisou que falhou",
        "last_signal": "último sinal há {dur}",
        "err_timeout": "sem resposta em {timeout:g} s",
        "err_dns": "o nome não foi encontrado (DNS)",
        "err_refused": "conexão recusada",
        "err_reset": "conexão interrompida",
        "err_cert": "certificado SSL inválido",
        "err_ssl": "erro de SSL",
        "err_protocol": "resposta inválida do servidor",
        "err_network": "erro de rede ({detail})",
        "err_other": "erro: {detail}",
        "bad_status": "HTTP {status}",
        "redirect_failed": "redirecionamento recusado ou em laço (HTTP {status})",
        "text_missing": "o texto esperado não apareceu na página",
        "tcp_ok": "porta aberta",
        "latency": "{ms} ms",
    },
    "en": {
        "down": "🔴 {name} is DOWN — {detail}",
        "up": "🟢 {name} is back up — it was down for {dur}",
        "still": "🔴 {name} is still down after {dur} — {detail}",
        "cert": "🟠 {name}: the SSL certificate expires in {days} day(s)",
        "cert_expired": "🟠 {name}: the SSL certificate has expired",
        "test": "✅ Guardian test: if you can read this, alerts work.",
        "started": "🛡️ Guardian {version} started, watching {n} item(s).",
        "status_head": "🛡️ Guardian — {up} up, {down} down, {unknown} waiting",
        "no_checks_yet": "not checked yet",
        "help": ("Commands:\n/status — current state\n/check [item] — check now\n"
                 "/pause [time] [item] — silence alerts (e.g. /pause 2h · /pause 30m My site)\n"
                 "/resume [item] — alert again\n/help — this message"),
        "unknown_cmd": "Unknown command. Use /help.",
        "pause_set_all": "⏸️ Alerts paused for {dur}. I keep checking and, if something is still down when it ends, I will tell you. To end early: /resume",
        "pause_set_one": "⏸️ Alerts for {name} paused for {dur}. To end early: /resume {name}",
        "pause_end_all": "▶️ The pause is over: alerts are back to normal.",
        "pause_end_one": "▶️ The pause for {name} is over: alerts are back to normal.",
        "resume_none": "There was no active pause.",
        "resume_none_one": "{name} was not paused.",
        "resume_blocked": "A pause for everything is active. Use /resume (without a name) to end it.",
        "pause_bad_time": "Invalid time. For example: /pause 30m, /pause 2h or /pause 1d (1 minute to 7 days).",
        "item_unknown": "I could not find the item \"{name}\". Items: {names}",
        "check_now_head": "🔄 Checked just now.",
        "check_now_nothing": "Nothing to check now: push items only change when the signal arrives or stops arriving.",
        "status_paused": "⏸️ Alerts paused ({scope}): {dur} left",
        "scope_all": "everything",
        "paused_tag": "paused",
        "no_signal": "no signal for {dur} (expected every {every})",
        "signal_failed": "the script reported a failure",
        "last_signal": "last signal {dur} ago",
        "err_timeout": "no answer in {timeout:g} s",
        "err_dns": "name not found (DNS)",
        "err_refused": "connection refused",
        "err_reset": "connection reset",
        "err_cert": "invalid SSL certificate",
        "err_ssl": "SSL error",
        "err_protocol": "invalid response from the server",
        "err_network": "network error ({detail})",
        "err_other": "error: {detail}",
        "bad_status": "HTTP {status}",
        "redirect_failed": "redirect refused or looping (HTTP {status})",
        "text_missing": "the expected text was not found on the page",
        "tcp_ok": "port open",
        "latency": "{ms} ms",
    },
}


def tr(lang: str, key: str, **kw: Any) -> str:
    return STR[lang][key].format(**kw)


def fmt_duration(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} s"
    m = s // 60
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h} h {m} min" if m else f"{h} h"
    d, h = divmod(h, 24)
    return f"{d} d {h} h" if h else f"{d} d"


def iso(ts: float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- segredos fora do registro
class Redactor(logging.Filter):
    """Troca qualquer segredo conhecido por *** antes de a linha chegar ao registro."""

    def __init__(self) -> None:
        super().__init__()
        self.secrets: set[str] = set()

    def add(self, value: str) -> None:
        if value and len(value) >= 4:
            self.secrets.add(value)

    def filter(self, record: logging.LogRecord) -> bool:
        text = record.getMessage()
        for secret in self.secrets:
            text = text.replace(secret, "***")
        record.msg, record.args = text, ()
        return True


REDACT = Redactor()
log.addFilter(REDACT)


# --------------------------------------------------------------------------- configuração
class ConfigError(Exception):
    pass


@dataclass
class Check:
    name: str
    kind: str                       # "http" | "tcp" | "push"
    interval: float
    timeout: float
    url: str = ""
    host: str = ""
    port: int = 0
    expect_status: list[int] | None = None
    contains: str = ""
    cert_warn_days: int = 14
    grace: float = 60               # só "push": folga, em segundos, além do intervalo esperado

    def target(self) -> str:
        """Destino SEM usuário, senha ou query (a URL pode carregar segredos)."""
        if self.kind == "tcp":
            return f"{self.host}:{self.port}"
        if self.kind == "push":
            return "/ping/" + urllib.parse.quote(self.name, safe="")
        p = urllib.parse.urlsplit(self.url)
        host = p.hostname or ""
        if p.port:
            host += f":{p.port}"
        return f"{p.scheme}://{host}{p.path}"


@dataclass
class Config:
    language: str = "pt"
    interval: float = 60
    timeout: float = 10
    failures_to_alert: int = 2
    remind_minutes: int = 0
    state_file: Path = Path("guardian-state.json")
    api: dict[str, Any] = field(default_factory=dict)
    heartbeat: dict[str, Any] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    notify: dict[str, dict[str, Any]] = field(default_factory=dict)
    path: Path | None = None


EXAMPLE_CONFIG = '''# Guardian — configuração. Copie para guardian.toml e edite.  (Config: copy to guardian.toml and edit.)
# Nunca coloque senhas/tokens aqui se puder evitar: use as variáveis de ambiente (token_env, password_env).

[guardian]
language = "pt"            # "pt" ou "en"
interval = 60              # segundos entre verificações (mínimo 5)
timeout = 10               # segundos de espera por resposta
failures_to_alert = 2      # falhas seguidas antes de avisar "fora do ar" (evita alarme falso)
remind_minutes = 0         # repetir o aviso enquanto continuar fora (0 = não repetir)
state_file = "guardian-state.json"

# API local somente leitura + página de status. Desligada por padrão.
[api]
enabled = false
host = "127.0.0.1"         # só este aparelho. Use "0.0.0.0" apenas se souber o que está fazendo e defina um token.
port = 8765
token_env = ""             # nome de uma variável de ambiente com a senha da API (opcional em 127.0.0.1; obrigatória se houver check "push")

# "Quem vigia o vigia": o Guardian avisa um serviço EXTERNO (ex.: healthchecks.io, Uptime Kuma em modo push) que está vivo.
# Se os sinais pararem (aparelho desligado, sem internet, Guardian travado), é esse serviço que te avisa.
[heartbeat]
enabled = false
url_env = "GUARDIAN_HEARTBEAT_URL"   # a URL de sinal do serviço externo (é um segredo: fica na variável de ambiente)
interval = 60                        # segundos entre sinais (10 a 3600)

# ---- o que vigiar (repita o bloco [[check]] quantas vezes quiser) -------------------------------

[[check]]
name = "Meu site"
type = "http"
url = "https://example.com"
# expect_status = [200]            # padrão: qualquer 200–399
# contains = "Bem-vindo"           # texto que precisa aparecer na página
# interval = 30                    # sobrescreve o padrão
# cert_warn_days = 14              # avisa quando o certificado SSL estiver perto de vencer (0 = não avisar)

# [[check]]
# name = "Servidor SSH"
# type = "tcp"
# host = "meu-servidor.exemplo.com"
# port = 22

# Check "push": quem dá o sinal é o SEU script/cron (ex.: no fim do backup: curl -fsS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/ping/Backup).
# O Guardian avisa se o sinal deixar de chegar. Exige [api] enabled = true e token_env. Use /ping/Backup/fail para o script dizer "falhei".
# [[check]]
# name = "Backup"
# type = "push"
# interval = 86400                 # de quanto em quanto tempo o sinal deve chegar (segundos)
# grace = 3600                     # folga antes de avisar (segundos; padrão 60)

# ---- onde avisar (ligue os que quiser) ---------------------------------------------------------

# Telegram: crie o bot com @BotFather, guarde o token em GUARDIAN_TELEGRAM_TOKEN e descubra seu chat_id (veja o README).
[notify.telegram]
enabled = false
token_env = "GUARDIAN_TELEGRAM_TOKEN"
chat_id = ""
commands = true            # responde /status, /verificar, /pausar e /retomar neste chat (só este chat_id manda)

# Webhook: Discord, Slack, ntfy ou genérico (JSON).
[notify.webhook]
enabled = false
url_env = "GUARDIAN_WEBHOOK_URL"
format = "generic"         # "generic" | "discord" | "slack" | "ntfy"

# E-mail (SMTP).
[notify.email]
enabled = false
host = "smtp.exemplo.com"
port = 587
security = "starttls"      # "starttls" | "ssl" | "none"
username = ""
password_env = "GUARDIAN_SMTP_PASSWORD"
from = "guardian@exemplo.com"
to = ["voce@exemplo.com"]
'''

_ALLOWED = {
    "guardian": {"language", "interval", "timeout", "failures_to_alert", "remind_minutes", "state_file"},
    "api": {"enabled", "host", "port", "token_env"},
    "heartbeat": {"enabled", "url_env", "url", "interval"},
    "check": {"name", "type", "url", "host", "port", "expect_status", "contains", "interval", "timeout", "cert_warn_days", "grace"},
    "telegram": {"enabled", "token_env", "token", "chat_id", "commands", "api_base"},
    "webhook": {"enabled", "url_env", "url", "format"},
    "email": {"enabled", "host", "port", "security", "username", "password_env", "password", "from", "to"},
}


def _only(section: str, data: dict, where: str) -> None:
    extra = set(data) - _ALLOWED[section]
    if extra:
        raise ConfigError(f"{where}: chave(s) desconhecida(s): {', '.join(sorted(extra))} (erro de digitação? / typo?)")


def _num(value: Any, where: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not (low <= value <= high):
        raise ConfigError(f"{where}: precisa ser um número entre {low:g} e {high:g}")
    return value


def secret_from(section: dict[str, Any], env_key: str, literal_key: str) -> str:
    env_name = str(section.get(env_key) or "")
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    return str(section.get(literal_key) or "")


def parse_config(data: dict[str, Any], path: Path | None = None) -> Config:
    unknown = set(data) - {"guardian", "api", "heartbeat", "check", "notify"}
    if unknown:
        raise ConfigError(f"seção desconhecida: {', '.join(sorted(unknown))}")
    g = data.get("guardian", {})
    _only("guardian", g, "[guardian]")
    cfg = Config(path=path)
    cfg.language = str(g.get("language", "pt"))
    if cfg.language not in STR:
        raise ConfigError('[guardian] language: use "pt" ou "en"')
    cfg.interval = _num(g.get("interval", 60), "[guardian] interval", MIN_INTERVAL, 86400)
    cfg.timeout = _num(g.get("timeout", 10), "[guardian] timeout", 1, 120)
    cfg.failures_to_alert = int(_num(g.get("failures_to_alert", 2), "[guardian] failures_to_alert", 1, 20))
    cfg.remind_minutes = int(_num(g.get("remind_minutes", 0), "[guardian] remind_minutes", 0, 10080))
    base = path.parent if path else Path.cwd()
    state = Path(str(g.get("state_file", "guardian-state.json"))).expanduser()
    cfg.state_file = state if state.is_absolute() else base / state

    api = data.get("api", {})
    _only("api", api, "[api]")
    cfg.api = {"enabled": bool(api.get("enabled", False)), "host": str(api.get("host", "127.0.0.1")),
               "port": int(_num(api.get("port", 8765), "[api] port", 1, 65535)), "token": os.environ.get(str(api.get("token_env") or ""), "")}
    REDACT.add(cfg.api["token"])
    if cfg.api["enabled"] and cfg.api["host"] not in {"127.0.0.1", "localhost", "::1"} and not cfg.api["token"]:
        raise ConfigError("[api] host fora de 127.0.0.1 exige um token (defina token_env e a variável de ambiente)")

    hb = data.get("heartbeat", {})
    _only("heartbeat", hb, "[heartbeat]")
    if hb.get("enabled", False):
        hb_url = secret_from(hb, "url_env", "url")
        hp = urllib.parse.urlsplit(hb_url)
        if hp.scheme not in {"http", "https"} or not hp.hostname:
            raise ConfigError("[heartbeat]: falta a URL (variável de ambiente url_env) ou ela não é http(s)")
        REDACT.add(hb_url)
        cfg.heartbeat = {"url": hb_url, "interval": _num(hb.get("interval", 60), "[heartbeat] interval", 10, 3600)}

    names: set[str] = set()
    for i, c in enumerate(data.get("check", []), 1):
        where = f"[[check]] #{i}"
        _only("check", c, where)
        name = str(c.get("name", "")).strip()
        if not name or len(name) > 80:
            raise ConfigError(f"{where}: 'name' é obrigatório (até 80 caracteres)")
        if name in names:
            raise ConfigError(f"{where}: o nome '{name}' está repetido")
        names.add(name)
        kind = str(c.get("type", "http"))
        if kind == "push" and "interval" not in c:
            raise ConfigError(f"{where} '{name}': type = \"push\" exige 'interval' (de quanto em quanto tempo o sinal deve chegar, em segundos)")
        if kind != "push" and "grace" in c:
            raise ConfigError(f"{where} '{name}': 'grace' só vale para type = \"push\"")
        interval = _num(c.get("interval", cfg.interval), f"{where} interval", MIN_INTERVAL, MAX_PUSH_INTERVAL if kind == "push" else 86400)
        timeout = _num(c.get("timeout", cfg.timeout), f"{where} timeout", 1, 120)
        check = Check(name=name, kind=kind, interval=interval, timeout=timeout)
        if kind == "push":
            check.grace = _num(c.get("grace", 60), f"{where} grace", 0, 86400)
        elif kind == "http":
            url = str(c.get("url", ""))
            p = urllib.parse.urlsplit(url)
            if p.scheme not in {"http", "https"} or not p.hostname:
                raise ConfigError(f"{where} '{name}': 'url' precisa começar com http:// ou https://")
            check.url = url
            es = c.get("expect_status")
            if es is not None:
                es = es if isinstance(es, list) else [es]
                if not es or not all(isinstance(x, int) and not isinstance(x, bool) and 100 <= x <= 599 for x in es):
                    raise ConfigError(f"{where} '{name}': expect_status precisa ser um código HTTP (ou lista) entre 100 e 599")
                check.expect_status = es
            check.contains = str(c.get("contains", ""))
            check.cert_warn_days = int(_num(c.get("cert_warn_days", 14), f"{where} cert_warn_days", 0, 365))
        elif kind == "tcp":
            host = str(c.get("host", "")).strip()
            if not host:
                raise ConfigError(f"{where} '{name}': 'host' é obrigatório")
            check.host = host
            check.port = int(_num(c.get("port", 0), f"{where} port", 1, 65535))
        else:
            raise ConfigError(f'{where} \'{name}\': type precisa ser "http", "tcp" ou "push"')
        cfg.checks.append(check)
    if any(c.kind == "push" for c in cfg.checks) and not (cfg.api["enabled"] and cfg.api["token"]):
        raise ConfigError('check type = "push" precisa de [api] enabled = true e de um token (token_env) para receber os sinais')

    notify = data.get("notify", {})
    if set(notify) - {"telegram", "webhook", "email"}:
        raise ConfigError("[notify]: canais aceitos: telegram, webhook, email")
    for key, section in notify.items():
        _only(key, section, f"[notify.{key}]")
        if not section.get("enabled", False):
            continue
        if key == "telegram":
            token = secret_from(section, "token_env", "token")
            if not token or not str(section.get("chat_id", "")).strip():
                raise ConfigError("[notify.telegram]: falta o token (variável de ambiente token_env) ou o chat_id")
            REDACT.add(token)
            cfg.notify[key] = {**section, "token": token, "chat_id": str(section["chat_id"]).strip()}
        elif key == "webhook":
            url = secret_from(section, "url_env", "url")
            fmt = str(section.get("format", "generic"))
            p = urllib.parse.urlsplit(url)
            if p.scheme not in {"http", "https"} or not p.hostname:
                raise ConfigError("[notify.webhook]: falta a URL (variável de ambiente url_env) ou ela não é http(s)")
            if fmt not in {"generic", "discord", "slack", "ntfy"}:
                raise ConfigError('[notify.webhook] format: "generic", "discord", "slack" ou "ntfy"')
            REDACT.add(url)
            cfg.notify[key] = {**section, "url": url, "format": fmt}
        elif key == "email":
            to = section.get("to", [])
            to = [to] if isinstance(to, str) else list(to)
            if not section.get("host") or not section.get("from") or not to:
                raise ConfigError("[notify.email]: host, from e to são obrigatórios")
            if section.get("security", "starttls") not in {"starttls", "ssl", "none"}:
                raise ConfigError('[notify.email] security: "starttls", "ssl" ou "none"')
            password = secret_from(section, "password_env", "password")
            REDACT.add(password)
            cfg.notify[key] = {**section, "to": to, "password": password}
    return cfg


def load_config(path: Path) -> Config:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"não consegui ler {path}: {exc.strerror}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: erro de sintaxe: {exc}") from exc
    cfg = parse_config(data, path.resolve())
    if os.name == "posix" and any(k in raw.decode("utf-8", "ignore") for k in ('token = "', 'password = "')):
        try:
            if path.stat().st_mode & 0o077:
                log.warning("%s tem segredo escrito e pode ser lido por outros usuários: rode chmod 600 %s", path, path)
        except OSError:
            pass
    return cfg


# --------------------------------------------------------------------------- verificações
@dataclass
class Result:
    ok: bool
    ms: int | None
    detail: str


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        if urllib.parse.urlsplit(newurl).scheme not in {"http", "https"}:
            raise urllib.error.URLError("redirect to unsupported scheme")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def describe_error(exc: BaseException, lang: str, timeout: float) -> str:
    e: BaseException = exc
    if isinstance(e, urllib.error.URLError) and isinstance(e.reason, BaseException):
        e = e.reason
    if isinstance(e, TimeoutError):
        return tr(lang, "err_timeout", timeout=timeout)
    if isinstance(e, socket.gaierror):
        return tr(lang, "err_dns")
    if isinstance(e, ConnectionRefusedError):
        return tr(lang, "err_refused")
    if isinstance(e, (ConnectionResetError, ConnectionAbortedError, http.client.RemoteDisconnected)):
        return tr(lang, "err_reset")
    if isinstance(e, ssl.SSLCertVerificationError):
        return tr(lang, "err_cert")
    if isinstance(e, ssl.SSLError):
        return tr(lang, "err_ssl")
    if isinstance(e, http.client.HTTPException):
        return tr(lang, "err_protocol")
    if isinstance(e, OSError):
        return tr(lang, "err_network", detail=e.strerror or type(e).__name__)
    return tr(lang, "err_other", detail=type(e).__name__)


def check_http(c: Check, lang: str) -> Result:
    opener = urllib.request.build_opener(_SafeRedirect)
    req = urllib.request.Request(c.url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    started = time.monotonic()
    body = b""
    try:
        try:
            with opener.open(req, timeout=c.timeout) as resp:
                status = resp.status
                if c.contains:
                    body = resp.read(262144)
        except urllib.error.HTTPError as exc:
            status = exc.code
            if c.contains:
                try:
                    body = exc.read(262144)
                except Exception:  # noqa: BLE001 - corpo ilegível não muda o veredito
                    body = b""
    except Exception as exc:  # noqa: BLE001 - qualquer falha é "fora do ar", com motivo
        return Result(False, None, describe_error(exc, lang, c.timeout))
    ms = int((time.monotonic() - started) * 1000)
    if 300 <= status < 400 and status not in (c.expect_status or []):
        return Result(False, ms, tr(lang, "redirect_failed", status=status))   # o urllib só devolve 3xx quando NÃO conseguiu seguir
    good = status in c.expect_status if c.expect_status else 200 <= status < 400
    if not good:
        return Result(False, ms, tr(lang, "bad_status", status=status))
    if c.contains and c.contains not in body.decode("utf-8", "replace"):
        return Result(False, ms, tr(lang, "text_missing"))
    return Result(True, ms, "")


def check_tcp(c: Check, lang: str) -> Result:
    started = time.monotonic()
    try:
        with socket.create_connection((c.host, c.port), timeout=c.timeout):
            pass
    except Exception as exc:  # noqa: BLE001
        return Result(False, None, describe_error(exc, lang, c.timeout))
    return Result(True, int((time.monotonic() - started) * 1000), "")


def run_check(c: Check, lang: str) -> Result:
    if c.kind == "push":
        raise ValueError("push checks are not polled")    # quem avalia "push" é o Engine, pelo sinal recebido
    return check_http(c, lang) if c.kind == "http" else check_tcp(c, lang)


def cert_days_left(host: str, port: int, timeout: float) -> int:
    ctx = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            cert = tls.getpeercert()
    return int((ssl.cert_time_to_seconds(cert["notAfter"]) - time.time()) // 86400)


# --------------------------------------------------------------------------- estado e regras de aviso
class Engine:
    """Guarda a situação de cada item e decide QUANDO avisar. Sem rede: fácil de testar."""

    def __init__(self, cfg: Config, clock: Callable[[], float] = time.time) -> None:
        self.cfg, self.clock = cfg, clock
        self.lock = threading.RLock()
        self.checks = {c.name: c for c in cfg.checks}
        self.state: dict[str, Any] = {"version": 1, "checks": {}, "events": [], "pauses": {}}
        self.started = clock()          # referência dos checks "push": quem acabou de ligar ganha uma folga inteira
        self._dirty = False
        self._last_save = 0.0

    # -- persistência
    def load(self) -> None:
        try:
            data = json.loads(self.cfg.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        saved = data.get("checks", {}) if isinstance(data, dict) else {}
        with self.lock:
            self.state["checks"] = {n: saved[n] for n in self.checks if isinstance(saved.get(n), dict)}
            events = data.get("events", []) if isinstance(data, dict) else []
            self.state["events"] = [e for e in events if isinstance(e, dict)][-MAX_EVENTS:]
            pauses = data.get("pauses", {}) if isinstance(data, dict) else {}
            self.state["pauses"] = {k: float(v) for k, v in (pauses.items() if isinstance(pauses, dict) else [])
                                    if (k == "*" or k in self.checks) and isinstance(v, (int, float)) and not isinstance(v, bool)}

    def save(self, force: bool = False) -> None:
        with self.lock:
            if not (self._dirty or force):
                return
            payload = json.dumps(self.state, ensure_ascii=False, indent=1)
            self._dirty = False
        path = self.cfg.state_file
        tmp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("não consegui salvar o estado: %s", exc.strerror)

    def _st(self, name: str) -> dict[str, Any]:
        return self.state["checks"].setdefault(name, {"status": "unknown", "failures": 0})

    def _event(self, kind: str, name: str, text: str, now: float, silent: bool = False) -> dict[str, Any]:
        """`silent`: fica no histórico, mas NÃO é enviado a nenhum canal (pausa/manutenção)."""
        ev: dict[str, Any] = {"time": iso(now), "kind": kind, "check": name, "text": text}
        if silent:
            ev["silent"] = True
        self.state["events"].append(ev)
        del self.state["events"][:-MAX_EVENTS]
        self._dirty = True
        return ev

    def _muted(self, name: str, now: float) -> bool:
        pauses = self.state.get("pauses", {})
        return pauses.get("*", 0) > now or pauses.get(name, 0) > now

    # -- regras
    def record(self, name: str, res: Result) -> list[dict[str, Any]]:
        lang, now, events = self.cfg.language, self.clock(), []
        check = self.checks.get(name)
        need = 1 if check and check.kind == "push" else self.cfg.failures_to_alert   # sinal que não chegou já é prova: não espera 2ª falha
        with self.lock:
            st = self._st(name)
            muted = self._muted(name, now)
            st.update(last_check=now, last_ms=res.ms, detail=res.detail)
            if res.ok:
                st["failures"] = 0
                if st["status"] == "down":
                    dur = fmt_duration(now - st.get("down_since", now))
                    # Se você JÁ foi avisado da queda, o "voltou" chega mesmo em pausa (senão ficaria sem desfecho).
                    quiet = muted and not st.get("down_notified", True)
                    events.append(self._event("up", name, tr(lang, "up", name=name, dur=dur), now, silent=quiet))
                if st["status"] != "up":
                    st.update(status="up", since=now)
                    st.pop("down_since", None)
                st.pop("down_notified", None)
            else:
                st["failures"] = st.get("failures", 0) + 1
                if st["status"] != "down" and st["failures"] >= need:
                    st.update(status="down", since=now, down_since=now, down_notified=not muted)
                    if not muted:
                        st["last_alert"] = now
                    events.append(self._event("down", name, tr(lang, "down", name=name, detail=res.detail), now, silent=muted))
                elif (st["status"] == "down" and not muted and self.cfg.remind_minutes
                      and now - st.get("last_alert", now) >= self.cfg.remind_minutes * 60):
                    st.update(last_alert=now, down_notified=True)
                    dur = fmt_duration(now - st.get("down_since", now))
                    events.append(self._event("still_down", name, tr(lang, "still", name=name, dur=dur, detail=res.detail), now))
            self._dirty = True
        return events

    def record_cert(self, name: str, days: int | None) -> list[dict[str, Any]]:
        lang, now, events = self.cfg.language, self.clock(), []
        warn = self.checks[name].cert_warn_days
        with self.lock:
            st = self._st(name)
            st["cert_days"], st["cert_checked"] = days, now
            if days is not None and warn and days <= warn and not st.get("cert_alerted"):
                if not self._muted(name, now):        # em pausa não marca como avisado: tenta de novo na próxima conferência
                    st["cert_alerted"] = True
                    text = tr(lang, "cert_expired", name=name) if days < 0 else tr(lang, "cert", name=name, days=days)
                    events.append(self._event("cert", name, text, now))
            elif days is not None and days > warn:
                st["cert_alerted"] = False
            self._dirty = True
        return events

    # -- sinais de scripts e cron ("push")
    def ping(self, name: str, ok: bool = True) -> list[dict[str, Any]] | None:
        """Um script deu sinal. Devolve None se `name` não for um check "push"."""
        check = self.checks.get(name)
        if not check or check.kind != "push":
            return None
        with self.lock:
            self._st(name)["last_ping"] = self.clock()
        res = Result(True, None, "") if ok else Result(False, None, tr(self.cfg.language, "signal_failed"))
        return self.record(name, res)

    def evaluate_push(self, name: str) -> list[dict[str, Any]]:
        """Chamado o tempo todo: se o sinal esperado atrasou além do intervalo + folga, é falha."""
        c, now = self.checks[name], self.clock()
        with self.lock:
            last = self.state["checks"].get(name, {}).get("last_ping")
            if now - max(last or 0, self.started) <= c.interval + c.grace:
                return []
            silent_for = now - (last if last else self.started)
        return self.record(name, Result(False, None, tr(self.cfg.language, "no_signal", dur=fmt_duration(silent_for), every=fmt_duration(c.interval))))

    # -- pausa e manutenção
    def pause(self, seconds: float, name: str | None = None) -> None:
        now = self.clock()
        with self.lock:
            self.state.setdefault("pauses", {})["*" if name is None else name] = now + seconds
            key = "pause_set_all" if name is None else "pause_set_one"
            self._event("pause", name or "*", tr(self.cfg.language, key, name=name or "", dur=fmt_duration(seconds)), now, silent=True)
        self.save(force=True)

    def _unmute(self, scopes: list[str], now: float, announce: bool) -> list[dict[str, Any]]:
        lang, events = self.cfg.language, []
        pauses = self.state.setdefault("pauses", {})
        for s in scopes:
            pauses.pop(s, None)
            if announce:
                events.append(self._event("resumed", s, tr(lang, "pause_end_all" if s == "*" else "pause_end_one", name=s), now))
        for n in (list(self.checks) if "*" in scopes else scopes):
            st = self.state["checks"].get(n, {})
            # caiu durante a pausa e ninguém foi avisado: avisa agora que continua fora
            if st.get("status") == "down" and not st.get("down_notified", True) and not self._muted(n, now):
                st.update(down_notified=True, last_alert=now)
                dur = fmt_duration(now - st.get("down_since", now))
                events.append(self._event("still_down", n, tr(lang, "still", name=n, dur=dur, detail=st.get("detail", "")), now))
        self._dirty = True
        return events

    def resume(self, name: str | None = None) -> tuple[str, list[dict[str, Any]]]:
        """Encerra a pausa. Devolve ("ok"|"none"|"blocked", eventos a enviar)."""
        now = self.clock()
        with self.lock:
            pauses = self.state.setdefault("pauses", {})
            if name is None:
                scopes = list(pauses)
            elif name in pauses:
                scopes = [name]
            else:
                return ("blocked" if "*" in pauses else "none"), []
            if not scopes:
                return "none", []
            self._event("resume", name or "*", tr(self.cfg.language, "pause_end_all" if name is None else "pause_end_one", name=name or ""), now, silent=True)
            events = self._unmute(scopes, now, announce=False)
        self.save(force=True)
        return "ok", events

    def expire_pauses(self) -> list[dict[str, Any]]:
        now = self.clock()
        with self.lock:
            ended = [s for s, until in self.state.get("pauses", {}).items() if until <= now]
            return self._unmute(ended, now, announce=True) if ended else []

    def resolve_name(self, text: str) -> str | None:
        wanted = " ".join(text.split()).casefold()
        return next((n for n in self.checks if " ".join(n.split()).casefold() == wanted), None)

    def cert_due(self, name: str) -> bool:
        with self.lock:
            return self.clock() - self.state["checks"].get(name, {}).get("cert_checked", 0) >= CERT_RECHECK_SECONDS

    # -- leitura
    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            now = self.clock()
            items, counts = [], {"up": 0, "down": 0, "unknown": 0}
            for name, c in self.checks.items():
                st = self.state["checks"].get(name, {})
                status = st.get("status", "unknown")
                counts[status] = counts.get(status, 0) + 1
                item = {"name": name, "type": c.kind, "target": c.target(), "status": status, "latency_ms": st.get("last_ms"),
                        "detail": st.get("detail", ""), "since": iso(st.get("since")), "last_check": iso(st.get("last_check")),
                        "cert_days": st.get("cert_days"), "paused": self._muted(name, now)}
                if c.kind == "push":
                    item["last_ping"] = iso(st.get("last_ping"))
                items.append(item)
            paused = {k: iso(v) for k, v in self.state.get("pauses", {}).items() if v > now}
            return {"guardian": __version__, "time": iso(now), "summary": counts, "paused": paused, "checks": items}

    def status_text(self, only: list[str] | None = None) -> str:
        lang, snap = self.cfg.language, self.snapshot()
        with self.lock:
            now = self.clock()
            left = {k: v - now for k, v in self.state.get("pauses", {}).items() if v > now}
            pings = {n: st.get("last_ping") for n, st in self.state["checks"].items()}
        s, lines = snap["summary"], []
        if not only:
            lines.append(tr(lang, "status_head", up=s["up"], down=s["down"], unknown=s["unknown"]))
            for scope, secs in left.items():
                lines.append(tr(lang, "status_paused", scope=tr(lang, "scope_all") if scope == "*" else scope, dur=fmt_duration(secs)))
        for it in snap["checks"]:
            if only and it["name"] not in only:
                continue
            icon = {"up": "🟢", "down": "🔴"}.get(it["status"], "⚪")
            if it["status"] == "up" and it["latency_ms"] is not None:
                extra = tr(lang, "latency", ms=it["latency_ms"])
            elif it["status"] == "up" and pings.get(it["name"]):
                extra = tr(lang, "last_signal", dur=fmt_duration(now - pings[it["name"]]))
            elif it["status"] == "down":
                extra = it["detail"]
            else:
                extra = tr(lang, "no_checks_yet")
            tag = f" · ⏸️ {tr(lang, 'paused_tag')}" if it["paused"] else ""
            lines.append(f"{icon} {it['name']} — {extra}{tag}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- avisos
def _post(url: str, body: bytes, headers: dict[str, str], timeout: float = 15) -> bytes:
    req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT, **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(1 << 20)


_DURATION = re.compile(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m(?:in)?)?")


def parse_duration(text: str) -> int | None:
    """'30m', '2h', '1d', '1h30m' ou só '45' (= minutos) -> segundos. None se não for um tempo."""
    t = text.strip().lower()
    if t.isdigit():
        return int(t) * 60 if len(t) < 7 else None
    m = _DURATION.fullmatch(t)
    if not t or not m or not any(m.groups()):
        return None
    d, h, mi = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60


def handle_command(text: str, engine: Engine, runner: Any) -> str | None:
    """Interpreta uma mensagem do chat de controle. Devolve a resposta (None = não era um comando).
    Os comandos só mexem nos AVISOS (pausar, retomar, verificar): nunca executam nada nem tocam em arquivos."""
    words = text.strip().split()
    if not words or not words[0].startswith("/"):
        return None
    cmd, args, lang = words[0].split("@")[0].lower(), words[1:], engine.cfg.language
    if cmd == "/status":
        return engine.status_text()
    if cmd in {"/start", "/help", "/ajuda"}:
        return tr(lang, "help")
    unknown_item = lambda name: tr(lang, "item_unknown", name=name, names=", ".join(engine.checks) or "-")  # noqa: E731

    if cmd in {"/pausar", "/pause", "/manutencao", "/manutenção", "/maintenance"}:
        secs, rest = None, []
        for a in args:
            d = parse_duration(a)
            if secs is None and d is not None:
                secs = d
            else:
                rest.append(a)
        name = None
        if rest:
            name = engine.resolve_name(" ".join(rest))
            if name is None:
                return tr(lang, "pause_bad_time") if rest[0][0].isdigit() else unknown_item(" ".join(rest))
        secs = DEFAULT_PAUSE if secs is None else secs
        if not 60 <= secs <= MAX_PAUSE:
            return tr(lang, "pause_bad_time")
        engine.pause(secs, name)
        return tr(lang, "pause_set_one" if name else "pause_set_all", name=name or "", dur=fmt_duration(secs))

    if cmd in {"/retomar", "/resume"}:
        name = None
        if args:
            name = engine.resolve_name(" ".join(args))
            if name is None:
                return unknown_item(" ".join(args))
        outcome, events = engine.resume(name)
        if outcome == "blocked":
            return tr(lang, "resume_blocked")
        if outcome == "none":
            return tr(lang, "resume_none_one", name=name) if name else tr(lang, "resume_none")
        runner.emit(events)
        return tr(lang, "pause_end_one" if name else "pause_end_all", name=name or "")

    if cmd in {"/verificar", "/check"}:
        rest = [a for a in args if a.lower() not in {"agora", "now"}]
        name = None
        if rest:
            name = engine.resolve_name(" ".join(rest))
            if name is None:
                return unknown_item(" ".join(rest))
        done = runner.check_now(name)
        if not done:
            return tr(lang, "check_now_nothing")
        return tr(lang, "check_now_head") + "\n" + engine.status_text(done if name else None)

    return tr(lang, "unknown_cmd")


class Telegram:
    name = "telegram"

    def __init__(self, section: dict[str, Any]) -> None:
        self.token, self.chat_id = section["token"], section["chat_id"]
        self.base = str(section.get("api_base") or "https://api.telegram.org").rstrip("/")
        self.commands = bool(section.get("commands", True))

    def call(self, method: str, params: dict[str, Any], timeout: float = 15) -> Any:
        raw = _post(f"{self.base}/bot{self.token}/{method}", json.dumps(params).encode(), {"Content-Type": "application/json"}, timeout)
        data = json.loads(raw)
        if not data.get("ok"):
            raise RuntimeError(str(data.get("description", "erro do Telegram")))
        return data.get("result")

    def send(self, text: str, event: dict[str, Any]) -> None:
        self.call("sendMessage", {"chat_id": self.chat_id, "text": text[:4000], "disable_web_page_preview": True})

    def poll(self, engine: Engine, stop: threading.Event, runner: Any = None) -> None:
        """Escuta comandos. Só obedece ao chat_id configurado; o resto é ignorado em silêncio."""
        offset = None
        while not stop.is_set():
            try:
                params: dict[str, Any] = {"timeout": 25, "allowed_updates": ["message"]}
                if offset is not None:
                    params["offset"] = offset
                for upd in self.call("getUpdates", params, timeout=40) or []:
                    offset = upd["update_id"] + 1
                    msg = upd.get("message") or {}
                    if str((msg.get("chat") or {}).get("id")) != self.chat_id:
                        continue
                    reply = handle_command(msg.get("text") or "", engine, runner)
                    if reply is not None:
                        self.send(reply, {})
            except Exception as exc:  # noqa: BLE001
                log.warning("telegram (comandos): %s: %s", type(exc).__name__, exc)
                stop.wait(10)
            stop.wait(0.2)


class Webhook:
    name = "webhook"

    def __init__(self, section: dict[str, Any]) -> None:
        self.url, self.format = section["url"], section["format"]

    def send(self, text: str, event: dict[str, Any]) -> None:
        if self.format == "ntfy":
            _post(self.url, text.encode(), {"Content-Type": "text/plain; charset=utf-8", "Title": "Guardian"})
            return
        if self.format == "discord":
            payload: dict[str, Any] = {"content": text[:1900]}
        elif self.format == "slack":
            payload = {"text": text}
        else:
            payload = {"text": text, "kind": event.get("kind", "message"), "check": event.get("check"), "time": event.get("time"),
                       "guardian": __version__}
        _post(self.url, json.dumps(payload).encode(), {"Content-Type": "application/json"})


class Email:
    name = "email"

    def __init__(self, section: dict[str, Any]) -> None:
        self.s = section

    def send(self, text: str, event: dict[str, Any]) -> None:
        s = self.s
        msg = EmailMessage()
        msg["Subject"] = "Guardian: " + text.splitlines()[0][:120]
        msg["From"], msg["To"] = s["from"], ", ".join(s["to"])
        msg.set_content(text)
        security = s.get("security", "starttls")
        port = int(s.get("port", 465 if security == "ssl" else 587))
        smtp: smtplib.SMTP = (smtplib.SMTP_SSL(s["host"], port, timeout=20, context=ssl.create_default_context())
                              if security == "ssl" else smtplib.SMTP(s["host"], port, timeout=20))
        with smtp:
            if security == "starttls":
                smtp.starttls(context=ssl.create_default_context())
            if s.get("username"):
                smtp.login(s["username"], s["password"])
            smtp.send_message(msg)


class Heartbeat:
    """'Quem vigia o vigia': dá sinal de vida a um serviço EXTERNO. Se o aparelho, a internet ou o Guardian
    morrerem, os sinais param e é esse serviço (que não depende de você) quem avisa."""

    def __init__(self, url: str, interval: float, alive: Callable[[], bool]) -> None:
        self.url, self.interval, self.alive = url, interval, alive
        self.ok: bool | None = None

    def ping(self) -> None:
        req = urllib.request.Request(self.url, headers={"User-Agent": USER_AGENT})
        with urllib.request.build_opener(_SafeRedirect).open(req, timeout=10) as resp:
            resp.read(1024)

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            if self.alive():            # laço principal travado = sem sinal (de propósito)
                try:
                    self.ping()
                    good = True
                except Exception as exc:  # noqa: BLE001
                    good = False
                    if self.ok is not False:   # registra só a mudança, não uma linha por tentativa
                        log.warning("sinal de vida não chegou ao serviço externo: %s: %s", type(exc).__name__, exc)
                if good and self.ok is False:
                    log.info("sinal de vida voltou a chegar ao serviço externo")
                self.ok = good
            stop.wait(self.interval)


class Dispatcher:
    """Envia os avisos em segundo plano, com 3 tentativas. Um canal quebrado não segura os outros nem as verificações."""

    def __init__(self, channels: list[Any], retry_delays: tuple[float, ...] = (0, 2, 6)) -> None:
        self.channels, self.delays = channels, retry_delays
        self.q: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._work, name="guardian-notify", daemon=True)
        self.thread.start()

    def send(self, text: str, event: dict[str, Any] | None = None) -> None:
        self.q.put((text, event or {}))

    def _work(self) -> None:
        while True:
            item = self.q.get()
            try:
                if item is None:
                    return
                for ch in self.channels:
                    self.deliver(ch, *item)
            finally:
                self.q.task_done()

    def deliver(self, ch: Any, text: str, event: dict[str, Any]) -> bool:
        error: Exception | None = None
        for delay in self.delays:
            if delay:
                time.sleep(delay)
            try:
                ch.send(text, event)
                return True
            except Exception as exc:  # noqa: BLE001
                error = exc
        log.warning("aviso por %s falhou: %s: %s", ch.name, type(error).__name__, error)
        return False

    def flush(self) -> None:
        self.q.join()

    def close(self) -> None:
        self.q.put(None)
        self.thread.join(timeout=10)


def build_channels(cfg: Config) -> list[Any]:
    kinds = {"telegram": Telegram, "webhook": Webhook, "email": Email}
    return [kinds[k](v) for k, v in cfg.notify.items()]


# --------------------------------------------------------------------------- API local (somente leitura)
PAGE = """<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30"><title>Guardian</title><style>
:root{{color-scheme:light dark;--bg:#f6f7f9;--fg:#15181d;--card:#fff;--line:#dfe3e8;--ok:#188a42;--bad:#c62828;--mute:#697386}}
@media(prefers-color-scheme:dark){{:root{{--bg:#101318;--fg:#e8ebf0;--card:#181c23;--line:#2a303a;--ok:#4cc27a;--bad:#ff6b6b;--mute:#9aa3b2}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}}main{{max-width:760px;margin:0 auto;padding:24px 16px}}
h1{{font-size:22px;margin:0 0 4px}}p.s{{color:var(--mute);margin:0 0 18px}}
.row{{display:flex;gap:12px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:8px}}
.dot{{width:12px;height:12px;border-radius:50%;flex:none;background:var(--mute)}}.up{{background:var(--ok)}}.down{{background:var(--bad)}}
.n{{flex:1;min-width:0}}.n b{{display:block;overflow-wrap:anywhere}}.n small{{color:var(--mute);overflow-wrap:anywhere}}.r{{color:var(--mute);font-size:14px;white-space:nowrap}}
footer{{color:var(--mute);font-size:13px;margin-top:16px}}</style></head><body><main>
<h1>🛡️ Guardian</h1><p class="s">{summary}</p>{rows}<footer>Guardian {version} · {time}</footer></main></body></html>"""


def make_api(engine: Engine, host: str, port: int, token: str, emit: Callable[[list[dict[str, Any]]], None] | None = None) -> ThreadingHTTPServer:
    """Somente leitura, com UMA exceção deliberada: /ping/<item> (GET ou POST), que só existe com token e só
    atualiza o relógio de um check "push". Não executa nada e não grava nada além do estado do Guardian."""
    lang = engine.cfg.language

    class Handler(BaseHTTPRequestHandler):
        server_version, sys_version = "Guardian", ""

        def log_message(self, *args: Any) -> None:
            pass

        def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, obj: Any) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _authorized(self) -> bool:
            if not token:
                return True
            header = self.headers.get("Authorization", "")
            given = ""
            if header.lower().startswith("bearer "):
                given = header[7:].strip()
            elif header.lower().startswith("basic "):
                try:
                    given = base64.b64decode(header[6:]).decode("utf-8").partition(":")[2]
                except Exception:  # noqa: BLE001
                    given = ""
            return hmac.compare_digest(given.encode(), token.encode())

        def _unauthorized(self) -> None:
            self._send(401, b'{"error":"unauthorized"}', "application/json", {"WWW-Authenticate": 'Basic realm="Guardian"'})

        def _ping(self, path: str) -> None:
            parts = path.split("/")                      # ["", "ping", "<nome>"] ou [..., "fail"]
            if not token:                                # defesa extra: sem token, nunca aceita sinal
                return self._json(403, {"error": "token_required"})
            if self.command == "HEAD":                   # HEAD nunca pode dar sinal
                return self._json(405, {"error": "read_only"})
            if not (len(parts) == 3 or (len(parts) == 4 and parts[3] == "fail")):
                return self._json(404, {"error": "not_found"})
            events = engine.ping(urllib.parse.unquote(parts[2]), ok=len(parts) == 3)
            if events is None:
                return self._json(404, {"error": "unknown_push_check"})
            if emit:
                emit(events)
            self._json(200, {"status": "ok"})

        def do_HEAD(self) -> None:
            self.do_GET()

        def do_POST(self) -> None:
            path = urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"
            if not path.startswith("/ping/"):
                return self._method_not_allowed()
            try:
                size = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                size = -1
            if not 0 <= size <= 65536:
                return self._json(413, {"error": "body_too_large"})
            if size:
                self.rfile.read(size)                    # o corpo não é usado; lê para a resposta chegar inteira
            if not self._authorized():
                return self._unauthorized()
            self._ping(path)

        def do_GET(self) -> None:
            path = urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"
            if path == "/health":
                return self._json(200, {"status": "ok", "guardian": __version__})
            if not self._authorized():
                return self._unauthorized()
            if path.startswith("/ping/"):
                return self._ping(path)
            snap = engine.snapshot()
            if path == "/api/v1/status":
                return self._json(200, snap)
            if path == "/api/v1/checks":
                return self._json(200, snap["checks"])
            if path == "/api/v1/events":
                with engine.lock:
                    return self._json(200, list(reversed(engine.state["events"])))
            if path == "/":
                s = snap["summary"]
                rows = []
                for it in snap["checks"]:
                    right = tr(lang, "latency", ms=it["latency_ms"]) if it["status"] == "up" and it["latency_ms"] is not None else html.escape(it["detail"])
                    rows.append(f'<div class="row"><span class="dot {html.escape(it["status"])}"></span><div class="n"><b>{html.escape(it["name"])}</b>'
                                f'<small>{html.escape(it["target"])}</small></div><span class="r">{right}</span></div>')
                page = PAGE.format(lang=lang, summary=html.escape(tr(lang, "status_head", up=s["up"], down=s["down"], unknown=s["unknown"])[2:].strip()),
                                   rows="".join(rows), version=__version__, time=html.escape(snap["time"] or ""))
                return self._send(200, page.encode(), "text/html; charset=utf-8")
            self._json(404, {"error": "not_found"})

        def _method_not_allowed(self) -> None:
            self._json(405, {"error": "read_only"})

        do_PUT = do_DELETE = do_PATCH = _method_not_allowed

    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv


# --------------------------------------------------------------------------- executor
class Runner:
    def __init__(self, cfg: Config, engine: Engine, dispatcher: Dispatcher | None, stop: threading.Event,
                 runner: Callable[[Check, str], Result] = run_check, cert_fn: Callable[[str, int, float], int] = cert_days_left) -> None:
        self.cfg, self.engine, self.dispatcher, self.stop = cfg, engine, dispatcher, stop
        self.run_check, self.cert_fn = runner, cert_fn
        self._running: set[str] = set()
        self._lock = threading.Lock()
        self.last_tick = time.monotonic()      # o sinal de vida só sai enquanto este relógio anda

    def emit(self, events: list[dict[str, Any]]) -> None:
        for ev in events:
            if ev.get("silent"):               # pausa/manutenção: fica no histórico, não vai a nenhum canal
                log.info("(em pausa) %s", ev["text"])
                continue
            log.info("%s", ev["text"])
            if self.dispatcher:
                self.dispatcher.send(ev["text"], ev)

    _emit = emit

    def check_now(self, only: str | None = None) -> list[str]:
        """Verifica agora (/verificar). Devolve os nomes verificados; itens "push" não são verificáveis à força."""
        targets = [c for c in self.cfg.checks if c.kind != "push" and (only is None or c.name == only)]
        if targets:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(targets)), thread_name_prefix="guardian-now") as pool:
                list(pool.map(self._probe, targets))
        return [c.name for c in targets]

    def _probe(self, c: Check) -> None:
        try:
            self.emit(self.engine.record(c.name, self.run_check(c, self.cfg.language)))
        except Exception:  # noqa: BLE001
            log.exception("erro inesperado ao verificar %s", c.name)

    def _job(self, c: Check) -> None:
        try:
            self._probe(c)
            p = urllib.parse.urlsplit(c.url) if c.kind == "http" else None
            if p and p.scheme == "https" and c.cert_warn_days and self.engine.cert_due(c.name):
                try:
                    days: int | None = self.cert_fn(p.hostname or "", p.port or 443, c.timeout)
                except Exception:  # noqa: BLE001 - o veredito de "no ar" já vem da verificação principal
                    days = None
                self._emit(self.engine.record_cert(c.name, days))
        except Exception:  # noqa: BLE001
            log.exception("erro inesperado ao verificar %s", c.name)
        finally:
            with self._lock:
                self._running.discard(c.name)

    def loop(self) -> None:
        start = time.monotonic()
        polled = [c for c in self.cfg.checks if c.kind != "push"]
        pushed = [c for c in self.cfg.checks if c.kind == "push"]
        due = {c.name: start + i * 0.3 for i, c in enumerate(polled)}
        workers = min(16, max(2, len(polled)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="guardian-check") as pool:
            while not self.stop.is_set():
                now = self.last_tick = time.monotonic()
                self.emit(self.engine.expire_pauses())
                for c in pushed:
                    self.emit(self.engine.evaluate_push(c.name))
                for c in polled:
                    with self._lock:
                        if due[c.name] > now or c.name in self._running:
                            continue
                        self._running.add(c.name)
                    due[c.name] = now + c.interval
                    pool.submit(self._job, c)
                if now - self.engine._last_save > 30:
                    self.engine._last_save = now
                    self.engine.save()
                self.stop.wait(0.5)
        self.engine.save(force=True)


def cmd_run(cfg: Config) -> int:
    if not cfg.checks:
        raise ConfigError("nenhum [[check]] configurado: edite o guardian.toml")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: stop.set())
        except (ValueError, OSError):
            pass
    engine = Engine(cfg)
    engine.load()
    channels = build_channels(cfg)
    dispatcher = Dispatcher(channels) if channels else None
    if not channels:
        log.info("nenhum canal de aviso ligado: os avisos só aparecem aqui no registro")
    runner = Runner(cfg, engine, dispatcher, stop)
    api = None
    if cfg.api["enabled"]:
        api = make_api(engine, cfg.api["host"], cfg.api["port"], cfg.api["token"], emit=runner.emit)
        threading.Thread(target=api.serve_forever, name="guardian-api", daemon=True).start()
        pushes = any(c.kind == "push" for c in cfg.checks)
        log.info("API em http://%s:%s/ (%s)", cfg.api["host"], cfg.api["port"], "somente leitura, exceto /ping/ dos checks push" if pushes else "somente leitura")
    for ch in channels:
        if isinstance(ch, Telegram) and ch.commands:
            threading.Thread(target=ch.poll, args=(engine, stop, runner), name="guardian-telegram", daemon=True).start()
    if cfg.heartbeat:
        beat = Heartbeat(cfg.heartbeat["url"], cfg.heartbeat["interval"], lambda: time.monotonic() - runner.last_tick < LOOP_ALIVE_SECONDS)
        threading.Thread(target=beat.run, args=(stop,), name="guardian-heartbeat", daemon=True).start()
        log.info("sinal de vida ligado: a cada %g s para o serviço externo", cfg.heartbeat["interval"])
    else:
        log.info("sem [heartbeat]: se este aparelho desligar, nenhum aviso chega (veja o README, 'Quem vigia o vigia')")
    log.info(tr(cfg.language, "started", version=__version__, n=len(cfg.checks)))
    if dispatcher:
        dispatcher.send(tr(cfg.language, "started", version=__version__, n=len(cfg.checks)), {"kind": "started"})
    runner.loop()
    if api:
        api.shutdown()
        api.server_close()
    if dispatcher:
        dispatcher.flush()
        dispatcher.close()
    log.info("Guardian parado.")
    return 0


def cmd_check(cfg: Config) -> int:
    """Verifica tudo uma vez. Código de saída 0 = tudo no ar, 1 = algo fora. Bom para cron e scripts."""
    down = 0
    polled = [c for c in cfg.checks if c.kind != "push"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, max(1, len(polled)))) as pool:
        results = list(pool.map(lambda c: run_check(c, cfg.language), polled))
    for c, r in zip(polled, results):
        down += 0 if r.ok else 1
        print(f"{'OK  ' if r.ok else 'FAIL'}  {c.name}  [{c.target()}]  {tr(cfg.language, 'latency', ms=r.ms) if r.ok else r.detail}")
    for c in cfg.checks:
        if c.kind == "push":
            print(f"SKIP  {c.name}  [{c.target()}]  push: só vale com 'run' / only works with 'run'")
    return 1 if down else 0


def cmd_test_notify(cfg: Config) -> int:
    channels = build_channels(cfg)
    if not channels and not cfg.heartbeat:
        print("nenhum canal ligado em [notify.*] / no notify channel enabled")
        return 1
    failed = 0
    disp = Dispatcher(channels, retry_delays=(0,))
    for ch in channels:
        ok = disp.deliver(ch, tr(cfg.language, "test"), {"kind": "test"})
        failed += 0 if ok else 1
        print(f"{'OK  ' if ok else 'FAIL'}  {ch.name}")
    disp.close()
    if cfg.heartbeat:
        try:
            Heartbeat(cfg.heartbeat["url"], cfg.heartbeat["interval"], lambda: True).ping()
            print("OK    heartbeat")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  heartbeat ({type(exc).__name__})")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="guardian", description="Guardian — bot de monitoramento simples / simple monitoring bot")
    parser.add_argument("--version", action="version", version=f"Guardian {__version__}")
    parser.add_argument("-c", "--config", default=os.environ.get("GUARDIAN_CONFIG", "guardian.toml"), help="arquivo de configuração (padrão: guardian.toml)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="cria um guardian.toml de exemplo")
    sub.add_parser("run", help="vigia continuamente e avisa")
    sub.add_parser("check", help="verifica tudo uma vez (saída 1 se algo estiver fora)")
    sub.add_parser("test-notify", help="manda uma mensagem de teste para os canais ligados")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    path = Path(args.config).expanduser()
    if args.cmd == "init":
        if path.exists():
            print(f"{path} já existe; não vou sobrescrever / already exists, not overwriting", file=sys.stderr)
            return 1
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(EXAMPLE_CONFIG)
        print(f"Criado {path}. Edite e rode: python3 guardian.py run")
        return 0
    try:
        cfg = load_config(path)
        return {"run": cmd_run, "check": cmd_check, "test-notify": cmd_test_notify}[args.cmd](cfg)
    except ConfigError as exc:
        print(f"Erro de configuração / config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
