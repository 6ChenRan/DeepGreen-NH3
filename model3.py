# -*- coding: utf-8 -*-
"""模块三：固定模块二设备规模，反向验证并优化风光装机。"""
import os
import sys
import numpy as np
import pandas as pd
from model1 import load_resource_data
from model2 import (
    DEFAULT_COST, DEFAULT_TECH, optimize_verification_ratio,
    save_dispatch_result,
)


if __name__ == "__main__":
    try:
        cf_w, cf_s, _ = load_resource_data(
            os.getenv("FILE_WIND", "风资源.xlsx"),
            os.getenv("FILE_SOLAR", "光资源.xlsx"))
        df = pd.read_excel(os.path.join("model2_result", "physical_scale.xlsx"))
        row = df.iloc[0]
        fixed_caps = (
            float(row["电解槽功率(kW)"]),
            float(row["储能容量(kWh)"]),
            float(row["高压储氢容量(kg)"]),
            float(row["合成氨产能(kg/h)"]),
        )
        old_wind = float(row["风电装机(kW)"])
        old_solar = float(row["光伏装机(kW)"])
        target_ton = float(row["目标年产绿氨量(吨)"])
        max_grid = float(row["最大允许下网电率(%)"]) / 100
        max_curtail = float(row["最大允许弃电率(%)"]) / 100
        maint_month = int(row["检修月份"])

        val_wind, val_solar, stats, success, message = optimize_verification_ratio(
            cf_w, cf_s, fixed_caps, old_wind, old_solar,
            target_ton, max_grid, max_curtail,
            cost=DEFAULT_COST, tech=DEFAULT_TECH,
            maintenance_month=maint_month,
            return_series=True,
        )
        os.makedirs("model3_result", exist_ok=True)
        save_dispatch_result(
            stats, "model3_result", "final_physical_scale.xlsx",
            target_nh3_ton=target_ton,
            max_grid_rate=max_grid, max_curtail_rate=max_curtail,
        )
        ratio_old = old_wind / old_solar if old_solar else np.inf
        ratio_new = val_wind / val_solar if val_solar else np.inf
        pd.DataFrame([{
            "模块一风电(kW)": old_wind, "模块一光伏(kW)": old_solar,
            "模块一风光配比(风比光)": ratio_old,
            "修正后风电(kW)": val_wind, "修正后光伏(kW)": val_solar,
            "修正后风光配比(风比光)": ratio_new,
            "全年绿氨产量(吨)": stats["nh3_total"] / 1000,
            "全年电网购电量(kWh)": stats["grid_buy"],
            "全年系统总用电量(kWh)": stats["system_total_consumption"],
            "全年下网电率(%)": stats["grid_rate"] * 100,
            "最大允许下网电率(%)": max_grid * 100,
            "全年弃电量(kWh)": stats["curtailment"],
            "全年弃电率(%)": stats["curtail_rate"] * 100,
            "最大允许弃电率(%)": max_curtail * 100,
            "LCOA(元/kg)": stats["lcoa"],
            "优化器收敛": "是" if success else "否",
            "优化器信息": message,
        }]).to_excel("model3_result/validation_result.xlsx", index=False)
        print("模块三反向验证完成：", message)
        print(f"修正后风电{val_wind/1000:.2f}MW、光伏{val_solar/1000:.2f}MW；"
              f"下网电率{stats['grid_rate']:.2%}；弃电率{stats['curtail_rate']:.2%}；"
              f"LCOA{stats['lcoa']:.4f}元/kg")
    except Exception as exc:
        print("模块三计算失败：", exc)
        sys.exit(1)
