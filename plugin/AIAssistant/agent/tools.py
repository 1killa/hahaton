"""The shared policy boundary for agent, MCP and manual UI operations."""
import copy
import json
import math
import os
import threading
import time
import uuid


class ToolError(ValueError):
    pass


def validate(schema, value, path="arguments"):
    kind = schema.get("type")
    matches = {"object": isinstance(value, dict), "array": isinstance(value, list),
               "string": isinstance(value, str), "boolean": isinstance(value, bool),
               "integer": isinstance(value, int) and not isinstance(value, bool),
               "number": isinstance(value, (int, float)) and not isinstance(value, bool),
               "null": value is None}
    if isinstance(kind, list):
        if not any(matches.get(item, False) for item in kind):
            raise ToolError("Неверный тип: " + path)
    elif kind and not matches.get(kind, False):
        raise ToolError("Неверный тип: " + path)
    if isinstance(value, float) and not math.isfinite(value):
        raise ToolError("Требуется конечное число: " + path)
    if "enum" in schema and value not in schema["enum"]:
        raise ToolError("Недопустимое значение: " + path)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", -float("inf")) or value > schema.get("maximum", float("inf")):
            raise ToolError("Значение вне диапазона: " + path)
    if isinstance(value, str) and len(value) > schema.get("maxLength", 12000):
        raise ToolError("Слишком длинное значение: " + path)
    if isinstance(value, list):
        if len(value) > schema.get("maxItems", 200):
            raise ToolError("Слишком много элементов: " + path)
        if len(value) < schema.get("minItems", 0):
            raise ToolError("Недостаточно элементов: " + path)
        for item in value:
            validate(schema.get("items", {}), item, path + "[]")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise ToolError("Отсутствует " + path + "." + key)
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(props):
            raise ToolError("Неизвестные аргументы: " + ", ".join(sorted(set(value) - set(props))))
        for key, item in value.items():
            validate(props.get(key, {}), item, path + "." + key)


def tool_schema(name, description, properties, required=None):
    return {"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": properties, "required": required or [], "additionalProperties": False}}


class ToolRegistry:
    def __init__(self, adapter, output_dir, invoke=None):
        self.adapter = adapter
        self.output_dir = os.path.realpath(output_dir)
        self.invoke = invoke or (lambda fn, cancel=None: fn())
        self.operation_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._requirements = {"settings": [], "material_id": "", "preserve_dimensions": False, "notes": ""}
        self.variants = []
        self.outputs = []
        self.upload_resolver = None
        self.event = lambda *args, **kwargs: None
        self._schemas = self.invoke(lambda: adapter.list_tools())
        self._schemas = copy.deepcopy(self._schemas)
        for schema in self._schemas:
            if schema["name"] == "load_model":
                schema["inputSchema"] = tool_schema("", "", {"upload_id": {"type": "string"}}, ["upload_id"])["inputSchema"]
        self._schemas.extend([
            tool_schema("compare_variants", "Сравнить 2–3 набора настроек настоящим слайсингом. Восстановить исходные overrides в конце.", {
                "variants": {"type": "array", "minItems": 2, "maxItems": 3, "items": {"type": "object",
                    "properties": {"label": {"type": "string", "maxLength": 80}, "settings": {"type": "object"}},
                    "required": ["label", "settings"], "additionalProperties": False}}}, ["variants"]),
            tool_schema("apply_variant", "Применить настройки сохранённого варианта. Далее требуется новый слайсинг.", {
                "variant_id": {"type": "string"}}, ["variant_id"])])

    def list_tools(self):
        return copy.deepcopy(self._schemas)

    def requirements(self):
        with self._state_lock:
            return copy.deepcopy(self._requirements)

    def set_requirements(self, value):
        validate({"type": "object", "properties": {
            "settings": {"type": "array", "maxItems": 30, "items": {"type": "object", "properties": {
                "key": {"type": "string", "maxLength": 150}, "value": {},
                "scope": {"enum": ["global", "extruder"]}, "extruder": {"type": "integer", "minimum": 0, "maximum": 15}},
                "required": ["key", "value"], "additionalProperties": False}},
            "material_id": {"type": "string", "maxLength": 300}, "preserve_dimensions": {"type": "boolean"},
            "notes": {"type": "string", "maxLength": 4000}}, "additionalProperties": False}, value)
        updated = {"settings": [], "material_id": "", "preserve_dimensions": False, "notes": ""}
        updated.update(copy.deepcopy(value))
        for pin in updated["settings"]:
            pin.setdefault("scope", "global")
            pin.setdefault("extruder", 0)
        with self._state_lock:
            self._requirements = updated
        return self.requirements()

    def _guard(self, name, args):
        pins = self.requirements()
        if name == "set_print_mode":
            name, args = "set_setting", {"key": "printing_mode", "value": args["mode"]}
        if name == "set_setting":
            for pin in pins["settings"]:
                if (pin["key"] == args.get("key") and pin.get("scope", "global") == args.get("scope", "global")
                        and pin.get("extruder", 0) == args.get("extruder", 0) and pin["value"] != args.get("value")):
                    raise ToolError("Конфликт с закреплённым требованием: %s = %s" % (pin["key"], pin["value"]))
        if name == "transform_model" and args.get("action") == "scale" and pins["preserve_dimensions"]:
            if any(args.get(axis, 1) != 1 for axis in ("x", "y", "z")):
                raise ToolError("Закреплено сохранение размеров: масштабирование запрещено.")
        if name in ("select_profile", "select_material") and pins["settings"]:
            raise ToolError("Смена профиля/материала может изменить закреплённые настройки. Сначала согласуйте ограничения в панели требований.")
        if name == "select_profile" and pins["material_id"]:
            raise ToolError("При закреплённом материале смена профиля требует проверки пользователем.")
        if name == "select_material" and pins["material_id"] and args.get("material_id") != pins["material_id"]:
            raise ToolError("Материал закреплён пользователем.")
        if name == "load_model" and (pins["settings"] or pins["material_id"]):
            upload = self.upload_resolver(args["upload_id"]) if self.upload_resolver else {}
            if upload.get("name", "").lower().endswith(".3mf"):
                raise ToolError("3MF может заменить настройки проекта. Импорт при закреплённых условиях заблокирован.")

    def _assert_requirements(self, pins):
        for pin in pins["settings"]:
            current = self._adapter_call("get_settings", {"keys": [pin["key"]],
                "scope": pin.get("scope", "global"), "extruder": pin.get("extruder", 0)})
            if pin["key"] not in current.get("values", {}) or current["values"][pin["key"]] != pin["value"]:
                raise ToolError("Перед слайсингом/экспортом примените закреплённое условие: %s = %s" % (pin["key"], pin["value"]))
        if pins["material_id"]:
            current = self._adapter_call("get_scene", {})
            if current.get("material_id") != pins["material_id"]:
                raise ToolError("Текущий материал не совпадает с закреплённым ID материала.")

    def _snapshot(self, cancel=None):
        result = self.invoke(lambda: self.adapter.snapshot_settings(), cancel=cancel)
        if not isinstance(result, dict) or result.get("ok") is False:
            raise ToolError(result.get("error", "Не удалось сохранить исходные настройки.") if isinstance(result, dict) else "Некорректный snapshot.")
        return result

    def _restore(self, snapshot, cancel=None):
        result = self.invoke(lambda: self.adapter.restore_settings(snapshot), cancel=cancel)
        if not isinstance(result, dict) or result.get("ok") is False:
            raise ToolError(result.get("error", "Не удалось восстановить исходные настройки.") if isinstance(result, dict) else "Восстановление не подтверждено.")
        return result

    def _adapter_call(self, name, args, cancel=None):
        if cancel is not None and cancel.is_set():
            raise ToolError("Операция отменена.")
        if name in ("start_slice", "save_project", "export_gcode"):
            self._assert_requirements(self.requirements())
        args = copy.deepcopy(args)
        if name in ("save_project", "export_gcode") and isinstance(args.get("filename"), str):
            candidate = os.path.join(self.output_dir, args["filename"])
            if os.path.isfile(candidate):
                stem, extension = os.path.splitext(args["filename"])
                args["filename"] = stem + "_" + uuid.uuid4().hex[:8] + extension
        result = self.invoke(lambda: self.adapter.call(name, args), cancel=cancel)
        if isinstance(result, dict) and (result.get("ok") is False or result.get("code") in ("UNAVAILABLE", "ERROR")):
            raise ToolError(result.get("error") or result.get("message") or "Инструмент недоступен.")
        if name in ("save_project", "export_gcode"):
            result = self._record_output(result)
        return result

    def _record_output(self, result):
        if not isinstance(result, dict) or not result.get("path"):
            raise ToolError("Writer не подтвердил путь результата.")
        path = os.path.realpath(result["path"])
        if os.path.dirname(path) != self.output_dir or not os.path.isfile(path) or os.path.getsize(path) <= 0:
            raise ToolError("Нативный writer не создал непустой файл в папке результатов.")
        record = {"id": uuid.uuid4().hex, "filename": os.path.basename(path), "size_bytes": os.path.getsize(path),
                  "path": path}
        record["url"] = "/api/download/" + record["id"]
        with self._state_lock:
            self.outputs.append(record)
        public = dict(record)
        public.pop("path")
        value = dict(result)
        value["output"] = public
        return value

    def public_outputs(self):
        with self._state_lock:
            return [{key: val for key, val in item.items() if key != "path"} for item in self.outputs]

    def call(self, name, arguments, cancel=None):
        schema = next((item for item in self._schemas if item["name"] == name), None)
        if not schema:
            raise ToolError("Неизвестный инструмент: " + str(name))
        validate(schema["inputSchema"], arguments)
        self._guard(name, arguments)
        if name == "compare_variants":
            return self._compare(arguments["variants"], cancel)
        if name == "apply_variant":
            return self._apply_variant(arguments["variant_id"], cancel)
        args = copy.deepcopy(arguments)
        if name == "load_model":
            if self.upload_resolver is None:
                raise ToolError("Загрузите файл через интерфейс.")
            upload = self.upload_resolver(args["upload_id"])
            if upload.get("kind") != "model":
                raise ToolError("Требуется STL, OBJ или 3MF.")
            args = {"path": upload["path"]}
        return self._adapter_call(name, args, cancel)

    def _compare(self, variants, cancel):
        if getattr(self.adapter, "is_demo", False):
            raise ToolError("DEMO: сравнение требует настоящего слайсинга внутри STE Slicer.")
        for variant in variants:
            if not variant["settings"] or len(variant["settings"]) > 30:
                raise ToolError("Вариант должен содержать 1–30 настроек.")
            for key, value in variant["settings"].items():
                self._guard("set_setting", {"key": key, "value": value})
        baseline = self._snapshot(cancel)
        batch = uuid.uuid4().hex[:10]
        completed = []
        try:
            self._adapter_call("save_project", {"filename": "baseline_" + batch + ".3mf"}, cancel)
            for index, variant in enumerate(variants):
                self._restore(baseline, cancel)
                for key, value in variant["settings"].items():
                    self._adapter_call("set_setting", {"key": key, "value": value, "scope": "global"}, cancel)
                stem = "variant_%s_%s" % (batch, index + 1)
                project = self._adapter_call("save_project", {"filename": stem + ".3mf"}, cancel)
                start = self._adapter_call("start_slice", {}, cancel)
                generation = start.get("plugin_generation_id")
                deadline = time.monotonic() + 600
                while True:
                    status = self._adapter_call("get_slice_status", {}, cancel)
                    self.event("info", message="Вариант %s/%s: %s" % (index + 1, len(variants), status.get("state")))
                    if status.get("state") == "done":
                        if generation is None or status.get("plugin_generation_id") != generation:
                            raise ToolError("Слайсинг завершился для другой generation.")
                        break
                    if status.get("state") in ("error", "unavailable", "stale", "not_started") or time.monotonic() >= deadline:
                        raise ToolError("Вариант не получил новый успешный результат слайсинга.")
                    if cancel is not None:
                        cancel.wait(0.5)
                    else:
                        time.sleep(0.5)
                gcode = self._adapter_call("export_gcode", {"filename": stem + ".gcode"}, cancel)
                record = {"id": uuid.uuid4().hex, "label": variant["label"], "settings": variant["settings"],
                          "result": status.get("result", {"available": False}),
                          "outputs": [project["output"], gcode["output"]]}
                completed.append(record)
                self.event("info", message="Вариант сохранён: " + variant["label"])
        finally:
            self._restore(baseline)
            with self._state_lock:
                self.variants = completed
        return {"variants": completed, "baseline_restored": True,
                "notice": "Исходные overrides восстановлены. Применение варианта требует нового слайсинга."}

    def _apply_variant(self, variant_id, cancel):
        selected = next((item for item in self.variants if item["id"] == variant_id), None)
        if not selected:
            raise ToolError("Вариант не найден.")
        for key, value in selected["settings"].items():
            self._guard("set_setting", {"key": key, "value": value})
        before = self._snapshot(cancel)
        try:
            for key, value in selected["settings"].items():
                self._adapter_call("set_setting", {"key": key, "value": value, "scope": "global"}, cancel)
        except Exception:
            self._restore(before)
            raise
        return {"applied": selected["label"], "settings": selected["settings"], "requires_slice": True}
