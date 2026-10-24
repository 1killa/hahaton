"""Small, strict HTTP client for the authenticated STE loopback API.

The native window deliberately talks to the same AssistantServer as the web
window.  This module contains no Qt code: callers can use it from worker
threads and deliver only decoded results to the GUI thread.
"""
import base64
import json
import os
import re
import socket
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NativeClientError(RuntimeError):
    """A transport or API error safe to show to the user."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise NativeClientError("Сервер попытался перенаправить loopback-запрос.")


_OUTPUT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class LoopbackClient(object):
    """Authenticated API-3 client restricted to one exact HTTP loopback origin."""

    _GET_ROUTES = ("/api/state",)
    _POST_ROUTES = (
        "/api/settings", "/api/message", "/api/continue", "/api/cancel",
        "/api/conversations", "/api/conversations/select", "/api/upload",
        "/api/requirements", "/api/tool",
    )

    def __init__(self, origin, token, timeout=20.0, opener=None):
        if not isinstance(token, str) or not token or "\r" in token or "\n" in token:
            raise ValueError("Недействительный токен локальной сессии.")
        if not isinstance(origin, str):
            raise ValueError("Недействительный loopback origin.")
        parsed = urlsplit(origin)
        if parsed.scheme != "http" or parsed.username or parsed.password:
            raise ValueError("Native client разрешает только HTTP loopback.")
        if parsed.hostname != "127.0.0.1" or parsed.query or parsed.fragment:
            raise ValueError("Native client разрешает только 127.0.0.1 без query.")
        try:
            port = parsed.port
        except ValueError:
            port = None
        if not port or parsed.path not in ("", "/"):
            raise ValueError("Недействительный loopback origin.")
        self.origin = urlunsplit(("http", "127.0.0.1:%s" % port, "", "", ""))
        self.token = token
        self.timeout = float(timeout)
        self._opener = opener or build_opener(ProxyHandler({}), _NoRedirect())

    def _url(self, path):
        if not isinstance(path, str) or not path.startswith("/") or "?" in path and path.split("?", 1)[0] not in self._GET_ROUTES:
            raise ValueError("Недопустимый native API route.")
        parsed = urlsplit(self.origin + path)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
            raise ValueError("Недопустимый native API origin.")
        return self.origin + path

    def _read_error(self, error, payload=None):
        if isinstance(error, HTTPError):
            detail = ""
            try:
                raw = error.read(4096)
                try:
                    value = json.loads(raw.decode("utf-8"))
                    if isinstance(value, dict):
                        detail = value.get("error") or value.get("message") or ""
                except (ValueError, UnicodeError):
                    detail = ""
            except Exception:
                pass
            detail = str(detail)[:512]
            detail = detail.replace(self.token, "[SESSION HIDDEN]")
            if isinstance(payload, dict):
                key = payload.get("api_key")
                if isinstance(key, str) and key:
                    detail = detail.replace(key, "[API KEY HIDDEN]")
            suffix = ": " + detail if detail else ""
            return NativeClientError("Сервер помощника вернул HTTP %s%s." % (error.code, suffix))
        if isinstance(error, (socket.timeout, TimeoutError)):
            return NativeClientError("Сервер помощника не ответил вовремя.")
        if isinstance(error, URLError):
            reason = getattr(error, "reason", None)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                return NativeClientError("Сервер помощника не ответил вовремя.")
            return NativeClientError("Соединение с локальным сервером помощника недоступно.")
        if isinstance(error, OSError):
            return NativeClientError("Соединение с локальным сервером помощника недоступно.")
        return NativeClientError("Ошибка локального транспорта помощника.")

    def _request_raw(self, method, path, payload=None, stream=False):
        if method == "GET":
            parsed_path = urlsplit(path)
            if parsed_path.path not in self._GET_ROUTES or parsed_path.fragment:
                raise ValueError("GET route не разрешён.")
            if parsed_path.query:
                query = parse_qs(parsed_path.query, keep_blank_values=False)
                if set(query) != set(("after",)) or len(query["after"]) != 1:
                    raise ValueError("GET query не разрешён.")
                try:
                    if int(query["after"][0]) < 0:
                        raise ValueError
                except (TypeError, ValueError):
                    raise ValueError("GET cursor не разрешён.")
        elif method == "POST":
            if path not in self._POST_ROUTES:
                raise ValueError("POST route не разрешён.")
        elif method != "GET-DOWNLOAD":
            raise ValueError("HTTP method не разрешён.")
        elif (not path.startswith("/api/download/") or
              not _OUTPUT_ID.match(path[len("/api/download/"):])):
            raise ValueError("Download route не разрешён.")
        url = self._url(path)
        headers = {
            "X-AIAssistant-Token": self.token,
            "Accept": "application/json",
        }
        data = None
        if method == "POST":
            headers["Origin"] = self.origin
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload if payload is not None else {}, ensure_ascii=False).encode("utf-8")
        elif method == "GET-DOWNLOAD":
            headers["Origin"] = self.origin
            headers["Accept"] = "application/octet-stream"
        request = Request(url, data=data, headers=headers, method="GET" if method == "GET-DOWNLOAD" else method)
        try:
            response = self._opener.open(request, timeout=self.timeout)
            return response
        except Exception as error:
            raise self._read_error(error, payload)

    def request(self, method, path, payload=None):
        """Issue one of the explicitly supported JSON API calls."""
        response = self._request_raw(method.upper(), path, payload)
        try:
            raw = response.read(8 * 1024 * 1024)
        finally:
            response.close()
        try:
            value = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise NativeClientError("Сервер помощника вернул некорректный JSON.")
        if not isinstance(value, dict):
            raise NativeClientError("Сервер помощника вернул некорректный ответ.")
        return value

    def state(self, cursor=0):
        try:
            cursor = int(cursor)
        except (TypeError, ValueError):
            raise ValueError("Некорректный cursor.")
        if cursor < 0:
            raise ValueError("Некорректный cursor.")
        return self.request("GET", "/api/state?after=%s" % cursor)

    def settings(self, base_url=None, model=None, api_key="", clear_key=False):
        value = {"api_key": api_key or ""}
        if base_url is not None:
            value["base_url"] = base_url
        if model is not None:
            value["model"] = model
        if clear_key:
            value["clear_key"] = True
        return self.request("POST", "/api/settings", value)

    def message(self, text, attachments=None):
        return self.request("POST", "/api/message", {
            "text": text if isinstance(text, str) else "",
            "attachments": list(attachments or []),
        })

    def continue_pending(self):
        return self.request("POST", "/api/continue", {})

    def cancel(self):
        return self.request("POST", "/api/cancel", {})

    def conversations(self):
        return self.request("POST", "/api/conversations", {})

    def select_conversation(self, conversation_id):
        return self.request("POST", "/api/conversations/select", {"id": conversation_id})

    def upload(self, filename, data):
        if not isinstance(filename, str) or not isinstance(data, bytes):
            raise ValueError("Некорректное native attachment.")
        return self.request("POST", "/api/upload", {
            "filename": filename,
            "data_base64": base64.b64encode(data).decode("ascii"),
        })

    def requirements(self, settings, material_id="", preserve_dimensions=False, notes=""):
        return self.request("POST", "/api/requirements", {
            "settings": list(settings or []),
            "material_id": material_id or "",
            "preserve_dimensions": bool(preserve_dimensions),
            "notes": notes or "",
        })

    def tool(self, name, arguments=None):
        return self.request("POST", "/api/tool", {
            "name": name,
            "arguments": dict(arguments or {}),
        })

    def download(self, output_id, target_path, cancel_event=None):
        if not isinstance(output_id, str) or not _OUTPUT_ID.match(output_id):
            raise ValueError("Недействительный идентификатор результата.")
        if not isinstance(target_path, str) or not target_path:
            raise ValueError("Не выбран файл результата.")
        target = os.path.abspath(target_path)
        folder = os.path.dirname(target)
        if not os.path.isdir(folder):
            raise ValueError("Папка результата не существует.")
        response = self._request_raw("GET-DOWNLOAD", "/api/download/" + quote(output_id, safe=""),
                                     stream=True)
        temporary = None
        try:
            expected = None
            try:
                header = response.headers.get("Content-Length")
                if header is not None:
                    expected = int(header)
                    if expected < 0:
                        raise ValueError
            except (AttributeError, TypeError, ValueError):
                raise NativeClientError("Сервер помощника вернул некорректный размер результата.")
            descriptor, temporary = tempfile.mkstemp(prefix="." + os.path.basename(target) + ".",
                                                      suffix=".part", dir=folder)
            received = 0
            with os.fdopen(descriptor, "wb") as stream:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise NativeClientError("Загрузка результата отменена.")
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    received += len(chunk)
                    stream.write(chunk)
            if expected is not None and received != expected:
                raise NativeClientError("Загрузка результата оборвалась до конца файла.")
            if cancel_event is not None and cancel_event.is_set():
                raise NativeClientError("Загрузка результата отменена.")
            os.replace(temporary, target)
            temporary = None
            return target
        except NativeClientError:
            raise
        except Exception as error:
            raise self._read_error(error)
        finally:
            try:
                response.close()
            except Exception:
                pass
            if temporary:
                try:
                    os.remove(temporary)
                except OSError:
                    pass
