import os
import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import numpy_financial as npf
import warnings

warnings.filterwarnings('ignore')

plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False

INPUT_EXCEL = os.path.join("model2_result", "physical_scale.xlsx")
OUTPUT_DIR = "economics_result"

try:
    df_phy = pd.read_excel(INPUT_EXCEL)
    WIND_KW = float(df_phy.loc[0, '风电装机(kW)'])
    SOLAR_KW = float(df_phy.loc[0, '光伏装机(kW)'])
    EL_KW = float(df_phy.loc[0, '电解槽功率(kW)'])
    BAT_KWH = float(df_phy.loc[0, '储能容量(kWh)'])
    TANK_KG = float(df_phy.loc[0, '高压储氢容量(kg)'])
    NH3_KGH = float(df_phy.loc[0, '合成氨产能(kg/h)'])

    NH3_YIELD_TONS = float(df_phy.loc[0, '全年绿氨产量(吨)'])
    H2_YIELD_TONS = float(df_phy.loc[0, '全年绿氢产量(吨)'])
    WATER_TONS = float(df_phy.loc[0, '全年纯水消耗(吨)'])

    EL_POWER_KWH = float(df_phy.loc[0, '制氢耗电(kWh)'])
    NH3_POWER_KWH = float(df_phy.loc[0, '制氨耗电(kWh)'])
    GRID_BUY_KWH = float(df_phy.loc[0, '电网购电量(kWh)'])

    print("成功读取工程物理规模，开始执行经济性评价分析...\n")
except FileNotFoundError:
    print(f"错误：未找到 {INPUT_EXCEL}。请先运行 model2.py！")
    sys.exit()

PRICE_NH3 = 4500
GRID_PRICE = 0.6
WATER_PRICE = 4.0
PROJECT_LIFE = 20
DISCOUNT_RATE = 0.08
OM_RATE = 0.02

COST = {'wind': 4000, 'solar': 3000, 'el': 1500, 'bat': 1000, 'tank': 300, 'nh3': 15000}


capex_wind = WIND_KW * COST['wind']
capex_solar = SOLAR_KW * COST['solar']
capex_el = EL_KW * COST['el']
capex_bat = BAT_KWH * COST['bat']
capex_tank = TANK_KG * COST['tank']
capex_nh3 = NH3_KGH * COST['nh3']

capex_total = capex_wind + capex_solar + capex_el + capex_bat + capex_tank + capex_nh3
capex_h2_part = capex_wind + capex_solar + capex_el + capex_bat


annual_revenue = NH3_YIELD_TONS * PRICE_NH3
annual_grid_cost = GRID_BUY_KWH * GRID_PRICE
annual_water_cost = WATER_TONS * WATER_PRICE
annual_om_cost = capex_total * OM_RATE
annual_opex = annual_grid_cost + annual_water_cost + annual_om_cost


crf = (DISCOUNT_RATE * (1 + DISCOUNT_RATE) ** PROJECT_LIFE) / ((1 + DISCOUNT_RATE) ** PROJECT_LIFE - 1)
lcoa = (capex_total * crf + annual_opex) / (NH3_YIELD_TONS * 1000)
lcoh = (capex_h2_part * crf) / (H2_YIELD_TONS * 1000)


unit_elec = (EL_POWER_KWH + NH3_POWER_KWH) / NH3_YIELD_TONS
unit_water = WATER_TONS / NH3_YIELD_TONS
unit_cost_grid = annual_grid_cost / NH3_YIELD_TONS
unit_cost_water = annual_water_cost / NH3_YIELD_TONS
unit_cost_dep_om = (capex_total * crf + annual_om_cost) / NH3_YIELD_TONS


cash_flows = [-capex_total] + [annual_revenue - annual_opex] * PROJECT_LIFE
npv = npf.npv(DISCOUNT_RATE, cash_flows)
irr = npf.irr(cash_flows)

discounted_cf = [-capex_total] + [(annual_revenue - annual_opex) / (1 + DISCOUNT_RATE) ** t for t in
                                  range(1, PROJECT_LIFE + 1)]
cumulative_dcf = np.cumsum(discounted_cf)

payback_period = -1
for i in range(1, len(cumulative_dcf)):
    if cumulative_dcf[i] >= 0:
        payback_period = i - 1 + abs(cumulative_dcf[i - 1]) / discounted_cf[i]
        break

print("=" * 45)
print("【项目综合经济性研判报告】")
print(f"▶ 总投资 (CAPEX)    : {capex_total / 1e8:.2f} 亿元")
print(f"▶ 年营业收入 (绿氨) : {annual_revenue / 1e4:.2f} 万元")
print(f"▶ 年运营成本 (OPEX) : {annual_opex / 1e4:.2f} 万元 (含水费 {annual_water_cost / 1e4:.1f} 万)")
print("-" * 45)
print("【吨氨单耗与成本拆解】")
print(f"▶ 吨氨综合电耗 : {unit_elec:.0f} kWh/t")
print(f"▶ 吨氨纯水消耗 : {unit_water:.2f} 吨/t")
print(f"▶ 吨氨折旧及运维: {unit_cost_dep_om:.0f} 元/t")
print(f"▶ 吨氨水、电费: {unit_cost_grid + unit_cost_water:.0f} 元/t")
print(f"▶ 绿氨平准化成本: {lcoa * 1000:.0f} 元/t (合 {lcoa:.2f} 元/kg)")
print("-" * 45)
print(f"▶ 内部收益率 (IRR)  : {irr * 100:.2f} %")
print(f"▶ 动态投资回收期    : {payback_period:.2f} 年" if payback_period > 0 else "生命周期内未收回")
print("=" * 45 + "\n")

if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)


if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)


df_econ = pd.DataFrame({
    '总投资_亿元': [capex_total / 1e8], '年收入_万元': [annual_revenue / 1e4],
    '年OPEX_万元': [annual_opex / 1e4], 'LCOA_元每kg': [lcoa], 'LCOH_元每kg': [lcoh],
    'NPV_亿元': [npv / 1e8], 'IRR_%': [irr * 100], '动态回收期_年': [payback_period]
})
df_econ.to_excel(os.path.join(OUTPUT_DIR, 'economic_evaluation.xlsx'), index=False)


plt.figure(figsize=(10, 6))
years = np.arange(PROJECT_LIFE + 1)
plt.bar(years, np.array(discounted_cf) / 1e8, color=['red'] + ['green'] * PROJECT_LIFE, label='年度折现净现金流 (亿元)')
plt.plot(years, cumulative_dcf / 1e8, color='blue', marker='o', linewidth=2, label='累计折现现金流 (亿元)')
plt.axhline(0, color='black', linestyle='--', linewidth=1)

if payback_period > 0:
    plt.axvline(x=payback_period, color='orange', linestyle=':', linewidth=2, label=f'回收期 {payback_period:.1f}年')

plt.title('项目全生命周期折现现金流分析', fontsize=15)
plt.xlabel('项目运营年份', fontsize=12)
plt.ylabel('金额 (亿元)', fontsize=12)
plt.xticks(years)
plt.legend(fontsize=11)
plt.grid(True, linestyle=':', alpha=0.6)
plt.tight_layout()

img_path = os.path.join(OUTPUT_DIR, "cash_flow_analysis.png")
plt.savefig(img_path, dpi=300)
print(f"[图像导出] 经济性现金流图表已保存至: {img_path}")
plt.show()