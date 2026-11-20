# -*- coding: utf-8 -*-
"""模块一：风光资源匹配寻优。"""
import os
import sys
import numpy as np
import pandas as pd
from scipy.optimize import minimize


def load_resource_data(file_wind, file_solar):
    """读取风光Excel前8760小时，按各自资源序列最大值归一化。"""
    dfw_all = pd.read_excel(file_wind)
    dfs_all = pd.read_excel(file_solar)
    dfw = dfw_all.copy()
    dfs = dfs_all.copy()
    required = {"time", "electricity/KW"}
    for label, df in (("风电", dfw), ("光伏", dfs)):
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"{label}资源文件缺少字段：{sorted(missing)}")
        if len(df) < 8760:
            raise ValueError(f"{label}资源数据不足8760小时，当前{len(df)}条。")
    dfw = dfw.iloc[:8760].copy()
    dfs = dfs.iloc[:8760].copy()
    tw = pd.to_datetime(dfw["time"], errors="coerce")
    ts = pd.to_datetime(dfs["time"], errors="coerce")
    if tw.isna().any() or ts.isna().any():
        raise ValueError("风光资源时间列含有无法识别的时间。")
    if not np.array_equal(tw.to_numpy(), ts.to_numpy()):
        raise ValueError("风电与光伏前8760小时的时间戳不一致。")
    w_all = pd.to_numeric(dfw_all["electricity/KW"], errors="coerce")
    s_all = pd.to_numeric(dfs_all["electricity/KW"], errors="coerce")
    wmax, smax = float(w_all.max()), float(s_all.max())
    if not np.isfinite(wmax) or wmax <= 0 or not np.isfinite(smax) or smax <= 0:
        raise ValueError("风光资源列最大值必须大于0。")
    wv = pd.to_numeric(dfw["electricity/KW"], errors="coerce").to_numpy(float)
    sv = pd.to_numeric(dfs["electricity/KW"], errors="coerce").to_numpy(float)
    if not np.all(np.isfinite(wv)) or not np.all(np.isfinite(sv)):
        raise ValueError("风光资源数据存在空值或非数值。")
    if np.any(wv < 0) or np.any(sv < 0):
        raise ValueError("风光资源出力不能为负数。")
    return wv / wmax, sv / smax, ts.to_numpy()


def optimize_complementarity(cf_w, cf_s, bounds_w, bounds_s,
                             target_load_kw, max_shortage, max_curtail):
    """最小化理论弃电率，受风光资源缺额率和理论弃电率双重约束。"""
    w, s = np.asarray(cf_w, float), np.asarray(cf_s, float)
    if len(w) != 8760 or len(s) != 8760:
        raise ValueError("模块一必须使用8760小时风光数据。")
    if target_load_kw <= 0:
        raise ValueError("目标供电基准线必须大于0。")

    def rates(x):
        p = x[0] * w + x[1] * s
        gen = float(p.sum())
        if gen <= 0:
            return 1.0, 1.0
        shortage = np.maximum(target_load_kw - p, 0).sum() / (target_load_kw * 8760)
        curtail = np.maximum(p - target_load_kw, 0).sum() / gen
        return float(shortage), float(curtail)

    def objective(x):
        return rates(x)[1]

    cons = [
        {"type": "ineq", "fun": lambda x: max_shortage - rates(x)[0]},
        {"type": "ineq", "fun": lambda x: max_curtail - rates(x)[1]},
    ]
    bounds = [tuple(bounds_w), tuple(bounds_s)]
    starts = [
        [(bounds_w[0] + bounds_w[1]) / 2, (bounds_s[0] + bounds_s[1]) / 2],
        [bounds_w[0], bounds_s[0]], [bounds_w[1], bounds_s[1]],
        [bounds_w[0], bounds_s[1]], [bounds_w[1], bounds_s[0]],
    ]
    feasible = []
    messages = []
    for x0 in starts:
        res = minimize(objective, np.clip(x0, [b[0] for b in bounds], [b[1] for b in bounds]),
                       method="SLSQP", bounds=bounds, constraints=cons,
                       options={"maxiter": 400, "ftol": 1e-9})
        messages.append(str(res.message))
        sr, cr = rates(res.x)
        if (np.all(np.isfinite(res.x)) and
            sr <= max_shortage + 1e-6 and cr <= max_curtail + 1e-6):
            res.shortage_rate, res.curtail_rate = sr, cr
            feasible.append((cr, res))
    if not feasible:
        raise ValueError("当前风光装机边界下不存在同时满足缺额率和理论弃电率的组合。"
                         + "优化器信息：" + " | ".join(messages))
    feasible.sort(key=lambda z: z[0])
    return feasible[0][1]


if __name__ == "__main__":
    try:
        cf_w, cf_s, _ = load_resource_data(
            os.getenv("FILE_WIND", "风资源.xlsx"),
            os.getenv("FILE_SOLAR", "光资源.xlsx"))
        target_mw = float(os.getenv("TARGET_LOAD_MW", "50"))
        max_shortage = float(os.getenv("MAX_SHORTAGE_RATE", "0.10"))
        max_curtail = float(os.getenv("MAX_CURTAIL_RATE", "0.20"))
        wb = (float(os.getenv("WIND_MIN_MW", "10")) * 1000,
              float(os.getenv("WIND_MAX_MW", "200")) * 1000)
        sb = (float(os.getenv("SOLAR_MIN_MW", "0")) * 1000,
              float(os.getenv("SOLAR_MAX_MW", "100")) * 1000)
        res = optimize_complementarity(cf_w, cf_s, wb, sb, target_mw * 1000,
                                       max_shortage, max_curtail)
        wind, solar = map(float, res.x)
        os.makedirs("model1_result", exist_ok=True)
        pd.DataFrame([{
            "最优风电规模(kW)": wind, "最优光伏规模(kW)": solar,
            "风光配比(风比光)": wind / solar if solar else np.inf,
            "目标供电基准线(MW)": target_mw,
            "风光资源缺额率(%)": res.shortage_rate * 100,
            "理论弃电率(%)": res.curtail_rate * 100,
            "最大允许缺额率(%)": max_shortage * 100,
            "最大允许弃电率(%)": max_curtail * 100,
        }]).to_excel("model1_result/optimal_capacity.xlsx", index=False)
        print("模块一完成：", wind / 1000, "MW风电；", solar / 1000, "MW光伏")
    except Exception as exc:
        print("模块一计算失败：", exc)
        sys.exit(1)
