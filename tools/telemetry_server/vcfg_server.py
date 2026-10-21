#!/usr/bin/env python3
"""VCFG Crash & Diagnostic Telemetry Server (PS4 -> VPS -> Disk / Telegram / Discord).

Zero external dependencies (Python 3.8+ standard library only).
Accepts plain-text HTTP POST reports from the PS4 VCFG Launcher:
  - POST /api/v1/report  (or POST /report)
  - GET  /health

Saves each report under:
  <data_dir>/<CUSAxxxxx>/<YYYYMMDD_HHMMSS>_<kind>_<ID>.txt
And returns:
  HTTP 200 OK
  OK #<6-char-ID>
"""

import argparse
import datetime
import glob
import hashlib
import http.server
import json
import os
import re
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_BODY_BYTES = 512 * 1024  # 512 KB max per report
TITLE_RE = re.compile(r"\b(CUSA\d{5})\b")


def extract_metadata(body_text: str, headers) -> dict:
    title = headers.get("X-VCFG-Title", "").strip().upper()
    if not TITLE_RE.fullmatch(title):
        m = TITLE_RE.search(body_text)
        title = m.group(1) if m else "GLOBAL"

    kind = headers.get("X-VCFG-Kind", "").strip().lower()
    if kind not in ("crash", "manual", "auto"):
        kind = "crash" if ("=== crash-" in body_text or "CRASH REPORT" in body_text or "sig=" in body_text) else "manual"

    ver = headers.get("X-VCFG-Version", "").strip()
    if not ver:
        mv = re.search(r"CLASSIC_BUILD\s+version=(\S+)", body_text)
        ver = mv.group(1) if mv else "v2.26"

    stage = ""
    for line in body_text.splitlines():
        ls = line.strip()
        if any(k in ls for k in ("sig=", "STAGE:", "safe mode", "INERT", "GPU time of our passes:", "adapt:")):
            stage = ls[:140]
            if "sig=" in ls or "INERT" in ls:
                break

    return {
        "title": title,
        "kind": kind,
        "version": ver,
        "summary_line": stage or "Diagnostic report bundle",
    }


def tg_send_text(bot_token: str, chat_id: str, text: str):
    if not bot_token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": str(chat_id), "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as exc:
        sys.stderr.write(f"[telegram] sendMessage failed for {chat_id}: {exc}\n")


def forward_telegram(bot_token: str, chat_id: str, report_id: str, meta: dict, filepath: str, body_bytes: bytes):
    if not bot_token or not chat_id:
        return
    caption = (
        f"📡 VCFG Report #{report_id}\n"
        f"• Title: {meta['title']}\n"
        f"• Type: {meta['kind'].upper()} ({meta['version']})\n"
        f"• Info: {meta['summary_line']}"
    )
    boundary = "----VCFGTelemetryBoundary" + report_id
    filename = os.path.basename(filepath)
    parts = []
    for name, val in (("chat_id", str(chat_id)), ("caption", caption)):
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{val}\r\n".encode("utf-8")
        )
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n".encode("utf-8")
        + body_bytes
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )
    payload = b"".join(parts)
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except Exception as exc:
        sys.stderr.write(f"[telegram] forward failed for #{report_id} -> {chat_id}: {exc}\n")


def forward_discord(webhook_url: str, report_id: str, meta: dict, filepath: str, body_bytes: bytes):
    if not webhook_url:
        return
    content = (
        f"**VCFG Report `#{report_id}`** | `{meta['title']}` | "
        f"**{meta['kind'].upper()}** (`{meta['version']}`)\n"
        f"> `{meta['summary_line']}`"
    )
    boundary = "----VCFGDiscordBoundary" + report_id
    filename = os.path.basename(filepath)
    payload_json = json.dumps({"content": content}).encode("utf-8")
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="payload_json"\r\n'
        f"Content-Type: application/json\r\n\r\n".encode("utf-8")
        + payload_json
        + f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n".encode("utf-8")
        + body_bytes
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "VCFG-Telemetry/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except Exception as exc:
        sys.stderr.write(f"[discord] forward failed for #{report_id}: {exc}\n")


def tg_poll_loop(srv):
    """Polls getUpdates so anyone messaging @vcfg_report_bot registers their chat_id automatically."""
    token = srv.tg_bot_token
    if not token:
        return
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates?timeout=25&offset={offset}"
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            for upd in data.get("result", []):
                offset = max(offset, int(upd.get("update_id", 0)) + 1)
                msg = upd.get("message") or upd.get("channel_post") or {}
                chat = msg.get("chat") or {}
                cid = str(chat.get("id", "")).strip()
                text = (msg.get("text") or "").strip()
                if not cid:
                    continue
                is_new = srv.register_chat(cid)
                if is_new or text.startswith("/start"):
                    tg_send_text(
                        token,
                        cid,
                        f"✅ Бот VCFG Telemetry подключён (chat_id: {cid}).\n"
                        f"Сюда автоматически будут приходить краш-логи и отчёты с PS4 при запуске апки VCFG.\n"
                        f"Команды:\n"
                        f"/status — сводка по сохранённым логам\n"
                        f"/last — прислать последний репорт",
                    )
                    if is_new:
                        files = sorted(glob.glob(os.path.join(srv.data_dir, "*", "*.txt")), key=os.path.getmtime)
                        if files:
                            latest = files[-1]
                            with open(latest, "rb") as f:
                                raw = f.read()
                            base = os.path.basename(latest)
                            parts = base.replace(".txt", "").split("_")
                            rep_id = parts[-1] if parts else "LATEST"
                            title = os.path.basename(os.path.dirname(latest))
                            forward_telegram(
                                token,
                                cid,
                                rep_id,
                                {"title": title, "kind": "latest", "version": "v2.26", "summary_line": base},
                                latest,
                                raw,
                            )
                elif text.startswith("/status"):
                    files = glob.glob(os.path.join(srv.data_dir, "*", "*.txt"))
                    tg_send_text(
                        token,
                        cid,
                        f"📊 VCFG Telemetry OK\nВсего сохранённых репортов: {len(files)}\nПодключённых чатов: {len(srv.get_chats())}",
                    )
                elif text.startswith("/last"):
                    files = sorted(glob.glob(os.path.join(srv.data_dir, "*", "*.txt")), key=os.path.getmtime)
                    if not files:
                        tg_send_text(token, cid, "Пока нет ни одного репорта.")
                    else:
                        latest = files[-1]
                        with open(latest, "rb") as f:
                            raw = f.read()
                        base = os.path.basename(latest)
                        parts = base.replace(".txt", "").split("_")
                        rep_id = parts[-1] if parts else "LATEST"
                        title = os.path.basename(os.path.dirname(latest))
                        forward_telegram(
                            token,
                            cid,
                            rep_id,
                            {"title": title, "kind": "latest", "version": "v2.26", "summary_line": base},
                            latest,
                            raw,
                        )
        except Exception as exc:
            sys.stderr.write(f"[telegram-poll] {exc}\n")
            time.sleep(5)


class TelemetryHandler(http.server.BaseHTTPRequestHandler):
    server_version = "VCFGTelemetry/1.0"

    def do_GET(self):
        if self.path.startswith("/health") or self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"VCFG Telemetry Server OK\n")
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if not (self.path.startswith("/api/v1/report") or self.path.startswith("/report")):
            self.send_response(404)
            self.end_headers()
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0

        if length <= 0 or length > MAX_BODY_BYTES:
            self.send_response(413 if length > MAX_BODY_BYTES else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ERR invalid Content-Length\n")
            return

        body_bytes = self.rfile.read(length)
        body_text = body_bytes.decode("utf-8", errors="replace")
        now = datetime.datetime.now(datetime.timezone.utc)
        ts_str = now.strftime("%Y%m%d_%H%M%S")
        digest = hashlib.sha256(body_bytes + now.isoformat().encode("ascii")).hexdigest()[:6].upper()

        meta = extract_metadata(body_text, self.headers)
        title_dir = os.path.join(self.server.data_dir, meta["title"])
        os.makedirs(title_dir, exist_ok=True)

        fname = f"{ts_str}_{meta['kind']}_{digest}.txt"
        fpath = os.path.join(title_dir, fname)

        header_banner = (
            f"# VCFG TELEMETRY REPORT #{digest}\n"
            f"# UTC: {now.isoformat()}\n"
            f"# Title: {meta['title']} | Kind: {meta['kind']} | Version: {meta['version']}\n"
            f"# Summary: {meta['summary_line']}\n"
            f"# ------------------------------------------------------------------------\n\n"
        ).encode("utf-8")
        full_bytes = header_banner + body_bytes

        with open(fpath, "wb") as f:
            f.write(full_bytes)

        # Async webhook forwarding so the PS4 client never waits on Telegram/Discord
        tg_token = self.server.tg_bot_token
        tg_chats = self.server.get_chats()
        discord_url = self.server.discord_webhook
        if tg_token or discord_url:
            threading.Thread(
                target=self._forward_async,
                args=(tg_token, tg_chats, discord_url, digest, meta, fpath, full_bytes),
                daemon=True,
            ).start()

        resp_bytes = f"OK #{digest}\n".encode("ascii")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)

    @staticmethod
    def _forward_async(tg_token, tg_chats, discord_url, digest, meta, fpath, full_bytes):
        if tg_token and tg_chats:
            for cid in tg_chats:
                forward_telegram(tg_token, cid, digest, meta, fpath, full_bytes)
        if discord_url:
            forward_discord(discord_url, digest, meta, fpath, full_bytes)

    def log_message(self, fmt, *args):
        sys.stdout.write(f"[{self.log_date_time_string()}] {fmt % args}\n")
        sys.stdout.flush()


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def __init__(self, server_address, RequestHandlerClass, data_dir, tg_bot_token="", tg_chat_id="", discord_webhook=""):
        super().__init__(server_address, RequestHandlerClass)
        self.data_dir = data_dir
        self.tg_bot_token = tg_bot_token
        self.discord_webhook = discord_webhook
        self._lock = threading.Lock()
        self._chats_file = os.path.join(data_dir, "tg_chats.json")
        self._chats = set()
        if tg_chat_id:
            for c in str(tg_chat_id).split(","):
                if c.strip():
                    self._chats.add(c.strip())
        if os.path.exists(self._chats_file):
            try:
                with open(self._chats_file, "r", encoding="utf-8") as f:
                    for c in json.load(f):
                        if str(c).strip():
                            self._chats.add(str(c).strip())
            except Exception:
                pass

    def register_chat(self, chat_id: str) -> bool:
        with self._lock:
            is_new = chat_id not in self._chats
            self._chats.add(chat_id)
            try:
                with open(self._chats_file, "w", encoding="utf-8") as f:
                    json.dump(sorted(self._chats), f)
            except Exception:
                pass
            return is_new

    def get_chats(self):
        with self._lock:
            return sorted(self._chats)


def main():
    parser = argparse.ArgumentParser(description="VCFG Crash & Log Telemetry Server for VPS")
    parser.add_argument("--host", default=os.environ.get("VCFG_HOST", "0.0.0.0"), help="Bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("VCFG_PORT", "8765")), help="Bind port (default 8765)")
    parser.add_argument("--data-dir", default=os.environ.get("VCFG_DATA_DIR", "./reports"), help="Directory to store reports")
    parser.add_argument("--tg-token", default=os.environ.get("VCFG_TG_BOT_TOKEN", os.environ.get("TG_BOT_TOKEN", "")), help="Optional Telegram Bot Token")
    parser.add_argument("--tg-chat", default=os.environ.get("VCFG_TG_CHAT_ID", os.environ.get("TG_CHAT_ID", "")), help="Optional Telegram Chat ID")
    parser.add_argument("--discord-webhook", default=os.environ.get("VCFG_DISCORD_WEBHOOK", os.environ.get("DISCORD_WEBHOOK_URL", "")), help="Optional Discord Webhook URL")
    args = parser.parse_args()

    os.makedirs(args.data_dir, exist_ok=True)
    srv = ThreadedHTTPServer(
        (args.host, args.port),
        TelemetryHandler,
        data_dir=os.path.abspath(args.data_dir),
        tg_bot_token=args.tg_token,
        tg_chat_id=args.tg_chat,
        discord_webhook=args.discord_webhook,
    )
    if args.tg_token:
        threading.Thread(target=tg_poll_loop, args=(srv,), daemon=True).start()
    print(f"VCFG Telemetry Server listening on http://{args.host}:{args.port}/api/v1/report -> {os.path.abspath(args.data_dir)}")
    sys.stdout.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        srv.server_close()


if __name__ == "__main__":
    main()
