# src/domains/agentverse/tools/math_tool.py
from __future__ import annotations

import ast
import logging
import math
import re
from datetime import UTC, datetime
from decimal import Decimal, getcontext
from typing import Any

from pydantic import Field

from src.base.handlers.exception import classify_error
from src.domains.agentverse.registries import tool_registry_instance as tool_registry
from src.domains.agentverse.tools.base import BaseTool, ToolConfig, ToolExecutionError

logger = logging.getLogger("agentverse.tools.math_tool")

# Regex cues for internal expression-type detection (not invocation triggers)
RE_ARITH_EXPR = r"(?=.*\d)(?=.*[+\-*/^()])"
RE_CALC_CUES = r"\b(d/dx|derivative|differentiate|integral|integrate|∫)\b"
RE_UNIT_CONVERT = (
    r"\b\d+(?:\.\d+)?\s*"
    r"(km|kilometers?|mi|miles?|m|meters?|cm|mm|in|inch(?:es)?|ft|feet|"
    r"kg|kilograms?|g|grams?|lb|pounds?|°c|celsius|°f|fahrenheit)"
    r"\s+to\s+"
    r"(km|kilometers?|mi|miles?|m|meters?|cm|mm|in|inch(?:es)?|ft|feet|"
    r"kg|kilograms?|g|grams?|lb|pounds?|°c|celsius|°f|fahrenheit)\b"
)

# --------------------------- sympy (optional) -------------------
try:
    import sympy as sp
    from sympy.parsing.sympy_parser import (
        convert_xor,
        implicit_multiplication_application,
        parse_expr,
        standard_transformations,
    )

    HAS_SYMPY = True
except Exception:
    HAS_SYMPY = False


# --------------------------- helpers ----------------------------
def _usage_addons(start: datetime, end: datetime) -> dict[str, Any]:
    return {
        "usage": {
            "latency_ms": int((end - start).total_seconds() * 1000),
            "model": None,
            "provider": "tools:math",
            "tokens": {"input": 0, "output": 0, "total": 0},
            "context_tokens": 0,
            "estimated": True,
            "token_breakdown": {"prompt_total_est": 0},
        }
    }


_UNIT_ALIASES = {
    "km": "km",
    "kilometer": "km",
    "kilometers": "km",
    "mi": "mi",
    "mile": "mi",
    "miles": "mi",
    "m": "m",
    "meter": "m",
    "meters": "m",
    "cm": "cm",
    "mm": "mm",
    "in": "in",
    "inch": "in",
    "inches": "in",
    "ft": "ft",
    "feet": "ft",
    "foot": "ft",
    "kg": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "g": "g",
    "gram": "g",
    "grams": "g",
    "lb": "lb",
    "pound": "lb",
    "pounds": "lb",
    "°c": "c",
    "celsius": "c",
    "c": "c",
    "°f": "f",
    "fahrenheit": "f",
    "f": "f",
}


def _to_base_len(value: float, unit: str) -> float:
    if unit == "km":
        return value * 1000.0
    if unit == "m":
        return value
    if unit == "cm":
        return value / 100.0
    if unit == "mm":
        return value / 1000.0
    if unit == "mi":
        return value * 1609.344
    if unit == "ft":
        return value * 0.3048
    if unit == "in":
        return value * 0.0254
    raise ToolExecutionError(f"Unsupported length unit: {unit}")


def _from_base_len(meters: float, unit: str) -> float:
    if unit == "km":
        return meters / 1000.0
    if unit == "m":
        return meters
    if unit == "cm":
        return meters * 100.0
    if unit == "mm":
        return meters * 1000.0
    if unit == "mi":
        return meters / 1609.344
    if unit == "ft":
        return meters / 0.3048
    if unit == "in":
        return meters / 0.0254
    raise ToolExecutionError(f"Unsupported length unit: {unit}")


def _to_base_mass(value: float, unit: str) -> float:
    if unit == "kg":
        return value
    if unit == "g":
        return value / 1000.0
    if unit == "lb":
        return value * 0.45359237
    raise ToolExecutionError(f"Unsupported mass unit: {unit}")


def _from_base_mass(kg: float, unit: str) -> float:
    if unit == "kg":
        return kg
    if unit == "g":
        return kg * 1000.0
    if unit == "lb":
        return kg / 0.45359237
    raise ToolExecutionError(f"Unsupported mass unit: {unit}")


def _convert_temp(value: float, from_u: str, to_u: str) -> float:
    if from_u == "c" and to_u == "f":
        return (value * 9 / 5) + 32
    if from_u == "f" and to_u == "c":
        return (value - 32) * 5 / 9
    if from_u == to_u:
        return value
    raise ToolExecutionError(f"Unsupported temperature conversion: {from_u} to {to_u}")


def convert_units(value: float, from_unit_raw: str, to_unit_raw: str) -> dict[str, Any]:
    fu = _UNIT_ALIASES.get(from_unit_raw.lower(), from_unit_raw.lower())
    tu = _UNIT_ALIASES.get(to_unit_raw.lower(), to_unit_raw.lower())
    if fu in {"c", "f"} or tu in {"c", "f"}:
        return {
            "value": _convert_temp(value, fu, tu),
            "from_unit": fu,
            "to_unit": tu,
            "category": "temperature",
        }
    if fu in {"km", "m", "cm", "mm", "mi", "ft", "in"} and tu in {
        "km",
        "m",
        "cm",
        "mm",
        "mi",
        "ft",
        "in",
    }:
        return {
            "value": _from_base_len(_to_base_len(value, fu), tu),
            "from_unit": fu,
            "to_unit": tu,
            "category": "length",
        }
    if fu in {"kg", "g", "lb"} and tu in {"kg", "g", "lb"}:
        return {
            "value": _from_base_mass(_to_base_mass(value, fu), tu),
            "from_unit": fu,
            "to_unit": tu,
            "category": "mass",
        }
    raise ToolExecutionError(f"Unsupported unit conversion: {from_unit_raw} → {to_unit_raw}")


# ---- safe arithmetic fallback (if SymPy unavailable) ----
_ALLOWED_AST_NODES = {
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Num,
    ast.Constant,
    ast.Load,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Mod,
    ast.FloorDiv,
    ast.Call,
    ast.Name,
    ast.Tuple,
    ast.List,
}
_ALLOWED_FUNCS = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "pi": math.pi,
    "e": math.e,
    "abs": abs,
}


def _safe_eval(expr: str, precision: int) -> float:
    getcontext().prec = max(10, min(precision, 100))
    node = ast.parse(expr, mode="eval")
    for n in ast.walk(node):
        if type(n) not in _ALLOWED_AST_NODES:
            raise ToolExecutionError(f"Unsupported expression element: {type(n).__name__}")
        if isinstance(n, ast.Call):
            if not isinstance(n.func, ast.Name) or n.func.id not in _ALLOWED_FUNCS:
                raise ToolExecutionError("Only basic math functions are allowed")
    return float(eval(compile(node, "<ast>", "eval"), {"__builtins__": {}}, _ALLOWED_FUNCS))


# --------------------------- config -----------------------------
class MathToolConfig(ToolConfig):
    default_precision: int = Field(12, description="Digits for numeric evaluation")
    angle_mode: str = Field("rad", description="'rad' or 'deg' for trig (SymPy only)")
    economy_enabled: bool = False
    economy_currency: str = "USD"
    price_cents_per_call: int = 0


# ---------------------------- tool ------------------------------
@tool_registry.register_tool(
    key="math",
    name="Math Tool",
    description="Evaluate math expressions, do basic calculus/algebra, small unit conversions. Returns {'answer': ..., 'addons': {...}}.",
    version="1.0.0",
    metadata={
        "execution_mode": "pure",
        "llm_free": True,
        "route": "pure",
        "actions": ["math.*", "calc.evaluate", "algebra.*", "calculus.*", "units.convert"],
        "tool_class": "math",
        "tags": ["builtin", "math", "utilities", "calculator"],
        "provider": "tools:math",
        "config_schema": "src.domains.agentverse.tools.math_tool.MathToolConfig",
        "config": MathToolConfig().model_dump(),
        "outputs": [
            {"key": "answer", "label": "Answer", "type": "string", "path": "answer"},
        ],
        "ui": {
            "icon": "🧮",
            "color": "#2563eb",
            "category": "utilities",
            "display_hints": ["code", "keyvalue"],
            "shortcuts": [
                {"label": "Eval 2*(3+4)^2", "overrides": {"op": "eval", "expr": "2*(3+4)^2"}},
                {
                    "label": "Differentiate",
                    "overrides": {"op": "differentiate", "expr": "sin(x)", "var": "x"},
                },
                {"label": "Integrate", "overrides": {"op": "integrate", "expr": "x^2", "var": "x"}},
                {"label": "Simplify", "overrides": {"op": "simplify", "expr": "(x^2 + 2x + 1)"}},
                {
                    "label": "10 km → mi",
                    "overrides": {
                        "op": "convert_units",
                        "value": 10,
                        "from_unit": "km",
                        "to_unit": "mi",
                    },
                },
            ],
        },
        "supports": {"streaming": False, "attachments": [], "multi_turn_memory": False},
    },
)
class MathTool(BaseTool):
    name = "Math Tool"
    key = "math"
    version = "1.0.0"
    required_dependencies: list[str] = []  # optional: sympy

    parameters: dict[str, Any] = {
        "op": {
            "type": "string",
            "required": False,
            "default": "auto",
            "description": "auto | eval | differentiate | integrate | simplify | convert_units",
        },
        "expr": {"type": "string", "required": False, "description": "Math expression to evaluate"},
        "var": {
            "type": "string",
            "required": False,
            "description": "Variable name for calculus ops (default 'x')",
        },
        "precision": {
            "type": "integer",
            "required": False,
            "description": "Digits for numeric output",
        },
        "value": {
            "type": "number",
            "required": False,
            "description": "Value to convert (e.g., 10)",
        },
        "from_unit": {
            "type": "string",
            "required": False,
            "description": "Source unit (e.g., 'km')",
        },
        "to_unit": {"type": "string", "required": False, "description": "Target unit (e.g., 'mi')"},
        "text": {
            "type": "string",
            "required": False,
            "description": "Raw user text for auto inference",
        },
        "context": {
            "type": "object",
            "required": False,
            "description": "Optional context/history (not used in model calls)",
        },
    }

    @staticmethod
    def infer_kwargs(text: str) -> dict:
        out: dict[str, Any] = {}
        low = (text or "").lower()

        m_uc = re.search(RE_UNIT_CONVERT, text or "", flags=re.I)
        if m_uc:
            try:
                val = float(re.search(r"\d+(?:\.\d+)?", m_uc.group(0)).group(0))
            except Exception:
                val = None
            if val is not None:
                parts = m_uc.group(0).split()
                out.update(
                    {
                        "op": "convert_units",
                        "value": val,
                        "from_unit": parts[1],
                        "to_unit": parts[-1],
                    }
                )
                return out

        if re.search(RE_CALC_CUES, low):
            out["op"] = (
                "differentiate"
                if ("d/dx" in low or "derivative" in low or "differentiate" in low)
                else "integrate"
            )
            m = re.search(r"(differentiate|derivative|d/dx|integrate)\s+(.+)", text, flags=re.I)
            if m:
                out["expr"] = m.group(2).strip()
            mvar = re.search(r"\bd/d([a-zA-Z])\b", low)
            if mvar:
                out["var"] = mvar.group(1)
            return out

        if re.search(RE_ARITH_EXPR, text or "", flags=re.I):
            return {"op": "eval", "expr": text.strip()}

        if "simplify" in low:
            m = re.search(r"simplify\s+(.+)", text or "", flags=re.I)
            return {"op": "simplify", "expr": (m.group(1).strip() if m else text.strip())}

        return {"op": "eval", "expr": text.strip()}

    async def _run(
        self,
        op: str = "auto",
        expr: str | None = None,
        var: str | None = None,
        precision: int | None = None,
        value: float | None = None,
        from_unit: str | None = None,
        to_unit: str | None = None,
        text: str | None = None,
        context: dict[str, Any] | None = None,
        **kwargs,
    ):
        started = datetime.now(UTC)
        try:
            if (op or "auto") == "auto":
                inferred = self.infer_kwargs(text or expr or "")
                op = inferred.get("op", "eval")
                expr = expr or inferred.get("expr")
                var = var or inferred.get("var")
                value = value or inferred.get("value")
                from_unit = from_unit or inferred.get("from_unit")
                to_unit = to_unit or inferred.get("to_unit")

            prec = int(precision or getattr(self.config, "default_precision", 12))
            answer: str = ""
            extras: dict[str, Any] = {}

            if op == "convert_units":
                if value is None or not from_unit or not to_unit:
                    raise ToolExecutionError("For convert_units provide value, from_unit, to_unit")
                conv = convert_units(float(value), from_unit, to_unit)
                answer = f"{conv['value']}"
                extras.update(conv)
                ended = datetime.now(UTC)
                return {"answer": answer, "extras": extras, "addons": _usage_addons(started, ended)}

            if op in {"differentiate", "integrate", "simplify"}:
                if not HAS_SYMPY:
                    raise ToolExecutionError("Calculus/algebra requires SymPy in this runtime")
                if not expr or not expr.strip():
                    raise ToolExecutionError(f"'{op}' requires 'expr'")
                transformations = standard_transformations + (
                    convert_xor,
                    implicit_multiplication_application,
                )
                sym_expr = parse_expr(expr, transformations=transformations)
                sym_var = sp.Symbol(var or "x")
                if op == "differentiate":
                    sym_out = sp.diff(sym_expr, sym_var)
                elif op == "integrate":
                    sym_out = sp.integrate(sym_expr, sym_var)
                else:
                    sym_out = sp.simplify(sym_expr)
                ans_str = str(sym_out)
                answer = ans_str
                extras = {"symbolic": ans_str}
                ended = datetime.now(UTC)
                return {"answer": answer, "extras": extras, "addons": _usage_addons(started, ended)}

            if not expr or not expr.strip():
                raise ToolExecutionError("No expression provided")

            if HAS_SYMPY:
                transformations = standard_transformations + (
                    convert_xor,
                    implicit_multiplication_application,
                )
                sym_expr = parse_expr(expr, transformations=transformations)
                numeric = sym_expr.evalf(prec)
                answer = str(numeric)
                extras = {"exact": str(sym_expr), "numeric": str(numeric)}
            else:
                res = _safe_eval(expr, prec)
                getcontext().prec = max(10, min(prec, 100))
                answer = str(Decimal(str(res)))
                extras = {"numeric": answer}

            ended = datetime.now(UTC)
            return {"answer": answer, "extras": extras, "addons": _usage_addons(started, ended)}

        except ToolExecutionError:
            raise
        except Exception as e:
            ended = datetime.now(UTC)
            logger.error("[MathTool] error: %s", e, exc_info=True)
            message, code = classify_error(e)
            return self._final_error(
                message=message,
                code=code,
                result_overrides={"addons": _usage_addons(started, ended)},
            )
