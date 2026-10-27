"""Run an ORM expression in the configured project's Odoo shell."""

from __future__ import annotations

import ast
import json
import os
import signal
import subprocess
import tempfile
import uuid
from pathlib import Path

from .runtime import Runtime

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


def execute(runtime: Runtime, expression: str, allow_mutation: bool = False, commit: bool = False, timeout: int = 30) -> dict:
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 16000:
        raise ValueError("expression must contain 1 to 16000 characters")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 120:
        raise ValueError("timeout must be between 1 and 120 seconds")
    if commit and not allow_mutation:
        raise ValueError("commit requires allow_mutation=true")
    if not allow_mutation:
        validate_read_expression(expression)
    else:
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
    _value = eval(compile(_expr, "<odoo-mcp>", "eval"), {{"__builtins__": {{}}}}, {{"env": env}})
    _encoded = _json.dumps(_value, default=_serialize, ensure_ascii=True, allow_nan=False)
    if len(_encoded) > 100000:
        raise ValueError("Result exceeds 100000 characters; narrow the query")
    if _commit:
        env.cr.commit()
    print(_marker + _json.dumps({{"ok": True, "result": _json.loads(_encoded), "committed": _commit}}, ensure_ascii=True))
except Exception as _error:
    env.cr.rollback()
    print(_marker + _json.dumps({{"ok": False, "error": {{"type": type(_error).__name__, "message": str(_error)[:2000]}}, "committed": None if _commit else False}}, ensure_ascii=True))
finally:
    _cursor_type.commit = _original_commit
'''
    args = [str(runtime.python), str(runtime.executable), "shell", "-c", str(runtime.config), "-d", runtime.database]
    if runtime.addons:
        args += ["--addons-path", ",".join(str(path) for path in runtime.addons)]
    environment = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONSTARTUP"):
        environment.pop(key, None)
    environment["PATH"] = str(runtime.python.parent) + os.pathsep + environment.get("PATH", "")
    if not allow_mutation:
        environment["PGOPTIONS"] = (environment.get("PGOPTIONS", "") + " -c default_transaction_read_only=on").strip()
    try:
        # Files avoid buffering unlimited addon output in the MCP process's memory.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, cwd=runtime.workspace,
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
    except OSError as exc:
        return {"ok": False, "error": {"type": "LaunchError", "message": str(exc)}, "committed": False}
    matches = [line[len(marker):] for line in output.splitlines() if line.startswith(marker)]
    if not matches or process.returncode != 0:
        return {"ok": False, "error": {"type": "ShellError", "message": f"Odoo shell failed or returned no result (exit {process.returncode}); check the runtime and Odoo logs."}, "committed": None if commit else False}
    try:
        return json.loads(matches[-1])
    except json.JSONDecodeError:
        return {"ok": False, "error": {"type": "ProtocolError", "message": "Invalid result from Odoo shell"}, "committed": None if commit else False}
