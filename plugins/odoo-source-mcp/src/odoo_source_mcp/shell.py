"""Run an ORM expression in the configured project's Odoo shell."""

from __future__ import annotations

import ast
import json
import os
import re
import signal
import subprocess
import tempfile
import uuid
from .runtime import Runtime, shell_environment

READ_METHODS = {"browse", "search", "search_count", "search_read", "read", "fields_get", "name_get", "exists", "mapped"}
READ_PROPERTIES = {"id", "ids"}


def validate_read_expression(expression: str) -> None:
    """Only allow literal arguments and a chain of common ORM read operations."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid Python expression: {exc.msg}") from exc
    if sum(1 for _ in ast.walk(tree)) > 500:
        raise ValueError("Expression is too complex")

    def check(node):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "env":
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str) and node.slice.value:
                return
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in READ_METHODS:
            check(node.func.value)
            try:
                for arg in node.args:
                    ast.literal_eval(arg)
                for keyword in node.keywords:
                    if keyword.arg is None:
                        raise ValueError("unpacking is not allowed")
                    ast.literal_eval(keyword.value)
            except (ValueError, TypeError, SyntaxError) as exc:
                raise ValueError("Read-only ORM method arguments must be literals") from exc
            return
        if isinstance(node, ast.Attribute) and node.attr in READ_PROPERTIES:
            check(node.value)
            return
        raise ValueError("Read-only mode allows only env['model'] and common read methods with literal arguments; mutations require allow_mutation=true")

    check(tree.body)


def _diagnostics(stderr: str, include_stderr: bool) -> dict:
    lower = stderr.lower()
    if any(term in lower for term in ("operationalerror", "connection refused", "could not connect to server", "password authentication failed")):
        stage = "database_connection"
    elif any(term in lower for term in ("config file", "configuration file", "unrecognized arguments", "no such option")):
        stage = "configuration"
    else:
        stage = "odoo_startup"
    info = {"stage": stage, "classification": "heuristic"}
    if include_stderr:
        # This removes common forms only; arbitrary addon logs may contain secrets.
        snippet = stderr[-2000:]
        snippet = re.sub(r"(?i)(password|passwd|token|secret|api[_-]?key)(\s*[:=]\s*)([^\s,;]+)", r"\1\2[REDACTED]", snippet)
        snippet = re.sub(r"(://[^:/\s]+:)[^@\s]+(@)", r"\1[REDACTED]\2", snippet)
        info["stderr_excerpt"] = snippet
        info["stderr_note"] = "Best-effort redaction only; addon logs may contain sensitive values."
    return info


def execute(runtime: Runtime, expression: str, allow_mutation: bool = False, commit: bool = False, timeout: int = 30,
            include_stderr: bool = False, *, _program: str | None = None) -> dict:
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 16000:
        raise ValueError("expression must contain 1 to 16000 characters")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 120:
        raise ValueError("timeout must be between 1 and 120 seconds")
    if commit and not allow_mutation:
        raise ValueError("commit requires allow_mutation=true")
    if _program is not None and (allow_mutation or commit):
        raise ValueError("Internal diagnostics must be read-only")
    if not allow_mutation and _program is None:
        validate_read_expression(expression)
    elif allow_mutation:
        try:
            ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ValueError(f"Invalid Python expression: {exc.msg}") from exc

    marker = "ODOO_MCP_" + uuid.uuid4().hex + ":"
    # stdin is a Python program, never a shell command. repr safely embeds user input.
    script = f'''import json as _json
import datetime as _datetime
import decimal as _decimal
_marker = {marker!r}
_expr = {expression!r}
_program = {_program!r}
_commit = {commit!r}
_cursor_type = type(env.cr)
_original_commit = _cursor_type.commit

def _blocked_commit(self, *args, **kwargs):
    raise RuntimeError("Commit is disabled for this call")

def _serialize(value):
    if hasattr(value, "_name") and hasattr(value, "ids"):
        return {{"model": value._name, "ids": value.ids}}
    if isinstance(value, (_datetime.date, _datetime.datetime, _datetime.time)):
        return value.isoformat()
    if isinstance(value, _decimal.Decimal):
        return str(value)
    raise TypeError("Result contains a non-JSON value: " + type(value).__name__)

try:
    if not _commit:
        _cursor_type.commit = _blocked_commit
    if _program is None:
        _value = eval(compile(_expr, "<odoo-mcp>", "eval"), {{"__builtins__": {{}}}}, {{"env": env}})
    else:
        _scope = {{"env": env}}
        exec(compile(_program, "<odoo-mcp-diagnostic>", "exec"), _scope)
        _value = _scope["_result"]
    _encoded = _json.dumps(_value, default=_serialize, ensure_ascii=True, allow_nan=False)
    if len(_encoded) > 100000:
        raise ValueError("Result exceeds 100000 characters; narrow the query")
    if _commit:
        env.cr.commit()
    print(_marker + _json.dumps({{"ok": True, "result": _json.loads(_encoded), "committed": _commit}}, ensure_ascii=True))
except Exception as _error:
    env.cr.rollback()
    print(_marker + _json.dumps({{"ok": False, "error": {{"type": type(_error).__name__, "message": str(_error)[:2000], "stage": "expression"}}, "committed": None if _commit else False}}, ensure_ascii=True))
finally:
    _cursor_type.commit = _original_commit
'''
    args = [str(runtime.python), str(runtime.executable), "shell", "-c", str(runtime.config), "-d", runtime.database]
    if runtime.addons:
        args += ["--addons-path", ",".join(str(path) for path in runtime.addons)]
    environment = shell_environment(runtime.python)
    if not allow_mutation:
        environment["PGOPTIONS"] = (environment.get("PGOPTIONS", "") + " -c default_transaction_read_only=on").strip()
    try:
        # Files avoid buffering unlimited addon output in the MCP process's memory.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, cwd=runtime.working_directory,
                                       env=environment, start_new_session=(os.name == "posix"))
            try:
                process.communicate(script.encode("utf-8"), timeout=timeout)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.communicate()
                return {"ok": False, "error": {"type": "Timeout", "message": f"Odoo shell exceeded {timeout} seconds"}, "committed": None if commit else False}
            stdout.seek(0, os.SEEK_END)
            size = stdout.tell()
            stdout.seek(max(0, size - 120000))
            output = stdout.read().decode("utf-8", errors="replace")
            stderr.seek(0, os.SEEK_END)
            stderr.seek(max(0, stderr.tell() - 16000))
            stderr_output = stderr.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "error": {"type": "LaunchError", "stage": "launch", "message": str(exc)}, "committed": False}
    matches = [line[len(marker):] for line in output.splitlines() if line.startswith(marker)]
    if not matches or process.returncode != 0:
        return {"ok": False, "error": {"type": "ShellError", "message": f"Odoo shell failed or returned no result (exit {process.returncode}); check the runtime and Odoo logs.", **_diagnostics(stderr_output, include_stderr)}, "committed": None if commit else False}
    try:
        return json.loads(matches[-1])
    except json.JSONDecodeError:
        return {"ok": False, "error": {"type": "ProtocolError", "message": "Invalid result from Odoo shell"}, "committed": None if commit else False}
