import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy.optimize import minimize
import matplotlib.dates as mdates
import warnings

warnings.filterwarnings('ignore')

plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False


FILE_SOLAR = r"your path"
FILE_WIND = r"your path"
OUTPUT_DIR = "model1_result"


BOUNDS_WIND = (10000, 200000)
BOUNDS_SOLAR = (0, 100000)


TARGET_LOAD_KW = 50000  # kW
MAX_SHORTAGE_RATE = 0.10


def load_data():
    df_solar = pd.read_excel(FILE_SOLAR)
    df_wind = pd.read_excel(FILE_WIND)

    df_solar['time'] = pd.to_datetime(df_solar['time'])
    df_wind['time'] = pd.to_datetime(df_wind['time'])


    cf_solar = df_solar['electricity/KW'].values[:8760] / df_solar['electricity/KW'].max()
    cf_wind = df_wind['electricity/KW'].values[:8760] / df_wind['electricity/KW'].max()
    time_series = df_solar['time'].values[:8760]

    return cf_wind, cf_solar, time_series

def optimize_complementarity(cf_wind, cf_solar):
    def objective(x):
        cap_wind, cap_solar = x
        power = cap_wind * cf_wind + cap_solar * cf_solar
        if np.sum(power) == 0:
            return 1.0
        curtailment = np.maximum(power - TARGET_LOAD_KW, 0)
        curtail_rate = np.sum(curtailment) / np.sum(power)
        return curtail_rate

    def constraint_shortage(x):
        cap_wind, cap_solar = x
        power = cap_wind * cf_wind + cap_solar * cf_solar
        shortage = np.maximum(TARGET_LOAD_KW - power, 0)
        shortage_rate = np.sum(shortage) / (TARGET_LOAD_KW * 8760)
        return MAX_SHORTAGE_RATE - shortage_rate

    x0 = [
        (BOUNDS_WIND[0] + BOUNDS_WIND[1]) / 2,
        (BOUNDS_SOLAR[0] + BOUNDS_SOLAR[1]) / 2
    ]
    bounds = [BOUNDS_WIND, BOUNDS_SOLAR]
    cons = [{'type': 'ineq', 'fun': constraint_shortage}]

    result = minimize(objective, x0, method='SLSQP', bounds=bounds, constraints=cons)
    return result

if __name__ == "__main__":
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    cf_wind, cf_solar, time_series = load_data()
    res = optimize_complementarity(cf_wind, cf_solar)

    opt_wind_kw, opt_solar_kw = res.x
    total_power = opt_wind_kw * cf_wind + opt_solar_kw * cf_solar

    curtail_rate = res.fun
    ratio = opt_wind_kw / opt_solar_kw if opt_solar_kw > 0 else float('inf')

    print("=== 模块一：风光互补优化结果 ===")
    print(f"设定目标基准线: {TARGET_LOAD_KW / 1000:.2f} MW")
    print(f"最优风电规模: {opt_wind_kw / 1000:.2f} MW")
    print(f"最优光伏规模: {opt_solar_kw / 1000:.2f} MW")
    print(f"最优风光配比 (风:光): {ratio:.2f} : 1")
    print(f"基准线弃电率: {curtail_rate * 100:.2f}%")

    # --- 保存数据至 Excel ---
    result_df = pd.DataFrame({
        '最优风电规模(kW)': [opt_wind_kw],
        '最优光伏规模(kW)': [opt_solar_kw],
        '风光配比(风比光)': [ratio],
        '基准弃电率': [curtail_rate]
    })
    excel_path = os.path.join(OUTPUT_DIR, "optimal_capacity.xlsx")
    result_df.to_excel(excel_path, index=False)
    print(f"\n[数据已保存] 规模及配比数据已输出至: {excel_path}")

    # --- 绘图并保存 ---
    plt.figure(figsize=(12, 6))
    plt.plot(time_series[:168], total_power[:168] / 1000, label='综合出力', color='green')
    plt.plot(time_series[:168], opt_wind_kw * cf_wind[:168] / 1000, label='风电出力', alpha=0.6)
    plt.plot(time_series[:168], opt_solar_kw * cf_solar[:168] / 1000, label='光伏出力', alpha=0.6)

    # 绘制实际基准线
    plt.axhline(y=TARGET_LOAD_KW / 1000, color='red', linestyle='--', linewidth=2, label='实际设定基准线')

    # 填充弃电部分 (仅作可视化参考)
    power_plot = total_power[:168] / 1000
    target_plot = np.full(168, TARGET_LOAD_KW / 1000)
    plt.fill_between(time_series[:168], target_plot, power_plot,
                     where=(power_plot > target_plot),
                     color='gray', alpha=0.3, hatch='//', label='弃电区域')

    plt.title(f'风光互补出力曲线与基准线匹配 (首周) - 风光比 {ratio:.2f}:1')
    plt.ylabel('功率 (MW)')
    plt.legend(loc='upper right')
    plt.grid(True, alpha=0.3)
    plt.tight_layout()

    img_path = os.path.join(OUTPUT_DIR, "complementarity_plot.png")
    plt.savefig(img_path, dpi=300)
    print(f"[图像已保存] 出力曲线图已输出至: {img_path}")

    plt.show()