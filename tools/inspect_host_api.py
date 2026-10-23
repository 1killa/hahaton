"""Read-only inspection of the installed STE runtime (run_host_python.ps1)."""
import inspect
import importlib
from steslicer.SteSlicerApplication import SteSlicerApplication

targets = {
    "UM.Backend.Backend": ("BackendState", "Backend"),
    "UM.Extension": ("Extension",),
    "UM.Operations.Operation": ("Operation",),
    "UM.Settings.InstanceContainer": ("InstanceContainer",),
    "steslicer.Machines.MaterialManager": ("MaterialManager",),
    "steslicer.Settings.ContainerManager": ("ContainerManager",),
    "steslicer.Backend.BackendManager": ("BackendManager",),
    "steslicer.SteSlicerApplication": ("SteSlicerApplication",)
}
for module_name, names in sorted(targets.items()):
    try:
        module = importlib.import_module(module_name)
        for name in names:
            cls = getattr(module, name)
            print("\n", module_name, name)
            for member_name in dir(cls):
                if any(word in member_name.lower() for word in ("material", "quality", "backend", "writer", "workspace", "menu", "calllater", "instance", "state")) or member_name == "push":
                    value = getattr(cls, member_name)
                    try:
                        print(member_name, inspect.signature(value))
                    except (ValueError, TypeError):
                        if name == "BackendState" and not member_name.startswith("_"):
                            print(member_name, value)
    except Exception as error:
        print(module_name, "UNAVAILABLE", str(error))
