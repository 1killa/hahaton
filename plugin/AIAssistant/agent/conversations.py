"""Small atomic, bounded conversation store used by the local assistant.

The store deliberately has no knowledge of providers or the native scene.  It
only persists JSON compatible chat records and reports I/O/validation failures
to its caller so the in-memory session can keep working.
"""
import copy
import json
import os
import tempfile
import threading
import time
import uuid


SCHEMA = 1
MAX_RECORDS = 50
MAX_RECORD_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
_DROP_KEYS = set((
    "api_key", "apikey", "authorization", "proxy-authorization",
    "password", "access_token", "refresh_token", "session_token",
    "headers", "config", "settings_connection"))


class HistoryError(IOError):
    """A history operation failed without changing the existing file."""


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _normal_key(value):
    try:
        return str(value).casefold()
    except Exception:
        return str(value).lower()


def sanitize_value(value, config=None):
    """Redact configured values and drop exact sensitive field names.

    Deliberately no substring matching is used: ordinary IDs such as
    ``upload_id`` remain available in public records.
    """
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if _normal_key(key) in _DROP_KEYS:
                continue
            safe_key = config.redact_value(str(key)) if config is not None else str(key)
            safe_item = sanitize_value(item, config)
            # Provider tool calls store their arguments as a JSON string.
            # Inspect that recognized boundary so a denied field cannot hide
            # inside an otherwise harmless serialized value.  Malformed
            # arguments remain a string, but known configured secrets are
            # still redacted from it.
            if (_normal_key(key) == "function" and isinstance(item, dict) and
                    isinstance(item.get("arguments"), str) and
                    isinstance(safe_item, dict)):
                arguments = item.get("arguments")
                try:
                    # Replace non-finite JSON constants while decoding so a
                    # malformed provider payload cannot bypass the strict
                    # finite-value serialization below.
                    parsed = json.loads(arguments, parse_constant=lambda token: None)
                    safe_item["arguments"] = json.dumps(
                        sanitize_value(parsed, config), ensure_ascii=False,
                        allow_nan=False, separators=(",", ":"))
                except (ValueError, TypeError):
                    safe_item["arguments"] = (config.redact_value(arguments)
                                                if config is not None else arguments)
            result[safe_key] = safe_item
        return result
    if isinstance(value, (list, tuple)):
        return [sanitize_value(item, config) for item in value]
    if isinstance(value, str) and config is not None:
        return config.redact_value(value)
    return value


def new_record(title="Новый диалог"):
    stamp = _now()
    return {
        "id": uuid.uuid4().hex,
        "title": title,
        "created_at": stamp,
        "updated_at": stamp,
        "messages": [],
        "turns": [],
        "events": [],
        "cursor": 0,
        "status": "idle",
        "requirements": {"settings": [], "material_id": "", "preserve_dimensions": False, "notes": ""},
        "pins": {},
        "variants": [],
        "outputs": []
    }


def empty_state():
    record = new_record()
    return {"schema": SCHEMA, "selected_id": record["id"], "records": [record]}


class ConversationStore(object):
    """Atomic JSON store rooted in a runtime directory."""

    def __init__(self, root_dir):
        self.root_dir = os.path.abspath(root_dir)
        self.path = os.path.join(self.root_dir, "conversations.json")
        self._lock = threading.RLock()
        self.history_error = ""
        self._state = None

    def _read_bytes(self):
        try:
            size = os.path.getsize(self.path)
        except OSError as error:
            if getattr(error, "errno", None) == 2:
                return None
            raise HistoryError("Не удалось прочитать историю: %s" % error)
        if size > MAX_FILE_BYTES:
            raise HistoryError("Файл истории превышает лимит 64 MiB.")
        try:
            with open(self.path, "rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
        except OSError as error:
            raise HistoryError("Не удалось прочитать историю: %s" % error)
        if len(raw) > MAX_FILE_BYTES:
            raise HistoryError("Файл истории превышает лимит 64 MiB.")
        return raw

    def _decode(self, raw):
        if raw is None or not raw.strip():
            raise HistoryError("Файл истории пуст.")
        try:
            state = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as error:
            raise HistoryError("Файл истории повреждён: %s" % error)
        if not isinstance(state, dict) or state.get("schema") != SCHEMA:
            raise HistoryError("Неподдерживаемая схема истории.")
        records = state.get("records")
        if not isinstance(records, list) or len(records) > MAX_RECORDS:
            raise HistoryError("Недопустимое число диалогов в истории.")
        selected = state.get("selected_id")
        if selected is not None and not isinstance(selected, str):
            raise HistoryError("Недопустимый выбранный диалог.")
        identifiers = set()
        for record in records:
            if not isinstance(record, dict):
                raise HistoryError("Недопустимая запись истории.")
            identifier = record.get("id")
            if not isinstance(identifier, str) or not identifier or identifier in identifiers:
                raise HistoryError("Недопустимый или повторяющийся id диалога.")
            identifiers.add(identifier)
            for field in ("title", "created_at", "updated_at", "status"):
                if not isinstance(record.get(field), str):
                    raise HistoryError("Недопустимое поле %s в истории." % field)
            for field in ("messages", "turns", "events", "variants", "outputs"):
                if not isinstance(record.get(field), list):
                    raise HistoryError("Поле %s записи истории должно быть списком." % field)
            cursor = record.get("cursor")
            if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
                raise HistoryError("Недопустимый курсор истории.")
            model_steps = record.get("model_steps", 0)
            if (isinstance(model_steps, bool) or not isinstance(model_steps, int) or
                    model_steps < 0):
                raise HistoryError("Недопустимое число шагов модели.")
            for field in ("last_error", "pause_reason"):
                if field in record and not isinstance(record.get(field), str):
                    raise HistoryError("Недопустимое поле %s в истории." % field)
            for field in ("requirements", "pins"):
                if not isinstance(record.get(field), dict):
                    raise HistoryError("Поле %s записи истории должно быть объектом." % field)
            if "legacy_public_only" in record and not isinstance(record.get("legacy_public_only"), bool):
                raise HistoryError("Недопустимый признак миграции истории.")
            try:
                encoded = json.dumps(record, ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":")).encode("utf-8")
            except (ValueError, TypeError) as error:
                raise HistoryError("Недопустимое значение в истории: %s" % error)
            if len(encoded) > MAX_RECORD_BYTES:
                raise HistoryError("Запись истории превышает лимит 32 MiB.")
        if records and selected not in [item["id"] for item in records]:
            raise HistoryError("Выбранный диалог отсутствует в истории.")
        try:
            json.dumps(state, ensure_ascii=False, allow_nan=False,
                       separators=(",", ":"))
        except (ValueError, TypeError) as error:
            raise HistoryError("Недопустимое значение в истории: %s" % error)
        return state

    def load(self):
        with self._lock:
            self.history_error = ""
            try:
                raw = self._read_bytes()
                if raw is None:
                    state = empty_state()
                else:
                    state = self._decode(raw)
            except HistoryError as error:
                self.history_error = str(error)
                # A corrupt/oversized file is never replaced.  Keep the last
                # successfully loaded state, or a fresh RAM-only state.
                state = copy.deepcopy(self._state) if self._state is not None else empty_state()
            self._state = copy.deepcopy(state)
            return copy.deepcopy(state)

    def save(self, state):
        """Validate and atomically replace the store, or raise HistoryError."""
        with self._lock:
            try:
                # Never replace a file that is already unreadable or outside
                # the bounds.  Callers can continue with their RAM snapshot
                # and surface this error explicitly.
                existing = self._read_bytes()
                if existing is not None:
                    self._decode(existing)
                safe = copy.deepcopy(state)
                safe["schema"] = SCHEMA
                records = safe.get("records")
                if not isinstance(records, list) or len(records) > MAX_RECORDS:
                    raise HistoryError("Недопустимое число диалогов в истории.")
                safe = sanitize_value(safe)
                encoded_records = []
                for record in safe.get("records", []):
                    encoded_record = json.dumps(record, ensure_ascii=False, allow_nan=False,
                                                separators=(",", ":")).encode("utf-8")
                    if len(encoded_record) > MAX_RECORD_BYTES:
                        raise HistoryError("Запись истории превышает лимит 32 MiB.")
                    encoded_records.append(encoded_record)
                encoded = json.dumps(safe, ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":")).encode("utf-8")
                if len(encoded) > MAX_FILE_BYTES:
                    raise HistoryError("Файл истории превышает лимит 64 MiB.")
                # Apply the same structural checks to caller-provided state
                # before creating a replacement file.
                self._decode(encoded)
                if not os.path.isdir(self.root_dir):
                    os.makedirs(self.root_dir)
                fd, temp_path = tempfile.mkstemp(prefix=".conversations-", suffix=".tmp",
                                                 dir=self.root_dir)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(encoded)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temp_path, self.path)
                except (OSError, IOError) as error:
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
                    raise HistoryError("Не удалось сохранить историю: %s" % error)
            except HistoryError:
                raise
            except (ValueError, TypeError, OSError, IOError) as error:
                raise HistoryError("Не удалось сохранить историю: %s" % error)
            self.history_error = ""
            self._state = copy.deepcopy(safe)
            return True

    def import_public(self, legacy_path, config=None):
        """Import only public messages/events into an empty store.

        Provider turns are intentionally not reconstructed.  Existing or
        corrupt history is never overwritten by this migration.
        """
        with self._lock:
            current = self.load()
            if self.history_error:
                return current
            if current.get("records"):
                # A fresh store has one placeholder record.  Treat that exact
                # placeholder as empty, while any user-created record blocks
                # migration.
                records = current.get("records")
                if not (len(records) == 1 and not records[0].get("messages") and
                        not records[0].get("events") and not records[0].get("turns") and
                        not records[0].get("variants") and not records[0].get("outputs") and
                        not records[0].get("pins") and
                        records[0].get("requirements") == new_record()["requirements"] and
                        records[0].get("title") == "Новый диалог"):
                    return current
            try:
                with open(legacy_path, "rb") as stream:
                    size = os.path.getsize(legacy_path)
                    if size > MAX_RECORD_BYTES:
                        raise HistoryError("Файл миграции превышает лимит 32 MiB.")
                    raw = stream.read(MAX_RECORD_BYTES + 1)
                if len(raw) > MAX_RECORD_BYTES:
                    raise HistoryError("Файл миграции превышает лимит 32 MiB.")
                legacy = json.loads(raw.decode("utf-8"))
            except OSError:
                return current
            except (ValueError, UnicodeError) as error:
                self.history_error = "Не удалось импортировать историю: %s" % error
                return current
            if not isinstance(legacy, dict):
                return current
            record = new_record(legacy.get("title") or "Предыдущий диалог")
            for key in ("messages", "events", "requirements", "pins", "variants", "outputs"):
                if key in legacy:
                    record[key] = sanitize_value(copy.deepcopy(legacy[key]), config)
            record["turns"] = []
            record["status"] = "idle"
            if not record.get("pins") and record.get("requirements"):
                record["pins"] = copy.deepcopy(record["requirements"])
            try:
                record["cursor"] = int(legacy.get("cursor", 0) or 0)
            except (TypeError, ValueError):
                record["cursor"] = 0
            record["legacy_public_only"] = True
            result = {"schema": SCHEMA, "selected_id": record["id"], "records": [record]}
            try:
                self.save(result)
            except HistoryError as error:
                self.history_error = str(error)
            return copy.deepcopy(self._state or result)
