# -*- coding: utf-8 -*-
"""模块四：绿电端LCOE/全投资IRR 6%反算售电价，化工端双电价情景评价。

模型口径：全投资、税前、无融资；建设期投资集中于第0年；运营期产量、电量、
运维费及水电费按年度保持不变，不计折旧抵税、所得税、增值税、补贴、残值和价格增长。
这是可研前期财务规划估算，而非完整的税后融资财务模型。
"""
import os
import sys
import numpy as np
import pandas as pd
import numpy_financial as npf
from model2 import capital_recovery_factor, DEFAULT_COST

TARGET_GREEN_IRR = 0.06


def _build_cashflows(capex, annual_revenue, annual_opex, discount_rate, project_life):
    """全投资税前现金流：第0年投资，之后每年营业收入减运营费用。"""
    n = int(project_life)
    capex, annual_revenue, annual_opex = map(float, (capex, annual_revenue, annual_opex))
    cash_flows = np.array([-capex] + [annual_revenue - annual_opex] * n, dtype=float)
    npv = float(npf.npv(discount_rate, cash_flows))
    irr_raw = npf.irr(cash_flows)
    irr = float(irr_raw) if irr_raw is not None and np.isfinite(irr_raw) else np.nan
    discounted_cf = cash_flows / np.power(1 + discount_rate, np.arange(n + 1))
    cumulative_dcf = np.cumsum(discounted_cf)
    payback = np.nan
    for i in range(1, n + 1):
        if cumulative_dcf[i] >= 0 and discounted_cf[i] > 0:
            payback = (i - 1) + abs(cumulative_dcf[i - 1]) / discounted_cf[i]
            break
    return {
        "cash_flows": cash_flows.tolist(),
        "discounted_cash_flows": discounted_cf.tolist(),
        "cumulative_discounted_cash_flows": cumulative_dcf.tolist(),
        "npv": npv, "irr": irr, "payback_year": payback,
        "annual_cash_flow": annual_revenue - annual_opex,
        "annual_revenue": annual_revenue, "annual_opex": annual_opex,
    }


def calculate_economics(stats, cost=None, price_nh3=4500.0,
                        discount_rate=0.08, project_life=20, om_rate=0.02,
                        target_green_irr=TARGET_GREEN_IRR):
    """返回整体财务结果、绿电侧LCOE及IRR定价、化工端两种售电价情景。

    资产划分：绿电端=风电+光伏；化工端=电解槽+储能+储氢+合成氨。
    能源边界：绿电端向化工端售电量=全年风光发电量-全年弃电量；
    储能归化工端，因此包含电池充电用电及其损失，避免把电池损失漏计。
    """
    cost = {**DEFAULT_COST, **(cost or {})}
    if not (0 <= discount_rate <= 1) or not (0 < target_green_irr < 1):
        raise ValueError("折现率及目标绿电IRR参数超出有效范围。")
    if int(project_life) <= 0 or om_rate < 0:
        raise ValueError("运营年限必须为正，运维费率不能为负。")

    nh3_kg, h2_kg = float(stats["nh3_total"]), float(stats["h2_total"])
    if nh3_kg <= 0 or h2_kg <= 0:
        raise ValueError("绿氨或绿氢产量为0，无法测算收益。")
    nh3_tons, h2_tons = nh3_kg / 1000, h2_kg / 1000
    wind_gen = float(stats.get("wind_total", 0.0))
    solar_gen = float(stats.get("solar_total", 0.0))
    green_generation = float(stats.get("p_green_total", wind_gen + solar_gen))
    curtailment = float(stats.get("curtailment", 0.0))
    green_sales_kwh = green_generation - curtailment
    if green_generation <= 0 or green_sales_kwh <= 0:
        raise ValueError("全年风光发电量或可售电量为0，无法计算绿电LCOE及IRR定价。")
    if curtailment < -1e-6 or curtailment > green_generation + 1e-6:
        raise ValueError("弃电量必须处于0至总发电量之间。")

    grid_buy = float(stats["grid_buy"])
    process_power = float(stats["el_power"]) + float(stats["nh3_power"])
    water_tons = float(stats["water_tons"])
    if min(grid_buy, process_power, water_tons) < 0:
        raise ValueError("购电、工艺电耗和水耗不能出现负值。")
    # 电量平衡：绿电售出量 + 网电 = 工艺用电 + 电池损失 + 库存变化。
    # 注意内部储能已归化工端，绿电售出量不等于最终工艺电耗。
    battery_loss = float(stats.get("battery_loss", 0.0))
    battery_delta = float(stats.get("battery_cycle_delta", 0.0))
    balance_gap = green_sales_kwh + grid_buy - process_power - battery_loss - battery_delta
    if abs(balance_gap) > max(0.1, (green_sales_kwh + grid_buy) * 1e-7):
        raise ValueError(f"绿电侧与化工侧计费电量无法闭合，请核对调度结果，偏差{balance_gap:,.2f}kWh。")

    # 投资分部：不得重复计入风光资产，电池归化工端。
    capex_green = (float(stats["wind_kw"]) * cost["wind"]
                   + float(stats["solar_kw"]) * cost["solar"])
    capex_chemical = (float(stats["cap_el"]) * cost["el"]
                      + float(stats["cap_bat"]) * cost["bat"]
                      + float(stats["cap_tank"]) * cost["tank"]
                      + float(stats["cap_nh3"]) * cost["nh3"])
    capex_total = capex_green + capex_chemical
    crf = capital_recovery_factor(discount_rate, project_life)
    crf_6 = capital_recovery_factor(target_green_irr, project_life)
    om_green = capex_green * om_rate
    om_chemical = capex_chemical * om_rate
    om_total = om_green + om_chemical
    annualized_green = capex_green * crf
    annualized_chemical = capex_chemical * crf
    annualized_total = capex_total * crf
    grid_cost = grid_buy * cost["grid"]
    water_cost = water_tons * cost["water"]
    green_annual_cost = annualized_green + om_green
    overall_annual_cost = annualized_total + om_total + grid_cost + water_cost
    annual_revenue_nh3 = nh3_tons * float(price_nh3)

    # (1) 绿电端生产LCOE：以项目全发电量为分母，符合原模块四口径。
    lcoe_green = green_annual_cost / green_generation
    # 增列实际可售口径成本，便于理解弃电对有效电价的影响（非用户销售电价情景）。
    lcoe_sold_equivalent = green_annual_cost / green_sales_kwh
    # (2) IRR6%反推售电价：第0年投入全投资，年1..n年以相同可售电量销售，
    # 令绿电端税前全投资IRR恰好达到6%。不把化工端资产或其利润计入绿电端。
    price_irr6 = (capex_green * crf_6 + om_green) / green_sales_kwh

    tariff_scenarios = {
        "LCOE成本电价": lcoe_green,
        "绿电端IRR6%售电价": price_irr6,
    }
    green_finances = {}
    chemical_finances = {}
    chemical_scenarios = []
    green_scenarios = []

    for scenario, tariff in tariff_scenarios.items():
        green_sale_income = green_sales_kwh * tariff
        chemical_opex = om_chemical + green_sale_income + grid_cost + water_cost
        chem_annual_cost = annualized_chemical + chemical_opex
        gfin = _build_cashflows(capex_green, green_sale_income, om_green,
                                discount_rate, project_life)
        cfin = _build_cashflows(capex_chemical, annual_revenue_nh3,
                                chemical_opex, discount_rate, project_life)
        green_finances[scenario] = {
            **gfin, "capex": capex_green, "annualized_capex": annualized_green,
            "annual_om": om_green, "annual_cost": green_annual_cost,
            "sale_price_yuan_per_kwh": tariff, "sale_volume_kwh": green_sales_kwh,
        }
        chemical_finances[scenario] = {
            **cfin, "capex": capex_chemical,
            "annualized_capex": annualized_chemical,
            "annual_om": om_chemical, "green_power_cost": green_sale_income,
            "annual_grid_cost": grid_cost, "annual_water_cost": water_cost,
            "annual_cost": chem_annual_cost,
            "lcoa_yuan_per_kg": chem_annual_cost / nh3_kg,
            "sale_price_yuan_per_kwh": tariff,
        }
        green_scenarios.append({
            "电价方案": scenario, "售电单价(元/kWh)": tariff,
            "年可售绿电量(kWh)": green_sales_kwh,
            "年售电收入(元)": green_sale_income,
            "绿电端总投资(元)": capex_green,
            "绿电端年运维费(元)": om_green,
            "绿电端全投资IRR(%)": gfin["irr"] * 100 if np.isfinite(gfin["irr"]) else np.nan,
            "绿电端NPV(元)": gfin["npv"],
            "绿电端动态回收期(年)": gfin["payback_year"],
        })
        chemical_scenarios.append({
            "电价方案": scenario, "化工端输入绿电价(元/kWh)": tariff,
            "化工端绿电购电量(kWh)": green_sales_kwh,
            "化工端绿电成本(元/年)": green_sale_income,
            "化工端外购网电费(元/年)": grid_cost,
            "化工端其他运营费(元/年)": om_chemical + water_cost,
            "化工端总投资(元)": capex_chemical,
            "化工端年化总成本(元/年)": chem_annual_cost,
            "化工端LCOA(元/kg)": chem_annual_cost / nh3_kg,
            "化工端年收入(元)": annual_revenue_nh3,
            "化工端年度净现金流(元)": cfin["annual_cash_flow"],
            "化工端全投资IRR(%)": cfin["irr"] * 100 if np.isfinite(cfin["irr"]) else np.nan,
            "化工端NPV(元)": cfin["npv"],
            "化工端动态回收期(年)": cfin["payback_year"],
        })

    # 两套情景共同的整体项目现金流：内部售电收入/成本完全抵销。
    overall_fin = _build_cashflows(capex_total, annual_revenue_nh3,
                                   om_total + grid_cost + water_cost,
                                   discount_rate, project_life)
    # 验证内部交易合并抵销的精度。
    for scenario in tariff_scenarios:
        combined = np.array(green_finances[scenario]["cash_flows"]) + np.array(chemical_finances[scenario]["cash_flows"])
        if not np.allclose(combined, overall_fin["cash_flows"], atol=0.01, rtol=1e-10):
            raise RuntimeError(f"{scenario}下分部现金流无法与整体项目现金流闭合。")

    actual_green_irr6 = green_finances["绿电端IRR6%售电价"]["irr"]
    if not np.isfinite(actual_green_irr6) or abs(actual_green_irr6 - target_green_irr) > 1e-7:
        raise RuntimeError("反推售电价未达到绿电端全投资IRR目标，请检查现金流计算。")

    # 详细财务表和逐年现金流
    cost_rows = [
        ["绿电端", capex_green, annualized_green, om_green, 0.0, 0.0,
         0.0, green_annual_cost],
        ["化工端-LCOE电价", capex_chemical, annualized_chemical, om_chemical,
         green_finances["LCOE成本电价"]["annual_revenue"], grid_cost, water_cost,
         chemical_finances["LCOE成本电价"]["annual_cost"]],
        ["化工端-IRR6%电价", capex_chemical, annualized_chemical, om_chemical,
         green_finances["绿电端IRR6%售电价"]["annual_revenue"], grid_cost, water_cost,
         chemical_finances["绿电端IRR6%售电价"]["annual_cost"]],
        ["整体项目(内部交易抵销)", capex_total, annualized_total, om_total, 0.0,
         grid_cost, water_cost, overall_annual_cost],
    ]
    detail_cost = pd.DataFrame(cost_rows, columns=[
        "项目分部", "总投资(元)", "年化投资(元/年)", "年运维费(元/年)",
        "内部绿电购电费(元/年)", "外购网电费(元/年)", "水成本(元/年)", "年总成本(元/年)",
    ])
    equipment = [
        ("绿电端", "风力发电", float(stats["wind_kw"]), "kW", cost["wind"]),
        ("绿电端", "光伏发电", float(stats["solar_kw"]), "kW", cost["solar"]),
        ("化工端", "电解槽", float(stats["cap_el"]), "kW", cost["el"]),
        ("化工端", "电化学储能", float(stats["cap_bat"]), "kWh", cost["bat"]),
        ("化工端", "储氢系统", float(stats["cap_tank"]), "kg", cost["tank"]),
        ("化工端", "合成氨装置", float(stats["cap_nh3"]), "kg/h", cost["nh3"]),
    ]
    capex_breakdown = pd.DataFrame([{
        "所属分部": part, "设备名称": name, "配置规模": qty, "规模单位": unit,
        "投资单价(元/单位)": unit_cost, "总投资(元)": qty * unit_cost,
        "年化资本成本(元/年)": qty * unit_cost * crf,
        "年运维成本(元/年)": qty * unit_cost * om_rate,
    } for part, name, qty, unit, unit_cost in equipment])

    detail_energy = pd.DataFrame([
        ["全年风电发电量(kWh)", wind_gen],
        ["全年光伏发电量(kWh)", solar_gen],
        ["全年风光发电量(kWh)", green_generation],
        ["全年弃电量(kWh)", curtailment],
        ["全年绿电销售/交付量(kWh)", green_sales_kwh],
        ["全年网购电量(kWh)", grid_buy],
        ["全年制氢耗电量(kWh)", float(stats["el_power"])],
        ["全年制氨耗电量(kWh)", float(stats["nh3_power"])],
        ["全年工艺总耗电量(kWh)", process_power],
        ["全年电池损耗(kWh)", battery_loss],
        ["年末电池库存变化(kWh)", battery_delta],
        ["全年纯水消耗(吨)", water_tons],
        ["全年绿氨产量(吨)", nh3_tons],
        ["全年绿氢产量(吨)", h2_tons],
    ], columns=["指标", "数值"])

    comparison = pd.DataFrame(green_scenarios)
    chemical_comparison = pd.DataFrame(chemical_scenarios)
    years = np.arange(int(project_life) + 1)
    yearly_cash_flows = pd.DataFrame({"年度": years})
    for scenario in tariff_scenarios:
        label = "LCOE电价" if scenario == "LCOE成本电价" else "绿电端IRR6%电价"
        for part, values in (("绿电端", green_finances[scenario]),
                             ("化工端", chemical_finances[scenario])):
            yearly_cash_flows[f"{label}-{part}现金流(元)"] = values["cash_flows"]
            yearly_cash_flows[f"{label}-{part}折现净现金流(元)"] = values["discounted_cash_flows"]
            yearly_cash_flows[f"{label}-{part}累计折现现金流(元)"] = values["cumulative_discounted_cash_flows"]
    yearly_cash_flows["整体项目现金流(元)"] = overall_fin["cash_flows"]
    yearly_cash_flows["整体项目折现净现金流(元)"] = overall_fin["discounted_cash_flows"]
    yearly_cash_flows["整体项目累计折现现金流(元)"] = overall_fin["cumulative_discounted_cash_flows"]

    # 三套全生命周期测算：一（整体项目）；二（绿电端LCOE/IRR6%两情景）；
    # 三（化工端LCOE/IRR6%两情景）。每套均展开第0年投资及第1..N年的明细。
    def lifecycle_detail(part, scenario, fin, capex, electricity_price=0.0,
                         annual_quantity=0.0, green_power_purchase=0.0,
                         grid_purchase_cost=0.0, water_purchase_cost=0.0,
                         maintenance_cost=0.0):
        n = int(project_life)
        records = []
        cum = 0.0
        for year in range(n + 1):
            running = year > 0
            invest = float(capex) if year == 0 else 0.0
            revenue = float(fin['annual_revenue']) if running else 0.0
            green_buy = float(green_power_purchase) if running else 0.0
            grid_cost_y = float(grid_purchase_cost) if running else 0.0
            water_cost_y = float(water_purchase_cost) if running else 0.0
            om_y = float(maintenance_cost) if running else 0.0
            annual_opex_y = green_buy + grid_cost_y + water_cost_y + om_y
            net = revenue - annual_opex_y - invest
            dcf = net / ((1.0 + discount_rate) ** year)
            cum += dcf
            records.append({
                '项目': part, '电价情景': scenario,
                '年份': year, '阶段': '建设投资期' if year == 0 else f'运营第{year}年',
                '售电单价/购电单价(元/kWh)': electricity_price if running else 0.0,
                '年绿电售电/购电量(kWh)': annual_quantity if running else 0.0,
                '绿氨产量(吨)': nh3_tons if running and part != '绿电端' else 0.0,
                '绿氨营业收入(元)': revenue if running and part != '绿电端' else 0.0,
                '绿电销售收入(元)': revenue if running and part == '绿电端' else 0.0,
                '营业收入合计(元)': revenue,
                '建设投资(元)': invest,
                '年运维费(元)': om_y,
                '内部绿电采购费(元)': green_buy,
                '外购网电费(元)': grid_cost_y,
                '工业水费(元)': water_cost_y,
                '运营现金支出合计(元)': annual_opex_y,
                '年度净现金流(元)': net,
                '折现系数': 1.0 / ((1.0 + discount_rate) ** year),
                '年度折现净现金流(元)': dcf,
                '累计折现净现金流(元)': cum,
            })
        df = pd.DataFrame(records)
        if not np.allclose(df['年度净现金流(元)'].to_numpy(), fin['cash_flows'], atol=0.01):
            raise RuntimeError(f'{part}/{scenario}全生命周期逐年现金流与财务指标口径不一致。')
        return df

    overall_detail = lifecycle_detail(
        '整体项目', '内部绿电交易抵销', overall_fin, capex_total,
        grid_purchase_cost=grid_cost, water_purchase_cost=water_cost,
        maintenance_cost=om_total)
    green_lcoe_detail = lifecycle_detail(
        '绿电端', 'LCOE成本电价', green_finances['LCOE成本电价'], capex_green,
        electricity_price=lcoe_green, annual_quantity=green_sales_kwh,
        maintenance_cost=om_green)
    green_irr_detail = lifecycle_detail(
        '绿电端', '绿电端IRR6%售电价', green_finances['绿电端IRR6%售电价'], capex_green,
        electricity_price=price_irr6, annual_quantity=green_sales_kwh,
        maintenance_cost=om_green)
    chemical_lcoe_detail = lifecycle_detail(
        '化工端', 'LCOE成本电价', chemical_finances['LCOE成本电价'], capex_chemical,
        electricity_price=lcoe_green, annual_quantity=green_sales_kwh,
        green_power_purchase=green_sales_kwh*lcoe_green,
        grid_purchase_cost=grid_cost, water_purchase_cost=water_cost,
        maintenance_cost=om_chemical)
    chemical_irr_detail = lifecycle_detail(
        '化工端', '绿电端IRR6%售电价', chemical_finances['绿电端IRR6%售电价'], capex_chemical,
        electricity_price=price_irr6, annual_quantity=green_sales_kwh,
        green_power_purchase=green_sales_kwh*price_irr6,
        grid_purchase_cost=grid_cost, water_purchase_cost=water_cost,
        maintenance_cost=om_chemical)

    # 形成“一套整体+两套绿电价格情景+两套化工价格情景”的三套测算分类。
    lifecycle_models = {
        '一_整体项目逐年详表': overall_detail,
        '二_绿电LCOE逐年详表': green_lcoe_detail,
        '二_绿电IRR6逐年详表': green_irr_detail,
        '三_化工LCOE逐年详表': chemical_lcoe_detail,
        '三_化工IRR6逐年详表': chemical_irr_detail,
    }
    scenario_summary_rows = [
        ('整体项目', '内部交易抵销', capex_total, annual_revenue_nh3,
         om_total + grid_cost + water_cost, overall_annual_cost, overall_annual_cost/nh3_kg,
         overall_fin),
        ('绿电端', 'LCOE成本电价', capex_green,
         green_finances['LCOE成本电价']['annual_revenue'], om_green,
         green_annual_cost, np.nan, green_finances['LCOE成本电价']),
        ('绿电端', '绿电端IRR6%售电价', capex_green,
         green_finances['绿电端IRR6%售电价']['annual_revenue'], om_green,
         green_annual_cost, np.nan, green_finances['绿电端IRR6%售电价']),
        ('化工端', 'LCOE成本电价', capex_chemical, annual_revenue_nh3,
         chemical_finances['LCOE成本电价']['annual_opex'],
         chemical_finances['LCOE成本电价']['annual_cost'],
         chemical_finances['LCOE成本电价']['lcoa_yuan_per_kg'],
         chemical_finances['LCOE成本电价']),
        ('化工端', '绿电端IRR6%售电价', capex_chemical, annual_revenue_nh3,
         chemical_finances['绿电端IRR6%售电价']['annual_opex'],
         chemical_finances['绿电端IRR6%售电价']['annual_cost'],
         chemical_finances['绿电端IRR6%售电价']['lcoa_yuan_per_kg'],
         chemical_finances['绿电端IRR6%售电价']),
    ]
    scenario_summary = pd.DataFrame([{
        '测算分类': area, '方案': plan,
        '总投资(元)': capex, '年营业收入(元)': revenue,
        '年现金运营支出(元)': opex, '年化总成本(元)': annual_cost,
        'LCOA(元/kg)': lcoa, '全投资NPV(元)': fin['npv'],
        '全投资IRR(%)': fin['irr']*100 if np.isfinite(fin['irr']) else np.nan,
        '动态回收期(年)': fin['payback_year'],
    } for area, plan, capex, revenue, opex, annual_cost, lcoa, fin in scenario_summary_rows])

    # 逐年资产与收益合并核对，必须对两个内部电价情景都恒成立。
    for scenario, gdetail, cdetail in (
        ('LCOE成本电价', green_lcoe_detail, chemical_lcoe_detail),
        ('绿电端IRR6%售电价', green_irr_detail, chemical_irr_detail),
    ):
        net_gap = gdetail['年度净现金流(元)'].to_numpy() + cdetail['年度净现金流(元)'].to_numpy() - overall_detail['年度净现金流(元)'].to_numpy()
        if not np.allclose(net_gap, 0.0, atol=0.01, rtol=1e-10):
            raise RuntimeError(f'{scenario}下年度现金流合并抵销不平衡。')

    # 兼容 V1.2 Web 结果字典，新增情景字段。
    return {
        "capex_total": capex_total,
        "annualized_capex": annualized_total,
        "annual_grid_cost": grid_cost,
        "annual_water_cost": water_cost,
        "annual_om_cost": om_total,
        "annual_opex": om_total + grid_cost + water_cost,
        "annual_total_cost": overall_annual_cost,
        "annual_revenue": annual_revenue_nh3,
        "nh3_tons": nh3_tons, "h2_tons": h2_tons,
        "unit_electricity_kwh_per_ton": process_power / nh3_tons,
        "unit_water_ton_per_ton": water_tons / nh3_tons,
        "unit_grid_cost_yuan_per_ton": grid_cost / nh3_tons,
        "unit_water_cost_yuan_per_ton": water_cost / nh3_tons,
        "unit_capital_om_yuan_per_ton": (annualized_total + om_total) / nh3_tons,
        "lcoa_yuan_per_kg": overall_annual_cost / nh3_kg,
        # 兼容原V1.2氢侧口径（总系统年化成本除以产氢量），不代表分制氢成本。
        "lcoh_yuan_per_kg": overall_annual_cost / h2_kg,
        "lcoe_green_yuan_per_kwh": lcoe_green,
        "lcoe_sale_equivalent_yuan_per_kwh": lcoe_sold_equivalent,
        "irr6_sale_price_yuan_per_kwh": price_irr6,
        "target_green_irr": target_green_irr,
        "green_generation_kwh": green_generation,
        "green_used_kwh": green_sales_kwh,
        "green_sales_kwh": green_sales_kwh,
        "curtailment_kwh": curtailment,
        "green_side": green_finances["LCOE成本电价"],
        "chemical_side": chemical_finances["LCOE成本电价"],
        "overall_side": {**overall_fin, "capex": capex_total,
                         "annualized_capex": annualized_total,
                         "annual_om": om_total,
                         "annual_cost": overall_annual_cost},
        "green_scenarios": green_finances,
        "chemical_scenarios": chemical_finances,
        "price_scenarios": tariff_scenarios,
        "npv": overall_fin["npv"],
        "irr": overall_fin["irr"],
        "payback_year": overall_fin["payback_year"],
        "cash_flows": overall_fin["cash_flows"],
        "discounted_cash_flows": overall_fin["discounted_cash_flows"],
        "cumulative_discounted_cash_flows": overall_fin["cumulative_discounted_cash_flows"],
        "detail_cost_table": detail_cost,
        "capex_breakdown": capex_breakdown,
        "detail_energy_table": detail_energy,
        "green_price_comparison": comparison,
        "chemical_price_comparison": chemical_comparison,
        "yearly_cash_flows": yearly_cash_flows,
        "three_model_summary": scenario_summary,
        "lifecycle_models": lifecycle_models,
        "overall_lifecycle_detail": overall_detail,
        "green_lcoe_lifecycle_detail": green_lcoe_detail,
        "green_irr6_lifecycle_detail": green_irr_detail,
        "chemical_lcoe_lifecycle_detail": chemical_lcoe_detail,
        "chemical_irr6_lifecycle_detail": chemical_irr_detail,
    }


def validate_module4_dual_price():
    """模块四双电价回归验证（内置版）。

    使用合成8760小时风光资源生成测试工况，不依赖项目真实资源数据。
    验证绿电端IRR 6%反推电价、LCOE口径、两种电价场景和分部现金流抵销。
    """
    from model2 import simulate_system

    hours = np.arange(8760)
    wind = np.clip(0.45 + 0.3 * np.sin(hours * 2 * np.pi / 24), 0, 1)
    solar = np.maximum(np.sin((hours % 24 - 6) / 12 * np.pi), 0) * 0.75
    stats = simulate_system(
        [50000, 12000, 15000, 6500],
        80000, 55000, wind, solar,
        max_state_cycles=3,
        return_series=False,
    )
    result = calculate_economics(
        stats, discount_rate=0.08, project_life=20,
        target_green_irr=TARGET_GREEN_IRR,
    )

    irr6 = result['green_scenarios']['绿电端IRR6%售电价']['irr']
    assert abs(irr6 - TARGET_GREEN_IRR) < 1e-8, (
        f'IRR6反推电价错误，实际IRR={irr6}'
    )

    generation = result['green_generation_kwh']
    sale_volume = result['green_sales_kwh']
    assert abs((generation - stats['curtailment']) - sale_volume) < 1e-7
    expected_lcoe = (
        result['green_scenarios']['LCOE成本电价']['annual_cost'] / generation
    )
    assert abs(result['lcoe_green_yuan_per_kwh'] - expected_lcoe) < 1e-12

    assert len(result['chemical_price_comparison']) == 2
    assert len(result['yearly_cash_flows']) == 21
    assert len(result['green_price_comparison']) == 2

    overall = np.asarray(result['overall_side']['cash_flows'], dtype=float)
    for scenario in result['price_scenarios']:
        green_cf = np.asarray(result['green_scenarios'][scenario]['cash_flows'], dtype=float)
        chemical_cf = np.asarray(result['chemical_scenarios'][scenario]['cash_flows'], dtype=float)
        assert np.allclose(green_cf + chemical_cf, overall, rtol=1e-12, atol=1e-5), (
            f'{scenario}场景下绿电端与化工端现金流未能抵销至整体项目。'
        )

    assert np.isclose(stats['capex_total'], result['capex_total'])
    assert np.isclose(stats['annual_cost'], result['annual_total_cost'])
    assert np.isclose(stats['lcoa'], result['lcoa_yuan_per_kg'])

    print('模块四双电价验证通过：')
    print(f"绿电生产LCOE：{result['lcoe_green_yuan_per_kwh']:.5f} 元/kWh")
    print(f"绿电IRR6%售电价：{result['irr6_sale_price_yuan_per_kwh']:.5f} 元/kWh")
    print(f"反推绿电端实际IRR：{irr6:.9%}")
    print('两套分部现金流与整体项目逐年抵销：通过')
    print('模块二年化总成本与模块四整体LCOA：一致')
    print(result['chemical_price_comparison'][[
        '电价方案', '化工端输入绿电价(元/kWh)', '化工端全投资IRR(%)',
        '化工端NPV(元)', '化工端LCOA(元/kg)',
    ]].to_string(index=False))
    return result


def validate_module4_three_lifecycle_models(result=None):
    """三套全生命周期测算及五张逐年现金流详表验证（内置版）。"""
    if result is None:
        result = validate_module4_dual_price()

    names = [
        '一_整体项目逐年详表',
        '二_绿电LCOE逐年详表',
        '二_绿电IRR6逐年详表',
        '三_化工LCOE逐年详表',
        '三_化工IRR6逐年详表',
    ]
    assert set(result['lifecycle_models']) == set(names), (
        '全生命周期现金流详表名称或数量不正确。'
    )
    assert len(result['three_model_summary']) == 5, '三套测算汇总应包含整体项目、绿电端两情景、化工端两情景共5行。'

    expected_rows = len(result['overall_side']['cash_flows'])
    for name in names:
        table = result['lifecycle_models'][name]
        assert len(table) == expected_rows, f'{name}逐年行数不正确。'
        assert table.iloc[0]['建设投资(元)'] > 0, f'{name}第0年未列示建设投资。'
        assert (table.iloc[1:]['建设投资(元)'] == 0).all(), f'{name}运营期重复计入建设投资。'
        assert np.isclose(
            table['年度折现净现金流(元)'].sum(),
            table.iloc[-1]['累计折现净现金流(元)'],
            atol=0.01,
        ), f'{name}累计折现现金流与逐年折现现金流合计不一致。'

    overall_cf = result['lifecycle_models'][names[0]]['年度净现金流(元)'].to_numpy(dtype=float)
    for green_name, chemical_name in ((names[1], names[3]), (names[2], names[4])):
        green_cf = result['lifecycle_models'][green_name]['年度净现金流(元)'].to_numpy(dtype=float)
        chemical_cf = result['lifecycle_models'][chemical_name]['年度净现金流(元)'].to_numpy(dtype=float)
        assert np.allclose(green_cf + chemical_cf, overall_cf, atol=0.01, rtol=1e-10), (
            f'{green_name}与{chemical_name}合并后不能与整体项目现金流闭合。'
        )

    irr6 = result['green_scenarios']['绿电端IRR6%售电价']['irr']
    assert abs(irr6 - TARGET_GREEN_IRR) < 1e-8, '绿电端IRR6%反推售电价未达到6%目标。'
    print('三套测算与五张逐年现金流详表验证全部通过。')
    return result


def run_module4_validation_tests():
    """一键运行模块四两组内置验证，返回测试所用合成数据测算结果。"""
    result = validate_module4_dual_price()
    validate_module4_three_lifecycle_models(result)
    print('模块四全部内置验证通过。')
    return result


if __name__ == "__main__":
    # 无需真实项目Excel即可运行：python model4_economics.py --self-test
    if len(sys.argv) > 1 and sys.argv[1] in ("--self-test", "--validate", "--test"):
        run_module4_validation_tests()
        sys.exit(0)

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
            "p_green_total": float(row["全年风光发电量(kWh)"]),
            "wind_total": float(row.get("全年风电发电量(kWh)", 0.0)),
            "solar_total": float(row.get("全年光伏发电量(kWh)", 0.0)),
            "curtailment": float(row["全年弃电量(kWh)"]),
            "battery_loss": float(row.get("电池损耗(kWh)", 0.0)),
            "battery_cycle_delta": float(row.get("电池期末库存(kWh)", 0.0)) - float(row.get("电池期初库存(kWh)", 0.0)),
        }
        econ = calculate_economics(stats)
        os.makedirs("economics_result", exist_ok=True)
        with pd.ExcelWriter("economics_result/economic_evaluation.xlsx") as writer:
            econ["three_model_summary"].to_excel(writer, index=False, sheet_name="三套财务方案汇总")
            econ["capex_breakdown"].to_excel(writer, index=False, sheet_name="设备投资明细")
            econ["detail_cost_table"].to_excel(writer, index=False, sheet_name="整体与分部成本")
            econ["detail_energy_table"].to_excel(writer, index=False, sheet_name="能源与产量依据")
            econ["green_price_comparison"].to_excel(writer, index=False, sheet_name="绿电端两种电价")
            econ["chemical_price_comparison"].to_excel(writer, index=False, sheet_name="化工端双电价")
            for sheet, table in econ["lifecycle_models"].items():
                table.to_excel(writer, index=False, sheet_name=sheet[:31])
        print(f"绿电LCOE: {econ['lcoe_green_yuan_per_kwh']:.4f} 元/kWh")
        print(f"绿电端IRR6%售电价: {econ['irr6_sale_price_yuan_per_kwh']:.4f} 元/kWh")
    except Exception as exc:
        print("模块四计算失败：", exc)
        sys.exit(1)
