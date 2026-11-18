"""Sinh đề riêng cho từng thí sinh, chấm điểm và truy vết đề bị lộ.

Mỗi thí sinh nhận: thứ tự câu khác nhau, thứ tự phương án khác nhau, cách diễn đạt (variant) và giá trị tham số
khác nhau. Bảng ánh xạ (PaperItem) cho phép quy mọi câu trả lời về ĐỀ GỐC (cần cho phân tích thông đồng) và
cho phép nhận ra một bản đề bị lộ thuộc về thí sinh nào.

Cú pháp câu hỏi có tham số:
    stem:    "Tính {{a}} + {{b}} * 2"
    params:  {"a": {"min": 1, "max": 9}, "b": {"min": 10, "max": 50, "step": 5}}
    options: [{"id": "A", "text": "{{a + b*2}}"}, {"id": "B", "text": "{{a*b}}"}]
    answer:  ["A"]                    (single/multi)  |  "a + b*2" (numeric)
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import hmac
import json
import math
import operator
import random
import re
import unicodedata
from dataclasses import dataclass

from ..models import Attempt, Exam, PaperItem, Question

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": round, "min": min, "max": max, "log": math.log, "pi": math.pi}


def safe_eval(expr: str, names: dict[str, float]) -> float:
    """Tính biểu thức số học an toàn (không dùng eval)."""
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in names:
                return names[node.id]
            if node.id in _FUNCS and not callable(_FUNCS[node.id]):
                return _FUNCS[node.id]
            raise ValueError(f"Biến không xác định: {node.id}")
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 10:
                raise ValueError("Số mũ quá lớn")
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and callable(_FUNCS.get(node.func.id)):
            return _FUNCS[node.func.id](*[ev(a) for a in node.args])
        raise ValueError("Biểu thức không hợp lệ")

    if len(expr) > 200:
        raise ValueError("Biểu thức quá dài")
    return ev(ast.parse(expr, mode="eval"))


def fmt_num(x: float) -> str:
    if isinstance(x, float) and x.is_integer():
        x = int(x)
    if isinstance(x, int):
        return str(x)
    return f"{x:.4g}"


_TPL = re.compile(r"\{\{\s*(.+?)\s*\}\}")


def render_text(text: str, params: dict[str, float]) -> str:
    return _TPL.sub(lambda m: fmt_num(safe_eval(m.group(1), params)), text)


def sample_params(spec: dict, rng: random.Random) -> dict[str, float]:
    out = {}
    for name, p in spec.items():
        if "values" in p:
            out[name] = rng.choice(p["values"])
            continue
        lo, hi, step = p.get("min", 0), p.get("max", 10), p.get("step", 1)
        n = int(round((hi - lo) / step))
        v = lo + step * rng.randint(0, max(n, 0))
        out[name] = int(v) if float(v).is_integer() else round(v, 6)
    return out


def q_options(q: Question) -> list[dict]:
    return json.loads(q.options_json or "[]")


def q_variants(q: Question) -> list[str]:
    return [q.stem] + [v for v in json.loads(q.variants_json or "[]") if v.strip()]


def attempt_rng(secret: str, attempt: Attempt) -> random.Random:
    seed = hmac.new(secret.encode(), f"paper:{attempt.pseudonym}".encode(), hashlib.sha256).hexdigest()
    return random.Random(int(seed[:16], 16))


def generate_paper(secret: str, exam: Exam, attempt: Attempt, questions: list[Question]) -> list[PaperItem]:
    rng = attempt_rng(secret, attempt)
    order = list(questions)
    if exam.shuffle_questions:
        rng.shuffle(order)
    items = []
    for pos, q in enumerate(order):
        opts = [o["id"] for o in q_options(q)]
        if exam.shuffle_options and q.qtype in ("single", "multi"):
            rng.shuffle(opts)
        variant = rng.randrange(len(q_variants(q))) if exam.use_variants else 0
        params = sample_params(json.loads(q.params_json or "{}"), rng)
        items.append(PaperItem(attempt_id=attempt.id, position=pos, question_id=q.id, variant_idx=variant,
                               option_perm_json=json.dumps(opts), params_json=json.dumps(params)))
    return items


@dataclass
class RenderedItem:
    position: int
    question_id: int
    qtype: str
    stem: str
    options: list[str]          # theo thứ tự hiển thị
    option_ids: list[str]       # id gốc tương ứng (KHÔNG gửi cho trình duyệt)
    params: dict


def render_item(item: PaperItem, q: Question) -> RenderedItem:
    params = json.loads(item.params_json or "{}")
    variants = q_variants(q)
    stem = render_text(variants[min(item.variant_idx, len(variants) - 1)], params)
    by_id = {o["id"]: o["text"] for o in q_options(q)}
    perm = json.loads(item.option_perm_json or "[]")
    return RenderedItem(item.position, q.id, q.qtype, stem, [render_text(by_id[i], params) for i in perm], perm, params)


def to_original(item: PaperItem, q: Question, value):
    """Câu trả lời theo vị trí hiển thị -> theo đề gốc."""
    perm = json.loads(item.option_perm_json or "[]")
    if q.qtype == "single":
        if value is None or value == "":
            return None
        return [perm[int(value)]]
    if q.qtype == "multi":
        return sorted(perm[int(v)] for v in (value or []))
    if q.qtype == "numeric":
        return None if value in (None, "") else float(value)
    return str(value or "")[:20000]


def grade(q: Question, item: PaperItem, original_value) -> bool | None:
    if original_value is None:
        return False
    if q.qtype in ("single", "multi"):
        return sorted(original_value) == sorted(json.loads(q.answer_json or "[]"))
    if q.qtype == "numeric":
        expected = safe_eval(json.loads(q.answer_json), json.loads(item.params_json or "{}"))
        return abs(float(original_value) - expected) <= max(q.tolerance, 1e-9) * max(1.0, abs(expected))
    return None  # tự luận: chấm tay


# ---------------------------------------------------------------------------
# Truy vết đề bị lộ
# ---------------------------------------------------------------------------
def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower()
    text = re.sub(r"[^\w\s.,-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+|\d+(?:[.,]\d+)?", text)


def _containment(part: str, whole_tokens: set[str]) -> float:
    toks = _tokens(part)
    return sum(t in whole_tokens for t in toks) / len(toks) if toks else 0.0


def _kendall(a: list[float]) -> float:
    """Tỉ lệ cặp đúng thứ tự (1 = cùng thứ tự, 0.5 = ngẫu nhiên)."""
    n = len(a)
    if n < 2:
        return 0.5
    good = sum(1 for i in range(n) for j in range(i + 1, n) if a[i] < a[j])
    return good / (n * (n - 1) / 2)


def trace_leak(leaked: str, papers: dict[int, list[RenderedItem]]) -> list[dict]:
    """So bản đề bị lộ (chép tay / OCR) với đề của từng thí sinh. Trả danh sách xếp hạng theo độ khớp."""
    L = normalize(leaked)
    ltoks = set(_tokens(L))
    out = []
    for attempt_id, items in papers.items():
        positions, opt_scores, param_scores, variant_scores = [], [], [], []
        for it in items:
            stem = normalize(it.stem)
            c = _containment(stem, ltoks)
            if c < 0.5:
                continue
            m = difflib.SequenceMatcher(None, L, stem, autojunk=False).find_longest_match(0, len(L), 0, len(stem))
            if m.size < min(12, len(stem)):
                continue
            positions.append(m.a)
            variant_scores.append(c)
            nums = [fmt_num(v) for v in it.params.values()]
            if nums:
                param_scores.append(sum(n in ltoks for n in nums) / len(nums))
            if len(it.options) >= 2:
                after = L[m.a:m.a + 3000]
                pos = [after.find(normalize(o)) for o in it.options]
                found = [p for p in pos if p >= 0]
                if len(found) >= 2:
                    opt_scores.append(_kendall(found))
        if not positions:
            continue
        # thứ tự xuất hiện trong bản lộ so với thứ tự hiển thị của thí sinh
        order = _kendall(positions)
        comps = {"question_order": order, "coverage": len(positions) / max(len(items), 1)}
        if opt_scores:
            comps["option_order"] = sum(opt_scores) / len(opt_scores)
        if param_scores:
            comps["param_values"] = sum(param_scores) / len(param_scores)
        comps["wording"] = sum(variant_scores) / len(variant_scores)
        weights = {"question_order": 2, "option_order": 2, "param_values": 3, "wording": 1, "coverage": 0.5}
        score = sum(weights[k] * v for k, v in comps.items()) / sum(weights[k] for k in comps)
        out.append({"attempt_id": attempt_id, "score": round(score, 4), "matched_questions": len(positions),
                    "components": {k: round(v, 3) for k, v in comps.items()}})
    out.sort(key=lambda r: -r["score"])
    return out


WATERMARK_RE = re.compile(r"\b[A-HJ-NP-Z2-9]{8}\b")


def find_watermarks(text: str) -> list[str]:
    return sorted(set(WATERMARK_RE.findall(text.upper())))
