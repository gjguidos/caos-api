from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse

from src.domains.agentverse.tools.web_crawler_tool.constants import (
    AUTH_KEYWORDS,
    CONTACT_KEYWORDS,
    NEWSLETTER_KEYWORDS,
    SEARCH_KEYWORDS,
    SENSITIVE_INPUT_TYPES,
    SENSITIVE_NAME_KEYWORDS,
)
from src.domains.agentverse.tools.web_crawler_tool.types import (
    FormControlItem,
    FormInventoryItem,
    FormInventoryReport,
)


def build_form_inventory_report(
    *,
    page_url: str,
    html_report: dict[str, Any] | None = None,
) -> FormInventoryReport:
    html_report = html_report or {}
    raw_forms = html_report.get("forms") or []

    forms: list[FormInventoryItem] = []

    for index, raw_form in enumerate(raw_forms, start=1):
        if not isinstance(raw_form, dict):
            continue

        form = classify_form(
            raw_form=raw_form,
            index=index,
            page_url=page_url,
        )
        forms.append(form)

    return FormInventoryReport(
        form_count=len(forms),
        total_controls=sum(form.control_count for form in forms),
        required_controls=sum(form.required_count for form in forms),
        unlabeled_controls=sum(form.unlabeled_count for form in forms),
        sensitive_controls=sum(form.sensitive_count for form in forms),
        search_forms=sum(1 for form in forms if form.form_role == "search"),
        auth_forms=sum(1 for form in forms if form.form_role == "auth"),
        newsletter_forms=sum(1 for form in forms if form.form_role == "newsletter"),
        contact_forms=sum(1 for form in forms if form.form_role == "contact"),
        file_upload_forms=sum(1 for form in forms if "file_upload" in form.risk_flags),
        external_action_forms=sum(1 for form in forms if "external_action" in form.risk_flags),
        forms=forms,
    )


def classify_form(
    *,
    raw_form: dict[str, Any],
    index: int,
    page_url: str,
) -> FormInventoryItem:
    method = str(raw_form.get("method") or "get").lower()
    action = raw_form.get("action")
    target = raw_form.get("target")
    enctype = raw_form.get("enctype")

    action_url = urljoin(page_url, str(action)) if action else page_url

    raw_controls = raw_form.get("controls") or []
    controls: list[FormControlItem] = []

    for raw_control in raw_controls:
        if not isinstance(raw_control, dict):
            continue

        control = classify_control(raw_control)
        controls.append(control)

    form_text = _join_form_text(raw_form=raw_form, controls=controls)
    form_role = infer_form_role(form_text=form_text, controls=controls)

    risk_flags = infer_form_risk_flags(
        page_url=page_url,
        action_url=action_url,
        method=method,
        enctype=enctype,
        controls=controls,
    )

    return FormInventoryItem(
        index=index,
        source_url=page_url,
        method=method,
        action=action,
        action_url=action_url,
        target=target,
        enctype=enctype,
        form_role=form_role,
        control_count=len(controls),
        required_count=sum(1 for control in controls if control.required),
        unlabeled_count=sum(1 for control in controls if _is_unlabeled(control)),
        sensitive_count=sum(1 for control in controls if control.risk_flags),
        risk_flags=risk_flags,
        controls=controls,
    )


def classify_control(raw_control: dict[str, Any]) -> FormControlItem:
    tag = str(raw_control.get("tag") or "").lower()
    input_type = str(raw_control.get("type") or "").lower() or None
    name = raw_control.get("name")
    control_id = raw_control.get("id")
    label = raw_control.get("label")
    placeholder = raw_control.get("placeholder")
    aria_label = raw_control.get("aria_label")
    required = bool(raw_control.get("required"))
    disabled = bool(raw_control.get("disabled"))
    autocomplete = raw_control.get("autocomplete")

    options = raw_control.get("options") or []
    options_count = len(options) if isinstance(options, list) else 0

    risk_flags = infer_control_risk_flags(
        tag=tag,
        input_type=input_type,
        name=name,
        label=label,
        placeholder=placeholder,
        autocomplete=autocomplete,
    )

    return FormControlItem(
        tag=tag,
        type=input_type,
        name=name,
        id=control_id,
        label=label,
        placeholder=placeholder,
        aria_label=aria_label,
        required=required,
        disabled=disabled,
        autocomplete=autocomplete,
        value_present=bool(raw_control.get("value")),
        options_count=options_count,
        control_role=infer_control_role(
            tag=tag,
            input_type=input_type,
            name=name,
            label=label,
            placeholder=placeholder,
        ),
        risk_flags=risk_flags,
        metadata={
            "raw_tag": tag,
        },
    )


def infer_control_role(
    *,
    tag: str,
    input_type: str | None,
    name: str | None,
    label: str | None,
    placeholder: str | None,
) -> str:
    haystack = " ".join(
        str(value or "").lower()
        for value in [input_type, name, label, placeholder]
    )

    if input_type == "password" or "password" in haystack:
        return "password"

    if input_type == "email" or "email" in haystack:
        return "email"

    if input_type == "tel" or "phone" in haystack or "mobile" in haystack:
        return "phone"

    if input_type == "file":
        return "file_upload"

    if input_type in {"submit", "button"} or tag == "button":
        return "submit_or_button"

    if any(keyword in haystack for keyword in SEARCH_KEYWORDS):
        return "search"

    if tag == "select":
        return "select"

    if tag == "textarea":
        return "textarea"

    return "input"


def infer_form_role(
    *,
    form_text: str,
    controls: list[FormControlItem],
) -> str:
    haystack = form_text.lower()

    control_roles = {control.control_role for control in controls}

    if "password" in control_roles:
        return "auth"

    if any(keyword in haystack for keyword in AUTH_KEYWORDS):
        return "auth"

    if any(keyword in haystack for keyword in NEWSLETTER_KEYWORDS):
        return "newsletter"

    if any(keyword in haystack for keyword in CONTACT_KEYWORDS):
        return "contact"

    if "search" in control_roles or any(keyword in haystack for keyword in SEARCH_KEYWORDS):
        return "search"

    if "file_upload" in control_roles:
        return "upload"

    return "generic"


def infer_control_risk_flags(
    *,
    tag: str,
    input_type: str | None,
    name: str | None,
    label: str | None,
    placeholder: str | None,
    autocomplete: str | None,
) -> list[str]:
    flags: list[str] = []

    haystack = " ".join(
        str(value or "").lower()
        for value in [input_type, name, label, placeholder, autocomplete]
    )

    if input_type in SENSITIVE_INPUT_TYPES:
        flags.append(f"sensitive_type:{input_type}")

    if any(keyword in haystack for keyword in SENSITIVE_NAME_KEYWORDS):
        flags.append("sensitive_name_or_label")

    if tag == "input" and not any([label, placeholder, autocomplete]):
        flags.append("low_context_input")

    return _dedupe(flags)


def infer_form_risk_flags(
    *,
    page_url: str,
    action_url: str,
    method: str,
    enctype: str | None,
    controls: list[FormControlItem],
) -> list[str]:
    flags: list[str] = []

    page_domain = urlparse(page_url).netloc.lower()
    action_domain = urlparse(action_url).netloc.lower()

    if action_domain and action_domain != page_domain:
        flags.append("external_action")

    if method == "get" and any(control.risk_flags for control in controls):
        flags.append("sensitive_fields_with_get_method")

    if any(control.control_role == "file_upload" for control in controls):
        flags.append("file_upload")

    if enctype and "multipart" in enctype.lower():
        flags.append("multipart_form")

    if any(control.control_role == "password" for control in controls):
        flags.append("password_field")

    return _dedupe(flags)


def _is_unlabeled(control: FormControlItem) -> bool:
    if control.tag in {"button"}:
        return False

    if control.type in {"hidden", "submit", "button"}:
        return False

    return not any([control.label, control.aria_label, control.placeholder])


def _join_form_text(
    *,
    raw_form: dict[str, Any],
    controls: list[FormControlItem],
) -> str:
    parts: list[str] = []

    for key in ["method", "action", "target", "enctype"]:
        if raw_form.get(key):
            parts.append(str(raw_form.get(key)))

    for control in controls:
        parts.extend(
            [
                control.tag,
                control.type or "",
                control.name or "",
                control.id or "",
                control.label or "",
                control.placeholder or "",
                control.aria_label or "",
                control.autocomplete or "",
                control.control_role,
            ]
        )

    return " ".join(part for part in parts if part)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)

    return result