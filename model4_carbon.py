import os
import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import warnings

warnings.filterwarnings('ignore')


plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False


FILE_SOLAR = r"E:\JJ\河南固始县_2021至2025光资源.xlsx"
FILE_WIND = r"E:\JJ\河南固始县_2021至2025风资源.xlsx"
INPUT_EXCEL = os.path.join("model2_result", "physical_scale.xlsx")
OUTPUT_DIR = "model4_carbon_result"


EF_GRID = 0.5703
EF_WIND = 0.011
EF_SOLAR = 0.040

EF_BASELINE = 2.50
ISCC_THRESHOLD = 0.70


def load_resource_data():
    df_solar = pd.read_excel(FILE_SOLAR)
    df_wind = pd.read_excel(FILE_WIND)
    cf_solar = df_solar['electricity/KW'].values[:8760] / df_solar['electricity/KW'].max()
    cf_wind = df_wind['electricity/KW'].values[:8760] / df_wind['electricity/KW'].max()
    return cf_wind, cf_solar

try:
    df_phy = pd.read_excel(INPUT_EXCEL)
    WIND_KW = float(df_phy.loc[0, '风电装机(kW)'])
    SOLAR_KW = float(df_phy.loc[0, '光伏装机(kW)'])
    NH3_YIELD_TONS = float(df_phy.loc[0, '全年绿氨产量(吨)'])
    EL_POWER_KWH = float(df_phy.loc[0, '制氢耗电(kWh)'])
    NH3_POWER_KWH = float(df_phy.loc[0, '制氨耗电(kWh)'])
    GRID_BUY_KWH = float(df_phy.loc[0, '电网购电量(kWh)'])
    print("成功读取物理规模数据，开始执行 ISCC-EU 碳足迹溯源分析...\n")
except FileNotFoundError:
    print(f"错误：未找到 {INPUT_EXCEL}。请先运行 model2.py！")
    sys.exit()

cf_wind, cf_solar = load_resource_data()


nh3_yield_kg = NH3_YIELD_TONS * 1000

# 1. 绿电获取阶段的 LCA 碳排放 (制造安装等)
wind_gen_kwh = np.sum(WIND_KW * cf_wind)
solar_gen_kwh = np.sum(SOLAR_KW * cf_solar)
emissions_wind = wind_gen_kwh * EF_WIND
emissions_solar = solar_gen_kwh * EF_SOLAR
emissions_green_lca = emissions_wind + emissions_solar


emissions_grid_total = GRID_BUY_KWH * EF_GRID


total_process_power = EL_POWER_KWH + NH3_POWER_KWH
ratio_el = EL_POWER_KWH / total_process_power if total_process_power > 0 else 0
ratio_nh3 = NH3_POWER_KWH / total_process_power if total_process_power > 0 else 0

emissions_grid_el = emissions_grid_total * ratio_el
emissions_grid_nh3 = emissions_grid_total * ratio_nh3


total_emissions_kgCO2 = emissions_green_lca + emissions_grid_total


se_project = total_emissions_kgCO2 / nh3_yield_kg if nh3_yield_kg > 0 else 0


ghg_saving_pct = ((EF_BASELINE - se_project) / EF_BASELINE) * 100 if EF_BASELINE > 0 else 0
is_compliant = ghg_saving_pct >= (ISCC_THRESHOLD * 100)


print("=" * 45)
print("【绿氨全生命周期碳足迹与 ISCC-EU 合规报告】")
print(f"▶ 边界内总碳排放量 : {total_emissions_kgCO2 / 1000:.2f} 吨 CO2eq")
print("-" * 45)
print("【LCA 碳排放溯源拆解】")
print(f"  ├─ 风光绿电基建LCA: {emissions_green_lca / 1000:.2f} 吨 CO2eq")
print(f"  ├─ 电网补电制氢分摊: {emissions_grid_el / 1000:.2f} 吨 CO2eq")
print(f"  └─ 电网补电制氨分摊: {emissions_grid_nh3 / 1000:.2f} 吨 CO2eq")
print("-" * 45)
print("【产品碳强度与合规结论】")
print(f"▶ 绿氨实际碳排强度 : {se_project:.3f} kgCO2eq / kgNH3")
print(f"▶ 化石基氨参考基线 : {EF_BASELINE:.3f} kgCO2eq / kgNH3")
print(f"▶ 全生命周期减排率 : {ghg_saving_pct:.2f}%")
print("-" * 45)
if is_compliant:
    print(f"[通过] 恭喜！本项目符合 ISCC-EU 认证体系标准，减排率超过 {ISCC_THRESHOLD*100}% 阈值。")
else:
    print(f"[警告] 未通过！本项目减排率低于 {ISCC_THRESHOLD*100}%，建议降低网电依赖或扩建绿电！")
print("=" * 45 + "\n")


if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))


labels = ['化石基灰氨\n(ISCC 基准线)', '本项目规划绿氨']
values = [EF_BASELINE, se_project]
bars = ax1.bar(labels, values, color=['#7f7f7f', '#2ca02c'], width=0.5)


ax1.set_ylabel('碳排放强度 (kgCO$_2$eq / kgNH$_3$)', fontsize=12)
ax1.set_title(f'绿氨产品全生命周期减排效益 (减排 {ghg_saving_pct:.1f}%)', fontsize=14)
ax1.axhline(y=EF_BASELINE * (1 - ISCC_THRESHOLD), color='red', linestyle='--', linewidth=2, label=f'ISCC-EU {ISCC_THRESHOLD*100}% 减排红线')

for bar in bars:
    yval = bar.get_height()
    ax1.text(bar.get_x() + bar.get_width()/2, yval + 0.05, f'{yval:.3f}', ha='center', va='bottom', fontsize=12, fontweight='bold')
ax1.legend(loc='upper right')
ax1.grid(axis='y', linestyle=':', alpha=0.6)


pie_labels = ['风光基建LCA溯源', '电网补电(制氢分摊)', '电网补电(制氨分摊)']
pie_sizes = [emissions_green_lca, emissions_grid_el, emissions_grid_nh3]

if sum(pie_sizes) > 0:
    ax2.pie([x for x in pie_sizes if x > 0],
            labels=[l for l, x in zip(pie_labels, pie_sizes) if x > 0],
            autopct='%1.1f%%', startangle=90, colors=['#8c564b', '#1f77b4', '#e377c2'])
    ax2.set_title('全生命周期边界残余碳排 LCA 溯源拆解', fontsize=14)

plt.tight_layout()
img_path = os.path.join(OUTPUT_DIR, "carbon_reduction_analysis.png")
plt.savefig(img_path, dpi=300)
print(f"[图像导出] 碳足迹对比图已保存至: {img_path}")
plt.show()