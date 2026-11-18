"""Phân tích thông đồng sau ca thi.

Đầu vào: câu trả lời ĐÃ QUY VỀ ĐỀ GỐC (vì mỗi thí sinh có đề hoán vị riêng) và thời điểm trả lời từng câu.

1. Chỉ số ω (Wollack 1997), xấp xỉ: xác suất thí sinh b trả lời TRÙNG đúng phương án của a ở câu q
       π_q = P_b(đúng)            nếu a đúng
       π_q = (1 - P_b(đúng)) s_qk nếu a chọn phương án sai k (s_qk: tỉ lệ chọn k trong số người sai câu q)
   với logit P_b(đúng) ≈ logit(năng lực b) + logit(độ dễ câu q) - logit(trung bình) (xấp xỉ Rasch).
   ω = (số câu trùng - Σπ) / sqrt(Σπ(1-π)); lấy max hai chiều (a chép b, b chép a).
2. Lớp nhỏ (≤ `full_pairs_max` cặp): tính MỌI cặp. Lớp lớn: lọc trước bằng chỉ mục "đáp án sai hiếm"
   (cặp phải cùng chọn ≥ `min_shared` đáp án sai hiếm) để tránh so N² cặp.
3. Ngưỡng mặc định hiệu chỉnh Bonferroni theo tổng số cặp (α = 0.05).
4. Đồng bộ thời gian: DTW giữa chuỗi thời điểm trả lời + tỉ lệ câu trả lời cách nhau < 30 giây.
5. Đồ thị các cặp vượt ngưỡng -> nhóm bằng Louvain.
Kết quả chỉ là gợi ý cho hội đồng xem xét, không phải kết luận.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from statistics import NormalDist

import networkx as nx
import numpy as np


@dataclass
class Response:
    attempt_id: int
    answers: dict[int, str]          # id câu gốc -> giá trị đã chuẩn hóa (chuỗi)
    correct: dict[int, bool]
    times: dict[int, float]          # id câu gốc -> giây kể từ lúc bắt đầu


def _wrong(r: Response, q: int) -> bool:
    return q in r.correct and not bool(r.correct[q])


def _logit(p: float) -> float:
    p = min(max(p, 1e-3), 1 - 1e-3)
    return math.log(p / (1 - p))


def dtw(a: np.ndarray, b: np.ndarray, band: int | None = None) -> float:
    n, m = len(a), len(b)
    band = band or max(n, m)
    D = np.full((n + 1, m + 1), np.inf)
    D[0, 0] = 0.0
    for i in range(1, n + 1):
        for j in range(max(1, i - band), min(m, i + band) + 1):
            cost = abs(a[i - 1] - b[j - 1])
            D[i, j] = cost + min(D[i - 1, j], D[i, j - 1], D[i - 1, j - 1])
    return float(D[n, m]) / (n + m)


def bonferroni_z(n_pairs: int, alpha: float = 0.05) -> float:
    return NormalDist().inv_cdf(1 - alpha / max(n_pairs, 1))


def analyze(responses: list[Response], z_threshold: float | None = None, rare_max_ratio: float = 0.25,
            min_shared: int = 2, full_pairs_max: int = 200_000, max_pairs: int = 200) -> dict:
    N = len(responses)
    total_pairs = N * (N - 1) // 2
    if N < 3:
        return {"pairs": [], "groups": [], "stats": {"attempts": N, "z_threshold": None}}
    thr = z_threshold if z_threshold is not None else bonferroni_z(total_pairs)
    by_id = {r.attempt_id: r for r in responses}

    # --- tham số mô hình đáp ứng (xấp xỉ Rasch) và tỉ lệ phương án sai
    qs = sorted({q for r in responses for q in r.correct})
    q_right = {q: [bool(r.correct[q]) for r in responses if q in r.correct] for q in qs}
    p_q = {q: (sum(v) + 0.5) / (len(v) + 1) for q, v in q_right.items()}
    p_bar = float(np.mean(list(p_q.values()))) if p_q else 0.5
    theta = {r.attempt_id: _logit((sum(map(bool, r.correct.values())) + 0.5) / (len(r.correct) + 1)) for r in responses}
    wrong_counts: dict[int, Counter] = defaultdict(Counter)
    for r in responses:
        for q, v in r.answers.items():
            if _wrong(r, q):
                wrong_counts[q][v] += 1
    share = {q: {v: (c + 0.5) / (sum(cnt.values()) + 0.5 * len(cnt)) for v, c in cnt.items()}
             for q, cnt in wrong_counts.items()}

    def p_correct(aid: int, q: int) -> float:
        return 1 / (1 + math.exp(-(theta[aid] + _logit(p_q[q]) - _logit(p_bar))))

    def omega(src: Response, cop: Response, common: list[int]) -> tuple[float, int, float]:
        M, E, V = 0, 0.0, 0.0
        for q in common:
            pc = p_correct(cop.attempt_id, q)
            pi = pc if src.correct[q] else (1 - pc) * share.get(q, {}).get(src.answers[q], 0.5)
            pi = min(max(pi, 1e-4), 1 - 1e-4)
            M += src.answers[q] == cop.answers[q]
            E += pi
            V += pi * (1 - pi)
        return ((M - E) / math.sqrt(V) if V > 0 else 0.0), M, E

    # --- chọn cặp cần tính
    if total_pairs <= full_pairs_max:
        candidates = list(combinations(sorted(by_id), 2))
        mode = "all_pairs"
    else:
        index: dict[tuple[int, str], list[int]] = defaultdict(list)
        for r in responses:
            for q, v in r.answers.items():
                if _wrong(r, q) and 2 <= wrong_counts[q][v] <= max(2, rare_max_ratio * N):
                    index[(q, v)].append(r.attempt_id)
        shared = Counter()
        for ids in index.values():
            if len(ids) <= 300:
                for a, b in combinations(sorted(ids), 2):
                    shared[(a, b)] += 1
        candidates = [p for p, c in shared.items() if c >= min_shared]
        mode = "rare_wrong_prefilter"

    pairs = []
    for a, b in candidates:
        ra, rb = by_id[a], by_id[b]
        common = [q for q in ra.correct if q in rb.correct and q in ra.answers and q in rb.answers]
        if len(common) < 5:
            continue
        z1, M, E1 = omega(ra, rb, common)
        z2, _, E2 = omega(rb, ra, common)
        z = max(z1, z2)
        both_wrong = sum(1 for q in common if _wrong(ra, q) and _wrong(rb, q))
        same_wrong = sum(1 for q in common if _wrong(ra, q) and _wrong(rb, q) and ra.answers[q] == rb.answers[q])
        pair = {"a": a, "b": b, "z": round(z, 2), "matches": M, "expected": round(E1 if z1 >= z2 else E2, 2),
                "items": len(common), "identical_wrong": same_wrong, "both_wrong": both_wrong}
        if z >= thr - 1:  # chỉ tính thời gian cho cặp đáng chú ý
            tc = sorted(set(ra.times) & set(rb.times))
            if len(tc) >= 3:
                ta = np.array([ra.times[q] for q in tc])
                tb = np.array([rb.times[q] for q in tc])
                scale = max(float(np.ptp(np.concatenate([ta, tb]))), 1.0)
                pair["dtw"] = round(dtw(ta / scale, tb / scale, band=5), 4)
                pair["within_30s"] = round(float(np.mean(np.abs(ta - tb) <= 30)), 3)
        pairs.append(pair)

    pairs.sort(key=lambda p: -p["z"])
    g = nx.Graph()
    for p in pairs:
        if p["z"] >= thr:
            g.add_edge(p["a"], p["b"], weight=p["z"])
    groups = []
    if g.number_of_edges():
        for comm in nx.community.louvain_communities(g, weight="weight", seed=0):
            if len(comm) >= 2:
                sub = g.subgraph(comm)
                groups.append({"members": sorted(comm), "edges": sub.number_of_edges(),
                               "max_z": round(max(d["weight"] for _, _, d in sub.edges(data=True)), 2)})
    groups.sort(key=lambda x: -x["max_z"])
    return {"pairs": pairs[:max_pairs], "groups": groups,
            "stats": {"attempts": N, "mode": mode, "candidate_pairs": len(candidates), "all_pairs": total_pairs,
                      "z_threshold": round(thr, 2), "flagged_pairs": sum(p["z"] >= thr for p in pairs)}}


def normalize_value(value_json: str) -> str:
    v = json.loads(value_json)
    if isinstance(v, list):
        return ",".join(sorted(map(str, v)))
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)
