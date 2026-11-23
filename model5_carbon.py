# -*- coding: utf-8 -*-
"""模块五：绿氨碳足迹核算与减排率预判（简化边界）。"""
import os
import sys
import numpy as np
import pandas as pd
from model1 import load_resource_data


EF_GRID = 0.5703       # kgCO2e/kWh
EF_WIND = 0.011        # kgCO2e/kWh
EF_SOLAR = 0.040       # kgCO2e/kWh
EF_BASELINE = 2.50     # kgCO2e/kgNH3
ISCC_THRESHOLD = 0.70


def calculate_carbon(cf_wind, cf_solar, stats, ef_grid=EF_GRID,
                     ef_wind=EF_WIND, ef_solar=EF_SOLAR,
                     ef_baseline=EF_BASELINE, threshold=ISCC_THRESHOLD):
    wind_gen = float(stats["wind_kw"]) * float(np.sum(cf_wind))
    solar_gen = float(stats["solar_kw"]) * float(np.sum(cf_solar))
    # 采用项目边界内全部风光发电量计入风光设备生命周期排放，含弃电部分，属于偏保守分摊。
    emissions_wind = wind_gen * ef_wind
    emissions_solar = solar_gen * ef_solar
    emissions_green = emissions_wind + emissions_solar
    emissions_grid = float(stats["grid_buy"]) * ef_grid
    total_emissions = emissions_green + emissions_grid
    nh3_kg = float(stats["nh3_total"])
    if nh3_kg <= 0:
        raise ValueError("绿氨产量为0，无法计算产品碳排放强度。")
    intensity = total_emissions / nh3_kg
    reduction_pct = (ef_baseline - intensity) / ef_baseline * 100 if ef_baseline > 0 else 0.0
    return {
        "wind_generation_kwh": wind_gen,
        "solar_generation_kwh": solar_gen,
        "emissions_wind_kg": emissions_wind,
        "emissions_solar_kg": emissions_solar,
        "emissions_green_kg": emissions_green,
        "emissions_grid_kg": emissions_grid,
        "total_emissions_kg": total_emissions,
        "carbon_intensity_kg_per_kg": intensity,
        "baseline_kg_per_kg": ef_baseline,
        "reduction_pct": reduction_pct,
        "threshold_pct": threshold * 100,
        "precheck_pass": reduction_pct >= threshold * 100,
    }


if __name__ == "__main__":
    try:
        final_path = os.path.join("model3_result", "final_physical_scale.xlsx")
        base_path = os.path.join("model2_result", "physical_scale.xlsx")
        input_path = final_path if os.path.exists(final_path) else base_path
        df = pd.read_excel(input_path, sheet_name="工程规模与经济指标")
        row = df.iloc[0]
        stats = {
            "wind_kw": float(row["风电装机(kW)"]),
            "solar_kw": float(row["光伏装机(kW)"]),
            "nh3_total": float(row["全年绿氨产量(吨)"]) * 1000,
            "grid_buy": float(row["全年电网购电量(kWh)"]),
        }
        cf_w, cf_s, _ = load_resource_data(
            os.getenv("FILE_WIND", "风资源.xlsx"),
            os.getenv("FILE_SOLAR", "光资源.xlsx"))
        result = calculate_carbon(cf_w, cf_s, stats)
        os.makedirs("model5_carbon_result", exist_ok=True)
        pd.DataFrame([{
            "风光生命周期排放(吨CO2e)": result["emissions_green_kg"] / 1000,
            "网电排放(吨CO2e)": result["emissions_grid_kg"] / 1000,
            "总排放(吨CO2e)": result["total_emissions_kg"] / 1000,
            "绿氨碳强度(kgCO2e/kgNH3)": result["carbon_intensity_kg_per_kg"],
            "灰氨基准(kgCO2e/kgNH3)": result["baseline_kg_per_kg"],
            "减排率(%)": result["reduction_pct"],
            "阈值(%)": result["threshold_pct"],
            "减排预判": "达到阈值" if result["precheck_pass"] else "未达到阈值",
        }]).to_excel("model5_carbon_result/carbon_footprint.xlsx", index=False)
        print("碳足迹核算完成：", result)
        print("提示：该结果是所设简化系统边界下的减排预判，不等同于ISCC-EU正式认证。")
    except Exception as exc:
        print("模块五计算失败：", exc)
        sys.exit(1)
