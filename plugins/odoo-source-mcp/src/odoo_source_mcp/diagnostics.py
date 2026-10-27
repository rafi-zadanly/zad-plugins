"""Read-only, fixed Odoo shell programs and static source diagnostics."""

from pathlib import Path

from .imports import trace_imports
from .shell import execute


def source_diagnostic(index, model: str, limit: int = 50) -> dict:
    limit = max(1, min(limit, 100))
    classes = [item for item in index.classes if model in item["models"]]
    items = []
    for cls in classes[:limit]:
        path = Path(cls["path"])
        module_dir = next((root / cls["module"] for root in index.roots if path.is_relative_to(root / cls["module"])), None)
        manifest = next((m for m in index.modules if module_dir and m["manifest"] == str(module_dir / "__manifest__.py")), None)
        items.append({"module": cls["module"], "path": cls["path"], "line": cls["line"], "class": cls["class"],
                      "manifest": manifest, "import_trace": trace_imports(module_dir, path) if module_dir else None})
    return {"found": bool(classes) if classes or not index.errors else None, "total": len(classes), "items": items,
            "truncated": len(classes) > limit, "roots": [str(root) for root in index.roots],
            "parse_error_count": len(index.errors), "note": "Static source candidates; duplicate addon names and runtime load order are not resolved."}


def diagnose_runtime(runtime, model: str, module_names: list[str], timeout: int = 30, include_stderr: bool = False) -> dict:
    program = f'''_model = {model!r}
_modules = {module_names!r}
_result = {{"registry": {{"present": None}}, "metadata": {{"present": None, "records": [], "external_ids": None}}, "modules": {{"records": None}}}}
try:
    _result["registry"]["present"] = _model in env
except Exception as error:
    _result["registry"]["error"] = {{"type": type(error).__name__, "message": str(error)[:1000]}}
try:
    rows = env["ir.model"].search_read([("model", "=", _model)], ["id", "model", "name", "state"], limit=20)
    _result["metadata"]["present"] = bool(rows)
    _result["metadata"]["records"] = rows
except Exception as error:
    env.cr.rollback()
    _result["metadata"]["error"] = {{"type": type(error).__name__, "message": str(error)[:1000]}}
try:
    if _result["metadata"]["present"] is not None:
        ids = [row["id"] for row in _result["metadata"]["records"]]
        links = env["ir.model.data"].search_read([("model", "=", "ir.model"), ("res_id", "in", ids)], ["module", "name", "res_id"], limit=101) if ids else []
        _result["metadata"]["external_ids"] = links[:100]
        _result["metadata"]["external_ids_truncated"] = len(links) > 100
        _modules = sorted(set(_modules + [link["module"] for link in links[:100]]))
except Exception as error:
    env.cr.rollback()
    _result["metadata"]["external_ids_error"] = {{"type": type(error).__name__, "message": str(error)[:1000]}}
try:
    _result["modules"]["candidate_names"] = _modules
    _result["modules"]["records"] = env["ir.module.module"].search_read([("name", "in", _modules)], ["name", "state"], limit=200)
except Exception as error:
    env.cr.rollback()
    _result["modules"]["error"] = {{"type": type(error).__name__, "message": str(error)[:1000]}}
'''
    return execute(runtime, "internal diagnostic", timeout=timeout, include_stderr=include_stderr, _program=program)


def inspect_access(runtime, model: str, user_id: int, company_ids: list[int] | None, record_ids: list[int],
                   timeout: int = 30, include_stderr: bool = False, include_evidence: bool = False) -> dict:
    program = f'''_user_id = {user_id!r}
_requested_companies = {company_ids!r}
_ids = {record_ids!r}
_user = env["res.users"].browse(_user_id).exists()
if not _user:
    raise ValueError("User does not exist")
_companies = _requested_companies if _requested_companies is not None else [_user.company_id.id]
if not _companies or any(company not in _user.company_ids.ids for company in _companies):
    raise ValueError("All requested companies must belong to the user")
_records = env[{model!r}].with_user(_user_id).with_context({{}}, allowed_company_ids=_companies)
_acl = {{operation: _records.env["ir.model.access"].check(_records._name, operation, raise_exception=False) for operation in ("read", "create", "write", "unlink")}}
_existing = set(env[{model!r}].browse(_ids).exists().ids)
_rules = []
for record_id in _ids:
    item = {{"id": record_id, "exists": record_id in _existing, "record_rules": {{}}}}
    if item["exists"]:
        for operation in ("read", "write", "unlink"):
            if _records.env.su:
                item["record_rules"][operation] = True
            else:
                domain = _records.env["ir.rule"]._compute_domain(_records._name, operation)
                item["record_rules"][operation] = bool(_records.browse(record_id).sudo().filtered_domain(domain or []))
    _rules.append(item)
_result = {{"model": {model!r}, "user_id": _records.env.uid, "superuser_mode": _records.env.su,
           "company_id": _records.env.company.id, "allowed_company_ids": _records.env.companies.ids,
           "acl": _acl, "records": _rules,
           "note": "ACL and record rules are reported separately. Create rules, field access and custom business logic are not evaluated."}}
if {include_evidence!r}:
    _access = env["ir.model.access"].sudo().search([("model_id.model", "=", {model!r}), ("active", "=", True)], limit=51)
    _rules_found = env["ir.rule"].sudo().search([("model_id.model", "=", {model!r}), ("active", "=", True)], limit=51)
    _group_ids = set(_user.groups_id.ids)
    _result["evidence"] = {{
        "acl": [{{"id": a.id, "name": a.name, "group_id": a.group_id.id or None,
                  "user_group_match": not a.group_id or a.group_id.id in _group_ids,
                  "operations": {{op: bool(getattr(a, "perm_" + op)) for op in ("read", "create", "write", "unlink")}}}} for a in _access[:50]],
        "rules": [{{"id": r.id, "name": r.name, "group_ids": r.groups.ids[:50], "groups_truncated": len(r.groups.ids) > 50,
                    "user_group_match": not r.groups or bool(set(r.groups.ids) & _group_ids),
                    "domain": (r.domain_force or "")[:2000], "domain_truncated": len(r.domain_force or "") > 2000,
                    "operations": {{op: bool(getattr(r, "perm_" + op)) for op in ("read", "create", "write", "unlink")}}}} for r in _rules_found[:50]],
        "acl_truncated": len(_access) > 50, "rules_truncated": len(_rules_found) > 50,
        "note": "Potentially relevant active records; group matches and listings do not establish why access was granted or denied."}}
'''
    return execute(runtime, "internal access inspection", timeout=timeout, include_stderr=include_stderr, _program=program)


def inspect_runtime_fields(runtime, model, fields, user_id, company_ids, timeout=30, include_stderr=False):
    program = f'''import odoo.release
_model = {model!r}
_names = {fields!r}
_uid = {user_id!r}
_companies = {company_ids!r}
_user = env["res.users"].browse(_uid or env.uid).exists()
if not _user:
    raise ValueError("User does not exist")
_selected = _companies if _companies is not None else [_user.company_id.id]
if not _selected or any(c not in _user.company_ids.ids for c in _selected):
    raise ValueError("All requested companies must belong to the user")
_records = env[_model].with_user(_user.id).with_context({{}}, allowed_company_ids=_selected)
_attributes = ["type", "string", "help", "required", "readonly", "store", "relation", "selection"]
_metadata = _records.fields_get(_names, attributes=_attributes)
_result = {{"model": _model, "database": env.cr.dbname, "odoo_version": odoo.release.version, "user_id": _records.env.uid,
           "superuser_mode": _records.env.su, "company_id": _records.env.company.id,
           "allowed_company_ids": _records.env.companies.ids, "fields": _metadata,
           "not_returned": [name for name in _names if name not in _metadata],
           "note": "Runtime fields_get metadata in this user context; omitted fields may be missing or inaccessible."}}
'''
    return execute(runtime, "internal field inspection", timeout=timeout, include_stderr=include_stderr, _program=program)


def observations(source: dict, runtime: dict) -> list[dict]:
    findings = []
    if not runtime.get("ok"):
        return [{"kind": "runtime_unknown", "message": "Runtime could not be inspected; registry and database state are unknown."}]
    values = runtime["result"]
    registry = values["registry"]["present"]
    metadata = values["metadata"]["present"]
    if registry is False and metadata is True:
        findings.append({"kind": "metadata_without_registry", "message": "Model metadata exists but the model is absent from this shell registry. Possible causes include stale metadata, an unloaded addon or a different runtime configuration; investigate before concluding."})
    if source.get("found") and registry is False:
        findings.append({"kind": "source_without_registry", "message": "Source candidates exist but this shell registry does not contain the model. Import paths and module states are candidates to investigate."})
    if source.get("found") is False and registry is True:
        findings.append({"kind": "registry_without_indexed_source", "message": "The model is in the registry but no source was found in the scanned roots; source may be outside them or dynamic."})
    return findings
