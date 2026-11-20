# -*- coding: utf-8 -*-
"""计算逻辑自检：使用合成8760小时资源曲线，不代表任何实际项目。"""
import numpy as np
from model2 import optimize_engineering_scale, optimize_verification_ratio
from economics import calculate_economics
from model4_carbon import calculate_carbon

hours = np.arange(8760)
wind = (
    0.25
    + 0.20 * (0.5 + 0.5 * np.sin(2 * np.pi * hours / 8760 * 3 + 0.4))
    + 0.12 * (0.5 + 0.5 * np.sin(2 * np.pi * hours / 168))
)
wind = np.clip(wind / wind.max(), 0, 1)
hour_of_day = hours % 24
season = 0.65 + 0.25 * np.sin(2 * np.pi * hours / 8760 - 1)
solar = np.maximum(0, np.sin(np.pi * (hour_of_day - 6) / 12)) * np.clip(season, 0.2, 1)
solar = solar / solar.max()

target_ton = 50000.0
max_grid = 0.10
max_curtail = 0.20

caps, stats, success, message = optimize_engineering_scale(
    120000, 80000, wind, solar,
    target_ton, max_grid, max_curtail,
    return_series=True,
)

assert stats["nh3_total"] >= target_ton * 1000 - 1
assert stats["grid_rate"] <= max_grid + 1e-5
assert stats["curtail_rate"] <= max_curtail + 1e-5
assert stats["balance_diff"] < 0.1
assert stats["h2_balance_diff"] < 0.01
assert stats["max_power_balance_error"] < 1e-4
assert np.max(stats["arr_p_el"]) <= caps[0] + 1e-5
assert np.min(stats["arr_bat_soc"]) >= -1e-6
assert np.max(stats["arr_bat_soc"]) <= caps[1] + 1e-5
assert np.min(stats["arr_tank_soc"]) >= -1e-6
assert np.max(stats["arr_tank_soc"]) <= caps[2] + 1e-5

wind3, solar3, stats3, success3, message3 = optimize_verification_ratio(
    wind, solar, caps, 120000, 80000,
    target_ton, max_grid, max_curtail,
    return_series=True,
)
assert stats3["nh3_total"] >= target_ton * 1000 - 1
assert stats3["grid_rate"] <= max_grid + 1e-5
assert stats3["curtail_rate"] <= max_curtail + 1e-5
assert stats3["balance_diff"] < 0.1
assert stats3["h2_balance_diff"] < 0.01

econ = calculate_economics(stats3)
carbon = calculate_carbon(wind, solar, stats3)

print("计算逻辑自检通过")
print(f"模块二设备规模：电解槽{caps[0]/1000:.2f}MW，储能{caps[1]/1000:.2f}MWh，"
      f"储氢{caps[2]:.0f}kg，合成氨{caps[3]:.0f}kg/h")
print(f"模块三风光装机：风电{wind3/1000:.2f}MW，光伏{solar3/1000:.2f}MW")
print(f"年产绿氨：{stats3['nh3_total']/1000:.2f}吨")
print(f"下网电率：{stats3['grid_rate']:.3%}；弃电率：{stats3['curtail_rate']:.3%}")
print(f"年度电量平衡偏差：{stats3['balance_diff']:.8f}kWh")
print(f"氢气物料平衡偏差：{stats3['h2_balance_diff']:.8f}kg")
print(f"LCOA：{econ['lcoa_yuan_per_kg']:.4f}元/kg；LCOH：{econ['lcoh_yuan_per_kg']:.4f}元/kg")
print(f"简化边界减排率预判：{carbon['reduction_pct']:.2f}%")
print("注意：以上使用合成资源曲线，仅用于程序逻辑自检。")
