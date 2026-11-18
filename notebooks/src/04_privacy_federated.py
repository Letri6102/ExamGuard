# %% [markdown]
# # 04 · Đánh giá quyền riêng tư bằng tấn công (C2) và huấn luyện liên kết có DP
#
# Bốn thí nghiệm, mỗi thí nghiệm trả lời một câu hỏi của phản biện:
#
# | # | Câu hỏi | Tấn công / thí nghiệm | Kỳ vọng |
# |---|---|---|---|
# | A | Đặc trưng nhịp gõ gửi đi có làm lộ danh tính? | **Tái nhận dạng**: đoán người gõ từ đặc trưng 1 phút, trước/sau lớp bảo vệ (chuẩn hóa khởi động + lượng tử hóa + nhiễu Laplace ε) | độ chính xác giảm về gần 1/N; **đường cong privacy–utility** |
# | B | Khung hình làm mờ mặt có dựng lại được mặt? | **Feature inversion**: huấn luyện decoder dựng mặt từ khung đã pixel hóa 6×8 (LFW) | SSIM thấp, nhận dạng ArcFace ~ ngẫu nhiên |
# | C | Mô hình đã huấn luyện có lộ ai nằm trong dữ liệu? | **Membership inference** (ngưỡng mất mát), không DP vs DP-SGD | AUC tấn công ~0,5 khi bật DP |
# | D | Huấn luyện không gom dữ liệu về một chỗ có kém đi nhiều? | Tập trung vs **FedAvg / FedProx / FedAvg + DP** (mô phỏng nhiều đơn vị) | mất mát độ chính xác nhỏ |
#
# Cần chạy **notebook 01** trước (file `keystroke_windows.parquet` trên Drive). Thời gian: ~40–60 phút trên GPU T4.
#
# Ghi chú cho bài báo: phần D mô phỏng nhiều client trên một máy (cùng thuật toán với Flower FedAvg/FedProx); nếu chỉ có một
# server nội bộ thì ghi rõ là mô phỏng.

# %%
!pip -q install xgboost insightface onnxruntime-gpu scikit-image opacus

# %%
import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
from torch import nn

from eg_common import (DIM, FEATURES, MOD_OF, MOD_SLICES, MODALITIES, build_fusion_model, make_synthetic_sessions,
                       privatize, summarize)

OUT = Path("/content/examguard")
try:
    from google.colab import drive
    drive.mount("/content/drive")
    OUT = Path("/content/drive/MyDrive/examguard")
except Exception as e:
    print("Không mount được Drive:", e)
OUT.mkdir(parents=True, exist_ok=True)
DEV = "cuda" if torch.cuda.is_available() else "cpu"
SEED = 0
rng = np.random.default_rng(SEED)
torch.manual_seed(SEED)
KFEATS = [f for f in FEATURES if MOD_OF[f] == "keystroke"]
kw = pd.read_parquet(OUT / "keystroke_windows.parquet")
print(kw.shape, kw.lang.value_counts().to_dict(), "người:", kw.user.nunique())

# %% [markdown]
# ## A. Tái nhận dạng người gõ từ đặc trưng nhịp gõ
# Kẻ tấn công (vd. người có quyền đọc CSDL) gom 30 cửa sổ liên tiếp (1 phút) của một người thành một mẫu và đoán người đó
# trong N người. Huấn luyện trên câu hỏi 1–3, kiểm tra trên câu hỏi 4–6 (khác nội dung) của cùng người.
#
# Ba cách biểu diễn:
# 1. **Thô**: thời gian giữ/chuyển phím tuyệt đối (ms) — thiết kế "ngây thơ" gửi nguyên nhịp gõ.
# 2. **Chuẩn hóa khởi động** (ε = ∞): chia cho trung vị 60 giây đầu của chính người đó, lượng tử hóa 0,05.
# 3. **+ nhiễu Laplace** với ε ∈ {8, 4, 2, 1, 0,5} (đúng lớp bảo vệ chạy trên trình duyệt).
#
# Đồng thời đo **độ hữu ích**: AUC phát hiện câu trả lời có hỗ trợ LLM (XGBoost, chia theo người) ở từng mức ε.

# %%
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import GroupKFold
from xgboost import XGBClassifier

RAW = ["_dwell_ms", "_flight_ms", "_dwell_ms_std", "keys", "burst_ratio", "pause_ratio", "backspace_ratio", "chars_per_sec"]
EPS = [float("inf"), 8, 4, 2, 1, 0.5]
POS = {"P", "T", "FP", "FT"}
users = kw.user.value_counts()
users = users[users >= 200].index  # đủ dữ liệu
base = kw[kw.user.isin(users) & kw.label.isin(["B", "P", "T"])].copy()
base["qnum"] = base.groupby("user")["sample"].rank(method="dense")
base["half"] = np.where(base.qnum <= base.groupby("user").qnum.transform("max") / 2, "train", "test")


def noisy(df: pd.DataFrame, eps: float, seed=0) -> pd.DataFrame:
    if not np.isfinite(eps):
        return df
    r = np.random.default_rng(seed)
    rows = [privatize(dict(zip(KFEATS, v)), eps, rng=r) for v in df[KFEATS].values]
    out = df.copy()
    out[KFEATS] = pd.DataFrame(rows, index=df.index)[KFEATS]
    return out


def minute_blocks(df: pd.DataFrame, cols: list[str], k=30) -> pd.DataFrame:
    rows = []
    for (u, half, sid), g in df.sort_values("t").groupby(["user", "half", "sample"]):
        for i in range(0, len(g) - k + 1, k):
            blk = g.iloc[i:i + k][cols]
            rows.append({"user": u, "half": half, **{f"{c}_m": v for c, v in blk.mean().items()},
                         **{f"{c}_s": v for c, v in blk.std().items()}})
    return pd.DataFrame(rows).fillna(0)


def reid_accuracy(df: pd.DataFrame, cols: list[str]) -> tuple[float, int]:
    B = minute_blocks(df, cols)
    tr, te = B[B.half == "train"], B[B.half == "test"]
    feats = [c for c in B.columns if c not in ("user", "half")]
    clf = RandomForestClassifier(500, random_state=SEED, n_jobs=-1).fit(tr[feats], tr.user)
    return float((clf.predict(te[feats]) == te.user).mean()), te.user.nunique()


def utility_auc(df: pd.DataFrame) -> float:
    g = df.groupby("sample")
    A = g[KFEATS].agg(["mean", "std", "median"]).fillna(0)
    A.columns = [f"{a}_{b}" for a, b in A.columns]
    meta = g[["user", "label"]].first()
    y = meta.label.isin(POS).astype(int).values
    us = meta.user.values
    scores = np.zeros(len(A))
    for tr, te in GroupKFold(5).split(A, groups=us):
        clf = XGBClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.8, eval_metric="logloss")
        clf.fit(A.iloc[tr], y[tr])
        scores[te] = clf.predict_proba(A.iloc[te])[:, 1]
    return summarize(y, scores)["auc"]


curve = []
acc, n = reid_accuracy(base, RAW)
curve.append({"biểu diễn": "Thô (ms tuyệt đối)", "eps": None, "reid_acc": acc, "chance": 1 / n, "utility_auc": None})
for eps in EPS:
    d = noisy(base, eps)
    acc, n = reid_accuracy(d, KFEATS)
    curve.append({"biểu diễn": "Chuẩn hóa" + ("" if not np.isfinite(eps) else f" + Laplace ε={eps}"), "eps": eps,
                  "reid_acc": acc, "chance": 1 / n, "utility_auc": utility_auc(d)})
curve = pd.DataFrame(curve)
curve.to_csv(OUT / "privacy_utility_keystroke.csv", index=False)
curve

# %%
fig, ax = plt.subplots(figsize=(6, 4))
c2 = curve.dropna(subset=["utility_auc"])
ax.plot(c2.reid_acc, c2.utility_auc, "o-")
for _, r in c2.iterrows():
    ax.annotate("ε=∞" if not np.isfinite(r.eps) else f"ε={r.eps:g}", (r.reid_acc, r.utility_auc), textcoords="offset points", xytext=(5, 5))
ax.axvline(curve.chance.iloc[0], ls="--", c="gray", label="đoán ngẫu nhiên")
ax.axvline(curve.reid_acc.iloc[0], ls=":", c="red", label="thô (ms)")
ax.set_xlabel("Độ chính xác tái nhận dạng (thấp = riêng tư hơn)")
ax.set_ylabel("AUC phát hiện hỗ trợ LLM (cao = hữu ích hơn)")
ax.legend()
ax.set_title("Đường cong privacy–utility (nhịp gõ)")
fig.tight_layout()
fig.savefig(OUT / "privacy_utility_keystroke.png", dpi=200)

# %% [markdown]
# ## B. Dựng lại khuôn mặt từ khung đã làm mờ (feature inversion)
# Trình duyệt pixel hóa vùng mặt (nới 1,5×1,7 lần) thành **6×8 khối** trước khi gửi. Ở đây làm khó cho phòng thủ hơn thực tế:
# pixel hóa **chỉ đúng vùng mặt** của ảnh LFW (không nới), rồi huấn luyện mạng giải mã có giám sát (cặp mờ → gốc) trên các danh
# tính huấn luyện; kiểm tra trên **danh tính chưa thấy**. Đo SSIM và nhận dạng 1:N bằng ArcFace (buffalo_l, chỉ dùng nghiên cứu).

# %%
import cv2
from skimage.metrics import structural_similarity as ssim
from sklearn.datasets import fetch_lfw_people

lfw = fetch_lfw_people(min_faces_per_person=10, color=True, resize=1.0, slice_=(slice(60, 190), slice(60, 190)))
imgs = (lfw.images * (255 if lfw.images.max() <= 1 else 1)).astype(np.uint8)
imgs = np.stack([cv2.resize(im, (112, 112)) for im in imgs])  # RGB 112×112
ids = lfw.target
uid = np.unique(ids)
rng.shuffle(uid)
test_ids = set(uid[: len(uid) // 4])
is_test = np.array([i in test_ids for i in ids])
print(len(imgs), "ảnh,", len(uid), "người;", is_test.sum(), "ảnh kiểm tra của", len(test_ids), "người chưa thấy")


def pixelate(im: np.ndarray) -> np.ndarray:
    small = cv2.resize(im, (6, 8), interpolation=cv2.INTER_AREA)
    return cv2.resize(small, (112, 112), interpolation=cv2.INTER_NEAREST)


blur = np.stack([pixelate(im) for im in imgs])


class Decoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.enc = nn.Sequential(nn.Flatten(), nn.Linear(6 * 8 * 3, 512), nn.GELU(), nn.Linear(512, 256 * 7 * 7), nn.GELU())
        ups = []
        c = 256
        for co in (128, 64, 32, 16):
            ups += [nn.Upsample(scale_factor=2), nn.Conv2d(c, co, 3, padding=1), nn.BatchNorm2d(co), nn.GELU()]
            c = co
        self.dec = nn.Sequential(*ups, nn.Conv2d(c, 3, 3, padding=1), nn.Sigmoid())

    def forward(self, tiny):
        return self.dec(self.enc(tiny).view(-1, 256, 7, 7))


tiny = torch.tensor(np.stack([cv2.resize(im, (6, 8), interpolation=cv2.INTER_AREA) for im in imgs])).permute(0, 3, 1, 2).float() / 255
target = torch.tensor(imgs).permute(0, 3, 1, 2).float() / 255
dec = Decoder().to(DEV)
opt = torch.optim.Adam(dec.parameters(), 2e-3)
tr_idx = np.where(~is_test)[0]
for ep in range(40):
    for b in np.array_split(rng.permutation(tr_idx), max(1, len(tr_idx) // 64)):
        loss = nn.functional.l1_loss(dec(tiny[b].to(DEV)), target[b].to(DEV))
        opt.zero_grad()
        loss.backward()
        opt.step()
    if ep % 10 == 9:
        print("epoch", ep + 1, "L1", round(loss.item(), 4))
dec.eval()
with torch.no_grad():
    recon = torch.cat([dec(tiny[b].to(DEV)).cpu() for b in np.array_split(np.where(is_test)[0], 20)])
recon = (recon.permute(0, 2, 3, 1).numpy() * 255).astype(np.uint8)
te_idx = np.where(is_test)[0]

# %%
from insightface.app import FaceAnalysis

fa = FaceAnalysis(name="buffalo_l", allowed_modules=["recognition"], providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
fa.prepare(ctx_id=0 if DEV == "cuda" else -1)
rec = fa.models["recognition"]


def emb(batch_rgb: np.ndarray) -> np.ndarray:
    out = []
    for i in range(0, len(batch_rgb), 128):
        f = rec.get_feat([im[..., ::-1] for im in batch_rgb[i:i + 128]])
        out.append(f / np.linalg.norm(f, axis=1, keepdims=True))
    return np.concatenate(out)


E_orig = emb(imgs[te_idx])
E_blur = emb(blur[te_idx])
E_rec = emb(recon)
test_lab = ids[te_idx]
# thư viện: ảnh gốc đầu tiên của mỗi người; mẫu thử: các ảnh còn lại
first = {}
for k, l in enumerate(test_lab):
    first.setdefault(l, k)
gal_k = np.array(list(first.values()))
gal_lab = test_lab[gal_k]
probe = np.array([k for k in range(len(te_idx)) if k not in set(gal_k)])


def top1(E):
    sims = E[probe] @ E_orig[gal_k].T
    return float((gal_lab[sims.argmax(1)] == test_lab[probe]).mean())


inv = pd.DataFrame([
    {"đầu vào nhận dạng": "Ảnh gốc (cận trên)", "top-1": top1(E_orig), "SSIM với gốc": 1.0},
    {"đầu vào nhận dạng": "Khung pixel hóa 6×8 (server nhận)", "top-1": top1(E_blur),
     "SSIM với gốc": float(np.mean([ssim(blur[i], imgs[i], channel_axis=2) for i in te_idx]))},
    {"đầu vào nhận dạng": "Mặt dựng lại bởi decoder", "top-1": top1(E_rec),
     "SSIM với gốc": float(np.mean([ssim(recon[k], imgs[i], channel_axis=2) for k, i in enumerate(te_idx)]))},
    {"đầu vào nhận dạng": "Đoán ngẫu nhiên", "top-1": 1 / len(gal_k), "SSIM với gốc": None},
])
inv.to_csv(OUT / "privacy_face_inversion.csv", index=False)
fig, axs = plt.subplots(3, 6, figsize=(9, 5))
for c in range(6):
    i = te_idx[c * 7]
    for r, im in enumerate([imgs[i], blur[i], recon[c * 7]]):
        axs[r, c].imshow(im)
        axs[r, c].axis("off")
for r, t in enumerate(["gốc", "pixel hóa", "dựng lại"]):
    axs[r, 0].set_title(t, loc="left", fontsize=9)
fig.tight_layout()
fig.savefig(OUT / "privacy_face_inversion.png", dpi=150)
inv

# %% [markdown]
# ## C. Membership inference: không DP vs DP-SGD
# Mô hình hợp nhất huấn luyện trên phiên **thành viên**; kẻ tấn công dùng mất mát của từng phiên để đoán phiên đó có nằm
# trong dữ liệu huấn luyện không (AUC 0,5 = không đoán được). DP-SGD: cắt gradient **theo từng phiên** (đơn vị riêng tư =
# một thí sinh) + nhiễu Gauss; ε tính bằng RDP accountant của Opacus.

# %%
from opacus.accountants import RDPAccountant
from sklearn.metrics import roc_auc_score

KS_B = kw[kw.label == "B"][KFEATS].values.astype(np.float32)
KS_C = kw[kw.label.isin(list(POS))][KFEATS].values.astype(np.float32)
pool = make_synthetic_sessions(360, 150, rng, ks_bona=KS_B, ks_copy=KS_C)
members, nonmembers, test_s = pool[:120], pool[120:240], pool[240:]
Xm = torch.tensor(np.stack([s["X"] for s in members]))
Ym = torch.tensor(np.stack([s["y"] for s in members]))


def session_loss(model, sess) -> np.ndarray:
    model.eval()
    with torch.no_grad():
        X = torch.tensor(np.stack([s["X"] for s in sess])).to(DEV)
        Y = torch.tensor(np.stack([s["y"] for s in sess])).float().to(DEV)
        logits, _, _ = model(X)
        return nn.functional.binary_cross_entropy_with_logits(logits, Y, reduction="none").mean(1).cpu().numpy()


def test_auc(model) -> float:
    model.eval()
    with torch.no_grad():
        X = torch.tensor(np.stack([s["X"] for s in test_s])).to(DEV)
        p = torch.sigmoid(model(X)[0]).cpu().numpy()
    return summarize(np.concatenate([s["y"] for s in test_s]), p.reshape(-1))["auc"]


def train_target(dp: bool, epochs=30, lr=2e-3, clip=1.0, sigma=1.0, batch=16):
    torch.manual_seed(SEED)
    model = build_fusion_model(dropout=0.0).to(DEV)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.Adam(params, lr)
    acct = RDPAccountant()
    q = batch / len(Xm)
    for ep in range(epochs):
        model.train()
        for idx in torch.randperm(len(Xm)).split(batch):
            if not dp:
                logits, _, _ = model(Xm[idx].to(DEV))
                loss = nn.functional.binary_cross_entropy_with_logits(logits, Ym[idx].float().to(DEV))
                opt.zero_grad()
                loss.backward()
                opt.step()
                continue
            # DP-SGD: gradient từng phiên -> cắt chuẩn -> cộng -> thêm nhiễu Gauss
            summed = [torch.zeros_like(p) for p in params]
            for i in idx:
                logits, _, _ = model(Xm[i:i + 1].to(DEV))
                loss = nn.functional.binary_cross_entropy_with_logits(logits, Ym[i:i + 1].float().to(DEV))
                g = torch.autograd.grad(loss, params)
                norm = torch.sqrt(sum((x ** 2).sum() for x in g))
                f = min(1.0, clip / (float(norm) + 1e-12))
                for s_, x in zip(summed, g):
                    s_.add_(x * f)
            for p, s_ in zip(params, summed):
                p.grad = (s_ + torch.randn_like(s_) * sigma * clip) / len(idx)
            opt.step()
            acct.step(noise_multiplier=sigma, sample_rate=q)
    eps = acct.get_epsilon(delta=1e-5) if dp else float("inf")
    return model, eps


mia = []
for dp, sigma in ((False, 0), (True, 0.8), (True, 1.5)):
    model, eps = train_target(dp, sigma=sigma)
    lm, ln = session_loss(model, members), session_loss(model, nonmembers)
    auc_attack = roc_auc_score(np.r_[np.ones(len(lm)), np.zeros(len(ln))], -np.r_[lm, ln])
    mia.append({"huấn luyện": "DP-SGD" if dp else "Thường", "sigma": sigma, "ε (δ=1e-5)": round(eps, 2),
                "AUC tấn công": round(auc_attack, 3), "AUC phát hiện (tập kiểm tra)": test_auc(model)})
mia = pd.DataFrame(mia)
mia.to_csv(OUT / "privacy_membership.csv", index=False)
mia

# %% [markdown]
# ## D. Huấn luyện liên kết (federated): tập trung vs FedAvg / FedProx / FedAvg + DP
# 5 "đơn vị" (khoa/trường) với dữ liệu **không đồng nhất** (khác tỉ lệ máy yếu và tỉ lệ gian lận). Dữ liệu không rời đơn vị;
# chỉ trọng số mô hình được gửi về và lấy trung bình. FedProx thêm phạt μ/2·‖w − w_global‖² để ổn định khi dữ liệu lệch.
# DP-FedAvg: cắt chuẩn cập nhật mỗi đơn vị và thêm nhiễu Gauss vào trung bình.

# %%
import copy

CLIENT_CFG = [((.7, .3, 0.), .3), ((.2, .6, .2), .5), ((0., .4, .6), .6), ((.5, .5, 0.), .2), ((.1, .3, .6), .7)]
clients = [make_synthetic_sessions(80, 150, rng, cheat_rate=cr, tier_p=tp, ks_bona=KS_B, ks_copy=KS_C) for tp, cr in CLIENT_CFG]
c_tensors = [(torch.tensor(np.stack([s["X"] for s in c])), torch.tensor(np.stack([s["y"] for s in c]))) for c in clients]


def local_train(model, X, Y, epochs=1, lr=2e-3, mu=0.0, global_params=None):
    opt = torch.optim.Adam(model.parameters(), lr)
    model.train()
    for _ in range(epochs):
        for idx in torch.randperm(len(X)).split(16):
            logits, _, _ = model(X[idx].to(DEV))
            loss = nn.functional.binary_cross_entropy_with_logits(logits, Y[idx].float().to(DEV))
            if mu > 0:
                loss = loss + mu / 2 * sum(((p - g) ** 2).sum() for p, g in zip(model.parameters(), global_params))
            opt.zero_grad()
            loss.backward()
            opt.step()
    return model


def federated(rounds=20, mu=0.0, dp_clip=None, dp_sigma=0.0):
    torch.manual_seed(SEED)
    glob = build_fusion_model().to(DEV)
    hist = []
    sizes = np.array([len(x) for x, _ in c_tensors], float)
    for r in range(rounds):
        gparams = [p.detach().clone() for p in glob.parameters()]
        updates = []
        for X, Y in c_tensors:
            local = copy.deepcopy(glob)
            local_train(local, X, Y, mu=mu, global_params=gparams)
            upd = [lp.detach() - gp for lp, gp in zip(local.parameters(), gparams)]
            if dp_clip:
                norm = torch.sqrt(sum((u ** 2).sum() for u in upd))
                upd = [u * min(1.0, dp_clip / (float(norm) + 1e-12)) for u in upd]
            updates.append(upd)
        w = sizes / sizes.sum()
        with torch.no_grad():
            for k, p in enumerate(glob.parameters()):
                avg = sum(w[c] * updates[c][k] for c in range(len(updates)))
                if dp_clip:
                    avg = avg + torch.randn_like(avg) * dp_sigma * dp_clip / len(updates)
                p.add_(avg)
        if r % 5 == 4:
            hist.append((r + 1, test_auc(glob)))
    return glob, hist


fed = []
central = build_fusion_model().to(DEV)
Xall = torch.cat([x for x, _ in c_tensors])
Yall = torch.cat([y for _, y in c_tensors])
local_train(central, Xall, Yall, epochs=20)
fed.append({"cách huấn luyện": "Tập trung (gom dữ liệu)", "AUC": test_auc(central)})
for name, kw_ in (("FedAvg", {}), ("FedProx μ=0.01", {"mu": 0.01}), ("FedAvg + DP (clip 1, σ 0.5)", {"dp_clip": 1.0, "dp_sigma": 0.5})):
    _, hist = federated(**kw_)
    fed.append({"cách huấn luyện": name, "AUC": hist[-1][1], "AUC theo vòng": hist})
fed = pd.DataFrame(fed)
fed.to_csv(OUT / "federated.csv", index=False)
fed

# %% [markdown]
# ## Tổng hợp cho bài báo
# Các file trên Drive: `privacy_utility_keystroke.{csv,png}`, `privacy_face_inversion.{csv,png}`, `privacy_membership.csv`,
# `federated.csv`. Khi viết: nêu rõ thí nghiệm C, D dùng phiên tổng hợp; A dùng dữ liệu keystroke công khai; B dùng LFW.

# %%
summary = {"keystroke_reid": curve.to_dict(orient="records"), "face_inversion": inv.to_dict(orient="records"),
           "membership": mia.to_dict(orient="records"), "federated": fed.drop(columns=["AUC theo vòng"], errors="ignore").to_dict(orient="records")}
json.dump(summary, open(OUT / "privacy_report.json", "w"), ensure_ascii=False, indent=1, default=str)
print(json.dumps(summary, ensure_ascii=False, indent=1, default=str)[:3000])
