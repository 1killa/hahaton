# -*- coding: utf-8 -*-
"""Small, dependency-free boundary around the STE Slicer API.

The web application deliberately talks to this module through ``call``.  The
module has no Qt or Uranium imports at import time: this is important both for
the demo mode and for the Python 3.5 runtime bundled with STE.  The real
adapter is expected to be constructed and called from STE's Qt main thread;
the registry above it is responsible for queuing calls there. Native file
writers run on the calling worker, with preparation and validation on Qt.
"""

from __future__ import absolute_import

import json
import io
import math
import os
import re
import time
import threading
import uuid


_UNSET = object()
_MODES = ("classic", "cylindrical", "cylindrical_full")
_SCOPES = ("global", "extruder")


def _error(message, code="UNAVAILABLE"):
    return {"ok": False, "error": str(message), "code": code}


def _success(**values):
    result = {"ok": True}
    result.update(values)
    return result


def _as_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s must be a number" % name)
    if not math.isfinite(float(value)):
        raise ValueError("%s must be finite" % name)
    return value


def _callable(obj, name):
    value = getattr(obj, name, None)
    return value if callable(value) else None


def _read_attr(obj, names, default=_UNSET):
    if obj is None:
        return default
    for name in names:
        try:
            value = getattr(obj, name)
        except Exception:
            continue
        if callable(value):
            try:
                return value()
            except TypeError:
                continue
            except Exception:
                continue
        return value
    return default


def _safe_json(value):
    """Convert host objects to the small JSON types exposed by the adapter."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    for method in ("getId", "getName", "getValue", "value"):
        candidate = getattr(value, method, None)
        try:
            candidate = candidate() if callable(candidate) else candidate
        except Exception:
            continue
        if candidate is not value and candidate is not None:
            return _safe_json(candidate)
    return str(value)


def _call_variants(obj, name, variants):
    method = _callable(obj, name)
    if method is None:
        return _UNSET
    last = None
    for args, kwargs in variants:
        try:
            return method(*args, **kwargs)
        except TypeError as exc:
            last = exc
            continue
    if last is not None:
        raise last
    return _UNSET


def _object_id(node):
    value = _read_attr(node, ("getId", "id", "object_id"), None)
    if value is None:
        value = id(node)
    return str(value)


def _object_name(node):
    value = _read_attr(node, ("getName", "name"), "Object")
    return str(value)


def _vector(value, fallback=(0.0, 0.0, 0.0)):
    if value is None:
        return [float(x) for x in fallback]
    if isinstance(value, dict):
        return [float(value.get(key, fallback[index])) for index, key in enumerate(("x", "y", "z"))]
    try:
        return [float(value[index]) for index in range(3)]
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    output = []
    for axis, default in zip(("x", "y", "z"), fallback):
        output.append(float(_read_attr(value, ("get" + axis.upper(), axis), default)))
    return output


def _bounds_from_node(node):
    box = _read_attr(node, ("getBoundingBox", "getWorldBoundingBox", "bounding_box", "bounds"), None)
    if box is not None:
        minimum = _read_attr(box, ("min", "minimum", "getMin"), None)
        maximum = _read_attr(box, ("max", "maximum", "getMax"), None)
        if minimum is not None and maximum is not None:
            low = _vector(minimum)
            high = _vector(maximum)
            return {"x": abs(high[0] - low[0]), "y": abs(high[1] - low[1]), "z": abs(high[2] - low[2])}
        try:
            size = _read_attr(box, ("size", "getSize"), None)
            if size is not None:
                vector = _vector(size)
                return {"x": abs(vector[0]), "y": abs(vector[1]), "z": abs(vector[2])}
        except Exception:
            pass
    mesh = _read_attr(node, ("getMeshData", "mesh_data"), None)
    if mesh is not None:
        box = _read_attr(mesh, ("getExtents", "getBoundingBox"), None)
        if box is not None and box is not mesh:
            return _bounds_from_node(type("BoundsNode", (object,), {"getBoundingBox": lambda self: box})())
    raw = _read_attr(node, ("bounds_mm",), None)
    if isinstance(raw, dict):
        return {axis: float(raw.get(axis, 0.0)) for axis in ("x", "y", "z")}
    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        return {axis: float(raw[index]) for index, axis in enumerate(("x", "y", "z"))}
    return {"x": 0.0, "y": 0.0, "z": 0.0}


def _safe_filename(filename):
    if not isinstance(filename, str) or not filename:
        raise ValueError("filename must be a non-empty string")
    if filename != os.path.basename(filename):
        raise ValueError("filename must be a name inside output_dir")
    if os.path.isabs(filename) or filename in (".", "..") or ".." in filename.split(os.sep):
        raise ValueError("filename must be a name inside output_dir")
    if re.search(r"[\\/:\x00]", filename):
        raise ValueError("filename must be a name inside output_dir")
    return filename


def _tool(name, description, properties=None, required=None):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
            "additionalProperties": False,
        },
    }


_TOOLS = [
    _tool("get_scene", "Return the current build plate scene."),
    _tool("get_settings", "Read validated global or extruder settings.", {
        "keys": {"type": "array", "items": {"type": "string"}},
        "scope": {"type": "string", "enum": list(_SCOPES)},
        "extruder": {"type": "integer", "minimum": 0},
    }, ["keys", "scope"]),
    _tool("set_setting", "Set one validated setting.", {
        "key": {"type": "string"}, "value": {},
        "scope": {"type": "string", "enum": list(_SCOPES)},
        "extruder": {"type": "integer", "minimum": 0},
    }, ["key", "value", "scope"]),
    _tool("set_print_mode", "Set classic or cylindrical print mode.", {
        "mode": {"type": "string", "enum": list(_MODES)},
    }, ["mode"]),
    _tool("list_profiles", "List quality profiles and materials."),
    _tool("select_profile", "Select a quality profile.", {"quality_type": {"type": "string"}}, ["quality_type"]),
    _tool("select_material", "Select a material for an extruder.", {
        "material_id": {"type": "string"}, "extruder": {"type": "integer", "minimum": 0},
    }, ["material_id"]),
    _tool("create_profile", "Create profile changes from a named base.", {"name": {"type": "string"}}, ["name"]),
    _tool("save_profile", "Save the currently selected profile."),
    _tool("load_model", "Queue an internal, root-validated model path for import.", {"path": {"type": "string"}}, ["path"]),
    _tool("transform_model", "Transform a scene model in STE coordinates (Y is up).", {
        "object_id": {"type": "string"},
        "action": {"type": "string", "enum": ["translate", "rotate", "scale"]},
        "x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"},
    }, ["object_id", "action", "x", "y", "z"]),
    _tool("arrange_models", "Ask the host to arrange models."),
    _tool("remove_model", "Remove one model.", {"object_id": {"type": "string"}}, ["object_id"]),
    _tool("start_slice", "Start a new native slice generation."),
    _tool("get_slice_status", "Return the current native slice state."),
    _tool("save_project", "Write a native 3MF project in output_dir.", {"filename": {"type": "string"}}, ["filename"]),
    _tool("export_gcode", "Write G-code for a newly completed native generation.", {"filename": {"type": "string"}}, ["filename"]),
    _tool("check_5d", "Report bounded five-axis facts and assumptions."),
]


class _AdapterBase(object):
    """Shared validation and generation bookkeeping."""

    def __init__(self, output_dir):
        # ``os.fspath`` is Python 3.6+, while the installed STE runtime is
        # Python 3.5.  The public contract supplies a normal path string.
        self.output_dir = os.path.realpath(os.path.abspath(str(output_dir)))
        self._generation = 0
        self._active_generation = None
        self._slice_state = "not_started"
        self._slice_progress = 0.0
        self._slice_result = {"available": False}
        self._slice_error = None
        self._slice_evidence = []
        self._slice_mutation = 0

    def list_tools(self):
        # Return copies so callers cannot alter the process-wide schemas.
        # ToolRegistry and the MCP server both consume the conventional list
        # returned by an adapter; the server adds its own ``tools`` envelope.
        return json.loads(json.dumps(_TOOLS))

    def snapshot_settings(self):
        return {"global": {"present": {}, "values": {}}, "extruders": []}

    def restore_settings(self, snapshot):
        if not isinstance(snapshot, dict):
            return _error("snapshot must be an object", "INVALID_ARGUMENTS")
        self._invalidate_slice()
        return _success()

    def _invalidate_slice(self):
        self._slice_mutation += 1
        if self._active_generation is not None or self._slice_state in ("running", "done", "error"):
            self._slice_state = "stale"
        self._slice_progress = 0.0
        self._slice_result = {"available": False}
        self._slice_error = None
        self._active_generation = None

    def _begin_generation(self):
        self._generation += 1
        self._active_generation = self._generation
        self._slice_state = "running"
        self._slice_progress = 0.0
        self._slice_result = {"available": False}
        self._slice_error = None
        self._slice_evidence = []
        return self._generation

    def _status(self):
        result = {
            "state": self._slice_state,
            "progress": max(0.0, min(1.0, float(self._slice_progress))),
            "result": dict(self._slice_result),
        }
        if self._active_generation is not None:
            result["plugin_generation_id"] = self._active_generation
        if self._slice_evidence:
            result["evidence"] = list(self._slice_evidence)
        if self._slice_error:
            result["error"] = self._slice_error
        return result

    def _validate_arguments(self, name, arguments):
        if not isinstance(arguments, dict):
            return _error("arguments must be an object", "INVALID_ARGUMENTS")
        known = None
        for schema in _TOOLS:
            if schema["name"] == name:
                known = schema["inputSchema"]
                break
        if known is None:
            return _error("unknown tool: %s" % name, "INVALID_ARGUMENTS")
        extra = set(arguments) - set(known.get("properties", {}))
        if extra:
            return _error("unknown argument(s): %s" % ", ".join(sorted(extra)), "INVALID_ARGUMENTS")
        missing = set(known.get("required", [])) - set(arguments)
        if missing:
            return _error("missing argument(s): %s" % ", ".join(sorted(missing)), "INVALID_ARGUMENTS")
        return None

    def call(self, name, arguments):
        invalid = self._validate_arguments(name, arguments)
        if invalid:
            return invalid
        try:
            method = getattr(self, "_tool_" + name)
            result = method(arguments)
            return _safe_json(result)
        except (ValueError, TypeError, KeyError) as exc:
            return _error(exc, "INVALID_ARGUMENTS")
        except Exception as exc:
            return _error(exc, "UNAVAILABLE")

    def _tool_get_slice_status(self, arguments):
        return self._status()

    def _tool_start_slice(self, arguments):
        return _error("native slicing is unavailable", "UNAVAILABLE")

    def _tool_save_project(self, arguments):
        return _error("native project export is unavailable", "UNAVAILABLE")

    def _tool_export_gcode(self, arguments):
        return _error("native G-code export is unavailable", "UNAVAILABLE")


class DemoAdapter(_AdapterBase):
    """Safe offline adapter with explicit example geometry.

    The demo is useful for exercising the SPA and settings workflow, but it
    never claims to have sliced a model and never writes a fake 3MF/G-code
    file.
    """

    is_demo = True

    def __init__(self, output_dir):
        _AdapterBase.__init__(self, output_dir)
        self._settings = {
            "global": {
                "printing_mode": "classic",
                "layer_height": 0.2,
                "infill_sparse_density": 20,
                "machine_five_axis": False,
                "cylindrical_mode_base_diameter": 20.0,
            },
            "extruders": [{"material_id": "demo-pla", "nozzle_size": 0.4}],
        }
        self._profiles = [{"id": "demo-quality", "name": "Demo quality", "quality_type": "demo"}]
        self._materials = [{"id": "demo-pla", "name": "Demo PLA"}]
        self._slice_state = "unavailable"
        self._objects = [{
            "id": "demo-cube", "name": "Demo cube",
            "bounds_mm": {"x": 20.0, "y": 20.0, "z": 20.0},
            "position_mm": [0.0, 10.0, 0.0], "scale": [1.0, 1.0, 1.0],
        }]

    def snapshot_settings(self):
        return {
            "global": {"present": dict((key, True) for key in self._settings["global"]),
                        "values": dict(self._settings["global"])},
            "extruders": [{"present": dict((key, True) for key in ext), "values": dict(ext)}
                          for ext in self._settings["extruders"]],
        }

    def restore_settings(self, snapshot):
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("global"), dict):
            return _error("invalid settings snapshot", "INVALID_ARGUMENTS")
        try:
            values = snapshot["global"].get("values", {})
            if not isinstance(values, dict):
                raise ValueError("snapshot values must be an object")
            self._settings["global"] = dict(values)
            ext_snapshots = snapshot.get("extruders", [])
            if not isinstance(ext_snapshots, list):
                raise ValueError("snapshot extruders must be an array")
            self._settings["extruders"] = [dict(item.get("values", {})) for item in ext_snapshots]
        except (AttributeError, TypeError, ValueError) as exc:
            return _error(exc, "INVALID_ARGUMENTS")
        self._invalidate_slice()
        return _success()

    def _tool_get_scene(self, arguments):
        return {"objects": [dict(item) for item in self._objects],
                "mode": self._settings["global"].get("printing_mode", "classic"),
                "material_id": self._settings["extruders"][0].get("material_id", "")}

    def _tool_get_settings(self, arguments):
        scope = arguments["scope"]
        index = int(arguments.get("extruder", 0))
        if scope == "global":
            values = self._settings["global"]
        elif scope == "extruder":
            if index < 0 or index >= len(self._settings["extruders"]):
                raise ValueError("unknown extruder")
            values = self._settings["extruders"][index]
        else:
            raise ValueError("invalid scope")
        return {"values": {key: values[key] for key in arguments["keys"] if key in values}, "scope": scope}

    def _tool_set_setting(self, arguments):
        scope = arguments["scope"]
        if scope not in _SCOPES:
            raise ValueError("invalid scope")
        if arguments["key"] == "printing_mode":
            mode = arguments["value"]
            if mode not in _MODES:
                raise ValueError("printing_mode must be classic, cylindrical, or cylindrical_full")
            if mode != "classic" and not self._settings["global"].get("machine_five_axis", False):
                return _error("cylindrical modes require a five-axis machine", "INVALID_ARGUMENTS")
        if arguments["key"] == "cylindrical_mode_base_diameter":
            diameter = _as_number(arguments["value"], "cylindrical_mode_base_diameter")
            if diameter < 4.0:
                raise ValueError("cylindrical_mode_base_diameter must be at least 4 mm")
        index = int(arguments.get("extruder", 0))
        if scope == "extruder":
            while len(self._settings["extruders"]) <= index:
                self._settings["extruders"].append({})
            target = self._settings["extruders"][index]
        else:
            target = self._settings["global"]
        target[arguments["key"]] = arguments["value"]
        self._invalidate_slice()
        return _success(key=arguments["key"], value=arguments["value"], scope=scope)

    def _tool_set_print_mode(self, arguments):
        mode = arguments["mode"]
        if mode not in _MODES:
            raise ValueError("unsupported print mode")
        if mode != "classic" and not self._settings["global"].get("machine_five_axis", False):
            return _error("cylindrical modes require a five-axis machine", "INVALID_ARGUMENTS")
        self._settings["global"]["printing_mode"] = mode
        self._invalidate_slice()
        return _success(mode=mode)

    def _tool_list_profiles(self, arguments):
        return {"profiles": list(self._profiles), "materials": list(self._materials)}

    def _tool_select_profile(self, arguments):
        selected = arguments["quality_type"]
        if not any(item["quality_type"] == selected for item in self._profiles):
            return _error("unknown quality profile", "INVALID_ARGUMENTS")
        self._settings["global"]["quality_type"] = selected
        self._invalidate_slice()
        return _success(quality_type=selected)

    def _tool_select_material(self, arguments):
        material = arguments["material_id"]
        if not any(item["id"] == material for item in self._materials):
            return _error("unknown material", "INVALID_ARGUMENTS")
        index = int(arguments.get("extruder", 0))
        while len(self._settings["extruders"]) <= index:
            self._settings["extruders"].append({})
        self._settings["extruders"][index]["material_id"] = material
        self._invalidate_slice()
        return _success(material_id=material, extruder=index)

    def _tool_create_profile(self, arguments):
        name = arguments["name"]
        if not name:
            raise ValueError("profile name must not be empty")
        profile = {"id": "demo-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "profile",
                   "name": name, "quality_type": name}
        self._profiles.append(profile)
        return {"profile": profile}

    def _tool_save_profile(self, arguments):
        return _success(saved=False, reason="demo profile is memory-only")

    def _tool_load_model(self, arguments):
        path = arguments["path"]
        if not isinstance(path, str) or not path:
            raise ValueError("path must be a string")
        self._invalidate_slice()
        return {"state": "pending", "path": path}

    def _tool_transform_model(self, arguments):
        target = None
        for item in self._objects:
            if item["id"] == arguments["object_id"]:
                target = item
                break
        if target is None:
            return _error("unknown object", "INVALID_ARGUMENTS")
        vector = [float(arguments[axis]) for axis in ("x", "y", "z")]
        action = arguments["action"]
        if action == "translate":
            target["position_mm"] = [a + b for a, b in zip(target["position_mm"], vector)]
        elif action == "rotate":
            target["rotation_deg"] = [a + b for a, b in zip(target.get("rotation_deg", [0.0, 0.0, 0.0]), vector)]
        elif action == "scale":
            if any(value <= 0 for value in vector):
                raise ValueError("scale values must be positive")
            target["scale"] = [a * b for a, b in zip(target["scale"], vector)]
            target["bounds_mm"] = dict((axis, target["bounds_mm"][axis] * vector[index])
                                         for index, axis in enumerate(("x", "y", "z")))
        else:
            raise ValueError("unsupported transform")
        self._invalidate_slice()
        return _success(object_id=arguments["object_id"], action=action,
                        coordinates="STE: Y is up; X/Z are the build-plate plane")

    def _tool_arrange_models(self, arguments):
        # The explicit demo scene has one object.  Keep its stable position and
        # expose that arrange is a no-op rather than pretending to call STE.
        self._invalidate_slice()
        return _success(arranged=False, reason="demo scene has no native arrange operation")

    def _tool_remove_model(self, arguments):
        before = len(self._objects)
        self._objects = [item for item in self._objects if item["id"] != arguments["object_id"]]
        if len(self._objects) == before:
            return _error("unknown object", "INVALID_ARGUMENTS")
        self._invalidate_slice()
        return _success(object_id=arguments["object_id"])

    def _tool_check_5d(self, arguments):
        return {"facts": {"mode": self._settings["global"].get("printing_mode", "classic"),
                           "machine_five_axis": bool(self._settings["global"].get("machine_five_axis", False)),
                           "object_count": len(self._objects)},
                "warnings": ["Demo geometry is illustrative and was not checked by a native slicer."],
                "assumptions": ["Demo cube dimensions are example values in millimetres."],
                "verified_geometry": False}


class SlicerAdapter(_AdapterBase):
    """Adapter for the installed STE Slicer runtime.

    All host imports and object discovery are lazy.  This keeps importing the
    plugin safe outside STE, while failures inside a host are returned as
    structured ``UNAVAILABLE`` results by ``call``.
    """

    is_demo = False

    def __init__(self, app, output_dir):
        _AdapterBase.__init__(self, output_dir)
        self.app = app
        self._qt = None
        self._operations = None
        self._math_types = None
        self._duration_format = None
        self._backend = None
        self._connected_backend = None
        self._signal_connections = []
        self._nodes_by_id = {}
        self._profile_changes = None
        self._backend_states = None
        self._slice_plate = None
        self._slice_started = False
        # Native STE 7.10 does not expose slicingStarted on CuraEngineBackend.
        # A Done state is emitted before the backend finishes populating the
        # selected plate's G-code, so keep Done as intent and finalize from a
        # later main-thread status/export call (or after synchronous forceSlice
        # returns).
        self._slice_done_seen = False
        self._slice_duration_plate = None
        self._slice_gcode_before = None
        self._slice_gcode_invalidated = False
        # These values are captured only after native Done evidence.  Export
        # re-checks them because a user can change the scene, stack, or active
        # plate directly in the STE UI after the plugin generation completed.
        self._slice_completed_plate = _UNSET
        self._slice_completed_gcode = None
        self._slice_completed_scene = None
        self._slice_completed_settings = None
        self._slice_started_at = None

    def _clear_completed_slice(self):
        self._slice_completed_plate = _UNSET
        self._slice_completed_gcode = None
        self._slice_completed_scene = None
        self._slice_completed_settings = None

    def _invalidate_slice(self):
        _AdapterBase._invalidate_slice(self)
        self._clear_completed_slice()
        self._slice_started_at = None
        self._slice_started = False
        self._slice_done_seen = False

    def _begin_generation(self):
        self._clear_completed_slice()
        generation = _AdapterBase._begin_generation(self)
        self._slice_started = False
        self._slice_done_seen = False
        return generation

    def _import_host(self):
        if self._qt is None:
            try:
                from PyQt5.QtCore import QUrl
            except ImportError:
                try:
                    from UM.Qt.QtCore import QUrl
                except ImportError:
                    QUrl = None
            self._qt = QUrl
        if self._operations is None:
            operations = {}
            try:
                from UM.Operations.TranslateOperation import TranslateOperation
                operations["translate"] = TranslateOperation
            except ImportError:
                pass
            try:
                from UM.Operations.RotateOperation import RotateOperation
                operations["rotate"] = RotateOperation
            except ImportError:
                pass
            try:
                from UM.Operations.ScaleOperation import ScaleOperation
                operations["scale"] = ScaleOperation
            except ImportError:
                pass
            try:
                from UM.Operations.RemoveSceneNodeOperation import RemoveSceneNodeOperation
                operations["remove"] = RemoveSceneNodeOperation
            except ImportError:
                pass
            self._operations = operations
        if self._math_types is None:
            math_types = {}
            try:
                from UM.Math.Vector import Vector
                math_types["Vector"] = Vector
            except ImportError:
                pass
            try:
                from UM.Math.Quaternion import Quaternion
                math_types["Quaternion"] = Quaternion
            except ImportError:
                pass
            self._math_types = math_types
        if self._backend_states is None:
            try:
                from UM.Backend.Backend import BackendState
                self._backend_states = BackendState
            except ImportError:
                self._backend_states = False
        if self._duration_format is None:
            try:
                from UM.Qt.Duration import DurationFormat
                self._duration_format = DurationFormat
            except ImportError:
                self._duration_format = False

    def _get_backend(self):
        backend = _call_variants(self.app, "getBackend", [((), {})])
        if backend is _UNSET or backend is None:
            manager = _read_attr(self.app, ("getBackendManager", "backend_manager"), None)
            backend = _call_variants(manager, "getCurrentBackend", [((), {})]) if manager is not None else _UNSET
        if backend is _UNSET or backend is None:
            raise RuntimeError("Could not load the backend plugin")
        return backend

    def _get_manager(self):
        manager = _read_attr(self.app, ("getMachineManager", "machine_manager"), None)
        if manager is None:
            manager = _read_attr(self.app, ("getGlobalContainerStack", "global_container_stack"), None)
        return manager

    def _get_stack(self, scope="global", extruder=0):
        # SteSlicerApplication exposes this directly.  Prefer it over the
        # machine manager's private stack path; the latter is retained only as
        # a compatibility path for the lightweight host test doubles.
        if scope == "global":
            direct = _read_attr(self.app, ("getGlobalContainerStack", "global_container_stack"), None)
            if direct is not None:
                return direct
        manager = self._get_manager()
        stack = manager
        if scope == "global":
            candidate = _read_attr(manager, ("getGlobalContainerStack", "global_container_stack", "_global_container_stack"), None)
            if candidate is not None:
                stack = candidate
        else:
            global_stack = self._get_stack("global", 0)
            extruders = _read_attr(global_stack, ("extruders", "getExtruders"), None)
            if extruders is not None:
                try:
                    if isinstance(extruders, dict):
                        if int(extruder) in extruders:
                            stack = extruders[int(extruder)]
                        elif str(int(extruder)) in extruders:
                            stack = extruders[str(int(extruder))]
                        else:
                            ordered = self._extruder_stacks(global_stack)
                            stack = ordered[int(extruder)]
                    else:
                        stack = extruders[int(extruder)]
                except (IndexError, KeyError, TypeError):
                    raise ValueError("unknown extruder")
            else:
                candidate = _call_variants(global_stack, "getExtruderStack", [((int(extruder),), {}), ((), {})])
                if candidate is not _UNSET and candidate is not None:
                    stack = candidate
                else:
                    raise ValueError("unknown extruder")
        if stack is None:
            raise RuntimeError("STE settings stack unavailable")
        return stack

    def _extruder_stacks(self, global_stack):
        extruders = _read_attr(global_stack, ("extruders", "getExtruders"), None)
        if isinstance(extruders, dict):
            def position(item):
                key, stack = item
                metadata = getattr(stack, "getMetaDataEntry", None)
                if callable(metadata):
                    try:
                        return int(metadata("position"))
                    except (TypeError, ValueError, KeyError):
                        pass
                try:
                    return int(key)
                except (TypeError, ValueError):
                    return 0
            return [stack for _, stack in sorted(extruders.items(), key=position)]
        if extruders is not None:
            try:
                return list(extruders)
            except TypeError:
                pass
        return []

    def _definition(self, key, scope="global", extruder=0):
        stack = self._get_stack(scope, extruder)
        for name in ("getSettingDefinition", "getDefinition", "getPropertyDefinition"):
            definition = _call_variants(stack, name, [((key,), {}), ((), {})])
            if definition is not _UNSET and definition is not None:
                return definition
        definitions = _read_attr(stack, ("definitions", "setting_definitions", "_definitions"), None)
        if isinstance(definitions, dict) and key in definitions:
            return definitions[key]
        definition = _read_attr(stack, ("definition",), None)
        if definition is not None:
            definitions = _read_attr(definition, ("settings", "definitions"), None)
            if isinstance(definitions, dict) and key in definitions:
                return definitions[key]
        return None

    def _get_property(self, stack, key):
        for name in ("getProperty", "getSettingValue", "getValue"):
            method = _callable(stack, name)
            if method is None:
                continue
            for args in ((key, "value"), (key,), (key, "value", None)):
                try:
                    value = method(*args)
                    if value is not None:
                        return value
                except (KeyError, TypeError, AttributeError):
                    continue
        values = _read_attr(stack, ("values", "settings", "_values"), None)
        if isinstance(values, dict) and key in values:
            return values[key]
        return _UNSET

    def _set_property(self, stack, key, value):
        changes = self._user_changes(stack)
        setter = _callable(changes, "setProperty")
        if setter is not None:
            setter(key, "value", value)
            return
        for name in ("setProperty", "setSettingValue", "setValue"):
            method = _callable(stack, name)
            if method is None:
                continue
            # ContainerStack inherits QObject.setProperty(name, value), which
            # writes a Qt dynamic property, NOT a slicer setting. Never use it.
            variants = ((key, "value", value),) if name == "setProperty" else ((key, value),)
            for args in variants:
                try:
                    method(*args)
                    return
                except TypeError:
                    continue
        values = _read_attr(stack, ("values", "settings", "_values"), None)
        if isinstance(values, dict):
            values[key] = value
            return
        raise RuntimeError("settings stack cannot set %s" % key)

    def prepare_setting(self, arguments):
        """Normalize LLM scalar strings using the actual native definition."""
        args = dict(arguments)
        definition = self._definition(args["key"], args["scope"], args.get("extruder", 0))
        kind = self._definition_value(definition, ("type", "value_type", "setting_type"), None)
        value = args["value"]
        if isinstance(value, str):
            text = value.strip()
            if kind in (bool, "bool", "boolean"):
                if len(text) >= 2 and text[0] == text[-1] == '"':
                    text = json.loads(text)
                boolean = {"true": True, "false": False, "yes": True, "no": False, "1": True, "0": False}
                if isinstance(text, str) and text.lower() in boolean:
                    value = boolean[text.lower()]
            elif kind in (int, "int", "integer", float, "float", "double", "number"):
                try:
                    number = float(text)
                    if math.isfinite(number):
                        if kind in (int, "int", "integer"):
                            if number.is_integer():
                                value = int(number)
                        else:
                            value = number
                except (ValueError, OverflowError):
                    pass
        args["value"] = value
        self._validate_setting(args["key"], value, args["scope"], args.get("extruder", 0))
        return args

    def _definition_value(self, definition, names, default=_UNSET):
        if definition is None:
            return default
        if isinstance(definition, dict):
            for key in names:
                if key in definition:
                    return definition[key]
        for name in names:
            value = _read_attr(definition, (name,), _UNSET)
            if value is not _UNSET:
                return value
        return default

    def _numeric_bound(self, value):
        """Return a finite numeric bound, treating absent bounds as unbounded.

        STE's SettingDefinition uses ``minimum_value``/``maximum_value`` and
        may expose those values as numeric strings.  Dynamic definitions can
        also return None, which means that side has no bound.
        """
        if value is _UNSET or value is None or isinstance(value, bool):
            return _UNSET
        if isinstance(value, (int, float)):
            try:
                if math.isfinite(float(value)):
                    return value
            except (TypeError, ValueError):
                return _UNSET
            return _UNSET
        if isinstance(value, str):
            try:
                parsed = float(value)
                if math.isfinite(parsed):
                    return parsed
            except (TypeError, ValueError):
                pass
        return _UNSET

    def _validate_setting(self, key, value, scope, extruder):
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        if key == "printing_mode" and value not in _MODES:
            raise ValueError("printing_mode must be classic, cylindrical, or cylindrical_full")
        definition = self._definition(key, scope, extruder)
        current = self._get_property(self._get_stack(scope, extruder), key)
        if definition is None and current is _UNSET:
            raise ValueError("unknown setting: %s" % key)
        if value is _UNSET:
            return definition
        kind = self._definition_value(definition, ("type", "value_type", "setting_type"), None)
        type_name = str(kind).lower() if kind is not None else ""
        if kind in (int, "int", "integer") or "int" in type_name and "print" not in type_name:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("%s must be an integer" % key)
        elif kind in (float, "float", "double", "number") or any(item in type_name for item in ("float", "double", "number")):
            _as_number(value, key)
        elif kind in (bool, "bool", "boolean") or "bool" in type_name:
            if not isinstance(value, bool):
                raise ValueError("%s must be boolean" % key)
        elif kind in (str, "str", "string", "enum") or "string" in type_name or "enum" in type_name:
            if not isinstance(value, str):
                raise ValueError("%s must be a string" % key)
        choices = self._definition_value(definition, ("values", "options", "choices"), _UNSET)
        if choices is not _UNSET and isinstance(choices, (list, tuple, set)) and value not in choices:
            raise ValueError("%s has an unsupported value" % key)
        minimum = self._numeric_bound(
            self._definition_value(definition, ("minimum", "min", "minimum_value"), _UNSET))
        maximum = self._numeric_bound(
            self._definition_value(definition, ("maximum", "max", "maximum_value"), _UNSET))
        if minimum is not _UNSET and isinstance(value, (int, float)) and value < minimum:
            raise ValueError("%s is below its minimum" % key)
        if maximum is not _UNSET and isinstance(value, (int, float)) and value > maximum:
            raise ValueError("%s is above its maximum" % key)
        if key == "cylindrical_mode_base_diameter" and isinstance(value, (int, float)):
            actual_min = 4.0
            if minimum is not _UNSET:
                actual_min = max(actual_min, float(minimum))
            if value < actual_min:
                raise ValueError("cylindrical_mode_base_diameter must be at least %.g mm" % actual_min)
        return definition

    def _user_changes(self, stack):
        for name in ("getUserChanges",):
            value = _call_variants(stack, name, [((), {})])
            if value is not _UNSET and value is not None:
                return value
        return _read_attr(stack, ("userChanges", "user_changes", "_user_changes"), None)

    def _mapping_snapshot(self, stack):
        changes = self._user_changes(stack)
        if isinstance(changes, dict):
            return {str(key): _safe_json(value) for key, value in changes.items()}
        if changes is not None:
            keys = _call_variants(changes, "getAllKeys", [((), {})])
            if keys is not _UNSET:
                result = {}
                for key in keys:
                    value = _call_variants(changes, "getProperty", [((key, "value"), {}), ((key,), {})])
                    if value is _UNSET:
                        raise RuntimeError("userChanges value API unavailable")
                    result[str(key)] = _safe_json(value)
                return result
            items = _call_variants(changes, "items", [((), {})])
            if items is not _UNSET:
                try:
                    return {str(key): _safe_json(value) for key, value in items}
                except (TypeError, ValueError):
                    pass
        raise RuntimeError("userChanges InstanceContainer API unavailable")

    def snapshot_settings(self):
        try:
            global_stack = self._get_stack("global", 0)
            result = {"global": {"present": {}, "values": {}}, "extruders": []}
            values = self._mapping_snapshot(global_stack)
            result["global"] = {"present": dict((key, True) for key in values), "values": values}
            ext_stacks = self._extruder_stacks(global_stack)
            if not ext_stacks:
                try:
                    ext_stacks = [self._get_stack("extruder", 0)]
                except Exception:
                    ext_stacks = []
            for index, stack in enumerate(ext_stacks):
                try:
                    ext_values = self._mapping_snapshot(stack)
                except Exception:
                    raise RuntimeError("extruder userChanges API unavailable")
                result["extruders"].append({"present": dict((key, True) for key in ext_values),
                                             "values": ext_values})
            return result
        except Exception as exc:
            return _error(exc, "UNAVAILABLE")

    def _restore_mapping(self, stack, values, present):
        changes = self._user_changes(stack)
        if isinstance(changes, dict):
            for key in list(changes):
                if key not in present:
                    del changes[key]
            for key, value in values.items():
                if key in present:
                    # Update the stack through its public setting API.  A
                    # direct mapping write would silently restore only the
                    # bookkeeping object while leaving STE's actual stack
                    # unchanged.
                    self._set_property(stack, key, value)
            return
        keys = _call_variants(changes, "getAllKeys", [((), {})])
        remove = _callable(changes, "removeInstance")
        set_property = _callable(changes, "setProperty")
        if keys is _UNSET or remove is None or set_property is None:
            raise RuntimeError("userChanges InstanceContainer restore API unavailable")
        for key in list(keys):
            if not present.get(str(key), False):
                remove(key)
        for key, value in values.items():
            if present.get(str(key), False):
                set_property(key, "value", value)

    def restore_settings(self, snapshot):
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("global"), dict):
            return _error("invalid settings snapshot", "INVALID_ARGUMENTS")
        # Never restore overrides while native slicing is still running.  A
        # late backend callback would otherwise be able to publish a Done
        # generation for settings that have already been restored.
        if self._slice_state == "running":
            backend = self._backend
            if backend is None:
                try:
                    backend = self._get_backend()
                except Exception as exc:
                    return _error("cannot stop native slicing: %s" % exc, "UNAVAILABLE")
            stop = _callable(backend, "stopSlicing")
            if stop is None:
                return _error("native stopSlicing API unavailable", "UNAVAILABLE")
            try:
                response = stop()
            except Exception as exc:
                return _error("cannot stop native slicing: %s" % exc, "UNAVAILABLE")
            if response is False:
                return _error("native stopSlicing returned false", "UNAVAILABLE")
            # Invalidate immediately after the confirmed stop, before any
            # settings mutation, so queued signals from the old generation
            # cannot be accepted.
            self._invalidate_slice()
        try:
            item = snapshot["global"]
            values = item.get("values", {})
            present = item.get("present", dict((key, True) for key in values))
            if not isinstance(values, dict) or not isinstance(present, dict):
                raise ValueError("invalid global snapshot")
            targets = [(self._get_stack("global", 0), values, present)]
            parsed_extruders = []
            for index, item in enumerate(snapshot.get("extruders", [])):
                if not isinstance(item, dict):
                    raise ValueError("invalid extruder snapshot")
                values = item.get("values", {})
                present = item.get("present", dict((key, True) for key in values))
                target = self._get_stack("extruder", index)
                targets.append((target, values, present))
            # Validate every restore API before applying the first mutation.
            for target, values, present in targets:
                changes = self._user_changes(target)
                if not isinstance(changes, dict):
                    if (_call_variants(changes, "getAllKeys", [((), {})]) is _UNSET or
                            _callable(changes, "removeInstance") is None or
                            _callable(changes, "setProperty") is None):
                        raise RuntimeError("userChanges InstanceContainer restore API unavailable")
            for target, values, present in targets:
                self._restore_mapping(target, values, present)
        except (ValueError, TypeError, KeyError) as exc:
            return _error(exc, "INVALID_ARGUMENTS")
        except Exception as exc:
            return _error(exc, "UNAVAILABLE")
        self._invalidate_slice()
        return _success()

    def _scene_root(self):
        controller = _read_attr(self.app, ("getController", "controller"), None)
        if controller is None:
            raise RuntimeError("scene controller unavailable")
        scene = _read_attr(controller, ("getScene", "scene"), None)
        if scene is None:
            raise RuntimeError("scene unavailable")
        root = _read_attr(scene, ("getRoot", "root"), scene)
        return scene, root

    def _scene_nodes(self):
        scene, root = self._scene_root()
        nodes = _call_variants(root, "getAllChildren", [((), {})])
        if nodes is _UNSET or nodes is None:
            nodes = _read_attr(root, ("children", "nodes"), [])
        try:
            return [node for node in nodes if self._is_model_node(node)]
        except TypeError:
            return []

    def _is_model_node(self, node):
        # STE's scene also contains the build volume, cameras (including a
        # new 'snapshot' Camera for every native export), shadows and layer
        # previews. Use the same isSliceable decoration as StartSliceJob;
        # names and the presence of a render mesh do not identify a model.
        decoration = _callable(node, "callDecoration")
        if decoration is not None:
            if decoration("isGroup"):
                return True
            return bool(decoration("isSliceable") and
                        _read_attr(node, ("getMeshData",), None) is not None)
        mesh = _callable(node, "getMeshData")
        if mesh is not None:
            return mesh() is not None
        # Lightweight adapters without the Uranium scene API expose models
        # directly, without decorations or getMeshData.
        return True

    def _find_node(self, object_id):
        for node in self._scene_nodes():
            if _object_id(node) == str(object_id):
                return node
        return None

    def _tool_get_scene(self, arguments):
        scene, root = self._scene_root()
        objects = []
        self._nodes_by_id = {}
        for node in self._scene_nodes():
            object_id = _object_id(node)
            self._nodes_by_id[object_id] = node
            objects.append({
                "id": object_id, "name": _object_name(node), "bounds_mm": _bounds_from_node(node),
                "position_mm": _vector(_read_attr(node, ("getPosition", "position"), None)),
                "scale": _vector(_read_attr(node, ("getScale", "scale"), None), (1.0, 1.0, 1.0)),
            })
        mode = "classic"
        material_id = ""
        try:
            mode_value = self._get_property(self._get_stack("global", 0), "printing_mode")
            if mode_value is not _UNSET:
                mode = str(mode_value)
        except Exception:
            pass
        try:
            ext_stack = self._get_stack("extruder", 0)
            material = self._get_property(ext_stack, "material_id")
            if material is not _UNSET:
                material_id = str(material)
        except Exception:
            pass
        return {"objects": objects, "mode": mode, "material_id": material_id}

    def _tool_get_settings(self, arguments):
        scope = arguments["scope"]
        index = int(arguments.get("extruder", 0))
        values = {}
        stack = self._get_stack(scope, index)
        for key in arguments["keys"]:
            self._validate_setting(key, self._get_property(stack, key), scope, index)
            value = self._get_property(stack, key)
            if value is not _UNSET:
                values[key] = _safe_json(value)
        return {"values": values, "scope": scope}

    def _tool_set_setting(self, arguments):
        arguments = self.prepare_setting(arguments)
        scope = arguments["scope"]
        index = int(arguments.get("extruder", 0))
        if scope not in _SCOPES or index < 0:
            raise ValueError("invalid setting scope")
        self._validate_setting(arguments["key"], arguments["value"], scope, index)
        self._set_property(self._get_stack(scope, index), arguments["key"], arguments["value"])
        self._invalidate_slice()
        actual = self._get_property(self._get_stack(scope, index), arguments["key"])
        if actual is _UNSET or actual != arguments["value"]:
            return _error("STE did not apply %s; read-back value differs" % arguments["key"], "UNAVAILABLE")
        return _success(key=arguments["key"], value=arguments["value"], scope=scope, extruder=index)

    def _machine_is_five_axis(self):
        for key in ("machine_five_axis", "five_axis", "machine_is_5d", "machine_type"):
            try:
                value = self._get_property(self._get_stack("global", 0), key)
                if value is _UNSET:
                    continue
                if key == "machine_type":
                    return "5" in str(value).lower() or "five" in str(value).lower()
                return bool(value)
            except Exception:
                continue
        return None

    def _tool_set_print_mode(self, arguments):
        mode = arguments["mode"]
        if mode not in _MODES:
            raise ValueError("unsupported print mode")
        is_five_axis = self._machine_is_five_axis()
        if mode != "classic" and is_five_axis is False:
            return _error("cylindrical modes require a five-axis machine", "INVALID_ARGUMENTS")
        self._validate_setting("printing_mode", mode, "global", 0)
        self._set_property(self._get_stack("global", 0), "printing_mode", mode)
        self._invalidate_slice()
        return _success(mode=mode)

    def _quality_manager(self):
        manager = self._get_manager()
        qmgr = _read_attr(manager, ("getQualityManager", "quality_manager", "_quality_manager"), None)
        if qmgr is None:
            qmgr = _read_attr(self.app, ("getQualityManager", "quality_manager"), None)
        return qmgr

    def _material_manager(self):
        manager = self._get_manager()
        mmgr = _read_attr(manager, ("getMaterialManager", "material_manager", "_material_manager"), None)
        if mmgr is None:
            mmgr = _read_attr(self.app, ("getMaterialManager", "material_manager"), None)
        return mmgr

    def _container_registry(self):
        registry = _read_attr(self.app, ("getContainerRegistry", "container_registry"), None)
        if registry is None:
            raise RuntimeError("container registry API unavailable")
        return registry

    def _metadata_value(self, metadata, key, default=""):
        if isinstance(metadata, dict):
            return metadata.get(key, default)
        all_metadata = _callable(metadata, "getMetaData")
        if all_metadata is not None:
            result = all_metadata()
            if isinstance(result, dict):
                return result.get(key, default)
        method = _callable(metadata, "getMetaDataEntry")
        if method is not None:
            return _call_variants(metadata, "getMetaDataEntry", [((key, default), {}), ((key,), {})])
        getter = _callable(metadata, "get")
        if getter is not None:
            return _call_variants(metadata, "get", [((key, default), {}), ((key,), {})])
        return default

    def _entry(self, value, key, default=""):
        if isinstance(value, dict):
            return _safe_json(value.get(key, default))
        result = _read_attr(value, ("get" + key.capitalize(), key, "id" if key == "id" else key), default)
        return _safe_json(result)

    def _tool_list_profiles(self, arguments):
        qmgr = self._quality_manager()
        if qmgr is None:
            raise RuntimeError("quality manager API unavailable")
        stack = self._get_stack("global", 0)
        groups = _call_variants(qmgr, "getQualityGroups", [((stack,), {})])
        if groups is _UNSET or groups is None:
            raise RuntimeError("quality groups API unavailable")
        profiles = []
        if isinstance(groups, dict):
            for quality_type, item in groups.items():
                profiles.append({"id": self._entry(item, "id", quality_type),
                                 "name": self._entry(item, "name", quality_type),
                                 "quality_type": str(quality_type)})
        elif groups is not _UNSET and groups is not None:
            for item in groups:
                quality_type = self._entry(item, "quality_type", self._entry(item, "id", ""))
                profiles.append({"id": self._entry(item, "id", quality_type),
                                 "name": self._entry(item, "name", quality_type),
                                 "quality_type": quality_type})
        # MaterialManager's machine/extruder query is the native source used
        # by the material selector.  Registry metadata alone can list files
        # that are not compatible with the current machine or nozzle.
        mmgr = self._material_manager()
        if mmgr is None:
            raise RuntimeError("material manager API unavailable")
        global_stack = self._get_stack("global", 0)
        extruder_stack = self._get_stack("extruder", 0)
        materials_raw = _call_variants(mmgr, "getAvailableMaterialsForMachineExtruder", [
            ((global_stack, extruder_stack), {}),
        ])
        if materials_raw is _UNSET or materials_raw is None:
            raise RuntimeError("compatible material API unavailable")
        if not isinstance(materials_raw, dict):
            raise RuntimeError("compatible material API returned an invalid value")
        materials = []
        for root_id, item in materials_raw.items():
            root_id = _safe_json(root_id)
            name = _read_attr(item, ("getName", "name"), _UNSET)
            if name is _UNSET or name is None:
                container = _read_attr(item, ("getContainer", "container"), None)
                name = _read_attr(container, ("getName", "name"), root_id)
            materials.append({"id": _safe_json(root_id),
                              "name": _safe_json(name)})
        return {"profiles": profiles, "materials": materials}

    def _tool_select_profile(self, arguments):
        manager = self._get_manager()
        method = _callable(manager, "setQualityGroupByQualityType")
        if method is None:
            raise RuntimeError("quality selection API unavailable")
        method(arguments["quality_type"])
        self._invalidate_slice()
        return _success(quality_type=arguments["quality_type"])

    def _tool_select_material(self, arguments):
        manager = self._get_manager()
        method = _callable(manager, "setMaterialById")
        if method is None:
            raise RuntimeError("material selection API unavailable")
        index = int(arguments.get("extruder", 0))
        method(index, arguments["material_id"])
        self._invalidate_slice()
        return _success(material_id=arguments["material_id"], extruder=index)

    def _tool_create_profile(self, arguments):
        qmgr = self._quality_manager()
        result = _call_variants(qmgr, "createQualityChanges", [((arguments["name"],), {})])
        if result is _UNSET or result is None:
            raise RuntimeError("profile API unavailable")
        self._profile_changes = result
        return {"created": True, "name": arguments["name"]}

    def _tool_save_profile(self, arguments):
        if self._profile_changes is None:
            return _error("no profile changes to save", "INVALID_ARGUMENTS")
        container_manager = None
        try:
            from steslicer.Settings.ContainerManager import ContainerManager
            container_manager = ContainerManager.getInstance()
        except ImportError:
            # A host test double may expose the same singleton through the
            # application; real STE uses the import above.
            container_manager = _read_attr(self.app, ("getContainerManager", "container_manager"), None)
        except Exception as exc:
            raise RuntimeError("ContainerManager singleton unavailable: %s" % exc)
        method = _callable(container_manager, "updateQualityChanges")
        if method is None:
            return _error("ContainerManager.updateQualityChanges API unavailable", "UNAVAILABLE")
        response = method()
        if response is not True:
            return _error("ContainerManager.updateQualityChanges did not return true", "UNAVAILABLE")
        self._profile_changes = None
        return _success(saved=True)

    def _tool_load_model(self, arguments):
        path = arguments["path"]
        if not isinstance(path, str) or not path or not os.path.isfile(path):
            raise ValueError("path must be an existing internal upload")
        self._import_host()
        method = _callable(self.app, "readLocalFile")
        if method is None:
            raise RuntimeError("application readLocalFile API unavailable")
        if self._qt is None:
            raise RuntimeError("Qt QUrl API unavailable")
        url = self._qt.fromLocalFile(path)
        method(url)
        self._invalidate_slice()
        # STE imports asynchronously.  The result deliberately says pending;
        # the next get_scene observes the native scene when it is ready.
        return {"state": "pending"}

    def _tool_transform_model(self, arguments):
        node = self._find_node(arguments["object_id"])
        if node is None:
            return _error("unknown object", "INVALID_ARGUMENTS")
        values = [_as_number(arguments[axis], axis) for axis in ("x", "y", "z")]
        action = arguments["action"]
        if action == "scale" and any(value <= 0 for value in values):
            raise ValueError("scale values must be positive")
        self._import_host()
        operation_class = self._operations.get(action)
        vector_class = self._math_types.get("Vector")
        if vector_class is None:
            raise RuntimeError("UM Vector API unavailable")
        vector = vector_class(*values)
        operation = None
        if operation_class is not None:
            if action == "translate":
                operation = operation_class(node, vector)
            elif action == "rotate":
                quaternion_class = self._math_types.get("Quaternion")
                if quaternion_class is None:
                    raise RuntimeError("UM Quaternion API unavailable")
                rotation = quaternion_class()
                axes = (getattr(vector_class, "Unit_X", None),
                        getattr(vector_class, "Unit_Y", None),
                        getattr(vector_class, "Unit_Z", None))
                for angle, axis in zip(values, axes):
                    if angle:
                        if axis is None:
                            raise RuntimeError("UM Vector unit-axis API unavailable")
                        delta = quaternion_class.fromAngleAxis(math.radians(float(angle)), axis)
                        rotation = delta * rotation
                operation = operation_class(node, rotation)
            else:
                operation = operation_class(node, vector, relative_scale=True)
        if operation is None or _callable(operation, "push") is None:
            raise RuntimeError("native transform operation API unavailable")
        operation.push()
        self._invalidate_slice()
        return _success(object_id=arguments["object_id"], action=action,
                        coordinates="STE: Y is up; X/Z are the build-plate plane")

    def _tool_arrange_models(self, arguments):
        controller = _read_attr(self.app, ("getController", "controller"), None)
        for obj in (controller, self.app):
            for name in ("arrange", "arrangeModels", "arrangeAll"):
                method = _callable(obj, name)
                if method is not None:
                    method()
                    self._invalidate_slice()
                    return _success(arranged=True)
        raise RuntimeError("arrange API unavailable")

    def _tool_remove_model(self, arguments):
        node = self._find_node(arguments["object_id"])
        if node is None:
            return _error("unknown object", "INVALID_ARGUMENTS")
        self._import_host()
        operation_class = self._operations.get("remove")
        if operation_class is None:
            raise RuntimeError("RemoveSceneNodeOperation API unavailable")
        operation = operation_class(node)
        if _callable(operation, "push") is None:
            raise RuntimeError("RemoveSceneNodeOperation.push API unavailable")
        operation.push()
        self._invalidate_slice()
        return _success(object_id=arguments["object_id"])

    def _connect_signal(self, backend, names, callback):
        for name in names:
            signal = getattr(backend, name, None)
            connector = getattr(signal, "connect", None)
            if callable(connector):
                connector(callback)
                # UM.Signal stores weak references to functions. Keep each
                # callback alive for the complete slice lifecycle.
                self._signal_connections.append((signal, callback))
                return name
        return None

    def _on_progress(self, generation, *args):
        if generation != self._active_generation or self._slice_state != "running":
            return
        self._observe_gcode(generation)
        value = None
        for item in reversed(args):
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                value = float(item)
                break
        if value is not None:
            self._slice_progress = max(0.0, min(1.0, value / 100.0 if value > 1.0 else value))

    def _gcode_signature_has_content(self, signature):
        """Return whether a G-code signature contains non-empty output."""
        if signature is None:
            return False
        try:
            _, length, content = signature
        except (TypeError, ValueError):
            return False
        if not length:
            return False
        if isinstance(content, (tuple, list)):
            if not content:
                return False
            for item in content:
                if item is None:
                    continue
                if isinstance(item, str) and not item.strip():
                    continue
                return True
            return False
        if isinstance(content, str):
            return bool(content.strip())
        return True

    def _gcode_signature_is_fresh(self, signature):
        """Require non-empty G-code with a changed native signature."""
        if not self._gcode_signature_has_content(signature):
            return False
        # Keep object identity in the comparison.  Two deterministic slices
        # can legitimately produce byte-identical G-code in a new container;
        # the new native object is still fresh output for that generation.
        return signature != self._slice_gcode_before

    def _maybe_finish_generation(self, generation):
        """Finalize only after explicit Done and after post-processing settles."""
        if generation != self._active_generation or self._slice_state != "running":
            return
        if not self._slice_done_seen:
            return
        self._finish_generation(generation)

    def _tool_get_slice_status(self, arguments):
        # A native forceSlice call can return without emitting any lifecycle
        # signal (for example when the backend has no work).  Bound that
        # otherwise indefinite running state so the registry can recover.
        if (self._slice_state == "running" and not self._slice_started and
                self._slice_started_at is not None and
                time.time() - self._slice_started_at >= 30.0):
            self._slice_state = "error"
            self._slice_error = "native slicing emitted no Processing or slicing-start evidence"
            self._active_generation = None
            self._slice_done_seen = False
        else:
            self._maybe_finish_generation(self._active_generation)
        if self._slice_state == "done":
            # PrintInformation can update after native Done/post-processing.
            self._slice_result = self._collect_print_information()
        return self._status()

    def _active_build_plate(self):
        model = _read_attr(self.app, ("getMultiBuildPlateModel", "multi_build_plate_model"), None)
        if model is None:
            return _UNSET
        return _read_attr(model, ("activeBuildPlate", "active_build_plate"), _UNSET)

    def _gcode_signature(self):
        try:
            scene, _ = self._scene_root()
            data = _read_attr(scene, ("gcode_dict", "gcodeDict"), None)
            if not isinstance(data, dict):
                return None
            plate = self._slice_plate
            value = data.get(plate)
            if value is None:
                return None
            if isinstance(value, (list, tuple)):
                content = tuple(_safe_json(item) for item in value)
                length = len(value)
            else:
                content = _safe_json(value)
                length = 1
            return (id(value), length, content)
        except Exception:
            return None

    def _scene_fingerprint(self):
        try:
            scene = self._tool_get_scene({})
            for item in scene["objects"]:
                item.pop("name", None)  # Renaming has no effect on slicing.
                node = self._nodes_by_id[item["id"]]
                transform = _read_attr(node, ("getWorldTransformation",), None)
                data = _read_attr(transform, ("getData",), None)
                if data is not None:
                    item["world_transform"] = _safe_json(data.tolist())
            scene["objects"].sort(key=lambda item: item["id"])
            return _safe_json(scene)
        except Exception:
            return None

    def _settings_fingerprint(self):
        snapshot = self.snapshot_settings()
        if isinstance(snapshot, dict) and snapshot.get("ok") is not False:
            return _safe_json(snapshot)
        return None

    def _validate_completed_generation(self):
        return self._completed_generation_problem() is None

    def _completed_generation_problem(self):
        if self._slice_completed_plate is not _UNSET:
            current_plate = self._active_build_plate()
            if current_plate is _UNSET or current_plate != self._slice_completed_plate:
                return "сменился активный стол печати"
        if self._slice_completed_gcode is not None:
            if self._gcode_signature() != self._slice_completed_gcode:
                return "изменился G-code"
        if self._slice_completed_scene is not None:
            if self._scene_fingerprint() != self._slice_completed_scene:
                return "изменилась модель или её расположение"
        if self._slice_completed_settings is not None:
            current_settings = self._settings_fingerprint()
            if current_settings is None or current_settings != self._slice_completed_settings:
                return "изменились настройки печати"
        return None

    def _observe_gcode(self, generation):
        if generation != self._active_generation:
            return
        signature = self._gcode_signature()
        if signature != self._slice_gcode_before:
            self._slice_gcode_invalidated = True

    def _on_slicing_started(self, generation, *args):
        if generation != self._active_generation or self._slice_state != "running":
            return
        self._slice_started = True
        self._slice_evidence.append("slicingStarted")
        self._observe_gcode(generation)

    def _on_print_duration(self, generation, *args):
        if generation != self._active_generation or not self._slice_started:
            return
        plate = args[0] if args else _UNSET
        if plate is _UNSET:
            return
        self._slice_duration_plate = plate
        if self._slice_plate == plate and self._active_build_plate() in (_UNSET, plate):
            self._slice_evidence.append("printDurationMessage:plate=%s" % plate)
            self._observe_gcode(generation)

    def _state_matches(self, value, name):
        states = self._backend_states
        if states:
            expected = getattr(states, name, None)
            if expected is not None and value == expected:
                return True
        text = str(value).lower()
        if name == "Done":
            return any(word in text for word in ("finished", "finish", "completed", "complete", "done"))
        if name == "Error":
            return any(word in text for word in ("error", "failed", "cancel"))
        if name == "NotStarted":
            return "notstarted" in text or "not started" in text or "idle" in text
        if name == "Processing":
            return "processing" in text or "slicing" in text
        if name == "Disabled":
            return "disabled" in text
        return False

    def _on_backend_state(self, generation, *args):
        if generation != self._active_generation:
            return
        self._observe_gcode(generation)
        state = args[-1] if args else _UNSET
        text = " ".join(str(item).lower() for item in args)
        self._slice_evidence.append("backendStateChange:" + text[:120])
        # Keep observing the backend after Done.  Manual changes in STE emit
        # Processing/NotStarted/Disabled and must invalidate the cached
        # generation before an export can use it.
        if self._slice_state == "done":
            if (self._state_matches(state, "Error") or
                    self._state_matches(state, "Processing") or
                    self._state_matches(state, "NotStarted") or
                    self._state_matches(state, "Disabled")):
                self._invalidate_slice()
            return
        if self._slice_state != "running":
            return
        if self._state_matches(state, "Error"):
            self._slice_state = "error"
            self._slice_error = text or "native slicing failed"
            self._active_generation = None
            return
        # Cura/Cylindrical backends emit NotStarted from stopSlicing while
        # entering forceSlice.  Ignore that pre-start transition, but treat a
        # Disabled state or a post-start NotStarted as a cancelled generation.
        if (self._state_matches(state, "Disabled") or
                (self._state_matches(state, "NotStarted") and self._slice_started)):
            self._slice_state = "error"
            self._slice_error = text or "native slicing cancelled"
            self._active_generation = None
            return
        if self._state_matches(state, "Processing"):
            self._slice_started = True
            self._slice_evidence.append("nativeProcessingStart")
            return
        if self._state_matches(state, "Done"):
            if not self._slice_started:
                self._slice_evidence.append("done ignored: no current-generation start")
                return
            # STE emits Done before it appends the final post-processed G-code
            # to gcode_dict.  Record the explicit completion intent and let a
            # later status/export call (or forceSlice return) finalize it.
            self._slice_done_seen = True
            self._slice_evidence.append("nativeDoneSeen")

    def _finish_generation(self, generation):
        if generation != self._active_generation or self._slice_state != "running":
            return
        if not self._slice_started or not self._slice_done_seen:
            return
        # Native STE appends post-processed lines after Done.  Re-read the
        # signature at the finalization boundary so that this trailing update
        # is included before completion fingerprints are captured.
        self._observe_gcode(generation)
        active_plate = self._active_build_plate()
        if active_plate is not _UNSET and active_plate != self._slice_plate:
            self._slice_evidence.append("done ignored: active build plate changed")
            return
        if self._slice_duration_plate is not None and self._slice_duration_plate != self._slice_plate:
            self._slice_evidence.append("done ignored: duration belongs to another build plate")
            return
        current_gcode = self._gcode_signature()
        if (not self._slice_gcode_invalidated or
                not self._gcode_signature_is_fresh(current_gcode)):
            self._slice_evidence.append("done ignored: no new gcode observed")
            return
        self._slice_progress = 1.0
        self._slice_result = self._collect_print_information()
        self._slice_state = "done"
        self._active_generation = generation
        self._slice_completed_plate = active_plate
        self._slice_completed_gcode = current_gcode
        self._slice_completed_scene = self._scene_fingerprint()
        self._slice_completed_settings = self._settings_fingerprint()

    def _collect_print_information(self):
        result = {"available": False}
        info = _read_attr(self.app, ("getPrintInformation", "printInformation"), None)
        if info is None:
            return result
        self._import_host()
        duration = _read_attr(info, ("currentPrintTime",), None)
        if duration is not None:
            seconds_format = "seconds"
            if self._duration_format:
                seconds_format = self._duration_format.Format.Seconds
            try:
                display = _call_variants(duration, "getDisplayString", [((seconds_format,), {})])
            except Exception:
                display = _UNSET
            if display is not _UNSET:
                try:
                    result["time_seconds"] = int(display)
                except (TypeError, ValueError):
                    pass
        for output, name in (("material_mm", "materialLengths"), ("material_g", "materialWeights")):
            value = _read_attr(info, (name,), None)
            # Native materialLengths is measured in metres, unlike the wire
            # contract's material_mm. Preserve extruder positions and units.
            factor = 1000.0 if output == "material_mm" else 1.0
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                result[output] = value * factor
            elif isinstance(value, (list, tuple)) and value:
                if all(isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item) for item in value):
                    result[output] = [item * factor for item in value]
        result["available"] = any(key in result for key in ("time_seconds", "material_mm", "material_g"))
        return result

    def _tool_start_slice(self, arguments):
        backend = self._get_backend()
        self._import_host()
        self._backend = backend
        for signal, callback in self._signal_connections:
            disconnect = _callable(signal, "disconnect")
            if disconnect:
                try:
                    disconnect(callback)
                except (TypeError, ValueError, RuntimeError):
                    pass
        self._signal_connections = []
        nodes = self._native_nodes()
        generation = self._begin_generation()
        if not nodes:
            self._slice_state = "error"
            self._slice_error = "cannot slice an empty scene"
            self._active_generation = None
            return {"state": "error", "plugin_generation_id": generation, "error": self._slice_error}
        self._slice_plate = self._active_build_plate()
        self._slice_started_at = time.time()
        self._slice_started = False
        self._slice_duration_plate = None
        self._slice_gcode_before = self._gcode_signature()
        self._slice_gcode_invalidated = False
        self._connect_signal(backend, ("processingProgress", "progressChanged", "processing_progress"),
                             lambda *args: self._on_progress(generation, *args))
        self._connect_signal(backend, ("backendStateChange", "stateChanged", "backend_state_changed"),
                             lambda *args: self._on_backend_state(generation, *args))
        self._connect_signal(backend, ("slicingStarted", "slicing_started"),
                             lambda *args: self._on_slicing_started(generation, *args))
        self._connect_signal(backend, ("printDurationMessage", "print_duration_message"),
                             lambda *args: self._on_print_duration(generation, *args))
        method = _callable(backend, "forceSlice")
        if method is None:
            self._slice_state = "unavailable"
            self._active_generation = None
            return _error("native forceSlice API unavailable", "UNAVAILABLE")
        try:
            response = method()
        except Exception as exc:
            self._slice_state = "error"
            self._slice_error = str(exc)
            self._active_generation = None
            return {"state": "error", "plugin_generation_id": generation, "error": str(exc)}
        if response is False:
            self._slice_state = "error"
            self._slice_error = "native forceSlice returned false"
            self._active_generation = None
        else:
            # Lightweight/native test doubles can emit Done and finish their
            # trailing G-code append synchronously inside forceSlice().
            self._maybe_finish_generation(generation)
        return {"state": self._slice_state, "plugin_generation_id": generation}

    def _native_nodes(self):
        return self._scene_nodes()

    def _native_writer(self, writer_name, file_handler_name):
        handler = _read_attr(self.app, (file_handler_name,), None)
        writer = _call_variants(handler, "getWriter", [((writer_name,), {})])
        if writer is _UNSET or writer is None:
            raise RuntimeError("native %s unavailable" % writer_name)
        return writer

    def export_file(self, name, arguments, invoke, cancel=None):
        """Called on the registry worker, like STE's native WriteFileJob.

        Never wait for this method from Qt: the writers synchronously queue
        OpenGL/3MF work there. Only the host writer crosses the thread boundary;
        our scene/settings checks and writer lookup stay on Qt.
        """
        bridge = getattr(invoke, "__self__", None)
        if getattr(bridge, "owner_thread", None) == threading.current_thread().ident:
            raise RuntimeError("Экспорт нужно запускать из рабочего потока, чтобы STE мог обработать миниатюру.")

        def check_cancel():
            if cancel is not None and cancel.is_set():
                raise RuntimeError("Операция отменена.")

        def prepare():
            check_cancel()
            invalid = self._validate_arguments(name, arguments)
            if invalid:
                raise ValueError(invalid["error"])
            if name == "export_gcode":
                self._require_completed_gcode()
                writer_name, handler = "GCodeWriter", "getMeshFileHandler"
                nodes = self._gcode_nodes()
            elif name == "save_project":
                writer_name, handler = "3MFWriter", "getWorkspaceFileHandler"
                nodes = self._native_nodes()
            else:
                raise ValueError("unsupported export operation")
            return {"writer": self._native_writer(writer_name, handler), "writer_name": writer_name,
                    "handler": handler, "nodes": nodes, "generation": self._active_generation,
                    "scene": self._scene_fingerprint(), "settings": self._settings_fingerprint()}

        plan = invoke(prepare, cancel=cancel)

        def validate_output():
            check_cancel()
            if (self._scene_fingerprint() != plan["scene"] or
                    self._settings_fingerprint() != plan["settings"]):
                raise RuntimeError("Модель или настройки изменились во время экспорта. Повторите экспорт.")
            if name == "export_gcode":
                if self._active_generation != plan["generation"]:
                    raise RuntimeError("Во время экспорта началась другая нарезка. Повторите экспорт.")
                self._require_completed_gcode()

        check_cancel()
        return self._write_native(arguments["filename"], plan["writer_name"], plan["handler"],
                                  plan["nodes"], writer=plan["writer"],
                                  before_commit=lambda: invoke(validate_output, cancel=cancel))

    def _write_native(self, filename, writer_name, file_handler_name, nodes, writer=None, before_commit=None):
        filename = _safe_filename(filename)
        if not os.path.isdir(self.output_dir):
            os.makedirs(self.output_dir)
        path = os.path.abspath(os.path.join(self.output_dir, filename))
        output_root = os.path.realpath(self.output_dir)
        if os.path.commonpath((os.path.realpath(path), output_root)) != output_root:
            raise ValueError("output path escaped output_dir")
        if os.path.lexists(path) and os.path.islink(path):
            raise ValueError("output path must not be a symlink")
        if os.path.realpath(os.path.dirname(path)) != output_root:
            raise ValueError("output path escaped output_dir")
        if writer is None:
            writer = self._native_writer(writer_name, file_handler_name)
        stream_mode = "w" if writer_name == "GCodeWriter" else "wb"
        temp_path = os.path.join(output_root, ".%s.%s.tmp" % (filename, uuid.uuid4().hex))
        if os.path.realpath(os.path.dirname(temp_path)) != output_root:
            raise RuntimeError("temporary output path escaped output_dir")
        try:
            if stream_mode == "w":
                stream = io.open(temp_path, "w", encoding="utf-8", newline="")
            else:
                stream = open(temp_path, "wb")
            with stream:
                response = _call_variants(writer, "write", [((stream, nodes), {}), ((stream,), {})])
                if response is not True:
                    raise RuntimeError("native %s writer did not return true" % writer_name)
            size = os.path.getsize(temp_path)
            if size <= 0:
                raise RuntimeError("native %s writer produced an empty file" % writer_name)
            if before_commit is not None:
                before_commit()
            # Re-check immediately before the atomic replacement.  A symlink
            # supplied by a caller is rejected, and a failed writer leaves an
            # existing destination untouched.
            if os.path.lexists(path) and os.path.islink(path):
                raise ValueError("output path must not be a symlink")
            os.replace(temp_path, path)
        finally:
            if os.path.lexists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
        return {"ok": True, "path": path, "filename": filename, "size_bytes": size,
                "evidence": "native %s" % writer_name}

    def _tool_save_project(self, arguments):
        return self._write_native(arguments["filename"], "3MFWriter", "getWorkspaceFileHandler", self._native_nodes())

    def _gcode_nodes(self):
        scene, root = self._scene_root()
        data = _read_attr(scene, ("gcode_dict", "gcodeDict"), None)
        if data is None:
            data = _read_attr(self.app, ("gcode_dict",), None)
        if not isinstance(data, dict):
            raise RuntimeError("native gcode data unavailable")
        model = _read_attr(self.app, ("getMultiBuildPlateModel", "multi_build_plate_model"), None)
        plate = _read_attr(model, ("activeBuildPlate", "active_build_plate"), _UNSET)
        if plate is _UNSET:
            raise RuntimeError("active build plate API unavailable")
        if plate not in data:
            raise RuntimeError("gcode for active build plate unavailable")
        return data[plate]

    def _require_completed_gcode(self):
        if self._active_generation is not None:
            self._maybe_finish_generation(self._active_generation)
        if self._slice_state != "done" or self._active_generation is None:
            raise RuntimeError(self._slice_error or "Для экспорта G-code сначала выполните нарезку текущей модели.")
        problem = self._completed_generation_problem()
        if problem is not None:
            self._invalidate_slice()
            self._slice_error = "Результат нарезки устарел: %s. Выполните нарезку заново." % problem
            raise RuntimeError(self._slice_error)

    def _tool_export_gcode(self, arguments):
        self._require_completed_gcode()
        return self._write_native(arguments["filename"], "GCodeWriter", "getMeshFileHandler", self._gcode_nodes())

    def _tool_check_5d(self, arguments):
        scene = self._tool_get_scene({})
        mode = scene.get("mode", "classic")
        machine = self._machine_is_five_axis()
        warnings = []
        assumptions = []
        facts = {"mode": mode, "machine_five_axis": machine, "object_count": len(scene.get("objects", [])),
                 "objects": [{"id": item.get("id"), "bounds_mm": item.get("bounds_mm"),
                              "position_mm": item.get("position_mm")}
                             for item in scene.get("objects", [])]}
        if mode in ("cylindrical", "cylindrical_full"):
            if machine is None:
                warnings.append("Cylindrical mode is selected but five-axis machine capability is unavailable.")
            elif not machine:
                warnings.append("Cylindrical mode is selected on a machine without reported five-axis support.")
        elif machine is None:
            assumptions.append("Machine five-axis capability was not exposed by the current settings API.")

        base_diameter = _UNSET
        try:
            base_diameter = self._get_property(self._get_stack("global", 0),
                                               "cylindrical_mode_base_diameter")
        except Exception:
            base_diameter = _UNSET
        if base_diameter is not _UNSET and base_diameter is not None:
            if (isinstance(base_diameter, (int, float)) and not isinstance(base_diameter, bool) and
                    math.isfinite(float(base_diameter))):
                facts["base_diameter_mm"] = base_diameter
                if mode in ("cylindrical", "cylindrical_full"):
                    minimum = 4.0
                    try:
                        definition = self._definition("cylindrical_mode_base_diameter", "global", 0)
                        defined_minimum = self._definition_value(
                            definition, ("minimum", "min", "minimum_value"), _UNSET)
                        if defined_minimum is not _UNSET:
                            minimum = max(minimum, float(defined_minimum))
                    except (TypeError, ValueError):
                        pass
                    if float(base_diameter) < minimum:
                        warnings.append("Cylindrical base diameter is below the native minimum of %.g mm." % minimum)
            elif mode in ("cylindrical", "cylindrical_full"):
                warnings.append("Cylindrical base diameter is invalid in the native settings.")
        elif mode in ("cylindrical", "cylindrical_full"):
            warnings.append("Cylindrical base diameter is unavailable in the native settings.")

        if mode in ("cylindrical", "cylindrical_full"):
            warnings.append("Cylindrical spiral inner-cylinder exclusion is an assumption; verify it in the native preview.")
        assumptions.append("Geometry facts are native scene bounds and positions; exact kinematics and remote geometry are not verified.")
        assumptions.append("Loads and mating faces are user-provided and were not verified.")
        assumptions.append("Material strength and structural suitability are not verified.")
        return {"facts": facts,
                "warnings": warnings, "assumptions": assumptions, "verified_geometry": False}
