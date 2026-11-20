# -*- coding: utf-8 -*-
"""模块四：吨氨单耗与全生命周期经济性评价。"""
import os
import sys
import numpy as np
import pandas as pd
import numpy_financial as npf
from model2 import capital_recovery_factor, DEFAULT_COST


def calculate_economics(stats, cost=None, price_nh3=4500.0,
                        discount_rate=0.08, project_life=20, om_rate=0.02):
    cost = {**DEFAULT_COST, **(cost or {})}
    nh3_kg = float(stats["nh3_total"])
    h2_kg = float(stats["h2_total"])
    if nh3_kg <= 0 or h2_kg <= 0:
        raise ValueError("绿氨或绿氢产量为0，无法计算单位成本。")

    capex = float(stats["capex_total"])
    grid_cost = float(stats["grid_buy"]) * cost["grid"]
    water_cost = float(stats["water_tons"]) * cost["water"]
    om_cost = capex * om_rate
    crf = capital_recovery_factor(discount_rate, project_life)
    annualized_capex = capex * crf
    annual_opex = grid_cost + water_cost + om_cost
    annual_cost = annualized_capex + annual_opex
    lcoa = annual_cost / nh3_kg

    # LCOH：氢系统投资（风光、电解槽、储能、储氢）+分摊网电费+制水费
    capex_h2 = (
        stats["wind_kw"] * cost["wind"]
        + stats["solar_kw"] * cost["solar"]
        + stats["cap_el"] * cost["el"]
        + stats["cap_bat"] * cost["bat"]
        + stats["cap_tank"] * cost["tank"]
    )
    process_power = float(stats["el_power"]) + float(stats["nh3_power"])
    h2_grid_share = float(stats["el_power"]) / process_power if process_power > 0 else 0.0
    h2_annual_cost = capex_h2 * (crf + om_rate) + grid_cost * h2_grid_share + water_cost
    lcoh = h2_annual_cost / h2_kg

    nh3_tons = nh3_kg / 1000.0
    h2_tons = h2_kg / 1000.0
    annual_revenue = nh3_tons * float(price_nh3)
    cash_flows = [-capex] + [annual_revenue - annual_opex] * int(project_life)
    npv = float(npf.npv(discount_rate, cash_flows))
    irr_raw = npf.irr(cash_flows)
    irr = float(irr_raw) if irr_raw is not None and np.isfinite(irr_raw) else np.nan

    discounted_cf = [-capex] + [
        (annual_revenue - annual_opex) / ((1 + discount_rate) ** year)
        for year in range(1, int(project_life) + 1)
    ]
    cumulative_dcf = np.cumsum(discounted_cf)
    payback = np.nan
    for i in range(1, len(cumulative_dcf)):
        if cumulative_dcf[i] >= 0 and discounted_cf[i] > 0:
            payback = i - 1 + abs(cumulative_dcf[i - 1]) / discounted_cf[i]
            break

    return {
        "capex_total": capex,
        "annualized_capex": annualized_capex,
        "annual_grid_cost": grid_cost,
        "annual_water_cost": water_cost,
        "annual_om_cost": om_cost,
        "annual_opex": annual_opex,
        "annual_total_cost": annual_cost,
        "annual_revenue": annual_revenue,
        "nh3_tons": nh3_tons,
        "h2_tons": h2_tons,
        "unit_electricity_kwh_per_ton": process_power / nh3_tons,
        "unit_water_ton_per_ton": float(stats["water_tons"]) / nh3_tons,
        "unit_grid_cost_yuan_per_ton": grid_cost / nh3_tons,
        "unit_water_cost_yuan_per_ton": water_cost / nh3_tons,
        "unit_capital_om_yuan_per_ton": (annualized_capex + om_cost) / nh3_tons,
        "lcoa_yuan_per_kg": lcoa,
        "lcoh_yuan_per_kg": lcoh,
        "npv": npv,
        "irr": irr,
        "payback_year": payback,
        "cash_flows": cash_flows,
        "discounted_cash_flows": discounted_cf,
        "cumulative_discounted_cash_flows": cumulative_dcf,
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
            "cap_el": float(row["电解槽功率(kW)"]),
            "cap_bat": float(row["储能容量(kWh)"]),
            "cap_tank": float(row["高压储氢容量(kg)"]),
            "cap_nh3": float(row["合成氨产能(kg/h)"]),
            "nh3_total": float(row["全年绿氨产量(吨)"]) * 1000,
            "h2_total": float(row["全年绿氢产量(吨)"]) * 1000,
            "water_tons": float(row["全年纯水消耗(吨)"]),
            "el_power": float(row["制氢耗电(kWh)"]),
            "nh3_power": float(row["制氨耗电(kWh)"]),
            "grid_buy": float(row["全年电网购电量(kWh)"]),
            "capex_total": float(row["CAPEX(元)"]),
        }
        econ = calculate_economics(stats)
        os.makedirs("economics_result", exist_ok=True)
        out = pd.DataFrame([{
            "总投资(亿元)": econ["capex_total"] / 1e8,
            "年营业收入(万元)": econ["annual_revenue"] / 1e4,
            "年运营成本(万元)": econ["annual_opex"] / 1e4,
            "吨氨综合电耗(kWh/t)": econ["unit_electricity_kwh_per_ton"],
            "吨氨纯水消耗(t/t)": econ["unit_water_ton_per_ton"],
            "LCOA(元/kg)": econ["lcoa_yuan_per_kg"],
            "LCOH(元/kg)": econ["lcoh_yuan_per_kg"],
            "NPV(亿元)": econ["npv"] / 1e8,
            "IRR(%)": econ["irr"] * 100 if np.isfinite(econ["irr"]) else np.nan,
            "动态回收期(年)": econ["payback_year"],
        }])
        out.to_excel("economics_result/economic_evaluation.xlsx", index=False)
        print(out.to_string(index=False))
    except Exception as exc:
        print("模块四计算失败：", exc)
        sys.exit(1)
