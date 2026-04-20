"""
dotmd-parser — command-line entry point with subcommands.

Subcommands
-----------
- `init    [path]`         Install the bundled SKILL.md into a project.
- `index   <path>`         Build & save `.claude/dotmd-index.json`.
- `check   <path>`         Exit non-zero on cycles / missing refs (CI use).
- `affects <path> <file>`  List files transitively depending on `<file>`.
- `deps    <path> <file>`  Direct dependencies of `<file>`.
- `digest  <path>`         Token-efficient text summary for Claude context.
- `tree    <path> [file]`  ASCII dependency tree.
- `resolve <file>`         Recursively expand `@include` directives.
- `analyze <path>`         AI-powered dependency detection (requires Claude API).
- `show    <path>`         Legacy summary + full graph JSON (default).

Invoking with a single positional path and no subcommand runs `show`, so
existing users of `dotmd-parser ./my-skill/` keep working.
"""

from __future__ import annotations

import argparse
import json
import sys
from importlib import resources
from pathlib import Path

from dotmd_parser import __version__
from dotmd_parser.parser import build_graph, resolve, summary
from dotmd_parser.analyze import (
    analyze_dependencies as _analyze_dependencies,
    apply_analysis as _apply_analysis,
    apply_analysis_from_file as _apply_analysis_from_file,
    estimate_cost as _estimate_cost,
    format_cost_estimate as _format_cost_estimate,
    format_host_agent_plan as _format_host_agent_plan,
    format_proposal as _format_proposal,
    load_dotenv as _load_dotenv,
)
from dotmd_parser.digest import digest as _digest, tree as _tree, affects as _affects, deps_of as _deps_of
from dotmd_parser.index import (
    build_index,
    build_scoped_index,
    default_index_path,
    load_index,
    merge_index,
    needs_rebuild,
    save_index,
)
from dotmd_parser.inventory import (
    inventory as _inventory,
    format_inventory as _format_inventory,
    suggest_next_command as _suggest_next_command,
)


def _maybe_warn_empty(path: str) -> None:
    """Emit a stderr hint if `path` has no .md files worth indexing."""
    try:
        inv = _inventory(path)
    except ValueError:
        return  # path check already handled by caller
    hint = _suggest_next_command(inv)
    if hint:
        print(f"warning: {hint}", file=sys.stderr)
        print(
            "        run `dotmd-parser inventory <path>` for details.",
            file=sys.stderr,
        )


def _load_or_build_index(path: str, use_cache: bool = True) -> dict:
    """Return a compact index for `path`, reusing the saved file when fresh."""
    target = Path(path)
    root = target if target.is_dir() else target.parent
    cached = default_index_path(root)
    if use_cache and cached.exists():
        try:
            idx = load_index(cached)
            if not needs_rebuild(idx, root):
                return idx
        except (ValueError, json.JSONDecodeError):
            pass
    return build_index(path)


SKILL_DIR_NAME = "dotmd-parser"
SKILL_TEMPLATE = "SKILL.md"


def _read_bundled_skill() -> str:
    """Load the packaged SKILL.md via importlib.resources."""
    return resources.files("dotmd_parser.templates").joinpath(SKILL_TEMPLATE).read_text(encoding="utf-8")


def cmd_init(args: argparse.Namespace) -> int:
    """Install the bundled SKILL.md into `<path>/.claude/skills/dotmd-parser/SKILL.md`."""
    project = Path(args.path).resolve()
    if not project.exists():
        print(f"error: path does not exist: {project}", file=sys.stderr)
        return 2

    target_dir = project / ".claude" / "skills" / SKILL_DIR_NAME
    target = target_dir / SKILL_TEMPLATE

    if target.exists() and not args.force:
        print(
            f"error: {target} already exists — pass --force to overwrite",
            file=sys.stderr,
        )
        return 1

    target_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(_read_bundled_skill(), encoding="utf-8")
    print(f"Installed skill: {target}")
    print("Next: run `dotmd-parser index .` from the project root.")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    if args.scope:
        try:
            scoped = build_scoped_index(args.path, args.scope)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        # Merge into any existing full-root index; fall back to scoped-only.
        existing_path = default_index_path(args.path)
        if existing_path.exists():
            try:
                existing = load_index(existing_path)
                idx = merge_index(existing, scoped, args.scope)
            except (ValueError, json.JSONDecodeError):
                idx = scoped
        else:
            idx = scoped
        out = save_index(idx, args.path, out_path=args.out)
        stats = idx["stats"]
        print(
            f"Wrote {out} — scope={args.scope!r}, "
            f"{stats['files']} files, {stats['edges']} edges, "
            f"{stats['cycles']} cycles, {stats['missing']} missing"
        )
        return 0

    idx = build_index(args.path)
    out = save_index(idx, args.path, out_path=args.out)
    stats = idx["stats"]
    print(
        f"Wrote {out} — {stats['files']} files, {stats['edges']} edges, "
        f"{stats['cycles']} cycles, {stats['missing']} missing"
    )
    if stats["files"] == 0:
        _maybe_warn_empty(args.path)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    idx = build_index(args.path)
    stats = idx["stats"]
    print(
        f"{stats['files']} files, {stats['edges']} edges — "
        f"cycles:{stats['cycles']} missing:{stats['missing']}"
    )
    for cycle in idx.get("cycles", []):
        print(f"  CYCLE   {cycle}")
    for miss in idx.get("missing", []):
        print(f"  MISSING {miss}")
    return 1 if (stats["cycles"] or stats["missing"]) else 0


def cmd_affects(args: argparse.Namespace) -> int:
    idx = _load_or_build_index(args.path, use_cache=not args.no_cache)
    for rel in _affects(idx, args.file):
        print(rel)
    return 0


def cmd_deps(args: argparse.Namespace) -> int:
    idx = _load_or_build_index(args.path, use_cache=not args.no_cache)
    for dep in _deps_of(idx, args.file):
        flag = " --parallel" if dep.get("parallel") else ""
        print(f"{dep['type']}\t{dep['to']}{flag}")
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    idx = _load_or_build_index(args.path, use_cache=not args.no_cache)
    print(_digest(idx))
    if idx.get("stats", {}).get("files", 0) == 0:
        _maybe_warn_empty(args.path)
    return 0


def cmd_tree(args: argparse.Namespace) -> int:
    idx = _load_or_build_index(args.path, use_cache=not args.no_cache)
    print(_tree(idx, root_rel=args.root, max_depth=args.max_depth))
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    variables = {}
    if args.var:
        for kv in args.var:
            if "=" not in kv:
                print(f"warning: ignoring malformed --var '{kv}' (expected key=value)", file=sys.stderr)
                continue
            k, v = kv.split("=", 1)
            variables[k] = v
    result = resolve(args.file, variables=variables or None)
    print(result["content"])
    for w in result["warnings"]:
        print(f"[{w['type'].upper()}] {w['message']}", file=sys.stderr)
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    """AI-powered dependency detection via Claude API (or host agent plan)."""
    extensions = None
    if args.ext:
        extensions = [(e if e.startswith(".") else f".{e}") for e in args.ext]

    # --dry-run: estimate cost without calling the API.
    if args.dry_run:
        est = _estimate_cost(args.path, model=args.model, extensions=extensions)
        if args.json:
            print(json.dumps(est, ensure_ascii=False, indent=2))
        else:
            print(_format_cost_estimate(est))
        return 0

    # --plan: emit a host-agent instruction pack instead of calling the API.
    if args.plan:
        print(_format_host_agent_plan(args.path, extensions=extensions))
        return 0

    # --apply-from <json>: skip the API call and apply a pre-computed result.
    if args.apply_from:
        try:
            result = _apply_analysis_from_file(args.path, args.apply_from)
        except FileNotFoundError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        if result["modified_files"]:
            print(f"Injected @include into {len(result['modified_files'])} file(s):")
            for f in result["modified_files"]:
                print(f"  {f}")
        if result["deps_yml"]:
            print(f"Wrote {result['deps_yml']}")
        if not result["modified_files"] and not result["deps_yml"]:
            print("No changes to apply.")
        return 0

    _load_dotenv()  # best-effort: read $CWD/.env if present

    try:
        analysis = _analyze_dependencies(
            args.path, extensions=extensions, model=args.model
        )
    except ValueError as e:  # missing API key
        print(f"error: {e}", file=sys.stderr)
        print(
            "hint: use `--plan` to get a no-API-key prompt pack instead.",
            file=sys.stderr,
        )
        return 2
    except RuntimeError as e:  # API or parse failure
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
    else:
        print(_format_proposal(analysis))

    if args.apply:
        result = _apply_analysis(args.path, analysis)
        if result["modified_files"]:
            print(f"\nInjected @include into {len(result['modified_files'])} file(s):")
            for f in result["modified_files"]:
                print(f"  {f}")
        if result["deps_yml"]:
            print(f"\nWrote {result['deps_yml']}")
        # Re-verify with the graph
        print("\n--- graph after apply ---")
        from dotmd_parser.parser import build_graph, summary  # local import
        graph = build_graph(args.path)
        print(summary(graph))
    return 0


def cmd_inventory(args: argparse.Namespace) -> int:
    """Report filesystem composition (API-free, no graph needed)."""
    try:
        inv = _inventory(args.path)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(inv, ensure_ascii=False, indent=2))
    else:
        print(_format_inventory(inv))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    graph = build_graph(args.path)
    print(summary(graph))
    if not args.quiet:
        print("\n--- JSON ---")
        print(json.dumps(graph, ensure_ascii=False, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dotmd-parser",
        description="Dependency graph parser for .md skill files.",
    )
    parser.add_argument("--version", action="version", version=f"dotmd-parser {__version__}")

    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="Install bundled SKILL.md into .claude/skills/dotmd-parser/")
    p_init.add_argument("path", nargs="?", default=".", help="Project root (default: current directory)")
    p_init.add_argument("--force", action="store_true", help="Overwrite an existing SKILL.md")
    p_init.set_defaults(func=cmd_init)

    p_index = sub.add_parser("index", help="Build and save .claude/dotmd-index.json")
    p_index.add_argument("path", help="Directory or SKILL.md")
    p_index.add_argument("--out", help="Override output path")
    p_index.add_argument(
        "--scope",
        metavar="SUBDIR",
        help="Incrementally re-index only SUBDIR, merging into the existing index",
    )
    p_index.set_defaults(func=cmd_index)

    p_check = sub.add_parser("check", help="Fail on cycles or missing references")
    p_check.add_argument("path", help="Directory or SKILL.md")
    p_check.set_defaults(func=cmd_check)

    p_affects = sub.add_parser("affects", help="List files transitively depending on <file>")
    p_affects.add_argument("path", help="Directory or SKILL.md")
    p_affects.add_argument("file", help="Relative path within the skill root")
    p_affects.add_argument("--no-cache", action="store_true", help="Force rebuild instead of using saved index")
    p_affects.set_defaults(func=cmd_affects)

    p_deps = sub.add_parser("deps", help="Direct dependencies of <file>")
    p_deps.add_argument("path", help="Directory or SKILL.md")
    p_deps.add_argument("file", help="Relative path within the skill root")
    p_deps.add_argument("--no-cache", action="store_true")
    p_deps.set_defaults(func=cmd_deps)

    p_digest = sub.add_parser("digest", help="Token-efficient summary for Claude")
    p_digest.add_argument("path", help="Directory or SKILL.md")
    p_digest.add_argument("--no-cache", action="store_true")
    p_digest.set_defaults(func=cmd_digest)

    p_tree = sub.add_parser("tree", help="ASCII dependency tree")
    p_tree.add_argument("path", help="Directory or SKILL.md")
    p_tree.add_argument("--root", help="Root file (defaults to the skill entry)")
    p_tree.add_argument("--max-depth", type=int, default=6)
    p_tree.add_argument("--no-cache", action="store_true")
    p_tree.set_defaults(func=cmd_tree)

    p_resolve = sub.add_parser("resolve", help="Expand @include directives")
    p_resolve.add_argument("file", help="Entry .md file")
    p_resolve.add_argument("--var", action="append", help="key=value placeholder substitution (repeatable)")
    p_resolve.set_defaults(func=cmd_resolve)

    p_analyze = sub.add_parser("analyze", help="AI dependency detection (requires Claude API key)")
    p_analyze.add_argument("path", help="Directory to scan")
    p_analyze.add_argument("--apply", action="store_true", help="Inject @include / write deps.yml")
    p_analyze.add_argument("--json", action="store_true", help="Emit JSON instead of formatted text")
    p_analyze.add_argument("--ext", action="append", help="File extension to include (repeatable; default: md, txt)")
    p_analyze.add_argument("--model", help="Claude model id (default: env CLAUDE_MODEL or claude-sonnet-4-5)")
    p_analyze.add_argument(
        "--plan",
        action="store_true",
        help="Emit a host-agent prompt pack instead of calling the API (no API key needed)",
    )
    p_analyze.add_argument(
        "--apply-from",
        metavar="JSON",
        help="Apply a pre-computed analysis JSON (pairs with --plan)",
    )
    p_analyze.add_argument(
        "--dry-run",
        action="store_true",
        help="Estimate token count and USD cost without calling the API",
    )
    p_analyze.set_defaults(func=cmd_analyze)

    p_inv = sub.add_parser(
        "inventory",
        help="Filesystem composition report (API-free; extension counts, sizes, markdown ratio)",
    )
    p_inv.add_argument("path", help="Directory to scan")
    p_inv.add_argument("--json", action="store_true", help="Emit JSON instead of formatted text")
    p_inv.set_defaults(func=cmd_inventory)

    p_show = sub.add_parser("show", help="Legacy summary + full JSON graph")
    p_show.add_argument("path", help="Directory or SKILL.md")
    p_show.add_argument("--quiet", action="store_true", help="Suppress JSON dump")
    p_show.set_defaults(func=cmd_show)

    return parser


def run(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args_list = list(sys.argv[1:] if argv is None else argv)

    # Backwards compatibility: `dotmd-parser <path>` with no subcommand → show
    known_cmds = {"init", "index", "check", "affects", "deps", "digest", "tree", "resolve", "analyze", "inventory", "show"}
    if args_list and args_list[0] not in known_cmds and not args_list[0].startswith("-"):
        args_list = ["show", *args_list]
    if not args_list:
        args_list = ["show", "."]

    args = parser.parse_args(args_list)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    rc = args.func(args)
    sys.exit(rc)


if __name__ == "__main__":
    run()
