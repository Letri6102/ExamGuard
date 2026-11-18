# %% [markdown]
# # 01 · Phát hiện chép câu trả lời LLM bằng nhịp gõ phím (song ngữ Việt – Anh)
#
# Notebook này phục vụ **C1** (phương thức keystroke trong mô hình hợp nhất) và chuẩn bị dữ liệu cho notebook 03, 04.
#
# **Dữ liệu (tự tải):**
# | Bộ | Ngôn ngữ | Nhãn | Nguồn |
# |---|---|---|---|
# | Dong et al., ICTAI 2026 (arXiv 2610.07700) | Tiếng Việt, 45 người | B (tự viết), P (diễn đạt lại câu trả lời LLM), T (chép LLM), FP/FT (P/T có thao túng nhịp gõ) | Google Drive của tác giả, `github.com/dongphuthanh/Vietnamese_keystrokes` |
# | Kundu et al., IJCB 2024 (arXiv 2406.15335) | Tiếng Anh, 40 người + SBU + Buffalo | free (tự viết) / fixed (chép) | `github.com/ijcb-2024/keystroke-llm-plagiarism` (Git LFS) |
#
# Hãy kiểm tra giấy phép của từng bộ dữ liệu trước khi dùng cho bài báo (README của hai repo không ghi license).
#
# **Đặc trưng:** dùng ĐÚNG công thức mà trình duyệt thí sinh tính (cửa sổ 2 giây, chuẩn hóa theo đoạn khởi động 60 giây,
# lượng tử hóa + nhiễu Laplace) — xem `eg_common.py`. Nhờ vậy mô hình huấn luyện ở đây chạy được trên dữ liệu thật của nền tảng.
#
# **Thí nghiệm:**
# 1. Baseline XGBoost trên đặc trưng tổng hợp mỗi câu trả lời.
# 2. Mô hình chuỗi (TCN nhỏ) trên chuỗi cửa sổ 2 giây.
# 3. Huấn luyện riêng từng ngôn ngữ / chung hai ngôn ngữ / chéo ngôn ngữ.
# 4. Chống né tránh: huấn luyện trên B, P, T rồi kiểm tra trên FP, FT (người gian lận cố gõ giống tự nhiên).
#
# Quy tắc: **chia theo người** (không người nào vừa ở tập huấn luyện vừa ở tập kiểm tra); báo cáo AUC, recall @ FPR 1% / 5%.
#
# Thời gian chạy: ~10–20 phút trên Colab (CPU đủ; GPU giúp phần TCN nhanh hơn).

# %%
!pip -q install gdown xgboost
!apt-get -qq install -y git-lfs > /dev/null && git lfs install --skip-repo > /dev/null

# %% [markdown]
# ## 1. Lưu kết quả lên Google Drive (khuyến nghị)
# Notebook 03 và 04 đọc file `keystroke_windows.parquet` tạo ở đây. Bỏ qua ô này nếu không dùng Drive.

# %%
import os
from pathlib import Path

OUT = Path("/content/examguard")
try:
    from google.colab import drive
    drive.mount("/content/drive")
    OUT = Path("/content/drive/MyDrive/examguard")
except Exception as e:  # chạy ngoài Colab
    print("Không mount được Drive:", e)
OUT.mkdir(parents=True, exist_ok=True)
DATA = Path("/content/data") if Path("/content").exists() else Path("data")
DATA.mkdir(parents=True, exist_ok=True)
print("Kết quả lưu tại", OUT)

# %% [markdown]
# ## 2. Tải dữ liệu
# Bộ tiếng Việt: thư mục `Attack4` (gồm file bình thường và file đã thao túng). Bộ tiếng Anh: clone repo kèm Git LFS.
# Nếu Google Drive báo quá lượt tải, mở link trong trình duyệt, "Add shortcut to Drive" rồi đặt `VI_DIR` trỏ tới đó.

# %%
import subprocess, zipfile

VI_DIR = DATA / "Attack4"
if not any(VI_DIR.glob("User*")):
    subprocess.run(["gdown", "--folder", "https://drive.google.com/drive/folders/1NdT6VrEq26uF2DigYAlRVg1wT-xFlSHT",
                    "-O", str(VI_DIR)], check=False)
print("Tiếng Việt:", len(list(VI_DIR.glob("User*"))), "người")

EN_REPO = DATA / "keystroke-llm-plagiarism"
if not EN_REPO.exists():
    subprocess.run(["git", "clone", "--depth", "1", "https://github.com/ijcb-2024/keystroke-llm-plagiarism", str(EN_REPO)], check=False)
    subprocess.run(["git", "lfs", "pull"], cwd=EN_REPO, check=False)
EN_DIR = DATA / "en"
for z in (EN_REPO / "Datasets").glob("*.zip"):
    if z.stat().st_size < 1000:
        print("⚠️", z.name, "vẫn là con trỏ Git LFS (hết băng thông LFS?). Tải tay từ GitHub và giải nén vào", EN_DIR)
        continue
    with zipfile.ZipFile(z) as f:
        f.extractall(EN_DIR / z.stem.replace(" ", "_"))
print("Tiếng Anh:", sorted(str(p.relative_to(EN_DIR)) for p in EN_DIR.rglob("*") if p.is_file())[:20])

# %% [markdown]
# ## 3. Đọc dữ liệu về một định dạng chung
# Mỗi **mẫu** = một câu trả lời: `(ngôn ngữ, người, câu hỏi, nhãn, danh sách sự kiện phím)`; sự kiện = `(t_ms, 'down'|'up', code, ime)`.
#
# - Tiếng Việt: `{"keystrokes": [{key, code, event, timestamp, question_index "phiên.câu"}]}`; phiên 1 = B, 2 = P, 3 = T;
#   file `*_attack.json`: phiên 2 = FP, 3 = FT. Phím ảo của Unikey đã được tác giả loại bỏ.
# - Tiếng Anh: `{id: {"keyboard_data": [[KD|KU, phím, t], ...]}}` (một số file lưu giá trị là chuỗi JSON, một số đảo thứ tự cột);
#   `free` = tự viết (B), `fixed` = chép văn bản cho sẵn (T).

# %%
import json
from collections import defaultdict

import numpy as np
import pandas as pd

from eg_common import session_keystroke_windows, summarize

VI_LABEL = {("normal", "1"): "B", ("normal", "2"): "P", ("normal", "3"): "T", ("attack", "2"): "FP", ("attack", "3"): "FT"}


def load_vietnamese(root: Path) -> list[dict]:
    samples = []
    for user_dir in sorted(root.glob("User*")):
        for f in sorted(user_dir.glob("*.json")):
            kind = "attack" if "attack" in f.name else "normal"
            phase = "2" if f.name.startswith("second") else "1"
            data = json.loads(f.read_text(encoding="utf-8"))
            by_q = defaultdict(list)
            for e in data.get("keystrokes", []):
                qi = str(e.get("question_index", ""))
                if "." not in qi:
                    continue
                sess, q = qi.split(".", 1)
                lab = VI_LABEL.get((kind, sess))
                if lab is None or (kind == "attack" and sess == "1"):
                    continue
                typ = "down" if str(e.get("event", "")).lower() == "keydown" else "up"
                by_q[(lab, q)].append((float(e["timestamp"]), typ, e.get("code") or e.get("key"), False))
            for (lab, q), ev in by_q.items():
                samples.append({"lang": "vi", "user": user_dir.name, "question": f"{phase}.{q}", "label": lab, "events": ev})
    return samples


def _rows(v):
    if isinstance(v, str):
        v = json.loads(v)
    if isinstance(v, dict):
        v = v.get("keyboard_data", v.get("data", []))
    return v


def load_english(root: Path) -> list[dict]:
    samples = []
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in (".json", ".txt"):
            continue
        name = f.name.lower()
        if "fixed" in name:
            lab = "T"
        elif "free" in name:
            lab = "B"
        else:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            continue
        source = "buffalo" if "buffalo" in name else "sbu" if "raw_temp" in name else "proposed"
        for key, val in data.items():
            ev = []
            for r in _rows(val):
                if len(r) < 3:
                    continue
                a, b = str(r[0]).upper(), str(r[1]).upper()
                if a in ("KD", "KU"):
                    typ, k = a, r[1]
                elif b in ("KD", "KU"):
                    typ, k = b, r[0]
                else:
                    continue
                ev.append((float(r[2]), "down" if typ == "KD" else "up", str(k).lower(), False))
            if len(ev) >= 20:
                user = f"{source}:" + str(key).split("_")[0]
                samples.append({"lang": "en", "user": user, "question": str(key), "label": lab, "events": ev, "source": source})
    return samples


vi = load_vietnamese(VI_DIR)
en = load_english(EN_DIR)
samples = vi + en
df_s = pd.DataFrame([{k: v for k, v in s.items() if k != "events"} | {"n_events": len(s["events"])} for s in samples])
print(df_s.groupby(["lang", "label"]).agg(samples=("user", "size"), users=("user", "nunique"), events=("n_events", "median")))

# %% [markdown]
# ## 4. Cắt thành cửa sổ 2 giây bằng đúng công thức của trình duyệt
# `epsilon=inf`: chưa thêm nhiễu (notebook 04 đánh giá ảnh hưởng của nhiễu Laplace lên độ chính xác và quyền riêng tư).

# %%
from eg_common import FEATURES, MOD_OF

KFEATS = [f for f in FEATURES if MOD_OF[f] == "keystroke"]
rows = []
for sid, s in enumerate(samples):
    ws = session_keystroke_windows(s["events"], warmup_s=20, epsilon=float("inf"))
    for t, w in enumerate(ws):
        if w["keys"] == 0:
            continue
        rows.append({"sample": sid, "t": t, "lang": s["lang"], "user": s["user"], "label": s["label"], **w})
win = pd.DataFrame(rows)
win.to_parquet(OUT / "keystroke_windows.parquet")
print(win.shape, "->", OUT / "keystroke_windows.parquet")
win.groupby("label")[KFEATS].mean().round(3)

# %% [markdown]
# ## 5. Baseline: XGBoost trên đặc trưng tổng hợp mỗi câu trả lời
# Nhãn nhị phân: **gian lận = có hỗ trợ LLM (P, T, FP, FT)**, bình thường = B. Chia theo người (GroupKFold).

# %%
from sklearn.model_selection import GroupKFold
from xgboost import XGBClassifier

POS = {"P", "T", "FP", "FT"}


def aggregate(w: pd.DataFrame) -> pd.DataFrame:
    g = w.groupby("sample")
    agg = g[KFEATS].agg(["mean", "std", "median"]).fillna(0)
    agg.columns = [f"{a}_{b}" for a, b in agg.columns]
    meta = g[["lang", "user", "label"]].first()
    return agg.join(meta).join(g.size().rename("n_windows"))


A = aggregate(win)
FEAT_COLS = [c for c in A.columns if c not in ("lang", "user", "label")]


def fit_xgb(tr: pd.DataFrame, seed=0) -> XGBClassifier:
    clf = XGBClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
                        random_state=seed, eval_metric="logloss")
    return clf.fit(tr[FEAT_COLS], tr.label.isin(POS).astype(int))


def cv_xgb(train_df: pd.DataFrame, test_df: pd.DataFrame | None = None, train_labels=("B", "P", "T"), folds=5, seed=0):
    """test_df=None: kiểm tra chéo theo NGƯỜI trên train_df (fold kiểm tra giữ mọi nhãn của người đó).
    test_df có giá trị: huấn luyện trên toàn bộ train_df, kiểm tra trên test_df (chéo ngôn ngữ).
    Trả về (y, điểm, nhãn gốc)."""
    tr_all = train_df[train_df.label.isin(train_labels)]
    if test_df is not None:
        clf = fit_xgb(tr_all, seed)
        return test_df.label.isin(POS).astype(int).values, clf.predict_proba(test_df[FEAT_COLS])[:, 1], test_df.label.values
    ys, ss, labs = [], [], []
    users = np.array(sorted(train_df.user.unique()))
    for tr_u, te_u in GroupKFold(n_splits=min(folds, len(users))).split(users, groups=users):
        clf = fit_xgb(tr_all[tr_all.user.isin(users[tr_u])], seed)
        te = train_df[train_df.user.isin(users[te_u])]
        ys.append(te.label.isin(POS).astype(int).values)
        ss.append(clf.predict_proba(te[FEAT_COLS])[:, 1])
        labs.append(te.label.values)
    return np.concatenate(ys), np.concatenate(ss), np.concatenate(labs)


results = []
A_main = A[A.label.isin(["B", "P", "T"])]  # FP/FT để riêng cho thí nghiệm né tránh (mục 6)
for lang in ("vi", "en"):
    sub = A_main[A_main.lang == lang]
    if len(sub) < 20:
        continue
    y, s, lab = cv_xgb(sub)
    results.append({"setting": f"riêng {lang}", "model": "XGBoost", **summarize(y, s)})
y, s, lab = cv_xgb(A_main)
results.append({"setting": "chung vi+en", "model": "XGBoost", **summarize(y, s)})
if (A_main.lang == "vi").any() and (A_main.lang == "en").any():
    for a, b in (("vi", "en"), ("en", "vi")):
        y, s, _ = cv_xgb(A_main[A_main.lang == a], A_main[A_main.lang == b])
        results.append({"setting": f"chéo {a}→{b}", "model": "XGBoost", **summarize(y, s)})
pd.DataFrame(results)

# %% [markdown]
# ## 6. Chống né tránh: huấn luyện B/P/T, kiểm tra trên FP/FT (tiếng Việt)
# FP/FT là câu trả lời có hỗ trợ LLM nhưng **nhịp gõ bị thao túng** (đổi tốc độ, chèn sửa xóa, chèn ngập ngừng).
# Recall giảm mạnh ở đây là lý do cần hợp nhất nhiều phương thức (C1) thay vì chỉ dựa vào keystroke.

# %%
vi_A = A[A.lang == "vi"]
evasion = []
for train_labels in (("B", "P", "T"), ("B", "P", "T", "FP", "FT")):
    y, s, lab = cv_xgb(vi_A, train_labels=train_labels)
    # kiểm tra trên toàn bộ nhãn của người ở fold kiểm tra
    for target in ("P", "T", "FP", "FT"):
        m = np.isin(lab, ["B", target])
        evasion.append({"train": "+".join(train_labels), "test": f"B vs {target}", **summarize(y[m], s[m])})
pd.DataFrame(evasion)

# %% [markdown]
# ## 7. Mô hình chuỗi (TCN nhỏ) trên chuỗi cửa sổ 2 giây
# Đầu vào là chuỗi vector keystroke (10 chiều) — đúng thứ mô hình hợp nhất nhận ở notebook 03.

# %%
import torch
from torch import nn

DEV = "cuda" if torch.cuda.is_available() else "cpu"
SCALE = np.array([10, 1, 1, 1, 1, 1, 1, 0.3, 3, 5], np.float32)  # khớp "scale" trong feature_schema.json
MAXLEN = 120


def to_seq(w: pd.DataFrame):
    X, y, users, labs, langs = [], [], [], [], []
    for sid, g in w.groupby("sample"):
        x = np.clip(g.sort_values("t")[KFEATS].values.astype(np.float32) / SCALE, -5, 5)[:MAXLEN]
        pad = np.zeros((MAXLEN, len(KFEATS)), np.float32)
        pad[:len(x)] = x
        X.append(pad)
        y.append(int(g.label.iloc[0] in POS))
        users.append(g.user.iloc[0])
        labs.append(g.label.iloc[0])
        langs.append(g.lang.iloc[0])
    return np.stack(X), np.array(y), np.array(users), np.array(labs), np.array(langs)


class TCN(nn.Module):
    def __init__(self, d_in=len(KFEATS), d=48):
        super().__init__()
        layers, c = [], d_in
        for dil in (1, 2, 4, 8):
            layers += [nn.Conv1d(c, d, 3, padding=dil, dilation=dil), nn.GELU(), nn.Dropout(0.1)]
            c = d
        self.net = nn.Sequential(*layers)
        self.out = nn.Linear(d, 1)

    def forward(self, x):  # [B, T, F]
        mask = (x.abs().sum(-1, keepdim=True) > 0).float()
        h = self.net(x.transpose(1, 2)).transpose(1, 2) * mask
        return self.out(h.sum(1) / mask.sum(1).clamp(min=1)).squeeze(-1)


def train_tcn(X, y, epochs=40, seed=0):
    torch.manual_seed(seed)
    m = TCN().to(DEV)
    opt = torch.optim.AdamW(m.parameters(), 2e-3, weight_decay=1e-3)
    Xt, yt = torch.tensor(X).to(DEV), torch.tensor(y, dtype=torch.float32).to(DEV)
    pos_w = torch.tensor((1 - y.mean()) / max(y.mean(), 1e-3), device=DEV)
    for _ in range(epochs):
        m.train()
        for idx in torch.randperm(len(Xt)).split(64):
            loss = nn.functional.binary_cross_entropy_with_logits(m(Xt[idx]), yt[idx], pos_weight=pos_w)
            opt.zero_grad()
            loss.backward()
            opt.step()
    return m.eval()


Xs, ys, us, ls, langs = to_seq(win)
tcn_rows = []
for lang in ("vi", "en", "all"):
    sel = (langs == lang) if lang != "all" else np.ones(len(ys), bool)
    sel &= np.isin(ls, ["B", "P", "T"])
    if sel.sum() < 30:
        continue
    yy, ss = [], []
    for tr, te in GroupKFold(min(5, len(set(us[sel])))).split(Xs[sel], groups=us[sel]):
        m = train_tcn(Xs[sel][tr], ys[sel][tr])
        with torch.no_grad():
            ss.append(torch.sigmoid(m(torch.tensor(Xs[sel][te]).to(DEV))).cpu().numpy())
        yy.append(ys[sel][te])
    tcn_rows.append({"setting": lang, "model": "TCN", **summarize(np.concatenate(yy), np.concatenate(ss))})
pd.DataFrame(tcn_rows)

# %% [markdown]
# ## 8. Lưu kết quả
# - `keystroke_windows.parquet`: cửa sổ 2 giây có nhãn (dùng ở notebook 03 để chèn đoạn "chép bài" vào phiên thi tổng hợp,
#   và notebook 04 cho thí nghiệm tái nhận dạng người dùng).
# - `results_keystroke.csv`: bảng số liệu cho bài báo.

# %%
pd.concat([pd.DataFrame(results), pd.DataFrame(evasion), pd.DataFrame(tcn_rows)]).to_csv(OUT / "results_keystroke.csv", index=False)
print("Đã lưu", OUT / "results_keystroke.csv")
