# -*- coding: utf-8 -*-
"""
柔性绿氨平台 V1.1：统一8760小时调度计算引擎。
单位：功率kW、电量kWh、氢/氨kg、储能kWh、储氢kg、成本元、LCOA元/kg。
采用8760小时物理调度 + SLSQP设备寻优；不采用大型逐时LP，以避免Web端求解时间不可控。
"""
import os
import sys
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from model1 import load_resource_data

DEFAULT_COST = {
    "wind": 4000.0, "solar": 3000.0, "el": 1500.0, "bat": 1000.0,
    "tank": 300.0, "nh3": 15000.0, "grid": 0.6, "water": 4.0,
}
DEFAULT_TECH = {
    "el_eff": 50.0, "nh3_elec_req": 1.0, "nh3_h2_req": 0.177,
    "water_req": 15.0, "eta_charge": 0.95, "eta_discharge": 0.95,
    "bat_power_rate": 0.5,
}


def capital_recovery_factor(discount_rate, project_life):
    r, n = float(discount_rate), int(project_life)
    if n <= 0 or r < 0:
        raise ValueError("项目生命周期必须大于0，贴现率不能小于0。")
    return 1.0 / n if r == 0 else r * (1 + r) ** n / ((1 + r) ** n - 1)


def validate_profiles(cf_wind, cf_solar):
    w, s = np.asarray(cf_wind, float).reshape(-1), np.asarray(cf_solar, float).reshape(-1)
    if len(w) != 8760 or len(s) != 8760:
        raise ValueError(f"风光数据必须各有8760小时，当前风电{len(w)}条、光伏{len(s)}条。")
    if not np.all(np.isfinite(w)) or not np.all(np.isfinite(s)):
        raise ValueError("风光资源数据存在空值或非数值。")
    if np.any(w < 0) or np.any(s < 0) or w.max() <= 0 or s.max() <= 0:
        raise ValueError("风光资源数据不能为负数，且风、光曲线均须有正出力。")
    return w, s


def select_maintenance_month(wind_kw, solar_kw, cf_wind, cf_solar):
    w, s = validate_profiles(cf_wind, cf_solar)
    p = float(wind_kw) * w + float(solar_kw) * s
    hrs = np.array([31,28,31,30,31,30,31,31,30,31,30,31]) * 24
    starts = np.r_[0, np.cumsum(hrs)]
    monthly = [p[starts[m]:starts[m+1]].sum() for m in range(12)]
    month = int(np.argmin(monthly))
    mask = np.zeros(8760, dtype=bool)
    mask[starts[month]:starts[month+1]] = True
    return month + 1, mask


def simulate_system(caps, wind_kw, solar_kw, cf_wind, cf_solar,
                    cost=None, tech=None, discount_rate=0.08, project_life=20,
                    om_rate=0.02, maintenance_month=None, max_state_cycles=6,
                    return_series=False):
    """
    8760小时逐时调度。电解槽不超额定功率；氢库存不为负、不超储罐容量；
    电池SOC不越界；每小时电量守恒；电网补电不直接给电池充电。
    """
    w, s = validate_profiles(cf_wind, cf_solar)
    cost, tech = {**DEFAULT_COST, **(cost or {})}, {**DEFAULT_TECH, **(tech or {})}
    cap_el, cap_bat, cap_tank, cap_nh3 = map(float, caps)
    wind_kw, solar_kw = float(wind_kw), float(solar_kw)
    if min(cap_el, cap_bat, cap_tank, cap_nh3, wind_kw, solar_kw) < 0:
        raise ValueError("风光装机和设备规模不能为负数。")
    el_eff = float(tech["el_eff"])
    nh3_elec = float(tech["nh3_elec_req"])
    h2_req = float(tech["nh3_h2_req"])
    water_req = float(tech["water_req"])
    eta_ch, eta_dis = float(tech["eta_charge"]), float(tech["eta_discharge"])
    bat_rate = float(tech["bat_power_rate"])
    if el_eff <= 0 or h2_req <= 0 or nh3_elec < 0 or water_req < 0:
        raise ValueError("制氢电耗、氨耗氢、制氨电耗或水耗参数不合理。")
    if not (0 < eta_ch <= 1 and 0 < eta_dis <= 1 and bat_rate > 0):
        raise ValueError("电池充放电效率必须在(0,1]，功率倍率必须大于0。")

    p_green = wind_kw * w + solar_kw * s
    if maintenance_month is None:
        maintenance_month, maintenance_mask = select_maintenance_month(wind_kw, solar_kw, w, s)
    else:
        maintenance_month = int(maintenance_month)
        if not 1 <= maintenance_month <= 12:
            raise ValueError("检修月份必须为1至12月。")
        hrs = np.array([31,28,31,30,31,30,31,31,30,31,30,31]) * 24
        starts = np.r_[0, np.cumsum(hrs)]
        maintenance_mask = np.zeros(8760, dtype=bool)
        maintenance_mask[starts[maintenance_month-1]:starts[maintenance_month]] = True

    bat_power_max = bat_rate * cap_bat
    bat_soc, tank_soc = 0.5 * cap_bat, 0.5 * cap_tank
    converged = False
    final = None

    # 重复年度运行，削弱人为设定期初库存对年度结果的影响，逼近周期稳态
    for cycle in range(max(1, int(max_state_cycles))):
        start_bat, start_tank = bat_soc, tank_soc
        sums = {k: 0.0 for k in (
            "nh3", "h2", "h2_cons", "el_power", "nh3_power", "grid",
            "bat_ch", "bat_dis", "curtail"
        )}
        max_bus_error = 0.0
        arrays = None
        if return_series:
            arrays = {k: np.zeros(8760) for k in (
                "el", "nh3_power", "nh3_prod", "grid", "bat_ch", "bat_dis",
                "bat_soc", "tank_soc", "h2_prod", "h2_cons", "curtail",
                "load", "bus_residual"
            )}

        for t in range(8760):
            green = float(p_green[t])
            charge = discharge = grid = curtail = 0.0
            p_el = p_nh3 = nh3_rate = h2_prod = h2_cons = 0.0

            if maintenance_mask[t]:
                # 检修月：制氢、制氨停产；风光余电可给电池充电
                charge = min(green, bat_power_max,
                             max(0.0, (cap_bat - bat_soc) / eta_ch) if cap_bat else 0.0)
                bat_soc += charge * eta_ch
                curtail = max(0.0, green - charge)
            else:
                # 氢气可供量=现有库存+电解槽满负荷可产氢量
                nh3_rate = min(cap_nh3, max(0.0, (tank_soc + cap_el / el_eff) / h2_req))
                h2_cons = nh3_rate * h2_req
                p_nh3 = nh3_rate * nh3_elec

                # 先制取满足当小时合成氨耗氢所需的氢气，绝不突破电解槽额定功率
                p_el_min = min(cap_el, max(0.0, (h2_cons - tank_soc) * el_eff))
                tank_after_min = tank_soc + p_el_min / el_eff - h2_cons

                # 有风光富余时，才额外制氢填充储氢罐；不使用网电额外制氢囤氢
                green_surplus_for_extra = max(0.0, green - p_nh3 - p_el_min)
                tank_free = max(0.0, cap_tank - tank_after_min)
                p_el_extra = min(max(0.0, cap_el - p_el_min),
                                 tank_free * el_eff, green_surplus_for_extra)
                p_el = p_el_min + p_el_extra
                h2_prod = p_el / el_eff
                tank_soc = tank_after_min + p_el_extra / el_eff

                process_load = p_el + p_nh3
                if green >= process_load:
                    surplus = green - process_load
                    charge = min(surplus, bat_power_max,
                                 max(0.0, (cap_bat - bat_soc) / eta_ch) if cap_bat else 0.0)
                    bat_soc += charge * eta_ch
                    curtail = max(0.0, surplus - charge)
                else:
                    deficit = process_load - green
                    discharge = min(deficit, bat_power_max, bat_soc * eta_dis)
                    bat_soc -= discharge / eta_dis
                    grid = max(0.0, deficit - discharge)

                bat_soc = min(cap_bat, max(0.0, bat_soc))
                tank_soc = min(cap_tank, max(0.0, tank_soc))

            sums["nh3"] += nh3_rate
            sums["h2"] += h2_prod
            sums["h2_cons"] += h2_cons
            sums["el_power"] += p_el
            sums["nh3_power"] += p_nh3
            sums["grid"] += grid
            sums["bat_ch"] += charge
            sums["bat_dis"] += discharge
            sums["curtail"] += curtail

            residual = green + grid + discharge - p_el - p_nh3 - charge - curtail
            max_bus_error = max(max_bus_error, abs(residual))
            if arrays is not None:
                arrays["el"][t], arrays["nh3_power"][t] = p_el, p_nh3
                arrays["nh3_prod"][t], arrays["grid"][t] = nh3_rate, grid
                arrays["bat_ch"][t], arrays["bat_dis"][t] = charge, discharge
                arrays["bat_soc"][t], arrays["tank_soc"][t] = bat_soc, tank_soc
                arrays["h2_prod"][t], arrays["h2_cons"][t] = h2_prod, h2_cons
                arrays["curtail"][t] = curtail
                arrays["load"][t] = p_el + p_nh3 + charge
                arrays["bus_residual"][t] = residual

        delta_bat, delta_tank = bat_soc - start_bat, tank_soc - start_tank
        final = (sums, arrays, start_bat, start_tank, bat_soc, tank_soc,
                 delta_bat, delta_tank, max_bus_error)
        if (abs(delta_bat) <= max(1e-3, cap_bat * 1e-4)
                and abs(delta_tank) <= max(1e-3, cap_tank * 1e-4)):
            converged = True
            break

    sums, arrays, start_bat, start_tank, end_bat, end_tank, delta_bat, delta_tank, max_bus_error = final
    nh3_total, h2_total = sums["nh3"], sums["h2"]
    water_tons = h2_total * water_req / 1000.0
    grid_buy, el_power, nh3_power = sums["grid"], sums["el_power"], sums["nh3_power"]
    system_total = el_power + nh3_power
    grid_rate = grid_buy / system_total if system_total > 0 else 0.0
    p_green_total = float(p_green.sum())
    curtailment = sums["curtail"]
    curtail_rate = curtailment / p_green_total if p_green_total > 0 else 1.0
    bat_chg, bat_dis = sums["bat_ch"], sums["bat_dis"]
    battery_loss = bat_chg - bat_dis - delta_bat

    # 年度系统边界：风光+网电=工艺用电+弃电+电池损耗+储能期末库存变化
    energy_in = p_green_total + grid_buy
    energy_out = el_power + nh3_power + curtailment + battery_loss + delta_bat
    balance_diff = abs(energy_in - energy_out)
    h2_balance_diff = abs(h2_total - sums["h2_cons"] - delta_tank)

    capex = (wind_kw * cost["wind"] + solar_kw * cost["solar"]
             + cap_el * cost["el"] + cap_bat * cost["bat"]
             + cap_tank * cost["tank"] + cap_nh3 * cost["nh3"])
    crf = capital_recovery_factor(discount_rate, project_life)
    annualized_capex = capex * crf
    annual_om = capex * om_rate
    annual_grid_cost = grid_buy * cost["grid"]
    annual_water_cost = water_tons * cost["water"]
    annual_cost = annualized_capex + annual_om + annual_grid_cost + annual_water_cost
    lcoa = annual_cost / nh3_total if nh3_total > 0 else float("inf")

    stats = {
        "wind_kw": wind_kw, "solar_kw": solar_kw,
        "cap_el": cap_el, "cap_bat": cap_bat, "cap_tank": cap_tank, "cap_nh3": cap_nh3,
        "nh3_total": nh3_total, "h2_total": h2_total, "h2_consumption": sums["h2_cons"],
        "water_tons": water_tons, "grid_buy": grid_buy,
        "system_total_consumption": system_total, "grid_rate": grid_rate,
        "curtailment": curtailment, "curtail_rate": curtail_rate,
        "p_green_total": p_green_total, "el_power": el_power, "nh3_power": nh3_power,
        "bat_chg": bat_chg, "bat_dis": bat_dis, "battery_loss": battery_loss,
        "battery_soc_start": start_bat, "battery_soc_end": end_bat,
        "tank_soc_start": start_tank, "tank_soc_end": end_tank,
        "battery_cycle_delta": delta_bat, "tank_cycle_delta": delta_tank,
        "state_cycles_converged": converged, "state_cycles_used": cycle + 1,
        "energy_in": energy_in, "energy_out": energy_out, "balance_diff": balance_diff,
        "h2_balance_diff": h2_balance_diff, "max_power_balance_error": max_bus_error,
        "maintenance_month": maintenance_month, "m_month": maintenance_month,
        "m_start": int(np.flatnonzero(maintenance_mask)[0]),
        "m_end": int(np.flatnonzero(maintenance_mask)[-1] + 1),
        "capex_total": capex, "annualized_capex": annualized_capex,
        "annual_om_cost": annual_om, "annual_grid_cost": annual_grid_cost,
        "annual_water_cost": annual_water_cost, "annual_cost": annual_cost, "lcoa": lcoa,
    }
    if return_series:
        stats.update({
            "arr_p_green": p_green, "arr_p_el": arrays["el"],
            "arr_p_nh3": arrays["nh3_power"], "arr_nh3_prod": arrays["nh3_prod"],
            "arr_grid_buy": arrays["grid"], "arr_curtail": arrays["curtail"],
            "arr_bat_soc": arrays["bat_soc"], "arr_tank_soc": arrays["tank_soc"],
            "arr_bat_dis": arrays["bat_dis"], "arr_bat_chg": arrays["bat_ch"],
            "arr_h2_prod": arrays["h2_prod"], "arr_h2_cons": arrays["h2_cons"],
            "arr_load": arrays["load"], "arr_power_balance_residual": arrays["bus_residual"],
        })
    return stats


def _constraint_values(stats, target_ton, max_grid_rate, max_curtail_rate):
    target_kg = target_ton * 1000.0
    return np.array([
        max_grid_rate - stats["grid_rate"],
        max_curtail_rate - stats["curtail_rate"],
        (stats["nh3_total"] - target_kg) / max(target_kg, 1.0),
        0.005 - abs(stats["battery_cycle_delta"]) / max(stats["cap_bat"], 1.0),
        0.005 - abs(stats["tank_cycle_delta"]) / max(stats["cap_tank"], 1.0),
    ], dtype=float)


def optimize_engineering_scale(opt_wind_kw, opt_solar_kw, cf_wind, cf_solar,
                               target_nh3_ton, max_grid_rate, max_curtail_rate,
                               cost=None, tech=None, discount_rate=0.08,
                               project_life=20, om_rate=0.02,
                               equipment_bounds=None, return_series=True):
    """固定模块一风光规模，优化电解槽、储能、储氢、合成氨四项设备规模。"""
    cost, tech = {**DEFAULT_COST, **(cost or {})}, {**DEFAULT_TECH, **(tech or {})}
    if target_nh3_ton <= 0:
        raise ValueError("目标年产绿氨量必须大于0吨/年。")
    if equipment_bounds is None:
        equipment_bounds = [(10000,300000),(0,500000),(1000,200000),(100,50000)]
    _, maint = select_maintenance_month(opt_wind_kw, opt_solar_kw, cf_wind, cf_solar)
    op_hours = 8760 - int(maint.sum())
    q0 = np.clip(target_nh3_ton * 1000 / op_hours * 1.08,
                 equipment_bounds[3][0], equipment_bounds[3][1])
    el0 = np.clip(q0 * tech["nh3_h2_req"] * tech["el_eff"] * 1.2,
                  equipment_bounds[0][0], equipment_bounds[0][1])
    bat0 = np.clip(opt_wind_kw * 0.05 * 2,
                   equipment_bounds[1][0], equipment_bounds[1][1])
    tank0 = np.clip(q0 * tech["nh3_h2_req"] * 24,
                    equipment_bounds[2][0], equipment_bounds[2][1])
    starts = [
        np.array([el0,bat0,tank0,q0]),
        np.array([min(el0*1.5,equipment_bounds[0][1]),
                  min(max(bat0*1.5,equipment_bounds[1][0]),equipment_bounds[1][1]),
                  min(tank0*1.5,equipment_bounds[2][1]),
                  min(q0*1.15,equipment_bounds[3][1])]),
        np.array([min(el0*1.8,equipment_bounds[0][1]),
                  max(equipment_bounds[1][0],bat0*0.6),
                  min(tank0*2,equipment_bounds[2][1]),
                  min(q0*1.25,equipment_bounds[3][1])]),
        # 高出力起点：防止低初始产能导致弃电约束无法进入可行域
        np.array([equipment_bounds[0][1], equipment_bounds[1][0],
                  equipment_bounds[2][1],
                  np.clip(max(q0*1.5, target_nh3_ton*1000/op_hours*1.5),
                          equipment_bounds[3][0], equipment_bounds[3][1])]),
        np.array([equipment_bounds[0][1], equipment_bounds[1][1],
                  equipment_bounds[2][1], equipment_bounds[3][1]]),
    ]
    cache = {}
    def evaluate(x, cycles=4):
        key = tuple(np.round(x, 3))
        if key not in cache:
            cache[key] = simulate_system(
                x,opt_wind_kw,opt_solar_kw,cf_wind,cf_solar,cost,tech,
                discount_rate,project_life,om_rate,max_state_cycles=cycles,
                return_series=False)
        return cache[key]
    def obj(x):
        v = evaluate(x)["lcoa"]
        return v if np.isfinite(v) else 1e30
    def cons(x):
        return _constraint_values(evaluate(x),target_nh3_ton,max_grid_rate,max_curtail_rate)
    candidates, messages = [], []
    for x0 in starts:
        res = minimize(obj,x0,method="SLSQP",bounds=equipment_bounds,
                       constraints=[{"type":"ineq","fun":cons}],
                       options={"maxiter":160,"ftol":1e-7,"disp":False})
        messages.append(str(res.message))
        st = simulate_system(res.x,opt_wind_kw,opt_solar_kw,cf_wind,cf_solar,
                             cost,tech,discount_rate,project_life,om_rate,
                             max_state_cycles=8,return_series=False)
        if np.all(_constraint_values(st,target_nh3_ton,max_grid_rate,max_curtail_rate)>=-1e-5):
            candidates.append((st["lcoa"],res.x.copy(),res,st))
    if not candidates:
        raise ValueError(
            "当前风光资源、设备边界和运行约束下未找到可行方案。"
            f"目标年产量{target_nh3_ton:.2f}吨；下网电率上限{max_grid_rate:.2%}；"
            f"弃电率上限{max_curtail_rate:.2%}。优化器信息：{' | '.join(messages)}。"
            "请检查风光装机规模、设备边界及约束设置。")
    candidates.sort(key=lambda z:z[0])
    _,best_x,best_res,_=candidates[0]
    final_stats=simulate_system(best_x,opt_wind_kw,opt_solar_kw,cf_wind,cf_solar,
                                cost,tech,discount_rate,project_life,om_rate,
                                max_state_cycles=10,return_series=return_series)
    return best_x,final_stats,best_res.success,best_res.message


def optimize_verification_ratio(cf_wind,cf_solar,fixed_caps,original_wind_kw,
                                original_solar_kw,target_nh3_ton,max_grid_rate,
                                max_curtail_rate,cost=None,tech=None,
                                discount_rate=0.08,project_life=20,om_rate=0.02,
                                wind_bounds=(10000,300000),solar_bounds=(0,200000),
                                return_series=True, maintenance_month=None):
    """固定模块二设备规模，反向优化风光装机；检修月份沿用模块二确定的月份。"""
    cost,tech={**DEFAULT_COST,**(cost or {})},{**DEFAULT_TECH,**(tech or {})}
    if maintenance_month is None:
        month,_=select_maintenance_month(original_wind_kw,original_solar_kw,cf_wind,cf_solar)
    else:
        month=int(maintenance_month)
    starts=[
        np.array([original_wind_kw,original_solar_kw],float),
        np.array([(wind_bounds[0]+wind_bounds[1])/2,(solar_bounds[0]+solar_bounds[1])/2]),
        np.array([wind_bounds[1]*0.8,solar_bounds[1]*0.8]),
    ]
    starts=[np.clip(x,[wind_bounds[0],solar_bounds[0]],[wind_bounds[1],solar_bounds[1]]) for x in starts]
    cache={}
    def evaluate(x,cycles=4):
        key=tuple(np.round(x,2))
        if key not in cache:
            cache[key]=simulate_system(fixed_caps,x[0],x[1],cf_wind,cf_solar,
                                       cost,tech,discount_rate,project_life,om_rate,
                                       maintenance_month=month,max_state_cycles=cycles,
                                       return_series=False)
        return cache[key]
    def obj(x):
        v=evaluate(x)["lcoa"]
        return v if np.isfinite(v) else 1e30
    def cons(x):
        return _constraint_values(evaluate(x),target_nh3_ton,max_grid_rate,max_curtail_rate)
    candidates=[]; messages=[]
    for x0 in starts:
        res=minimize(obj,x0,method="SLSQP",bounds=[wind_bounds,solar_bounds],
                     constraints=[{"type":"ineq","fun":cons}],
                     options={"maxiter":160,"ftol":1e-7,"disp":False})
        messages.append(str(res.message))
        st=simulate_system(fixed_caps,res.x[0],res.x[1],cf_wind,cf_solar,
                           cost,tech,discount_rate,project_life,om_rate,
                           maintenance_month=month,max_state_cycles=8,return_series=False)
        if np.all(_constraint_values(st,target_nh3_ton,max_grid_rate,max_curtail_rate)>=-1e-5):
            candidates.append((st["lcoa"],res.x.copy(),res,st))
    if not candidates:
        raise ValueError("固定设备规模后，当前风光装机边界下不存在满足约束的方案。"
                         f"优化器信息：{' | '.join(messages)}。")
    candidates.sort(key=lambda z:z[0])
    _,best_x,best_res,_=candidates[0]
    final_stats=simulate_system(fixed_caps,best_x[0],best_x[1],cf_wind,cf_solar,
                                cost,tech,discount_rate,project_life,om_rate,
                                maintenance_month=month,max_state_cycles=10,
                                return_series=return_series)
    return float(best_x[0]),float(best_x[1]),final_stats,best_res.success,best_res.message


def save_dispatch_result(stats,output_dir="model2_result",filename="physical_scale.xlsx",
                         target_nh3_ton=None,max_grid_rate=None,max_curtail_rate=None):
    os.makedirs(output_dir,exist_ok=True)
    summary={
        "风电装机(kW)":stats["wind_kw"],"光伏装机(kW)":stats["solar_kw"],
        "电解槽功率(kW)":stats["cap_el"],"储能容量(kWh)":stats["cap_bat"],
        "高压储氢容量(kg)":stats["cap_tank"],"合成氨产能(kg/h)":stats["cap_nh3"],
        "目标年产绿氨量(吨)":target_nh3_ton if target_nh3_ton is not None else stats["nh3_total"]/1000,
        "全年绿氨产量(吨)":stats["nh3_total"]/1000,"全年绿氢产量(吨)":stats["h2_total"]/1000,
        "全年纯水消耗(吨)":stats["water_tons"],"制氢耗电(kWh)":stats["el_power"],
        "制氨耗电(kWh)":stats["nh3_power"],"全年风光发电量(kWh)":stats["p_green_total"],
        "全年电网购电量(kWh)":stats["grid_buy"],"全年系统总用电量(kWh)":stats["system_total_consumption"],
        "全年下网电率(%)":stats["grid_rate"]*100,
        "最大允许下网电率(%)":max_grid_rate*100 if max_grid_rate is not None else np.nan,
        "全年弃电量(kWh)":stats["curtailment"],"全年弃电率(%)":stats["curtail_rate"]*100,
        "最大允许弃电率(%)":max_curtail_rate*100 if max_curtail_rate is not None else np.nan,
        "电池充电量(kWh)":stats["bat_chg"],"电池放电量(kWh)":stats["bat_dis"],
        "电池损耗(kWh)":stats["battery_loss"],"电池期初库存(kWh)":stats["battery_soc_start"],
        "电池期末库存(kWh)":stats["battery_soc_end"],"储氢期初库存(kg)":stats["tank_soc_start"],
        "储氢期末库存(kg)":stats["tank_soc_end"],"电量平衡偏差(kWh)":stats["balance_diff"],
        "逐时最大电力平衡偏差(kW)":stats["max_power_balance_error"],
        "氢气物料平衡偏差(kg)":stats["h2_balance_diff"],
        "库存迭代收敛":"是" if stats["state_cycles_converged"] else "否",
        "库存迭代次数":stats["state_cycles_used"],"检修月份":stats["maintenance_month"],
        "CAPEX(元)":stats["capex_total"],"年化投资(元/年)":stats["annualized_capex"],
        "年运维费(元/年)":stats["annual_om_cost"],"年网电费(元/年)":stats["annual_grid_cost"],
        "年水费(元/年)":stats["annual_water_cost"],"年总成本(元/年)":stats["annual_cost"],
        "LCOA(元/kg)":stats["lcoa"],
    }
    path=os.path.join(output_dir,filename)
    with pd.ExcelWriter(path,engine="openpyxl") as writer:
        pd.DataFrame([summary]).to_excel(writer,index=False,sheet_name="工程规模与经济指标")
        if "arr_p_green" in stats:
            pd.DataFrame({
                "小时序号":np.arange(1,8761),"风光出力(kW)":stats["arr_p_green"],
                "电解槽耗电(kW)":stats["arr_p_el"],"合成氨耗电(kW)":stats["arr_p_nh3"],
                "合成氨产量(kg/h)":stats["arr_nh3_prod"],"电网购电(kW)":stats["arr_grid_buy"],
                "电池充电(kW)":stats["arr_bat_chg"],"电池放电(kW)":stats["arr_bat_dis"],
                "电池SOC(kWh)":stats["arr_bat_soc"],"储氢库存(kg)":stats["arr_tank_soc"],
                "氢气产量(kg/h)":stats["arr_h2_prod"],"氢气消耗(kg/h)":stats["arr_h2_cons"],
                "弃电(kW)":stats["arr_curtail"],"逐时电力平衡偏差(kW)":stats["arr_power_balance_residual"],
            }).to_excel(writer,index=False,sheet_name="8760小时调度")
    return path


if __name__=="__main__":
    try:
        target=float(os.getenv("TARGET_NH3_TON","0"))
        if target<=0: target=float(input("请输入目标年产绿氨量（吨/年）：").strip())
        grid=float(os.getenv("MAX_GRID_RATE","1.0"))
        curtail=float(os.getenv("MAX_CURTAIL_RATE","0.2"))
        cf_w,cf_s,_=load_resource_data(os.getenv("FILE_WIND","风资源.xlsx"),
                                      os.getenv("FILE_SOLAR","光资源.xlsx"))
        df=pd.read_excel("model1_result/optimal_capacity.xlsx")
        wind=float(df.loc[0,"最优风电规模(kW)"]); solar=float(df.loc[0,"最优光伏规模(kW)"])
        caps,stats,success,message=optimize_engineering_scale(
            wind,solar,cf_w,cf_s,target,grid,curtail,return_series=True)
        out=save_dispatch_result(stats,target_nh3_ton=target,max_grid_rate=grid,max_curtail_rate=curtail)
        print("设备寻优完成：",message)
        print(f"电解槽{caps[0]/1000:.2f}MW；储能{caps[1]/1000:.2f}MWh；储氢{caps[2]:.0f}kg；合成氨{caps[3]:.0f}kg/h")
        print(f"年产氨{stats['nh3_total']/1000:.2f}吨；下网电率{stats['grid_rate']:.2%}；弃电率{stats['curtail_rate']:.2%}；LCOA{stats['lcoa']:.4f}元/kg")
        print("结果文件：",out)
    except Exception as exc:
        print("模块二计算失败：",exc)
        sys.exit(1)
