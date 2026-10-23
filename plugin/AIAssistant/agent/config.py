"""Provider settings. Secret values never enter the public state."""
import threading
from urllib.parse import urlsplit, urlunsplit

DEFAULT_URL = "https://api.timeweb.ai/v1"
DEFAULT_MODEL = "dashscope/qwen3.8-flash"


class ConfigurationError(ValueError):
    pass


def validate_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ConfigurationError("Некорректный Base URL.")
    parsed = urlsplit(value.strip())
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError("Base URL не должен содержать ключ, логин, query или fragment.")
    if not parsed.hostname or parsed.scheme not in ("https", "http"):
        raise ConfigurationError("Укажите HTTPS Base URL провайдера.")
    if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ConfigurationError("HTTP разрешён только для локального провайдера.")
    try:
        parsed.port
    except ValueError:
        raise ConfigurationError("Некорректный порт Base URL.")
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        path = path[:-len("/chat/completions")]
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class ConfigStore:
    def __init__(self, read=None, write=None):
        self._write = write
        self._lock = threading.RLock()
        self._values = {"base_url": DEFAULT_URL, "model": DEFAULT_MODEL, "api_key": ""}
        if read:
            for key in self._values:
                value = read(key)
                if isinstance(value, str) and value:
                    self._values[key] = value

    def public(self):
        with self._lock:
            return {"base_url": self._values["base_url"], "model": self._values["model"],
                    "has_key": bool(self._values["api_key"])}

    def private(self):
        with self._lock:
            return dict(self._values)

    def update(self, values):
        if not isinstance(values, dict):
            raise ConfigurationError("Настройки должны быть объектом.")
        with self._lock:
            updated = dict(self._values)
            if "base_url" in values:
                updated["base_url"] = validate_url(values["base_url"])
            if "model" in values:
                model = values["model"]
                if not isinstance(model, str) or not model.strip() or len(model) > 200:
                    raise ConfigurationError("Укажите точное имя модели.")
                updated["model"] = model.strip()
            if values.get("clear_key"):
                updated["api_key"] = ""
            elif values.get("api_key"):
                key = values["api_key"]
                if not isinstance(key, str) or len(key) > 8192 or "\n" in key or "\r" in key:
                    raise ConfigurationError("Некорректный API key.")
                if key.strip():
                    updated["api_key"] = key.strip()
            if self._write:
                for key, value in updated.items():
                    self._write(key, value)
            self._values = updated
        return self.public()

    def redact(self, text):
        key = self.private()["api_key"]
        value = str(text)
        return value.replace(key, "[API KEY HIDDEN]") if key else value

    def redact_value(self, value):
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            return {self.redact(str(key)): self.redact_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.redact_value(item) for item in value]
        return value
