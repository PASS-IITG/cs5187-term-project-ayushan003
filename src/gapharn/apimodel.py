"""Stage 1: build an API model of a C library from its public headers.

Extracts, using libclang:
  * function declarations (return type, parameters, variadic, deprecated, doc comment)
  * enums and their constants
  * integer-valued object-like macros (including simple expressions such as
    `(ARCHIVE_FORMAT_TAR | 1)`), so generators can pick legal flag/enum values
  * opaque structs (declared but never defined in the headers) = library handles
  * typedefs

Usage:
    python -m gapharn.apimodel <lib>                       # uses targets/<lib>/config.yaml
    python -m gapharn.apimodel --headers a.h b.h -I inc --out api.json
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import clang.cindex as ci

# ---------------------------------------------------------------- macro values

_INT_RE = re.compile(r"^(0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)[uUlL]*$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_OPERATORS = {"(", ")", "|", "&", "^", "+", "-", "*", "<<", ">>", "~"}
_BINOPS = (ast.BitOr, ast.BitAnd, ast.BitXor, ast.Add, ast.Sub, ast.Mult, ast.LShift, ast.RShift)
_UNOPS = (ast.USub, ast.UAdd, ast.Invert)


def c_int(token: str) -> int | None:
    """Value of a C integer literal token (hex, octal, decimal, with U/L suffixes)."""
    m = _INT_RE.match(token)
    if not m:
        return None
    s = m.group(1)
    if s[:2] in ("0x", "0X"):
        return int(s, 16)
    if len(s) > 1 and s[0] == "0":
        return int(s, 8)
    return int(s)


def _safe_eval(node: ast.AST) -> int:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, _BINOPS):
        left, right = _safe_eval(node.left), _safe_eval(node.right)
        return {
            ast.BitOr: lambda: left | right,
            ast.BitAnd: lambda: left & right,
            ast.BitXor: lambda: left ^ right,
            ast.Add: lambda: left + right,
            ast.Sub: lambda: left - right,
            ast.Mult: lambda: left * right,
            ast.LShift: lambda: left << right,
            ast.RShift: lambda: left >> right,
        }[type(node.op)]()
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, _UNOPS):
        v = _safe_eval(node.operand)
        return {ast.USub: -v, ast.UAdd: v, ast.Invert: ~v}[type(node.op)]
    raise ValueError("unsupported expression")


def eval_macro_body(tokens: list[str], known: dict[str, int]) -> int | None:
    """Evaluate an integer macro body; None if it is not a plain integer expression."""
    if not tokens:
        return None
    parts = []
    for tok in tokens:
        value = c_int(tok)
        if value is not None:
            parts.append(str(value))
        elif tok in _OPERATORS:
            parts.append(tok)
        elif _IDENT_RE.match(tok) and tok in known:
            parts.append(f"({known[tok]})")
        else:
            return None
    try:
        return _safe_eval(ast.parse(" ".join(parts), mode="eval"))
    except (SyntaxError, ValueError, ZeroDivisionError):
        return None


def resolve_macros(bodies: dict[str, list[str]]) -> dict[str, int]:
    """Resolve macros that may refer to each other (iterate until no progress)."""
    known: dict[str, int] = {}
    pending = dict(bodies)
    progress = True
    while pending and progress:
        progress = False
        for name, body in list(pending.items()):
            value = eval_macro_body(body, known)
            if value is not None:
                known[name] = value
                del pending[name]
                progress = True
    return dict(sorted(known.items()))


# ------------------------------------------------------------------- libclang


def clang_resource_include() -> list[str]:
    """`-isystem <resource>/include` so libclang finds stddef.h & co.

    The pip `libclang` wheel ships without clang's builtin headers; borrow them
    from the system clang (same major version recommended).
    """
    clang = shutil.which("clang")
    if not clang:
        return []
    try:
        res = subprocess.run([clang, "-print-resource-dir"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return []
    inc = Path(res.stdout.strip()) / "include"
    return ["-isystem", str(inc)] if inc.is_dir() else []


def _type_info(t: ci.Type) -> dict:
    canonical = t.get_canonical()
    depth, pointee = 0, canonical
    while pointee.kind == ci.TypeKind.POINTER:
        depth += 1
        pointee = pointee.get_pointee()
    return {
        "type": t.spelling,
        "canonical": canonical.spelling,
        "kind": canonical.kind.name,
        "pointer_depth": depth,
        "pointee_const": depth > 0 and pointee.is_const_qualified(),
    }


def _is_function_like_macro(cursor: ci.Cursor, tokens: list[ci.Token]) -> bool:
    method = getattr(cursor, "is_macro_function_like", None)
    if method is not None:
        try:
            return bool(method())
        except Exception:  # older bindings without the C entry point
            pass
    return (
        len(tokens) > 1
        and tokens[1].spelling == "("
        and tokens[1].extent.start.offset == tokens[0].extent.end.offset
    )


def _anonymous(spelling: str) -> bool:
    return not spelling or "(unnamed" in spelling or "(anonymous" in spelling


def parse_headers(
    headers: list[Path],
    include_dirs: list[Path] = (),
    defines: list[str] = (),
    extra_args: list[str] = (),
    rel_to: Path | None = None,
) -> dict:
    headers = [Path(h).resolve() for h in headers]
    missing = [str(h) for h in headers if not h.is_file()]
    if missing:
        raise FileNotFoundError(f"headers not found: {missing}")
    header_set = {str(h) for h in headers}

    def rel(p: Path) -> str:
        if rel_to is not None:
            try:
                return Path(p).resolve().relative_to(Path(rel_to).resolve()).as_posix()
            except ValueError:
                pass
        return Path(p).name

    args = ["-x", "c", "-std=gnu11", *clang_resource_include()]
    args += [f"-I{d}" for d in include_dirs]
    args += [f"-D{d}" for d in defines]
    args += list(extra_args)
    umbrella = "gapharn_umbrella.c"
    content = "".join(f'#include "{h.as_posix()}"\n' for h in headers)
    options = (
        ci.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
        | ci.TranslationUnit.PARSE_SKIP_FUNCTION_BODIES
    )
    tu = ci.Index.create().parse(umbrella, args=args, unsaved_files=[(umbrella, content)], options=options)

    diagnostics = [
        f"{d.location.file}:{d.location.line}: {d.spelling}"
        for d in tu.diagnostics
        if d.severity >= ci.Diagnostic.Error
    ]

    functions: dict[str, dict] = {}
    enums: dict[str, dict[str, int]] = {}
    structs: dict[str, bool] = {}  # name -> has definition
    typedefs: dict[str, str] = {}
    macro_bodies: dict[str, list[str]] = {}

    def in_headers(c: ci.Cursor) -> bool:
        f = c.location.file
        return f is not None and str(Path(f.name).resolve()) in header_set

    def visit(c: ci.Cursor) -> None:
        kind = c.kind
        if kind == ci.CursorKind.LINKAGE_SPEC:
            for child in c.get_children():
                visit(child)
            return
        if not in_headers(c):
            return
        if kind == ci.CursorKind.FUNCTION_DECL:
            if c.spelling in functions:
                return
            ftype = c.type
            functions[c.spelling] = {
                "name": c.spelling,
                "return": _type_info(c.result_type),
                "params": [{"name": a.spelling, **_type_info(a.type)} for a in c.get_arguments()],
                "variadic": ftype.kind == ci.TypeKind.FUNCTIONPROTO and ftype.is_function_variadic(),
                "deprecated": c.availability == ci.AvailabilityKind.DEPRECATED,
                "header": rel(c.location.file.name),
                "line": c.location.line,
                "comment": c.raw_comment,
            }
        elif kind == ci.CursorKind.ENUM_DECL:
            name = c.spelling if not _anonymous(c.spelling) else f"<anonymous@{rel(c.location.file.name)}:{c.location.line}>"
            consts = {e.spelling: e.enum_value for e in c.get_children() if e.kind == ci.CursorKind.ENUM_CONSTANT_DECL}
            if consts:
                enums.setdefault(name, {}).update(consts)
        elif kind in (ci.CursorKind.STRUCT_DECL, ci.CursorKind.UNION_DECL):
            if not _anonymous(c.spelling):
                structs[c.spelling] = structs.get(c.spelling, False) or c.is_definition() or c.get_definition() is not None
        elif kind == ci.CursorKind.TYPEDEF_DECL:
            typedefs[c.spelling] = c.underlying_typedef_type.spelling
        elif kind == ci.CursorKind.MACRO_DEFINITION:
            tokens = list(c.get_tokens())
            if tokens and not _is_function_like_macro(c, tokens):
                macro_bodies[c.spelling] = [t.spelling for t in tokens[1:]]

    for cursor in tu.cursor.get_children():
        visit(cursor)

    return {
        "headers": [rel(h) for h in headers],
        "diagnostics": diagnostics,
        "functions": sorted(functions.values(), key=lambda f: f["name"]),
        "enums": dict(sorted(enums.items())),
        "int_macros": resolve_macros(macro_bodies),
        "opaque_structs": sorted(n for n, defined in structs.items() if not defined),
        "typedefs": dict(sorted(typedefs.items())),
    }


def summary(model: dict) -> str:
    fns = model["functions"]
    return (
        f"{len(fns)} functions ({sum(f['deprecated'] for f in fns)} deprecated, "
        f"{sum(f['variadic'] for f in fns)} variadic), {len(model['enums'])} enums, "
        f"{len(model['int_macros'])} integer macros, {len(model['opaque_structs'])} opaque structs, "
        f"{len(model['diagnostics'])} parse errors"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lib", nargs="?", help="target name (targets/<lib>/config.yaml)")
    ap.add_argument("--headers", nargs="+", type=Path, help="public headers (instead of a target)")
    ap.add_argument("-I", dest="include_dirs", action="append", type=Path, default=[])
    ap.add_argument("-D", dest="defines", action="append", default=[])
    ap.add_argument("--out", type=Path, help="output JSON (default: results/<lib>/api.json)")
    a = ap.parse_args(argv)

    if a.lib:
        from gapharn.config import load_target

        t = load_target(a.lib)
        headers = t.header_paths()
        if not headers:
            raise SystemExit(f"[apimodel] no headers matched {t.headers} under {t.header_root} (built yet?)")
        model = parse_headers(headers, t.include_paths(), t.defines, rel_to=t.header_root)
        out = a.out or t.results_dir / "api.json"
    elif a.headers:
        model = parse_headers(a.headers, a.include_dirs, a.defines)
        out = a.out or Path("api.json")
    else:
        ap.error("give a target name or --headers")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(model, indent=1) + "\n")
    print(f"[apimodel] {summary(model)} -> {out}")
    for d in model["diagnostics"][:10]:
        print(f"  parse error: {d}", file=sys.stderr)
    return 1 if model["diagnostics"] else 0


if __name__ == "__main__":
    sys.exit(main())
