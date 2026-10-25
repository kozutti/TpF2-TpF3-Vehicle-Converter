"""CLI and Explorer drag-and-drop dispatch; shares the GUI pipeline."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from converter import __version__
from converter.batch import DEFAULT_MOD_WORKERS, MAX_MOD_WORKERS
from converter.transforms.texture_usage import DEFAULT_TEXTURE_WORKERS, MAX_TEXTURE_WORKERS

COMMANDS = {"analyze", "plan", "convert", "batch", "validate", "gui"}


def default_output() -> Path:
    return Path.home() / "Documents" / "TPF3ConverterOutput"


def result_exit_code(result: dict) -> int:
    status = str(result.get("status", "")).lower()
    if status in {"failed", "failure", "invalid", "error"}:
        return 1
    if status in {"manual", "manual_required", "needs_manual", "partial", "blocked", "cancelled", "canceled"}:
        return 2
    for diagnostic in result.get("diagnostics", []):
        if isinstance(diagnostic, dict) and diagnostic.get("severity") in {"error", "fatal"}:
            return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    args = list(sys.argv[1:] if argv is None else argv)
    # Explorer passes dropped paths without a subcommand.
    if not args or (args[0] not in COMMANDS and not args[0].startswith("-")):
        from converter.gui import launch
        launch(args)
        return 0

    parser = argparse.ArgumentParser(description="TPF2 → TPF3 vehicle mod converter")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    gui_parser = sub.add_parser("gui", help="Japanese drag-and-drop GUI")
    gui_parser.add_argument("sources", nargs="*")
    gui_parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    gui_parser.add_argument("--smoke-report", type=Path, help=argparse.SUPPRESS)
    for command in ("analyze", "plan", "convert", "batch", "validate"):
        action = sub.add_parser(command)
        action.add_argument("source", type=Path, help="MOD folder or ZIP; batch takes a folder that holds many MODs; "
                                                      "validate accepts an output package folder")
        action.add_argument("--json", dest="json_file", type=Path, help="Save the result as JSON")
        if command != "validate":
            action.add_argument("--game-root", type=Path, default=None,
                                help="Optional TPF2 installation folder; read missing base assets from it")
        if command in {"convert", "batch"}:
            action.add_argument("--output", type=Path, default=default_output())
            action.add_argument("--texture-workers", type=int, choices=range(1, MAX_TEXTURE_WORKERS + 1),
                                default=DEFAULT_TEXTURE_WORKERS,
                                help=f"parallel processes for DDS conversion and farthest-LOD simplification: 1-{MAX_TEXTURE_WORKERS} (default: 8; 1 for sequential conversion)")
            action.add_argument("--singles", choices=("group", "show", "hide"), default="hide",
                                help="Parts of multiple units in the depot list: hide (default; as in TPF2, they exist only inside "
                                     "units), group (experimental: fold them into one row per MOD) or show (flat list)")
        if command == "batch":
            action.add_argument("--only", help="Comma-separated MOD folder names to convert (default: all vehicle MODs)")
            action.add_argument("--no-sibling-search", action="store_true",
                                help="Do not look for missing assets in the other MODs of the folder")
            action.add_argument("--mod-workers", type=int, choices=range(1, MAX_MOD_WORKERS + 1), default=DEFAULT_MOD_WORKERS,
                                help=f"MODs converted at the same time, each in its own process: 1-{MAX_MOD_WORKERS} (default: 1, one after another). "
                                     "Each MOD uses --texture-workers of its own, so the process count is the product of the two")
        if command == "convert":
            action.add_argument("--no-sibling-search", action="store_true",
                                help="Do not look for missing assets in the other MODs next to this one")
    parsed = parser.parse_args(args)
    if parsed.command == "gui":
        from converter.gui import launch
        result = launch(parsed.sources, smoke_test=parsed.smoke_test)
        if parsed.smoke_test:
            payload = json.dumps(result, ensure_ascii=False, indent=2)
            if parsed.smoke_report:
                parsed.smoke_report.write_text(payload + "\n", encoding="utf-8")
            if sys.stdout is not None:
                print(payload)
        return 0
    try:
        from converter import pipeline
        operation = getattr(pipeline, parsed.command)
        if parsed.command == "convert":
            result = operation(parsed.source, parsed.output, game_root=parsed.game_root,
                               single_vehicles=parsed.singles, siblings=None if parsed.no_sibling_search else "auto",
                               texture_workers=parsed.texture_workers, mods_dir=parsed.output / "mods_ready")
        elif parsed.command == "batch":
            result = operation(parsed.source, parsed.output, game_root=parsed.game_root, single_vehicles=parsed.singles,
                               search_siblings=not parsed.no_sibling_search,
                               only=[n for n in (parsed.only or "").split(",") if n.strip()] or None,
                               texture_workers=parsed.texture_workers, mod_workers=parsed.mod_workers)
        elif parsed.command == "validate":
            result = operation(parsed.source)
        else:
            result = operation(parsed.source, game_root=parsed.game_root)
        payload = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        if parsed.json_file:
            parsed.json_file.parent.mkdir(parents=True, exist_ok=True)
            parsed.json_file.write_text(payload + "\n", encoding="utf-8")
        if sys.stdout is not None:
            print(payload)
        if parsed.command == "batch" and sys.stderr is not None and isinstance(result.get("batch"), dict):
            from converter.batch import format_table
            print("\n" + format_table(result["batch"]), file=sys.stderr)
        return result_exit_code(result)
    except KeyboardInterrupt:
        if sys.stderr is not None:
            print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as error:
        if sys.stderr is not None:
            print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
