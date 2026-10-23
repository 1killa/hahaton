"""Build the organizer's plugin/results ZIP; runtime settings are excluded."""
import argparse
import os
import re
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="hahaton-draft")
    parser.add_argument("--require-results", action="store_true")
    args = parser.parse_args()
    if not re.match(r"^[A-Za-z0-9_-]{1,64}$", args.name):
        parser.error("Use 1–64 Latin letters, digits, hyphens or underscores for the team name.")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result_root = os.path.join(root, "results")
    models = [name for name in os.listdir(result_root) if os.path.isdir(os.path.join(result_root, name))]
    if args.require_results:
        if not models:
            parser.error("No real model results yet. This draft is not a final competition submission.")
        for model in models:
            directory = os.path.join(result_root, model)
            for filename in ("project.3mf", "result.gcode"):
                path = os.path.join(directory, filename)
                if not os.path.isfile(path) or not os.path.getsize(path):
                    parser.error("Missing real result: " + path)
            if not any(name.startswith("chat_") and name.endswith(".png") for name in os.listdir(directory)):
                parser.error("Missing chat screenshots: " + directory)
    target_dir = os.path.join(root, "dist")
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, args.name + ".zip")
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for top in ("plugin", "results"):
            for directory, folders, files in os.walk(os.path.join(root, top)):
                folders[:] = [name for name in folders if name != "__pycache__"]
                for filename in sorted(files):
                    if filename.endswith((".pyc", ".pyo", ".log")):
                        continue
                    path = os.path.join(directory, filename)
                    archive.write(path, os.path.relpath(path, root).replace(os.sep, "/"))
                    count += 1
    print("ZIP:", target)
    print("Files:", count)
    print("Mode:", "results structure checked" if args.require_results else "DRAFT — no claim of final competition results")


if __name__ == "__main__":
    main()
