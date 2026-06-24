"""CLI entrypoint — ``pydantic-zod-codegen <command>``.

Subcommands:
    generate     — run the full pipeline, write output to file
    check-drift  — CI hook: regenerate and diff against committed file
    doctor       — verify runtime prereqs (bun, json2ts cache)

Invoked via ``[project.scripts]`` in pyproject.toml. Also runnable as
``uvx --from . pydantic-zod-codegen ...`` or ``python -m pydantic_zod_codegen.cli``.

EXIT CODES
==========
- ``0`` — success / no drift / prereqs met
- ``1`` — drift detected / pipeline error
- ``2`` — usage error (handled by argparse) / unexpected error during check-drift

REFERENCE
=========
- the drift-gate design (see ARCHITECTURE.md)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from pydantic_zod_codegen.drift import check_drift, regenerate
from pydantic_zod_codegen.emitter import doctor


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pydantic-zod-codegen",
        description="Pydantic v2.13 -> JSON-Schema -> Zod v4 codegen pipeline.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="<command>")

    gen = sub.add_parser(
        "generate",
        help="Run the pipeline against a Pydantic models module and write the .gen.ts output.",
    )
    gen.add_argument(
        "module",
        help="dotted import path of a Pydantic models module (e.g. myapp.protocol).",
    )
    gen.add_argument(
        "-o",
        "--output",
        required=True,
        help="path to write the generated .gen.ts file.",
    )

    chk = sub.add_parser(
        "check-drift",
        help="CI gate: regenerate and diff against the committed .gen.ts file.",
    )
    chk.add_argument(
        "module",
        help="dotted import path of a Pydantic models module.",
    )
    chk.add_argument(
        "committed",
        help="path to the committed .gen.ts file to compare against.",
    )

    sub.add_parser(
        "doctor",
        help="Check runtime prerequisites (bun, json2ts).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entrypoint for the ``pydantic-zod-codegen`` console script.

    Args:
        argv: optional argv override (for tests). Default = ``sys.argv[1:]``.

    Returns:
        Process exit code. ``0`` = ok, ``1`` = drift / pipeline error,
        ``2`` = unexpected exception in check-drift (or argparse usage error).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Resolve `module` against the user's working directory — `uv run` does not
    # honor pytest's pythonpath, so a CLI invocation that references a project-
    # local module (e.g. `tests.goldenfile.fixtures.models`) would otherwise raise
    # `ModuleNotFoundError`. Prepending cwd is the KISS equivalent of running the
    # CLI under `python -m`.
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    if args.cmd == "generate":
        try:
            regenerate(args.module, Path(args.output))
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Generated: {args.output}")
        return 0

    if args.cmd == "check-drift":
        try:
            drift_detected, diff_text = check_drift(args.module, Path(args.committed))
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if drift_detected:
            print(diff_text, end="")
            return 1
        print("No drift detected.")
        return 0

    if args.cmd == "doctor":
        diagnostics = doctor()
        if not diagnostics:
            print("All prerequisites met.")
            return 0
        for line in diagnostics:
            print(line)
        return 1

    return 0  # unreachable: argparse `required=True` rejects missing subcommand


if __name__ == "__main__":
    raise SystemExit(main())
