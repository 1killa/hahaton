"""Loopback-only SPA, uploads and authenticated JSON endpoints."""
import base64
import binascii
import hmac
import io
import json
import os
import re
import struct
import threading
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, quote, urlsplit

from .rpc import dispatch_rpc
from .tools import ToolError

MAX_UPLOAD = 24 * 1024 * 1024
MAX_BODY = 34 * 1024 * 1024


def validate_upload(filename, data):
    if (not isinstance(filename, str) or not filename or len(filename) > 180 or
            re.search(r"[\\/\x00-\x1f:]", filename) or filename in (".", "..")):
        raise ToolError("Требуется имя файла без пути.")
    extension = os.path.splitext(filename)[1].lower()
    if not data or len(data) > MAX_UPLOAD:
        raise ToolError("Размер вложения должен быть от 1 байта до 24 MiB.")
    if extension == ".png":
        valid = len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR"
        kind, mime = "image", "image/png"
    elif extension in (".jpg", ".jpeg"):
        valid = data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")
        kind, mime = "image", "image/jpeg"
    elif extension == ".stl":
        binary = len(data) >= 84 and 84 + struct.unpack("<I", data[80:84])[0] * 50 == len(data)
        ascii_stl = data.lstrip().lower().startswith(b"solid") and b"facet" in data.lower() and b"endsolid" in data.lower()
        valid, kind, mime = binary or ascii_stl, "model", "model/stl"
    elif extension == ".obj":
        try:
            text = data.decode("utf-8-sig")
            valid = bool(re.search(r"^v\s+", text, re.M) and re.search(r"^f\s+", text, re.M))
        except UnicodeDecodeError:
            valid = False
        kind, mime = "model", "model/obj"
    elif extension == ".3mf":
        valid, kind, mime = False, "model", "model/3mf"
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > 2000 or sum(item.file_size for item in entries) > 200 * 1024 * 1024:
                    raise ToolError("Слишком большой распакованный 3MF.")
                names = []
                for item in entries:
                    name = item.filename.replace("\\", "/")
                    if name.startswith("/") or ":" in name or ".." in name.split("/"):
                        raise ToolError("Недопустимые пути внутри 3MF.")
                    names.append(name.lower())
                valid = "[content_types].xml" in names and any(name.startswith("3d/") and name.endswith(".model") for name in names)
        except (zipfile.BadZipFile, OSError):
            pass
    else:
        raise ToolError("Поддерживаются PNG/JPG и STL/OBJ/3MF.")
    if not valid:
        raise ToolError("Содержимое файла не соответствует расширению " + extension)
    return extension, kind, mime


class AssistantServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, session, tools, config, runtime_dir, mode="demo", port=0):
        self.session, self.tools, self.config = session, tools, config
        self.mode = mode
        self.token = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")
        self.web_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
        self.upload_dir = os.path.join(runtime_dir, "uploads_" + uuid.uuid4().hex[:10])
        os.makedirs(self.upload_dir, exist_ok=True)
        self._uploads = {}
        self._uploads_lock = threading.RLock()
        self._slots = threading.BoundedSemaphore(12)
        HTTPServer.__init__(self, ("127.0.0.1", port), Handler)
        self.authority = "127.0.0.1:%s" % self.server_port
        self.origin = "http://" + self.authority
        self.tools.upload_resolver = self.resolve_upload

    def browser_url(self):
        return self.origin + "/#token=" + self.token

    def get_request(self):
        sock, address = HTTPServer.get_request(self)
        sock.settimeout(20)
        return sock, address

    def process_request(self, request, client_address):
        if not self._slots.acquire(False):
            self.shutdown_request(request)
            return
        try:
            ThreadingMixIn.process_request(self, request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            ThreadingMixIn.process_request_thread(self, request, client_address)
        finally:
            self._slots.release()

    def resolve_upload(self, upload_id):
        with self._uploads_lock:
            item = self._uploads.get(upload_id)
            if not item:
                raise ToolError("Вложение не найдено. Прикрепите файл заново.")
            value = dict(item)
        if item["kind"] == "image":
            with open(item["path"], "rb") as stream:
                value["data_url"] = "data:" + item["mime_type"] + ";base64," + base64.b64encode(stream.read()).decode("ascii")
        return value

    def upload(self, value):
        if not isinstance(value.get("data_base64"), str):
            raise ToolError("Требуется data_base64.")
        try:
            data = base64.b64decode(value["data_base64"].encode("ascii"), validate=True)
        except (binascii.Error, UnicodeError, ValueError):
            raise ToolError("Некорректный base64.")
        extension, kind, mime = validate_upload(value.get("filename"), data)
        with self._uploads_lock:
            if len(self._uploads) >= 32 or sum(item["size"] for item in self._uploads.values()) + len(data) > 128 * 1024 * 1024:
                raise ToolError("Достигнут лимит вложений сессии (32 файла / 128 MiB). Перезапустите помощника.")
            upload_id = uuid.uuid4().hex
            path = os.path.join(self.upload_dir, upload_id + extension)
            with open(path, "wb") as stream:
                stream.write(data)
            item = {"id": upload_id, "name": value["filename"], "kind": kind,
                    "mime_type": mime, "path": path, "size": len(data)}
            self._uploads[upload_id] = item
        return {key: item[key] for key in ("id", "name", "kind", "mime_type")}

    def state(self, after):
        result = self.session.state(after)
        for name, tool in (("scene", "get_scene"), ("slice", "get_slice_status")):
            try:
                result[name] = self.tools.invoke(lambda tool=tool: self.tools.adapter.call(tool, {}))
            except Exception as error:
                result[name] = {"error": self.config.redact(str(error)), "objects": [],
                                "state": "unavailable", "result": {"available": False}}
        result.update({"mode": self.mode, "configured": self.config.public()["has_key"],
                       "settings": self.config.public(), "requirements": self.tools.requirements(),
                       "variants": copy_variants(self.tools.variants), "outputs": self.tools.public_outputs()})
        return result

    def cleanup_uploads(self):
        # Only generated files in this instance's private upload directory.
        with self._uploads_lock:
            for item in self._uploads.values():
                try:
                    os.remove(item["path"])
                except OSError:
                    pass
            self._uploads.clear()
        try:
            os.rmdir(self.upload_dir)
        except OSError:
            pass


def copy_variants(variants):
    return json.loads(json.dumps(variants, ensure_ascii=False))


class Handler(BaseHTTPRequestHandler):
    server_version = "STEAssistant/0.1"

    def log_message(self, *args):
        # The URL fragment never reaches here; suppress logs containing payloads.
        pass

    def _reply(self, value, status=200):
        raw = json.dumps(self.server.config.redact_value(value), ensure_ascii=False, allow_nan=False, default=str)
        body = raw.encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _headers(self, status, content_type, length, disposition=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()

    def _authorize(self, mutation=False):
        if self.headers.get("Host") != self.server.authority:
            raise PermissionError("Host не разрешён.")
        origin = self.headers.get("Origin")
        if (mutation and origin != self.server.origin) or (origin is not None and origin != self.server.origin):
            raise PermissionError("Origin не разрешён.")
        token = self.headers.get("X-AIAssistant-Token", "")
        if not hmac.compare_digest(token, self.server.token):
            raise PermissionError("Откройте чат через меню STE Assistant, чтобы получить токен сессии.")

    def _body(self):
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            raise ToolError("Требуется Content-Type: application/json.")
        if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Encoding"):
            raise ToolError("Chunked/compressed requests не поддерживаются.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ToolError("Некорректный Content-Length.")
        if length <= 0 or length > MAX_BODY:
            raise ToolError("Слишком большой или пустой запрос (лимит 34 MiB).")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeError):
            raise ToolError("Некорректный JSON.")
        if not isinstance(value, dict):
            raise ToolError("Требуется JSON-объект.")
        return value

    def _idle(self):
        if self.server.session.state()["busy"]:
            raise ToolError("Помощник занят. Дождитесь завершения операции.")

    def _exclusive_update(self, function):
        lock = self.server.tools.operation_lock
        if not lock.acquire(False):
            raise ToolError("Помощник занят. Дождитесь завершения операции.")
        try:
            self._idle()
            return function()
        finally:
            lock.release()

    def do_GET(self):
        try:
            parsed = urlsplit(self.path)
            if self.headers.get("Host") != self.server.authority:
                raise PermissionError("Host не разрешён.")
            if parsed.path.startswith("/api/"):
                self._authorize()
                if parsed.path == "/api/state":
                    after = int(parse_qs(parsed.query).get("after", ["0"])[0])
                    self._reply(self.server.state(after))
                elif parsed.path.startswith("/api/download/"):
                    output_id = parsed.path[len("/api/download/"):]
                    item = next((item for item in self.server.tools.outputs if item["id"] == output_id), None)
                    if not item or os.path.dirname(os.path.realpath(item["path"])) != self.server.tools.output_dir:
                        raise ToolError("Файл результата не найден.")
                    with open(item["path"], "rb") as stream:
                        self._headers(200, "application/octet-stream", os.path.getsize(item["path"]),
                                      "attachment; filename*=UTF-8''" + quote(item["filename"], safe=""))
                        while True:
                            chunk = stream.read(65536)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                else:
                    self._reply({"error": "Endpoint не найден."}, 404)
                return
            static = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "application/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if parsed.path not in static:
                self._reply({"error": "Файл не найден."}, 404)
                return
            filename, mime = static[parsed.path]
            with open(os.path.join(self.server.web_dir, filename), "rb") as stream:
                body = stream.read()
            self._headers(200, mime, len(body))
            self.wfile.write(body)
        except PermissionError as error:
            self._reply({"error": str(error)}, 403)
        except (ToolError, ValueError) as error:
            self._reply({"error": str(error)}, 400)
        except (OSError, RuntimeError) as error:
            self._reply({"error": self.server.config.redact(str(error))}, 500)

    def do_POST(self):
        try:
            self._authorize(mutation=True)
            value = self._body()
            path = urlsplit(self.path).path
            if path == "/api/settings":
                self._reply(self._exclusive_update(lambda: self.server.config.update(value)))
            elif path == "/api/message":
                ids = value.get("attachments", [])
                if not isinstance(ids, list) or len(ids) > 8 or any(not isinstance(item, str) for item in ids):
                    raise ToolError("Некорректный список вложений.")
                files = [self.server.resolve_upload(item) for item in ids]
                self._reply(self.server.session.submit(value.get("text", ""), files), 202)
            elif path == "/api/tool":
                if not isinstance(value.get("name"), str) or not isinstance(value.get("arguments", {}), dict):
                    raise ToolError("Требуются name и arguments.")
                self._reply(self.server.session.submit_tool(value["name"], value.get("arguments", {})), 202)
            elif path == "/api/upload":
                self._reply(self._exclusive_update(lambda: self.server.upload(value)), 201)
            elif path == "/api/cancel":
                self.server.session.cancel()
                self._reply({"cancel_requested": True})
            elif path == "/api/reset":
                self._exclusive_update(self.server.session.reset)
                self._reply({"reset": True})
            elif path == "/api/requirements":
                self._reply(self._exclusive_update(lambda: self.server.tools.set_requirements(value)))
            elif path in ("/api/mcp", "/mcp"):
                session = self.server.session
                session._begin()
                try:
                    result = dispatch_rpc(value, self.server.tools, cancel=session._cancel)
                    self._reply(result) if result is not None else self._reply({}, 202)
                finally:
                    session._finish()
            else:
                self._reply({"error": "Endpoint не найден."}, 404)
        except PermissionError as error:
            self._reply({"error": str(error)}, 403)
        except (ToolError, ValueError, KeyError, TypeError) as error:
            self._reply({"error": self.server.config.redact(str(error))}, 400)
        except Exception as error:
            self._reply({"error": self.server.config.redact(str(error))}, 500)
