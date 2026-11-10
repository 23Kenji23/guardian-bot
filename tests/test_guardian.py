"""Testes do Guardian. Rodar:  python3 -m unittest discover -s tests -v
Tudo local: servidores falsos de HTTP, Telegram, webhook e SMTP. Nenhum teste acessa a internet."""
import base64
import http.client
import json
import logging
import os
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import guardian as g  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def stop_server(srv) -> None:
    srv.shutdown()
    srv.server_close()


def serve(handler) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Site(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/slow":
            time.sleep(3)
        if path == "/to-file":
            self.send_response(302)
            self.send_header("Location", "file:///etc/passwd")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        status = {"/fail": 503, "/gone": 404}.get(path, 200)
        body = b"hello guardian"
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:                       # o cliente já desistiu (teste de timeout)
            pass


class Capture(BaseHTTPRequestHandler):
    """Recebe POSTs (webhook / Telegram) e guarda; também finge getUpdates do Telegram."""
    posts: list = []
    updates: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).posts.append((self.path, dict(self.headers), raw))
        out = {"ok": True, "result": True}
        if self.path.endswith("/getUpdates"):
            out["result"], type(self).updates = type(self).updates, []
        body = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def fresh_capture():
    cls = type("C", (Capture,), {"posts": [], "updates": []})
    return cls, serve(cls)


def make_cfg(extra="", checks=None, **top):
    tmp = Path(tempfile.mkdtemp())
    guardian = {"failures_to_alert": 2, **top}
    lines = "\n".join(f"{k} = {json.dumps(v)}" for k, v in guardian.items())
    text = f"[guardian]\n{lines}\nstate_file = {json.dumps(str(tmp / 'state.json'))}\n" + (checks or '[[check]]\nname = "Site"\nurl = "http://127.0.0.1:1/"\n') + extra
    return g.parse_config(tomllib.loads(text), tmp / "guardian.toml")


class FakeClock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class ConfigTests(unittest.TestCase):
    def test_example_config_is_valid_and_matches_the_repo_file(self):
        cfg = g.parse_config(tomllib.loads(g.EXAMPLE_CONFIG))
        self.assertEqual(cfg.checks[0].kind, "http")
        self.assertEqual((ROOT / "guardian.example.toml").read_text(encoding="utf-8"), g.EXAMPLE_CONFIG)

    def test_bad_configs_get_clear_errors(self):
        bad = {
            "typo": '[guardian]\nintervall = 30\n',
            "interval": '[guardian]\ninterval = 1\n',
            "dup": '[[check]]\nname="a"\nurl="http://x.test"\n[[check]]\nname="a"\nurl="http://y.test"\n',
            "scheme": '[[check]]\nname="a"\nurl="file:///etc/passwd"\n',
            "ftp": '[[check]]\nname="a"\nurl="ftp://x.test"\n',
            "tcp-port": '[[check]]\nname="a"\ntype="tcp"\nhost="x"\nport=70000\n',
            "type": '[[check]]\nname="a"\ntype="ping"\n',
            "status": '[[check]]\nname="a"\nurl="http://x.test"\nexpect_status=[999]\n',
            "telegram": '[notify.telegram]\nenabled=true\nchat_id="1"\n',
            "api-open": '[api]\nenabled=true\nhost="0.0.0.0"\n',
            "lang": '[guardian]\nlanguage="xx"\n',
        }
        for name, text in bad.items():
            with self.subTest(name), self.assertRaises(g.ConfigError):
                g.parse_config(tomllib.loads(text))

    def test_secrets_come_from_the_environment_and_never_reach_the_log(self):
        os.environ["GUARDIAN_TEST_TOKEN"] = "123456:SECRET-VALUE"
        self.addCleanup(os.environ.pop, "GUARDIAN_TEST_TOKEN", None)
        cfg = g.parse_config(tomllib.loads('[notify.telegram]\nenabled=true\ntoken_env="GUARDIAN_TEST_TOKEN"\nchat_id="42"\n'))
        self.assertEqual(cfg.notify["telegram"]["token"], "123456:SECRET-VALUE")
        with self.assertLogs("guardian", level="WARNING") as cm:
            g.log.warning("falhou em https://api.telegram.org/bot123456:SECRET-VALUE/sendMessage")
        self.assertNotIn("SECRET-VALUE", cm.output[0])
        self.assertIn("***", cm.output[0])

    def test_target_hides_user_password_and_query(self):
        c = g.Check(name="x", kind="http", interval=60, timeout=5, url="https://user:pw@example.com:8443/p/a?token=abc#f")
        self.assertEqual(c.target(), "https://example.com:8443/p/a")


class CheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = serve(Site)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        stop_server(cls.srv)

    def http(self, path, **kw):
        c = g.Check(name="s", kind="http", interval=60, timeout=kw.pop("timeout", 5), url=self.base + path, **kw)
        return g.check_http(c, "pt")

    def test_ok_and_latency(self):
        r = self.http("/ok")
        self.assertTrue(r.ok)
        self.assertIsNotNone(r.ms)

    def test_bad_status_and_expected_status(self):
        r = self.http("/fail")
        self.assertEqual((r.ok, r.detail), (False, "HTTP 503"))
        self.assertTrue(self.http("/gone", expect_status=[404]).ok)
        self.assertFalse(self.http("/ok", expect_status=[201]).ok)

    def test_redirect_is_followed(self):
        self.assertTrue(self.http("/redirect").ok)

    def test_redirect_to_a_local_file_is_refused(self):
        self.assertFalse(self.http("/to-file").ok)

    def test_contains(self):
        self.assertTrue(self.http("/ok", contains="guardian").ok)
        r = self.http("/ok", contains="não existe")
        self.assertFalse(r.ok)
        self.assertIn("texto", r.detail)

    def test_timeout(self):
        r = self.http("/slow", timeout=1)
        self.assertFalse(r.ok)
        self.assertIn("sem resposta", r.detail)

    def test_refused_and_dns(self):
        c = g.Check(name="s", kind="http", interval=60, timeout=3, url=f"http://127.0.0.1:{free_port()}/")
        self.assertIn("recusada", g.check_http(c, "pt").detail)
        c = g.Check(name="s", kind="http", interval=60, timeout=3, url="http://nao-existe.invalid/")
        self.assertFalse(g.check_http(c, "en").ok)

    def test_tcp(self):
        up = g.Check(name="t", kind="tcp", interval=60, timeout=3, host="127.0.0.1", port=self.srv.server_address[1])
        down = g.Check(name="t", kind="tcp", interval=60, timeout=3, host="127.0.0.1", port=free_port())
        self.assertTrue(g.check_tcp(up, "pt").ok)
        self.assertEqual(g.check_tcp(down, "en").detail, "connection refused")


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.cfg = make_cfg(failures_to_alert=2, remind_minutes=10)
        self.engine = g.Engine(self.cfg, self.clock)
        self.fail = g.Result(False, None, "HTTP 503")
        self.ok = g.Result(True, 120, "")

    def test_first_ok_is_silent_and_one_failure_is_not_enough(self):
        self.assertEqual(self.engine.record("Site", self.ok), [])
        self.assertEqual(self.engine.record("Site", self.fail), [], "uma falha só não avisa (evita alarme falso)")
        self.assertEqual(self.engine.snapshot()["checks"][0]["status"], "up")

    def test_down_alert_once_then_recovery_with_duration(self):
        self.engine.record("Site", self.ok)
        self.engine.record("Site", self.fail)
        down = self.engine.record("Site", self.fail)
        self.assertEqual([e["kind"] for e in down], ["down"])
        self.assertIn("FORA DO AR", down[0]["text"])
        self.assertEqual(self.engine.record("Site", self.fail), [], "sem repetir o mesmo aviso")
        self.clock.t += 125
        up = self.engine.record("Site", self.ok)
        self.assertEqual([e["kind"] for e in up], ["up"])
        self.assertIn("2 min", up[0]["text"])
        self.assertEqual(self.engine.record("Site", self.ok), [])

    def test_starting_already_down_alerts(self):
        self.engine.record("Site", self.fail)
        self.assertEqual([e["kind"] for e in self.engine.record("Site", self.fail)], ["down"])

    def test_reminder_every_10_minutes(self):
        for _ in range(2):
            self.engine.record("Site", self.fail)
        self.clock.t += 9 * 60
        self.assertEqual(self.engine.record("Site", self.fail), [])
        self.clock.t += 2 * 60
        self.assertEqual([e["kind"] for e in self.engine.record("Site", self.fail)], ["still_down"])
        self.assertEqual(self.engine.record("Site", self.fail), [])

    def test_state_survives_restart_without_repeating_the_alert(self):
        for _ in range(2):
            self.engine.record("Site", self.fail)
        self.engine.save(force=True)
        self.assertEqual(oct(self.cfg.state_file.stat().st_mode & 0o777), "0o600")
        again = g.Engine(self.cfg, self.clock)
        again.load()
        self.assertEqual(again.record("Site", self.fail), [], "reiniciar o Guardian não repete 'fora do ar'")
        self.assertEqual([e["kind"] for e in again.record("Site", self.ok)], ["up"])

    def test_corrupt_state_file_is_ignored(self):
        self.cfg.state_file.write_text("{isso não é json")
        self.engine.load()
        self.assertEqual(self.engine.snapshot()["checks"][0]["status"], "unknown")

    def test_removed_checks_are_dropped_from_state(self):
        self.engine.record("Site", self.ok)
        self.engine.state["checks"]["Antigo"] = {"status": "down"}
        self.engine.save(force=True)
        again = g.Engine(self.cfg, self.clock)
        again.load()
        self.assertNotIn("Antigo", again.state["checks"])

    def test_certificate_warning_fires_once_and_rearms(self):
        self.assertEqual(self.engine.record_cert("Site", 40), [])
        first = self.engine.record_cert("Site", 10)
        self.assertEqual([e["kind"] for e in first], ["cert"])
        self.assertIn("10 dia", first[0]["text"])
        self.assertEqual(self.engine.record_cert("Site", 9), [])
        self.engine.record_cert("Site", 90)
        self.assertEqual(len(self.engine.record_cert("Site", 5)), 1, "renovou e voltou a vencer: avisa de novo")
        self.engine.record_cert("Site", 90)
        self.assertIn("venceu", self.engine.record_cert("Site", -1)[0]["text"])

    def test_english(self):
        eng = g.Engine(make_cfg(language="en", failures_to_alert=1), self.clock)
        self.assertEqual(eng.record("Site", self.fail)[0]["text"], "🔴 Site is DOWN — HTTP 503")


class NotifierTests(unittest.TestCase):
    def test_telegram_send_and_token_in_path_only(self):
        cls, srv = fresh_capture()
        self.addCleanup(stop_server, srv)
        tg = g.Telegram({"token": "T0KEN", "chat_id": "42", "api_base": f"http://127.0.0.1:{srv.server_address[1]}"})
        tg.send("olá", {})
        path, _, raw = cls.posts[0]
        self.assertEqual(path, "/botT0KEN/sendMessage")
        self.assertEqual(json.loads(raw)["chat_id"], "42")
        self.assertEqual(json.loads(raw)["text"], "olá")

    def test_webhook_formats(self):
        cls, srv = fresh_capture()
        self.addCleanup(stop_server, srv)
        url = f"http://127.0.0.1:{srv.server_address[1]}/hook"
        ev = {"kind": "down", "check": "Site", "time": "2026-01-01T00:00:00Z"}
        for fmt in ("generic", "discord", "slack", "ntfy"):
            g.Webhook({"url": url, "format": fmt}).send("🔴 Site caiu", ev)
        bodies = [(h.get("Content-Type"), raw) for _, h, raw in cls.posts]
        self.assertEqual(json.loads(bodies[0][1])["kind"], "down")
        self.assertEqual(json.loads(bodies[1][1]), {"content": "🔴 Site caiu"})
        self.assertEqual(json.loads(bodies[2][1]), {"text": "🔴 Site caiu"})
        self.assertTrue(bodies[3][0].startswith("text/plain"))
        self.assertEqual(bodies[3][1].decode(), "🔴 Site caiu")

    def test_email_via_a_fake_smtp_server(self):
        received = []

        class SMTP(socketserver.StreamRequestHandler):
            def handle(self):
                w = lambda s: (self.wfile.write(s.encode() + b"\r\n"), self.wfile.flush())
                w("220 fake ESMTP")
                data_mode, buf = False, []
                for raw in self.rfile:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if data_mode:
                        if line == ".":
                            received.append("\n".join(buf))
                            data_mode = False
                            w("250 ok")
                        else:
                            buf.append(line)
                        continue
                    cmd = line.upper()
                    if cmd.startswith(("EHLO", "HELO")):
                        w("250 fake")
                    elif cmd.startswith(("MAIL", "RCPT")):
                        w("250 ok")
                    elif cmd == "DATA":
                        data_mode = True
                        w("354 go")
                    elif cmd == "QUIT":
                        w("221 bye")
                        return
                    else:
                        w("250 ok")

        srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SMTP)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(stop_server, srv)
        em = g.Email({"host": "127.0.0.1", "port": srv.server_address[1], "security": "none", "from": "g@x.test", "to": ["a@x.test"]})
        em.send("🔴 Site FORA DO AR — HTTP 503", {})
        self.assertEqual(len(received), 1)
        self.assertIn("Subject: Guardian:", received[0])
        self.assertIn("a@x.test", received[0])

    def test_dispatcher_retries_and_a_broken_channel_does_not_block_the_next(self):
        calls = []

        class Flaky:
            name = "flaky"

            def send(self, text, event):
                calls.append("flaky")
                if len(calls) < 3:
                    raise OSError("rede caiu")

        class Dead:
            name = "dead"

            def send(self, text, event):
                raise OSError("sempre falha")

        class Good:
            name = "good"

            def send(self, text, event):
                calls.append("good")

        d = g.Dispatcher([Dead(), Flaky(), Good()], retry_delays=(0, 0, 0))
        with self.assertLogs("guardian", level="WARNING") as cm:
            d.send("oi")
            d.flush()
        d.close()
        self.assertEqual(calls, ["flaky", "flaky", "flaky", "good"])
        self.assertTrue(any("dead" in line for line in cm.output))

    def test_telegram_commands_only_obey_the_configured_chat(self):
        cls, srv = fresh_capture()
        self.addCleanup(stop_server, srv)
        eng = g.Engine(make_cfg(), FakeClock())
        eng.record("Site", g.Result(True, 80, ""))
        cls.updates = [
            {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/status"}},          # estranho: ignorado
            {"update_id": 2, "message": {"chat": {"id": 42}, "text": "/status"}},
            {"update_id": 3, "message": {"chat": {"id": 42}, "text": "/ajuda@MeuBot"}},
            {"update_id": 4, "message": {"chat": {"id": 42}, "text": "rm -rf /"}},           # texto livre: ignorado
            {"update_id": 5, "message": {"chat": {"id": 42}, "text": "/foo"}},
        ]
        tg = g.Telegram({"token": "T", "chat_id": "42", "api_base": f"http://127.0.0.1:{srv.server_address[1]}"})
        stop = threading.Event()
        t = threading.Thread(target=tg.poll, args=(eng, stop), daemon=True)
        t.start()
        deadline = time.time() + 10
        while time.time() < deadline and sum(p.endswith("/sendMessage") for p, _, _ in cls.posts) < 3:
            time.sleep(0.1)
        stop.set()
        t.join(5)
        sent = [json.loads(raw)["text"] for p, _, raw in cls.posts if p.endswith("/sendMessage")]
        self.assertEqual(len(sent), 3, sent)
        self.assertIn("1 no ar", sent[0])
        self.assertIn("Site", sent[0])
        self.assertIn("/status", sent[1])
        self.assertIn("não reconhecido", sent[2])


class ApiTests(unittest.TestCase):
    def serve_api(self, token=""):
        eng = g.Engine(make_cfg(checks='[[check]]\nname = "<b>Loja</b>"\nurl = "https://u:pw@example.com/p?secret=1"\n'), FakeClock())
        eng.record("<b>Loja</b>", g.Result(True, 33, ""))
        srv = g.make_api(eng, "127.0.0.1", 0, token)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(stop_server, srv)
        self.addCleanup(srv.server_close)
        return srv.server_address[1]

    def get(self, port, path, method="GET", headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request(method, path, headers=headers or {})
        r = c.getresponse()
        return r.status, r.read(), r

    def test_status_json_hides_secrets_in_the_url(self):
        port = self.serve_api()
        status, body, r = self.get(port, "/api/v1/status")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["summary"]["up"], 1)
        self.assertEqual(data["checks"][0]["target"], "https://example.com/p")
        self.assertNotIn("secret", body.decode())
        self.assertNotIn("pw", data["checks"][0]["target"])
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")

    def test_html_page_escapes_names(self):
        port = self.serve_api()
        status, body, r = self.get(port, "/")
        self.assertEqual(status, 200)
        self.assertNotIn("<b>Loja</b>", body.decode())
        self.assertIn("&lt;b&gt;Loja&lt;/b&gt;", body.decode())
        self.assertIn("default-src 'none'", r.getheader("Content-Security-Policy"))

    def test_token_protects_everything_except_health(self):
        port = self.serve_api("s3gredo-da-api")
        self.assertEqual(self.get(port, "/health")[0], 200)
        self.assertEqual(self.get(port, "/api/v1/status")[0], 401)
        self.assertEqual(self.get(port, "/api/v1/status", headers={"Authorization": "Bearer errado"})[0], 401)
        self.assertEqual(self.get(port, "/api/v1/status", headers={"Authorization": "Bearer s3gredo-da-api"})[0], 200)
        basic = base64.b64encode(b"qualquer:s3gredo-da-api").decode()
        self.assertEqual(self.get(port, "/", headers={"Authorization": f"Basic {basic}"})[0], 200)
        status, _, r = self.get(port, "/")
        self.assertEqual(status, 401)
        self.assertIn("Basic", r.getheader("WWW-Authenticate"))

    def test_read_only_and_unknown_paths(self):
        port = self.serve_api()
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            self.assertEqual(self.get(port, "/api/v1/status", method=method)[0], 405, method)
        self.assertEqual(self.get(port, "/api/v1/nada")[0], 404)
        self.assertEqual(self.get(port, "/api/v1/events")[0], 200)


class CliAndRunnerTests(unittest.TestCase):
    def run_cli(self, *args, cwd):
        return subprocess.run([sys.executable, str(ROOT / "guardian.py"), *args], cwd=cwd, capture_output=True, text=True, timeout=60)

    def test_init_creates_a_private_config_and_never_overwrites(self):
        tmp = tempfile.mkdtemp()
        r = self.run_cli("init", cwd=tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        path = Path(tmp) / "guardian.toml"
        self.assertEqual(path.read_text(encoding="utf-8"), g.EXAMPLE_CONFIG)
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        path.write_text("# meu\n")
        self.assertEqual(self.run_cli("init", cwd=tmp).returncode, 1)
        self.assertEqual(path.read_text(), "# meu\n")

    def test_check_exit_codes_and_config_errors(self):
        srv = serve(Site)
        self.addCleanup(stop_server, srv)
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        tmp = Path(tempfile.mkdtemp())
        (tmp / "ok.toml").write_text(f'[[check]]\nname="a"\nurl="{base}/ok"\n')
        (tmp / "bad.toml").write_text(f'[[check]]\nname="a"\nurl="{base}/ok"\n[[check]]\nname="b"\nurl="{base}/fail"\n')
        (tmp / "err.toml").write_text('[guardian]\nintervall=1\n')
        self.assertEqual(self.run_cli("-c", "ok.toml", "check", cwd=tmp).returncode, 0)
        r = self.run_cli("-c", "bad.toml", "check", cwd=tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL  b", r.stdout)
        self.assertEqual(self.run_cli("-c", "err.toml", "check", cwd=tmp).returncode, 2)
        self.assertEqual(self.run_cli("-c", "nao-existe.toml", "check", cwd=tmp).returncode, 2)

    def test_check_skips_push_items_and_test_notify_tries_the_heartbeat(self):
        srv = serve(Site)
        self.addCleanup(stop_server, srv)
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        tmp = Path(tempfile.mkdtemp())
        (tmp / "p.toml").write_text(f'[api]\nenabled=true\ntoken_env="GUARDIAN_T"\n[[check]]\nname="a"\nurl="{base}/ok"\n'
                                    '[[check]]\nname="Backup"\ntype="push"\ninterval=60\n')
        (tmp / "hb.toml").write_text('[heartbeat]\nenabled=true\nurl_env="GUARDIAN_HB"\n')
        env = {**os.environ, "GUARDIAN_T": "x", "GUARDIAN_HB": f"{base}/ok/SEGREDO-DO-SINAL"}
        run = lambda *a: subprocess.run([sys.executable, str(ROOT / "guardian.py"), *a], cwd=tmp, env=env, capture_output=True, text=True, timeout=60)  # noqa: E731
        r = run("-c", "p.toml", "check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("SKIP  Backup", r.stdout)
        r = run("-c", "hb.toml", "test-notify")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK    heartbeat", r.stdout)
        self.assertNotIn("SEGREDO-DO-SINAL", r.stdout + r.stderr)
        env["GUARDIAN_HB"] = f"http://127.0.0.1:{free_port()}/x"
        self.assertEqual(run("-c", "hb.toml", "test-notify").returncode, 1)

    def test_runner_end_to_end_down_then_back_up(self):
        """O ciclo completo: site no ar → cai → avisa → volta → avisa, com a mensagem chegando num Telegram falso."""
        cls, tg_srv = fresh_capture()
        self.addCleanup(stop_server, tg_srv)
        site_port = free_port()
        site = ThreadingHTTPServer(("127.0.0.1", site_port), Site)
        threading.Thread(target=site.serve_forever, daemon=True).start()
        cfg = make_cfg(failures_to_alert=1, checks=f'[[check]]\nname = "Meu site"\nurl = "http://127.0.0.1:{site_port}/ok"\ninterval = 5\ntimeout = 2\n')
        cfg.checks[0].interval = 0.6                      # a configuração real exige >= 5 s; aqui acelera só o teste
        tg = g.Telegram({"token": "T", "chat_id": "42", "api_base": f"http://127.0.0.1:{tg_srv.server_address[1]}"})
        disp = g.Dispatcher([tg], retry_delays=(0,))
        eng = g.Engine(cfg)
        stop = threading.Event()
        runner = threading.Thread(target=g.Runner(cfg, eng, disp, stop).loop, daemon=True)
        runner.start()

        def messages():
            return [json.loads(raw)["text"] for p, _, raw in cls.posts if p.endswith("/sendMessage")]

        def wait_for(fragment, seconds=20):
            end = time.time() + seconds
            while time.time() < end:
                if any(fragment in m for m in messages()):
                    return True
                time.sleep(0.2)
            return False

        time.sleep(1.5)
        self.assertEqual(messages(), [], "no ar desde o começo: nenhum aviso")
        site.shutdown()
        site.server_close()
        self.assertTrue(wait_for("FORA DO AR"), messages())
        site = ThreadingHTTPServer(("127.0.0.1", site_port), Site)
        threading.Thread(target=site.serve_forever, daemon=True).start()
        self.addCleanup(stop_server, site)
        self.assertTrue(wait_for("voltou ao ar"), messages())
        stop.set()
        runner.join(10)
        disp.flush()
        disp.close()
        self.assertEqual(sum("FORA DO AR" in m for m in messages()), 1, "um aviso de queda só")
        self.assertTrue(cfg.state_file.exists(), "o estado foi salvo ao parar")

    def test_certificate_check_runs_only_for_https_and_a_failure_is_not_an_outage(self):
        cfg = make_cfg(checks='[[check]]\nname = "Site"\nurl = "https://example.invalid/"\ncert_warn_days = 14\n')
        eng = g.Engine(cfg, FakeClock())
        seen = []
        runner = g.Runner(cfg, eng, None, threading.Event(), runner=lambda c, lang: g.Result(True, 5, ""),
                          cert_fn=lambda h, p, t: seen.append((h, p)) or 3)
        runner._job(cfg.checks[0])
        self.assertEqual(seen, [("example.invalid", 443)])
        self.assertEqual(eng.snapshot()["checks"][0]["cert_days"], 3)
        self.assertEqual(eng.state["events"][-1]["kind"], "cert")

        def boom(*a):
            raise OSError("sem rede")
        eng2 = g.Engine(make_cfg(checks='[[check]]\nname = "Site"\nurl = "https://example.invalid/"\n'), FakeClock())
        g.Runner(eng2.cfg, eng2, None, threading.Event(), runner=lambda c, lang: g.Result(True, 5, ""), cert_fn=boom)._job(eng2.cfg.checks[0])
        self.assertEqual(eng2.snapshot()["checks"][0]["status"], "up")


# =========================================================================== 1.1: quem vigia o vigia + controle pelo Telegram
TWO = '[[check]]\nname = "Meu site"\nurl = "http://127.0.0.1:1/"\n[[check]]\nname = "Outro"\nurl = "http://127.0.0.1:1/b"\n'
PUSH = '[[check]]\nname = "Backup"\ntype = "push"\ninterval = 100\ngrace = 20\n[[check]]\nname = "Meu Backup"\ntype = "push"\ninterval = 100\n'
API_ON = '[api]\nenabled = true\ntoken_env = "GUARDIAN_TEST_API_TOKEN"\n'
OK, FAIL = g.Result(True, 50, ""), g.Result(False, None, "HTTP 503")


def push_cfg(**top):
    os.environ["GUARDIAN_TEST_API_TOKEN"] = "tok-de-teste"
    return make_cfg(extra=API_ON, checks=PUSH + '[[check]]\nname = "Site"\nurl = "http://127.0.0.1:1/"\n', **top)


def wait_until(cond, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


class Sink:
    """Finge o Dispatcher: guarda o que seria enviado aos canais."""

    def __init__(self):
        self.sent = []

    def send(self, text, event=None):
        self.sent.append(text)


class ControlConfigTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("GUARDIAN_TEST_API_TOKEN", None)
        os.environ.pop("GUARDIAN_TEST_HB", None)

    def test_push_check_rules(self):
        cfg = push_cfg()
        backup = next(c for c in cfg.checks if c.name == "Backup")
        self.assertEqual((backup.kind, backup.interval, backup.grace), ("push", 100, 20))
        self.assertEqual(backup.target(), "/ping/Backup")
        self.assertEqual(next(c for c in cfg.checks if c.name == "Meu Backup").target(), "/ping/Meu%20Backup")
        os.environ["GUARDIAN_TEST_API_TOKEN"] = "x"
        bad = {
            "sem-interval": ('[[check]]\nname="a"\ntype="push"\n', API_ON),
            "sem-api": ('[[check]]\nname="a"\ntype="push"\ninterval=60\n', ""),
            "api-sem-token": ('[[check]]\nname="a"\ntype="push"\ninterval=60\n', '[api]\nenabled=true\n'),
            "grace-em-http": ('[[check]]\nname="a"\nurl="http://x.test"\ngrace=5\n', API_ON),
            "interval-demais": ('[[check]]\nname="a"\ntype="push"\ninterval=99999999\n', API_ON),
        }
        for name, (checks, extra) in bad.items():
            with self.subTest(name), self.assertRaises(g.ConfigError):
                make_cfg(checks=checks, extra=extra)

    def test_heartbeat_config(self):
        self.assertEqual(make_cfg().heartbeat, {})
        os.environ["GUARDIAN_TEST_HB"] = "https://hc.example/ping/SEGREDO-DO-PING"
        cfg = make_cfg(extra='[heartbeat]\nenabled = true\nurl_env = "GUARDIAN_TEST_HB"\ninterval = 30\n')
        self.assertEqual(cfg.heartbeat, {"url": "https://hc.example/ping/SEGREDO-DO-PING", "interval": 30})
        with self.assertLogs("guardian", level="WARNING") as cm:
            g.log.warning("falhou em https://hc.example/ping/SEGREDO-DO-PING")
        self.assertNotIn("SEGREDO-DO-PING", cm.output[0])
        for name, text in {"sem-url": '[heartbeat]\nenabled = true\n', "file": '[heartbeat]\nenabled = true\nurl = "file:///etc/passwd"\n',
                           "rapido": '[heartbeat]\nenabled = true\nurl = "http://x.test/p"\ninterval = 1\n',
                           "typo": '[heartbeat]\nenabled = true\nurl = "http://x.test/p"\nintervall = 60\n'}.items():
            with self.subTest(name), self.assertRaises(g.ConfigError):
                g.parse_config(tomllib.loads(text))

    def test_parse_duration(self):
        for text, secs in {"30m": 1800, "2h": 7200, "1d": 86400, "1h30m": 5400, "45": 2700, "10min": 600, "2H": 7200}.items():
            self.assertEqual(g.parse_duration(text), secs, text)
        for text in ("abc", "", "m", "h", "99999999", "-5", "2x", "1.5h", "Meu"):
            self.assertIsNone(g.parse_duration(text), text)


class PauseTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.cfg = make_cfg(failures_to_alert=1, checks=TWO)
        self.eng = g.Engine(self.cfg, self.clock)

    def kinds(self, events):
        return [(e["kind"], bool(e.get("silent"))) for e in events]

    def test_down_during_a_pause_is_silent_and_announced_when_it_ends(self):
        self.eng.record("Meu site", OK)
        self.eng.pause(600)
        self.assertEqual(self.kinds(self.eng.record("Meu site", FAIL)), [("down", True)], "fica no histórico, não vai a nenhum canal")
        self.assertEqual(self.eng.record("Meu site", FAIL), [])
        self.clock.t += 601
        ended = self.eng.expire_pauses()
        self.assertEqual(self.kinds(ended), [("resumed", False), ("still_down", False)])
        self.assertIn("continua fora do ar", ended[1]["text"])
        self.assertEqual(self.eng.expire_pauses(), [], "avisa uma vez só")
        self.assertEqual([e["kind"] for e in self.eng.record("Meu site", OK)], ["up"], "agora o 'voltou' é normal")

    def test_back_up_during_a_pause_you_were_never_told_about_stays_silent(self):
        self.eng.pause(600)
        self.eng.record("Meu site", FAIL)
        self.assertEqual(self.kinds(self.eng.record("Meu site", OK)), [("up", True)])
        self.clock.t += 601
        self.assertEqual(self.kinds(self.eng.expire_pauses()), [("resumed", False)], "voltou sozinho: nada mais a dizer")

    def test_if_you_were_told_it_went_down_the_recovery_is_delivered_even_in_a_pause(self):
        self.eng.record("Meu site", FAIL)                 # avisou "fora do ar"
        self.eng.pause(600)
        self.assertEqual(self.kinds(self.eng.record("Meu site", OK)), [("up", False)], "senão a queda ficaria sem desfecho")

    def test_a_pause_for_one_item_does_not_mute_the_others(self):
        self.eng.pause(600, "Meu site")
        self.assertEqual(self.kinds(self.eng.record("Meu site", FAIL)), [("down", True)])
        self.assertEqual(self.kinds(self.eng.record("Outro", FAIL)), [("down", False)])
        snap = {c["name"]: c["paused"] for c in self.eng.snapshot()["checks"]}
        self.assertEqual(snap, {"Meu site": True, "Outro": False})

    def test_reminders_wait_for_the_pause_to_end(self):
        cfg = make_cfg(failures_to_alert=1, remind_minutes=10, checks=TWO)
        eng = g.Engine(cfg, self.clock)
        eng.record("Meu site", FAIL)                      # avisado
        eng.pause(3600)
        self.clock.t += 20 * 60
        self.assertEqual(eng.record("Meu site", FAIL), [], "em pausa não repete o lembrete")
        self.clock.t += 3600
        self.assertEqual([e["kind"] for e in eng.expire_pauses()], ["resumed"], "já tinha sido avisado: sem aviso extra")
        self.assertEqual([e["kind"] for e in eng.record("Meu site", FAIL)], ["still_down"])

    def test_pause_survives_a_restart_and_a_pause_that_ended_while_off_is_announced(self):
        self.eng.pause(600)
        self.eng.pause(900, "Outro")
        self.assertEqual(oct(self.cfg.state_file.stat().st_mode & 0o777), "0o600", "gravou na hora, não só a cada 30 s")
        again = g.Engine(self.cfg, self.clock)
        again.load()
        self.assertEqual(again.snapshot()["paused"].keys(), {"*", "Outro"})
        self.assertEqual(again.record("Meu site", FAIL)[0].get("silent"), True)
        self.clock.t += 5000                              # o Guardian ficou desligado além da pausa
        late = g.Engine(self.cfg, self.clock)
        late.load()
        self.assertEqual({e["check"] for e in late.expire_pauses() if e["kind"] == "resumed"}, {"*", "Outro"})

    def test_pause_for_a_removed_check_is_dropped_on_load(self):
        self.eng.state["pauses"]["Antigo"] = self.clock.t + 999
        self.eng.save(force=True)
        again = g.Engine(self.cfg, self.clock)
        again.load()
        self.assertNotIn("Antigo", again.state["pauses"])

    def test_certificate_warning_is_not_lost_when_it_comes_during_a_pause(self):
        self.eng.pause(600)
        self.assertEqual(self.eng.record_cert("Meu site", 5), [])
        self.clock.t += 601
        self.eng.expire_pauses()
        self.assertEqual([e["kind"] for e in self.eng.record_cert("Meu site", 5)], ["cert"])

    def test_status_text_shows_the_pause(self):
        self.eng.pause(1800)
        text = self.eng.status_text()
        self.assertIn("⏸️ Avisos em pausa (tudo): faltam 30 min", text)
        self.assertIn("em pausa", text.splitlines()[-1])

    def test_old_state_files_without_pause_keys_still_load(self):
        self.cfg.state_file.write_text(json.dumps({"version": 1, "checks": {"Meu site": {"status": "down", "failures": 3, "down_since": 1}}, "events": []}))
        self.eng.load()
        self.assertEqual(self.eng.record("Meu site", FAIL), [], "estado do 1.0: já tinha avisado, não repete")
        self.assertEqual(self.eng.state["pauses"], {})


class PushTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("GUARDIAN_TEST_API_TOKEN", None)

    def test_missing_signal_alerts_once_and_a_signal_recovers(self):
        clock, eng = FakeClock(), None
        eng = g.Engine(push_cfg(), clock)                 # Backup: a cada 100 s + 20 s de folga
        self.assertEqual(eng.evaluate_push("Backup"), [])
        self.assertEqual(eng.ping("Backup"), [], "primeiro sinal: fica 'no ar', sem barulho")
        clock.t += 119
        self.assertEqual(eng.evaluate_push("Backup"), [])
        clock.t += 2
        down = eng.evaluate_push("Backup")
        self.assertEqual([e["kind"] for e in down], ["down"], "um atraso já é prova: não espera uma 2ª falha")
        self.assertIn("nenhum sinal há 2 min", down[0]["text"])
        self.assertEqual(eng.evaluate_push("Backup"), [])
        up = eng.ping("Backup")
        self.assertEqual([e["kind"] for e in up], ["up"])
        self.assertEqual(eng.snapshot()["checks"][0]["last_ping"], g.iso(clock.t))

    def test_never_signalling_is_down_after_the_first_window(self):
        clock = FakeClock()
        eng = g.Engine(push_cfg(), clock)
        clock.t += 121
        self.assertEqual([e["kind"] for e in eng.evaluate_push("Backup")], ["down"])

    def test_an_explicit_failure_signal_alerts_immediately(self):
        eng = g.Engine(push_cfg(failures_to_alert=5), FakeClock())
        down = eng.ping("Backup", ok=False)
        self.assertEqual([e["kind"] for e in down], ["down"])
        self.assertIn("avisou que falhou", down[0]["text"])
        self.assertEqual(eng.evaluate_push("Backup"), [], "o sinal chegou: não vira 'sem sinal' antes da hora")

    def test_unknown_or_non_push_names_are_refused(self):
        eng = g.Engine(push_cfg(), FakeClock())
        self.assertIsNone(eng.ping("Nada"))
        self.assertIsNone(eng.ping("Site"))

    def test_restart_gives_a_fresh_window_and_remembers_the_last_signal(self):
        clock = FakeClock()
        cfg = push_cfg()
        eng = g.Engine(cfg, clock)
        eng.ping("Backup")
        eng.save(force=True)
        clock.t += 10 * 86400                             # o Guardian ficou 10 dias desligado
        again = g.Engine(cfg, clock)
        again.load()
        self.assertEqual(again.evaluate_push("Backup"), [], "recém-ligado: não acusa falta de sinal de imediato")
        clock.t += 121
        down = again.evaluate_push("Backup")
        self.assertEqual([e["kind"] for e in down], ["down"])
        self.assertIn("10 d", down[0]["text"], "o texto conta desde o último sinal de verdade")

    def test_push_status_line(self):
        clock = FakeClock()
        eng = g.Engine(push_cfg(), clock)
        eng.ping("Backup")
        clock.t += 90
        self.assertIn("🟢 Backup — último sinal há 1 min", eng.status_text())


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.cfg = make_cfg(failures_to_alert=1, checks=TWO)
        self.eng = g.Engine(self.cfg, self.clock)
        self.sink = Sink()
        self.calls = []

        def fake_check(c, lang):
            self.calls.append(c.name)
            return OK
        self.runner = g.Runner(self.cfg, self.eng, self.sink, threading.Event(), runner=fake_check)

    def cmd(self, text):
        return g.handle_command(text, self.eng, self.runner)

    def test_only_slash_commands_are_commands(self):
        self.assertIsNone(self.cmd("rm -rf /"))
        self.assertIsNone(self.cmd(""))
        self.assertIn("não reconhecido", self.cmd("/foo"))
        self.assertIn("/pausar", self.cmd("/ajuda@MeuBot"))

    def test_pause_defaults_to_one_hour_and_takes_a_time(self):
        self.assertIn("1 h", self.cmd("/pausar"))
        self.assertEqual(self.eng.state["pauses"]["*"], self.clock.t + 3600)
        self.assertIn("30 min", self.cmd("/pausar 30m"))
        self.assertEqual(self.eng.state["pauses"]["*"], self.clock.t + 1800)
        self.assertIn("2 h", self.cmd("/manutencao 2h"))

    def test_pause_one_item_by_name_in_any_order_and_case(self):
        self.assertIn("Meu site", self.cmd("/pausar 2h meu SITE"))
        self.assertEqual(list(self.eng.state["pauses"]), ["Meu site"])
        self.assertIn("Outro", self.cmd("/pausar Outro 15m"))
        self.assertEqual(self.eng.state["pauses"]["Outro"], self.clock.t + 900)

    def test_bad_times_and_unknown_items(self):
        for text in ("/pausar 2x", "/pausar 0m", "/pausar 8d", "/pausar 1m30"):
            self.assertIn("Tempo inválido", self.cmd(text), text)
        answer = self.cmd("/pausar 1h Nada")
        self.assertIn('Não achei o item "Nada"', answer)
        self.assertIn("Meu site, Outro", answer)
        self.assertEqual(self.eng.state["pauses"], {}, "pedido inválido não pausa nada")

    def test_resume(self):
        self.assertIn("nenhuma pausa", self.cmd("/retomar"))
        self.cmd("/pausar 1h")
        self.assertIn("pausa geral", self.cmd("/retomar Outro"), "pausa geral só se encerra sem nome")
        self.assertIn("voltaram ao normal", self.cmd("/retomar"))
        self.assertEqual(self.eng.state["pauses"], {})
        self.cmd("/pausar 1h Outro")
        self.assertIn("não estava em pausa", self.cmd("/retomar Meu site"))
        self.assertIn("Outro", self.cmd("/retomar Outro"))

    def test_resume_announces_what_is_still_down(self):
        self.cmd("/pausar 1h")
        self.eng.record("Meu site", FAIL)
        self.eng.record("Outro", OK)
        self.assertEqual(self.sink.sent, [])
        self.cmd("/retomar")
        self.assertEqual(len(self.sink.sent), 1, self.sink.sent)
        self.assertIn("Meu site continua fora do ar", self.sink.sent[0])

    def test_check_now(self):
        answer = self.cmd("/verificar agora")
        self.assertEqual(sorted(self.calls), ["Meu site", "Outro"])
        self.assertIn("Verificado agora", answer)
        self.assertIn("2 no ar", answer)
        self.calls.clear()
        answer = self.cmd("/verificar outro")
        self.assertEqual(self.calls, ["Outro"])
        self.assertIn("Outro", answer)
        self.assertNotIn("Meu site", answer)
        self.assertIn("Não achei", self.cmd("/verificar Nada"))

    def test_check_now_while_paused_keeps_the_alert_silent(self):
        self.runner.run_check = lambda c, lang: FAIL
        self.cmd("/pausar 1h")
        self.cmd("/verificar")
        self.assertEqual(self.sink.sent, [])
        self.assertEqual(self.eng.snapshot()["summary"]["down"], 2, "continua medindo: o /status mostra a verdade")

    def test_check_now_skips_push_checks(self):
        os.environ["GUARDIAN_TEST_API_TOKEN"] = "x"
        self.addCleanup(os.environ.pop, "GUARDIAN_TEST_API_TOKEN", None)
        cfg = make_cfg(checks=PUSH, extra=API_ON)
        eng = g.Engine(cfg, self.clock)
        runner = g.Runner(cfg, eng, None, threading.Event(), runner=lambda c, lang: self.fail("push não é verificado à força"))
        self.assertIn("Não há o que verificar", g.handle_command("/verificar", eng, runner))

    def test_english_commands_and_texts(self):
        eng = g.Engine(make_cfg(language="en", failures_to_alert=1, checks=TWO), self.clock)
        runner = g.Runner(eng.cfg, eng, self.sink, threading.Event(), runner=lambda c, lang: OK)
        self.assertIn("Alerts paused for 2 h", g.handle_command("/pause 2h", eng, runner))
        self.assertIn("back to normal", g.handle_command("/resume", eng, runner))
        self.assertIn("Checked just now", g.handle_command("/check now", eng, runner))

    def test_commands_via_the_real_telegram_poll_loop(self):
        cls, srv = fresh_capture()
        self.addCleanup(stop_server, srv)
        cls.updates = [
            {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/pausar 5d"}},        # estranho: ignorado
            {"update_id": 2, "message": {"chat": {"id": 42}, "text": "/pausar 45m Outro"}},
            {"update_id": 3, "message": {"chat": {"id": 42}, "text": "/status"}},
            {"update_id": 4, "message": {"chat": {"id": 42}, "text": "/retomar"}},
        ]
        tg = g.Telegram({"token": "T", "chat_id": "42", "api_base": f"http://127.0.0.1:{srv.server_address[1]}"})
        stop = threading.Event()
        t = threading.Thread(target=tg.poll, args=(self.eng, stop, self.runner), daemon=True)
        t.start()
        self.assertTrue(wait_until(lambda: sum(p.endswith("/sendMessage") for p, _, _ in cls.posts) >= 3))
        stop.set()
        t.join(5)
        sent = [json.loads(raw)["text"] for p, _, raw in cls.posts if p.endswith("/sendMessage")]
        self.assertIn("Outro", sent[0])
        self.assertIn("pausa", sent[1])
        self.assertIn("voltaram ao normal", sent[2])
        self.assertEqual(len(sent), 3, "o chat desconhecido não mexeu em nada")


class PushApiTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.eng = g.Engine(push_cfg(), self.clock)
        self.emitted = []
        self.port = self.serve("tok-de-teste")

    def tearDown(self):
        os.environ.pop("GUARDIAN_TEST_API_TOKEN", None)

    def serve(self, token):
        srv = g.make_api(self.eng, "127.0.0.1", 0, token, emit=self.emitted.extend)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(stop_server, srv)
        return srv.server_address[1]

    def req(self, path, method="GET", token="tok-de-teste", port=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", port or self.port, timeout=5)
        c.request(method, path, body=body, headers={"Authorization": f"Bearer {token}"} if token else {})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"null")

    def pinged(self, name="Backup"):
        return "last_ping" in self.eng.state["checks"].get(name, {})

    def test_a_signal_needs_the_token(self):
        self.assertEqual(self.req("/ping/Backup", token=None)[0], 401)
        self.assertEqual(self.req("/ping/Backup", token="errado")[0], 401)
        self.assertEqual(self.req("/ping/Backup", method="POST", token=None)[0], 401)
        self.assertFalse(self.pinged())
        self.assertEqual(self.req("/ping/Backup"), (200, {"status": "ok"}))
        self.assertTrue(self.pinged())
        self.assertEqual(self.eng.snapshot()["checks"][0]["status"], "up")

    def test_post_with_a_body_works_and_head_never_counts(self):
        self.assertEqual(self.req("/ping/Backup", method="HEAD")[0], 405)
        self.assertFalse(self.pinged(), "HEAD não pode dar sinal (prefetch, proxies...)")
        self.assertEqual(self.req("/ping/Backup", method="POST", body=b"backup ok, 3 arquivos")[0], 200)
        self.assertTrue(self.pinged())
        self.assertEqual(self.req("/ping/Backup", method="POST", body=b"x" * 70000)[0], 413)

    def test_names_with_spaces_and_the_fail_signal(self):
        self.assertEqual(self.req("/ping/Meu%20Backup")[0], 200)
        self.assertTrue(self.pinged("Meu Backup"))
        self.assertEqual(self.req("/ping/Backup/fail")[0], 200)
        self.assertEqual([e["kind"] for e in self.emitted], ["down"], "o aviso sai pelo mesmo caminho dos outros")
        self.assertEqual(self.req("/ping/Backup")[0], 200)
        self.assertEqual([e["kind"] for e in self.emitted], ["down", "up"])

    def test_unknown_names_and_shapes(self):
        self.assertEqual(self.req("/ping/Nada")[0], 404)
        self.assertEqual(self.req("/ping/Site")[0], 404, "só check 'push' recebe sinal")
        self.assertEqual(self.req("/ping/Backup/xyz")[0], 404)
        self.assertEqual(self.req("/ping/Backup/fail/extra")[0], 404)
        self.assertEqual(self.req("/ping/..%2F..%2Fetc")[0], 404)

    def test_everything_else_is_still_read_only(self):
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            self.assertEqual(self.req("/api/v1/status", method=method)[0], 405, method)
        self.assertEqual(self.req("/api/v1/status")[0], 200)

    def test_without_a_token_the_signal_route_refuses_everything(self):
        port = self.serve("")
        self.assertEqual(self.req("/ping/Backup", token=None, port=port)[0], 403)
        self.assertEqual(self.req("/ping/Backup", method="POST", token=None, port=port)[0], 403)
        self.assertFalse(self.pinged())


class HeartbeatTests(unittest.TestCase):
    def beat_server(self, status=200):
        cls = type("B", (BaseHTTPRequestHandler,), {"hits": [], "status": status, "log_message": lambda *a: None})

        def do_GET(self):
            type(self).hits.append(self.path)
            self.send_response(type(self).status)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"OK")
        cls.do_GET = do_GET
        srv = serve(cls)
        self.addCleanup(stop_server, srv)
        return cls, srv.server_address[1]

    def test_pings_while_alive_and_goes_silent_when_the_loop_is_stuck(self):
        cls, port = self.beat_server()
        alive = [True]
        hb = g.Heartbeat(f"http://127.0.0.1:{port}/beat/SEGREDO", 0.1, lambda: alive[0])
        stop = threading.Event()
        t = threading.Thread(target=hb.run, args=(stop,), daemon=True)
        t.start()
        self.assertTrue(wait_until(lambda: len(cls.hits) >= 3))
        self.assertEqual(cls.hits[0], "/beat/SEGREDO")
        alive[0] = False                                  # o laço principal travou: não pode "dar sinal de vida"
        time.sleep(0.3)
        frozen = len(cls.hits)
        time.sleep(0.6)
        self.assertEqual(len(cls.hits), frozen)
        alive[0] = True
        self.assertTrue(wait_until(lambda: len(cls.hits) > frozen))
        stop.set()
        t.join(5)

    def test_a_failing_service_is_logged_once_and_the_recovery_once_without_the_secret(self):
        g.REDACT.add("SEGREDO-HB")
        cls, port = self.beat_server(status=500)
        hb = g.Heartbeat(f"http://127.0.0.1:{port}/beat/SEGREDO-HB", 0.1, lambda: True)
        stop = threading.Event()
        with self.assertLogs("guardian", level="INFO") as cm:
            t = threading.Thread(target=hb.run, args=(stop,), daemon=True)
            t.start()
            self.assertTrue(wait_until(lambda: len(cls.hits) >= 4))
            cls.status = 200
            self.assertTrue(wait_until(lambda: hb.ok is True))
            stop.set()
            t.join(5)
        warnings = [line for line in cm.output if "WARNING" in line]
        self.assertEqual(len(warnings), 1, cm.output)
        self.assertEqual(sum("voltou a chegar" in line for line in cm.output), 1)
        self.assertNotIn("SEGREDO-HB", "\n".join(cm.output))

    def test_unreachable_service_never_breaks_the_loop(self):
        hb = g.Heartbeat(f"http://127.0.0.1:{free_port()}/x", 0.1, lambda: True)
        stop = threading.Event()
        t = threading.Thread(target=hb.run, args=(stop,), daemon=True)
        with self.assertLogs("guardian", level="WARNING"):
            t.start()
            self.assertTrue(wait_until(lambda: hb.ok is False))
        self.assertTrue(t.is_alive())
        stop.set()
        t.join(5)


class ControlRunnerTests(unittest.TestCase):
    """Com o Runner de verdade e um Telegram falso: o caminho completo, do relógio ao aviso."""

    def setUp(self):
        self.cls, srv = fresh_capture()
        self.addCleanup(stop_server, srv)
        self.tg = g.Telegram({"token": "T", "chat_id": "42", "api_base": f"http://127.0.0.1:{srv.server_address[1]}"})
        self.disp = g.Dispatcher([self.tg], retry_delays=(0,))
        self.stop = threading.Event()

    def tearDown(self):
        os.environ.pop("GUARDIAN_TEST_API_TOKEN", None)

    def messages(self):
        return [json.loads(raw)["text"] for p, _, raw in self.cls.posts if p.endswith("/sendMessage")]

    def start(self, cfg, eng):
        runner = g.Runner(cfg, eng, self.disp, self.stop)
        t = threading.Thread(target=runner.loop, daemon=True)
        t.start()
        return runner, t

    def finish(self, t):
        self.stop.set()
        t.join(10)
        self.disp.flush()
        self.disp.close()

    def test_a_site_taken_down_on_purpose_is_silent_and_you_hear_when_the_pause_ends(self):
        cfg = make_cfg(failures_to_alert=1, checks=f'[[check]]\nname = "Meu site"\nurl = "http://127.0.0.1:{free_port()}/"\ntimeout = 1\n')
        cfg.checks[0].interval = 0.5
        eng = g.Engine(cfg)
        eng.pause(2.5)                                    # "vou mexer no servidor"
        runner, t = self.start(cfg, eng)
        self.assertTrue(wait_until(lambda: eng.snapshot()["summary"]["down"] == 1), "continua medindo durante a pausa")
        self.assertEqual(self.messages(), [], "mas não avisa")
        self.assertTrue(wait_until(lambda: any("continua fora do ar" in m for m in self.messages()), 15), self.messages())
        self.finish(t)
        msgs = self.messages()
        self.assertEqual(sum("FORA DO AR" in m for m in msgs), 0, "o 'fora do ar' original foi engolido de propósito")
        self.assertTrue(any("A pausa terminou" in m for m in msgs), msgs)

    def test_missing_signal_from_a_script_alerts_and_a_late_signal_clears_it(self):
        cfg = push_cfg(failures_to_alert=1)
        for c in cfg.checks:
            if c.kind == "push":
                c.interval, c.grace = 0.8, 0.2
        cfg.checks = [c for c in cfg.checks if c.name == "Backup"]
        eng = g.Engine(cfg)
        runner, t = self.start(cfg, eng)
        self.assertTrue(wait_until(lambda: any("nenhum sinal" in m for m in self.messages()), 15), self.messages())
        eng_events = runner.emit(eng.ping("Backup"))
        self.assertTrue(wait_until(lambda: any("voltou ao ar" in m for m in self.messages())), self.messages())
        self.finish(t)
        self.assertEqual(sum("FORA DO AR" in m for m in self.messages()), 1)


if __name__ == "__main__":
    logging.disable(logging.NOTSET)
    unittest.main()
