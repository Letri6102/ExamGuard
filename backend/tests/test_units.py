from __future__ import annotations

import asyncio
import json
import random

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper

from app.config import load_settings
from app.services import collusion
from app.services.bus import InMemoryBus, RedisBus
from app.services.paper import _kendall, find_watermarks, render_text, safe_eval, sample_params
from app.services.risk import FeatureSpec, FusionScorer, RuleScorer, update_risk


# ------------------------------------------------------------------ đề
def test_safe_eval_blocks_code():
    assert safe_eval("2**(h+1) - 1", {"h": 3}) == 15
    assert safe_eval("sqrt(16) + pi*0", {}) == 4
    for bad in ["__import__('os')", "a.__class__", "(lambda: 1)()", "9**99999", "[1,2]"]:
        with pytest.raises(Exception):
            safe_eval(bad, {"a": 1})


def test_params_render():
    rng = random.Random(1)
    p = sample_params({"a": {"min": 10, "max": 50, "step": 5}, "b": {"values": [2, 4]}}, rng)
    assert p["a"] % 5 == 0 and 10 <= p["a"] <= 50 and p["b"] in (2, 4)
    assert render_text("{{a}} + {{ a * b }}", {"a": 3, "b": 1.5}) == "3 + 4.5"
    assert _kendall([1, 2, 3, 4]) == 1.0 and _kendall([4, 3, 2, 1]) == 0.0
    assert find_watermarks("ảnh có mã ABCD2345 ở góc, và 12345678") == ["ABCD2345"]


# ------------------------------------------------------------------ thông đồng (mô phỏng có nhãn đúng)
def simulate(n=60, q=40, k=4, copy_rate=0.6, seed=0):
    rng = np.random.default_rng(seed)
    ability = rng.normal(0, 1, n)
    diff = rng.normal(0, 1, q)
    resp = []
    for i in range(n):
        ans, cor, times = {}, {}, {}
        for j in range(q):
            p = 1 / (1 + np.exp(-(ability[i] - diff[j])))
            ok = rng.random() < p
            wrong = rng.choice(["B", "C", "D"], p=[0.6, 0.3, 0.1])  # phân bố đáp án sai không đều
            ans[j], cor[j], times[j] = ("A" if ok else wrong), ok, j * 60 + rng.normal(0, 25)
        resp.append(collusion.Response(i, ans, cor, times))
    # cặp thông đồng: b chép a với tỉ lệ copy_rate, trễ ~10 giây
    pairs = [(2 * t, 2 * t + 1) for t in range(k)]
    for a, b in pairs:
        ra, rb = resp[a], resp[b]
        for j in range(q):
            if rng.random() < copy_rate:
                rb.answers[j], rb.correct[j] = ra.answers[j], ra.correct[j]
                rb.times[j] = ra.times[j] + 10 + rng.normal(0, 3)
    return resp, pairs


@pytest.mark.parametrize("rate,min_recall", [(0.5, 0.5), (0.9, 1.0)])
def test_collusion_finds_planted_pairs(rate, min_recall):
    recalls, fps = [], []
    for seed in range(3):
        resp, planted = simulate(copy_rate=rate, seed=seed)
        res = collusion.analyze(resp)  # ngưỡng Bonferroni theo số cặp
        thr = res["stats"]["z_threshold"]
        found = {(p["a"], p["b"]) for p in res["pairs"] if p["z"] >= thr}
        recalls.append(len(found & set(planted)) / len(planted))
        fps.append(len(found - set(planted)))
    assert np.mean(recalls) >= min_recall, recalls
    assert sum(fps) == 0, fps
    if rate == 0.9:
        top = next(p for p in res["pairs"] if (p["a"], p["b"]) in planted)
        assert top["within_30s"] > 0.4 and top["identical_wrong"] >= 5
        assert any(set(g["members"]) <= {a for p in planted for a in p} for g in res["groups"])


def test_collusion_prefilter_large_class():
    resp, planted = simulate(n=300, copy_rate=0.9, seed=1, k=5)
    res = collusion.analyze(resp, full_pairs_max=1000)
    assert res["stats"]["mode"] == "rare_wrong_prefilter"
    assert res["stats"]["candidate_pairs"] < res["stats"]["all_pairs"]
    found = {(p["a"], p["b"]) for p in res["pairs"] if p["z"] >= res["stats"]["z_threshold"]}
    assert found == set(planted)


# ------------------------------------------------------------------ hàng đợi
def test_inmemory_drops_only_low_priority():
    async def run():
        bus = InMemoryBus(frame_queue_max=10)
        for i in range(50):
            await bus.publish_frame({"i": i, "jpeg": b"x"}, high=False)
        for i in range(30):
            await bus.publish_frame({"i": 1000 + i, "jpeg": b"x"}, high=True)
        got = []
        while True:
            b = await bus.consume_frames(16, 0.05)
            if not b:
                break
            got += b
        return bus, got
    bus, got = asyncio.run(run())
    hi = [g for g in got if g["i"] >= 1000]
    assert len(hi) == 30 and got[0]["i"] == 1000      # chùm sự kiện luôn giữ và ưu tiên
    assert len(got) - len(hi) == 10 and bus.dropped == 40


def test_redis_bus_roundtrip():
    fakeredis = pytest.importorskip("fakeredis")

    async def run():
        bus = RedisBus("redis://fake", 100, client=fakeredis.FakeAsyncRedis())
        await bus.publish_features({"a": 1, "w": {"t": 0}})
        await bus.publish_frame({"a": 1, "t": 2.0, "hi": True, "jpeg": b"\xff\xd8abc"}, high=True)
        await bus.publish_frame({"a": 1, "t": 3.0, "hi": False, "jpeg": b"\xff\xd8def"}, high=False)
        f = await bus.consume_features(10, 0.1)
        fr = await bus.consume_frames(10, 0.1)
        again = await bus.consume_features(10, 0.05)
        return f, fr, again
    f, fr, again = asyncio.run(run())
    assert f == [{"a": 1, "w": {"t": 0}}] and again == []
    assert [x["jpeg"] for x in fr] == [b"\xff\xd8abc", b"\xff\xd8def"]


# ------------------------------------------------------------------ chấm rủi ro
def spec():
    return FeatureSpec(load_settings().schema)


def test_rules_respect_missing_modalities():
    sp = spec()
    v = {n: 0.0 for n in sp.names}
    v.update(obj_phone=0.9, hidden_sec=2)
    s1, r1 = RuleScorer().score(v, {"object": 1, "browser": 1})
    s2, r2 = RuleScorer().score(v, {"object": 0, "browser": 1})
    assert s1 > s2 and {x["code"] for x in r1} == {"PHONE", "TAB_AWAY"} and [x["code"] for x in r2] == ["TAB_AWAY"]
    assert update_risk(80, 0.0) == 72 and update_risk(10, 0.6) == 60
    x = sp.model_input(v, {"object": 1})
    assert x.shape == (sp.dim,) and x[sp.index["hidden_sec"]] == 0  # browser không có mask -> bị che
    assert x[sp.index["obj_phone"]] == pytest.approx(0.9)


def make_fusion_onnx(path, dim, hidden=8, mods=8):
    rng = np.random.default_rng(0)
    w = rng.normal(0, 0.3, (dim, 1)).astype(np.float32)
    wh = rng.normal(0, 0.3, (dim, hidden)).astype(np.float32)
    nodes = [
        helper.make_node("MatMul", ["x", "w"], ["logit"]), helper.make_node("Sigmoid", ["logit"], ["prob"]),
        helper.make_node("MatMul", ["x", "wh"], ["xh"]), helper.make_node("Add", ["xh", "h"], ["hs"]),
        helper.make_node("Tanh", ["hs"], ["h_out"]),
        helper.make_node("Slice", ["x", "st", "en", "ax"], ["mx"]), helper.make_node("Softmax", ["mx"], ["gates"], axis=1),
    ]
    inits = [helper.make_tensor(n, TensorProto.FLOAT, a.shape, a.flatten()) for n, a in (("w", w), ("wh", wh))]
    inits += [helper.make_tensor(n, TensorProto.INT64, [1], [v]) for n, v in (("st", dim - mods), ("en", dim), ("ax", 1))]
    g = helper.make_graph(nodes, "fusion", [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, dim]),
                                            helper.make_tensor_value_info("h", TensorProto.FLOAT, [1, hidden])],
                          [helper.make_tensor_value_info(n, TensorProto.FLOAT, None) for n in ("prob", "h_out", "gates")], inits)
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 9  # onnxruntime cũ chỉ đọc tới IR 11
    onnx.save(m, path)


def test_fusion_scorer_streaming(tmp_path):
    sp = spec()
    p = tmp_path / "fusion.onnx"
    make_fusion_onnx(str(p), sp.dim)
    fs = FusionScorer(p, sp)
    h = fs.init_state()
    v = {n: 0.0 for n in sp.names}
    prob, reasons, h2 = fs.score(v, {"face": 1}, h)
    assert 0 <= prob <= 1 and h2.shape == h.shape and reasons[-1]["code"] == "MODEL"
    bad = tmp_path / "bad.onnx"
    make_fusion_onnx(str(bad), sp.dim + 3)
    with pytest.raises(ValueError):
        FusionScorer(bad, sp)


def make_yolo_onnx(path, names, n=6):
    nc = len(names)
    out = np.zeros((1, 4 + nc, n), np.float32)
    # hai người (2 hộp tách nhau) + một điện thoại
    out[0, :4, 0] = [100, 100, 80, 160]; out[0, 4 + 0, 0] = 0.9
    out[0, :4, 1] = [250, 100, 80, 160]; out[0, 4 + 0, 1] = 0.7
    out[0, :4, 2] = [102, 101, 80, 160]; out[0, 4 + 0, 2] = 0.85  # trùng hộp 0 -> bị NMS loại
    out[0, :4, 3] = [160, 200, 30, 50]; out[0, 4 + 1, 3] = 0.66
    nodes = [helper.make_node("ReduceMean", ["images"], ["m"], keepdims=0),
             helper.make_node("Mul", ["m", "zero"], ["z"]), helper.make_node("Add", ["const", "z"], ["output0"])]
    inits = [helper.make_tensor("const", TensorProto.FLOAT, out.shape, out.flatten()),
             helper.make_tensor("zero", TensorProto.FLOAT, [], [0.0])]
    g = helper.make_graph(nodes, "yolo", [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 320, 320])],
                          [helper.make_tensor_value_info("output0", TensorProto.FLOAT, [1, 4 + nc, n])], inits)
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 9
    helper.set_model_props(m, {"names": str(dict(enumerate(names))), "imgsz": "[320, 320]"})
    onnx.save(m, path)


def test_yolo_summary(tmp_path):
    import cv2

    from app.config import DEFAULT_YOLO_CLASSES
    from app.services.vision import YoloDetector

    p = tmp_path / "y.onnx"
    make_yolo_onnx(str(p), ["person", "cell phone", "book"])
    det = YoloDetector(p, DEFAULT_YOLO_CLASSES)
    jpg = cv2.imencode(".jpg", np.zeros((240, 320, 3), np.uint8))[1].tobytes()
    res = det.predict([jpg, b"not a jpeg", jpg])
    assert res[1] is None
    assert res[0]["obj_phone"] == pytest.approx(0.66) and res[0]["obj_person_extra"] == pytest.approx(0.7)
    assert res[0]["obj_book"] == 0.0 and "obj_headphone" not in res[0]


def test_schema_contract():
    s = load_settings().schema
    names = [f["name"] for f in s["features"]]
    assert len(names) == len(set(names)) == 54 and len(s["modalities"]) == 8
    mods = [f["modality"] for f in s["features"]]
    assert mods == sorted(mods, key=s["modalities"].index)  # nhóm liền nhau (mask theo khối)
    assert json.dumps(s, ensure_ascii=False)
