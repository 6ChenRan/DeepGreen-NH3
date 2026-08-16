# model2.py
import os
import pandas as pd
import numpy as np
import sys
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from scipy.optimize import minimize
import warnings

warnings.filterwarnings('ignore')

plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False


FILE_SOLAR = r"your path"
FILE_WIND = r"your path"
INPUT_EXCEL = os.path.join("model1_result", "optimal_capacity.xlsx")
OUTPUT_DIR = "model2_result"

try:
    df_opt = pd.read_excel(INPUT_EXCEL)
    OPT_WIND_KW = float(df_opt.loc[0, '最优风电规模(kW)'])
    OPT_SOLAR_KW = float(df_opt.loc[0, '最优光伏规模(kW)'])
    print(f"成功读取模块一结果：风电 {OPT_WIND_KW / 1000:.2f} MW, 光伏 {OPT_SOLAR_KW / 1000:.2f} MW\n")
except FileNotFoundError:
    print(f"错误：未找到 {INPUT_EXCEL}。请先运行 model1.py 生成结果！")
    sys.exit()


COST = {'wind': 4000, 'solar': 3000, 'el': 1500, 'bat': 1000, 'tank': 300, 'nh3': 15000, 'grid': 0.6, 'water': 4.0}
TECH = {'el_eff': 50, 'nh3_elec_req': 1.0, 'nh3_h2_req': 0.177, 'water_req': 15.0, 'crf': 0.0802}

def load_resource_data():
    df_solar = pd.read_excel(FILE_SOLAR)
    df_wind = pd.read_excel(FILE_WIND)
    df_solar['time'] = pd.to_datetime(df_solar['time'])
    cf_solar = df_solar['electricity/KW'].values[:8760] / df_solar['electricity/KW'].max()
    cf_wind = df_wind['electricity/KW'].values[:8760] / df_wind['electricity/KW'].max()
    time_series = df_solar['time'].values[:8760]
    return cf_wind, cf_solar, time_series

def simulate_system(caps, cf_wind, cf_solar, return_series=False):
    cap_el, cap_bat, cap_tank, cap_nh3 = caps
    steps = 8760
    p_green = OPT_WIND_KW * cf_wind + OPT_SOLAR_KW * cf_solar

    days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    month_starts = [0]
    for d in days_in_month:
        month_starts.append(month_starts[-1] + d * 24)

    monthly_gen = [np.sum(p_green[month_starts[m]:month_starts[m + 1]]) for m in range(12)]
    min_month = np.argmin(monthly_gen)
    m_start = month_starts[min_month]
    m_end = month_starts[min_month + 1]

    bat_soc = cap_bat * 0.5
    tank_soc = cap_tank * 0.5

    sum_h2_prod, sum_nh3_prod = 0.0, 0.0
    sum_grid_buy, sum_bat_dis, sum_bat_chg, sum_curtailment = 0.0, 0.0, 0.0, 0.0
    sum_el_power, sum_nh3_power = 0.0, 0.0

    h2_demand_per_hour = cap_nh3 * TECH['nh3_h2_req']
    p_nh3_req = cap_nh3 * TECH['nh3_elec_req']

    if return_series:
        arr_p_el, arr_p_nh3 = np.zeros(steps), np.zeros(steps)
        arr_grid_buy, arr_curtail = np.zeros(steps), np.zeros(steps)
        arr_bat_soc, arr_tank_soc = np.zeros(steps), np.zeros(steps)
        arr_bat_dis, arr_bat_chg = np.zeros(steps), np.zeros(steps)
        arr_h2_prod, arr_h2_cons = np.zeros(steps), np.zeros(steps)

    for i in range(steps):
        p_available = p_green[i]
        is_maintenance = (m_start <= i < m_end)

        if is_maintenance:
            charge = min(p_available, cap_bat - bat_soc)
            bat_soc += charge
            curtail = p_available - charge
            sum_bat_chg += charge
            sum_curtailment += curtail
            if return_series:
                arr_p_el[i], arr_p_nh3[i], arr_grid_buy[i], arr_bat_dis[i] = 0, 0, 0, 0
                arr_curtail[i], arr_bat_chg[i] = curtail, charge
                arr_bat_soc[i], arr_tank_soc[i] = bat_soc, tank_soc
                arr_h2_prod[i], arr_h2_cons[i] = 0, 0
            continue

        dis, charge, grid_buy, curtail, p_el = 0.0, 0.0, 0.0, 0.0, 0.0

        p_rem = p_available - p_nh3_req
        if p_rem < 0:
            deficit_nh3 = -p_rem
            dis_nh3 = min(bat_soc, deficit_nh3, cap_bat * 0.5)
            bat_soc -= dis_nh3
            dis += dis_nh3
            grid_buy += (deficit_nh3 - dis_nh3)
            p_rem = 0.0

        if p_rem > 0:
            p_el = min(p_rem, cap_el)
            p_rem -= p_el
            el_deficit = cap_el - p_el
            if el_deficit > 0 and bat_soc > 0:
                dis_el = min(bat_soc, el_deficit, cap_bat * 0.5 - dis)
                bat_soc -= dis_el
                dis += dis_el
                p_el += dis_el

        h2_prod = p_el / TECH['el_eff']
        tank_soc += h2_prod

        if tank_soc < h2_demand_per_hour:
            h2_shortage = h2_demand_per_hour - tank_soc
            p_el_extra = h2_shortage * TECH['el_eff']
            grid_buy += p_el_extra
            p_el += p_el_extra
            tank_soc += h2_shortage

        tank_soc -= h2_demand_per_hour
        sum_nh3_prod += cap_nh3

        if p_rem > 0:
            charge = min(p_rem, cap_bat - bat_soc)
            bat_soc += charge
            curtail = p_rem - charge

        if tank_soc > cap_tank:
            tank_soc = cap_tank

        sum_h2_prod += (p_el / TECH['el_eff'])
        sum_nh3_power += p_nh3_req
        sum_el_power += p_el
        sum_grid_buy += grid_buy
        sum_bat_dis += dis
        sum_bat_chg += charge
        sum_curtailment += curtail

        if return_series:
            arr_p_el[i], arr_p_nh3[i] = p_el, p_nh3_req
            arr_grid_buy[i], arr_curtail[i] = grid_buy, curtail
            arr_bat_soc[i], arr_tank_soc[i] = bat_soc, tank_soc
            arr_bat_dis[i], arr_bat_chg[i] = dis, charge
            arr_h2_prod[i] = p_el / TECH['el_eff']
            arr_h2_cons[i] = h2_demand_per_hour


    sum_water_tons = sum_h2_prod * TECH['water_req'] / 1000.0

    capex_total = (OPT_WIND_KW * COST['wind'] + OPT_SOLAR_KW * COST['solar'] +
                   cap_el * COST['el'] + cap_bat * COST['bat'] +
                   cap_tank * COST['tank'] + cap_nh3 * COST['nh3'])

    annual_cost = capex_total * TECH['crf'] + sum_grid_buy * COST['grid'] + sum_water_tons * COST['water']
    lcoa = annual_cost / sum_nh3_prod if sum_nh3_prod > 0 else float('inf')

    energy_in = np.sum(p_green) + sum_grid_buy + sum_bat_dis
    energy_out = sum_el_power + sum_nh3_power + sum_bat_chg + sum_curtailment

    res_dict = {
        'lcoa': lcoa,
        'nh3_total': sum_nh3_prod, 'h2_total': sum_h2_prod, 'water_tons': sum_water_tons,
        'grid_buy': sum_grid_buy, 'bat_dis': sum_bat_dis, 'bat_chg': sum_bat_chg,
        'p_green_total': np.sum(p_green), 'curtailment': sum_curtailment,
        'el_power': sum_el_power, 'nh3_power': sum_nh3_power,
        'm_start': m_start, 'm_end': m_end, 'm_month': min_month + 1,
        'energy_in': energy_in, 'energy_out': energy_out, 'balance_diff': abs(energy_in - energy_out)
    }

    if return_series:
        res_dict.update({
            'arr_p_green': p_green, 'arr_p_el': arr_p_el, 'arr_p_nh3': arr_p_nh3,
            'arr_grid_buy': arr_grid_buy, 'arr_curtail': arr_curtail,
            'arr_bat_soc': arr_bat_soc, 'arr_tank_soc': arr_tank_soc,
            'arr_bat_dis': arr_bat_dis, 'arr_bat_chg': arr_bat_chg,
            'arr_h2_prod': arr_h2_prod, 'arr_h2_cons': arr_h2_cons
        })

    return res_dict

def optimize_engineering_scale(cf_wind, cf_solar):
    def objective(x):
        res = simulate_system(x, cf_wind, cf_solar, return_series=False)
        return res['lcoa']

    x0 = [OPT_WIND_KW * 0.6, OPT_WIND_KW * 0.2, OPT_WIND_KW * 0.1, OPT_WIND_KW * 0.05]
    bounds = [(10000, 300000), (0, 500000), (1000, 200000), (100, 50000)]

    print("开始工程规模全局寻优 (正在进行 8760 小时包含检修期的时序计算)...")
    result = minimize(objective, x0, method='L-BFGS-B', bounds=bounds)
    return result.x

if __name__ == "__main__":
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    cf_w, cf_s, times = load_resource_data()
    opt_caps = optimize_engineering_scale(cf_w, cf_s)
    f_stats = simulate_system(opt_caps, cf_w, cf_s, return_series=True)
    cap_el, cap_bat, cap_tank, cap_nh3 = opt_caps

    print("\n" + "=" * 45)
    print("【1. 项目最优工程物理规模】")
    print(f"  ▶ 电解槽总功率 : {cap_el / 1000:.2f} MW")
    print(f"  ▶ 储能系统容量 : {cap_bat / 1000:.2f} MWh")
    print(f"  ▶ 高压储氢容量 : {cap_tank:.2f} kg")
    print(f"  ▶ 合成氨产能   : {cap_nh3:.2f} kg/h")

    print("\n【2. 全年化工检修与生产指标】")
    print(f"  ▶ 智能分配检修期: 第 {f_stats['m_month']} 月 (全线停产保库)")
    print(f"  ▶ 全年绿氨总产量: {f_stats['nh3_total'] / 10000:.2f} 万kg")
    print(f"  ▶ 全年绿氢制取量: {f_stats['h2_total'] / 10000:.2f} 万kg")
    print(f"  ▶ 全年纯水消耗量: {f_stats['water_tons'] / 10000:.2f} 万吨")

    print("\n【3. 物理电量平衡校验】")
    print("  [输入端] = 风光绿电 + 下网电 + 储能放电")
    print(f"           = {f_stats['p_green_total'] / 10000:.1f} + {f_stats['grid_buy'] / 10000:.1f} + {f_stats['bat_dis'] / 10000:.1f} = {f_stats['energy_in'] / 10000:.1f} 万kWh")
    print("  [输出端] = 制氢用电 + 制氨用电 + 储能充电 + 弃电")
    print(f"           = {f_stats['el_power'] / 10000:.1f} + {f_stats['nh3_power'] / 10000:.1f} + {f_stats['bat_chg'] / 10000:.1f} + {f_stats['curtailment'] / 10000:.1f} = {f_stats['energy_out'] / 10000:.1f} 万kWh")
    print("=" * 45 + "\n")

    # --- 保存至 Excel ---
    result_df = pd.DataFrame({
        '风电装机(kW)': [OPT_WIND_KW], '光伏装机(kW)': [OPT_SOLAR_KW],
        '电解槽功率(kW)': [cap_el], '储能容量(kWh)': [cap_bat],
        '高压储氢容量(kg)': [cap_tank], '合成氨产能(kg/h)': [cap_nh3],
        '全年绿氨产量(吨)': [f_stats['nh3_total'] / 1000],
        '全年绿氢产量(吨)': [f_stats['h2_total'] / 1000],
        '全年纯水消耗(吨)': [f_stats['water_tons']],
        '制氢耗电(kWh)': [f_stats['el_power']],
        '制氨耗电(kWh)': [f_stats['nh3_power']],
        '电网购电量(kWh)': [f_stats['grid_buy']]
    })
    excel_path = os.path.join(OUTPUT_DIR, "physical_scale.xlsx")
    result_df.to_excel(excel_path, index=False)
    print(f"[文件导出] 工程物理规模及平衡数据已保存至: {excel_path}")


    plt.figure(figsize=(15, 4))
    plt.plot(times, f_stats['arr_p_green'] / 1000, label='风光综合出力', color='#2ca02c', linewidth=0.5)
    m_start = f_stats['m_start']
    m_end = f_stats['m_end']
    plt.axvspan(times[m_start], times[m_end - 1], color='red', alpha=0.3, label=f'第 {f_stats["m_month"]} 月 (化工检修停产期)')
    plt.title('全年风光综合出力曲线及化工检修期智能落位', fontsize=14)
    plt.xlabel('时间 (月份)')
    plt.ylabel('功率 (MW)')
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%m'))
    plt.legend(loc='upper right')
    plt.grid(True, linestyle=':', alpha=0.6)
    plt.tight_layout()
    img1_path = os.path.join(OUTPUT_DIR, "annual_generation_maintenance.png")
    plt.savefig(img1_path, dpi=300)


    p_start = 4000
    if m_start <= p_start < m_end:
        p_start = (m_end + 1000) % 8760
        if p_start + 336 > 8760: p_start = 0
    p_end = p_start + 336  # 14天

    t_plot = times[p_start:p_end]
    p_green_plot = f_stats['arr_p_green'][p_start:p_end]
    grid_buy_plot = f_stats['arr_grid_buy'][p_start:p_end]
    bat_dis_plot = f_stats['arr_bat_dis'][p_start:p_end]
    p_el_plot = f_stats['arr_p_el'][p_start:p_end]
    p_nh3_plot = f_stats['arr_p_nh3'][p_start:p_end]
    bat_chg_plot = f_stats['arr_bat_chg'][p_start:p_end]

    bat_soc_plot = f_stats['arr_bat_soc'][p_start:p_end] / 1000
    tank_soc_plot = f_stats['arr_tank_soc'][p_start:p_end]
    h2_prod_plot = f_stats['arr_h2_prod'][p_start:p_end]
    h2_cons_plot = f_stats['arr_h2_cons'][p_start:p_end]

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(16, 14), sharex=True)


    ax1.fill_between(t_plot, 0, p_nh3_plot / 1000, label='制氨耗电', color='#e377c2')
    ax1.fill_between(t_plot, p_nh3_plot / 1000, (p_nh3_plot + p_el_plot) / 1000, label='制氢耗电', color='#1f77b4')
    ax1.fill_between(t_plot, (p_nh3_plot + p_el_plot) / 1000, (p_nh3_plot + p_el_plot + bat_chg_plot) / 1000,
                     label='储能充电', color='#9467bd')

    supply_total = (p_green_plot + grid_buy_plot + bat_dis_plot) / 1000
    ax1.plot(t_plot, p_green_plot / 1000, label='可用风光绿电', color='#2ca02c', linewidth=2)
    ax1.plot(t_plot, supply_total, label='综合供电(含下电与放电)', color='red', linestyle='--', linewidth=1.5)


    ax1.fill_between(t_plot, (p_nh3_plot + p_el_plot + bat_chg_plot) / 1000, supply_total,
                     where=(supply_total > (p_nh3_plot + p_el_plot + bat_chg_plot) / 1000),
                     color='gray', alpha=0.5, hatch='//', label='弃电量')

    ax1.set_title('微电网物理电量流向与供需平衡监控图 (截取典型双周)', fontsize=14)
    ax1.set_ylabel('功率 (MW)')
    ax1.legend(loc='upper right', ncol=2)
    ax1.grid(True, linestyle=':', alpha=0.6)

    # 子图2：电池充放电与SOC监控图 (新增的功能)
    ax2.bar(t_plot, bat_chg_plot / 1000, width=0.04, color='blue', alpha=0.6, label='储能充电 (+)')
    ax2.bar(t_plot, -bat_dis_plot / 1000, width=0.04, color='red', alpha=0.6, label='储能放电 (-)')
    ax2.set_ylabel('充放电功率 (MW)')

    ax2_t = ax2.twinx()
    ax2_t.plot(t_plot, bat_soc_plot, color='purple', linewidth=2, label='电池电量 SOC')
    ax2_t.set_ylabel('电池电量 (MWh)', color='purple')
    ax2.set_title('微电网储能充放电与库存状态监控', fontsize=12)
    lines_1, labels_1 = ax2.get_legend_handles_labels()
    lines_1t, labels_1t = ax2_t.get_legend_handles_labels()
    ax2.legend(lines_1 + lines_1t, labels_1 + labels_1t, loc='upper right')
    ax2.grid(True, linestyle=':', alpha=0.6)


    ax3.bar(t_plot, h2_prod_plot, width=0.04, color='teal', alpha=0.6, label='电解制氢入罐 (+)')
    ax3.bar(t_plot, -h2_cons_plot, width=0.04, color='darkorange', alpha=0.6, label='合成氨耗氢出罐 (-)')
    ax3.set_ylabel('氢气流量 (kg/h)')

    ax3_t = ax3.twinx()
    ax3_t.plot(t_plot, tank_soc_plot, color='brown', linewidth=2, linestyle='-.', label='高压储氢罐库存')
    ax3_t.set_ylabel('氢气库存 (kg)', color='brown')
    ax3.set_title('管网注气、抽气与储氢库存状态监控', fontsize=12)
    lines_2, labels_2 = ax3.get_legend_handles_labels()
    lines_2t, labels_2t = ax3_t.get_legend_handles_labels()
    ax3.legend(lines_2 + lines_2t, labels_2 + labels_2t, loc='upper right')
    ax3.grid(True, linestyle=':', alpha=0.6)

    ax3.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    plt.xticks(rotation=45)
    plt.tight_layout()

    img2_path = os.path.join(OUTPUT_DIR, "storage_dispatch_details.png")
    plt.savefig(img2_path, dpi=300)
    print(f"[图像导出] 电量平衡与调度细节三联图已保存至: {img2_path}")

    plt.show()