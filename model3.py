import os
import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from scipy.optimize import minimize
import warnings

warnings.filterwarnings('ignore')

plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False


FILE_SOLAR = r"E:\JJ\河南固始县_2021至2025光资源.xlsx"
FILE_WIND = r"E:\JJ\河南固始县_2021至2025风资源.xlsx"
INPUT_M1_EXCEL = os.path.join("model1_result", "optimal_capacity.xlsx")

INPUT_M2_EXCEL = os.path.join("model2_result", "physical_scale.xlsx")
OUTPUT_DIR = "model3_result"


try:
    df_m1 = pd.read_excel(INPUT_M1_EXCEL)
    M1_RATIO = float(df_m1.loc[0, '风光配比(风比光)'])

    df_m2 = pd.read_excel(INPUT_M2_EXCEL)
    FIXED_CAP_EL = float(df_m2.loc[0, '电解槽功率(kW)'])
    FIXED_CAP_BAT = float(df_m2.loc[0, '储能容量(kWh)'])
    FIXED_CAP_TANK = float(df_m2.loc[0, '高压储氢容量(kg)'])
    FIXED_CAP_NH3 = float(df_m2.loc[0, '合成氨产能(kg/h)'])
    print(f"成功读取模块二设备规模：")
    print(f"电解槽: {FIXED_CAP_EL / 1000:.2f} MW | 储能: {FIXED_CAP_BAT / 1000:.2f} MWh")
    print(f"储氢罐: {FIXED_CAP_TANK:.2f} kg | 合成氨: {FIXED_CAP_NH3:.2f} kg/h\n")
except FileNotFoundError as e:
    print(f"错误：未找到前置结果文件 {e.filename}。请先依次运行 model1.py 和 model2.py！")
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



def simulate_validation(wind_kw, solar_kw, cf_wind, cf_solar, return_series=False):
    """
    固定模块二的下游设备规模，输入新的风光规模进行8760小时电量与水耗平衡核算
    """
    steps = 8760
    p_green = wind_kw * cf_wind + solar_kw * cf_solar

    # === 划定化工检修期 ===
    days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    month_starts = [0]
    for d in days_in_month:
        month_starts.append(month_starts[-1] + d * 24)

    monthly_gen = [np.sum(p_green[month_starts[m]:month_starts[m + 1]]) for m in range(12)]
    min_month = np.argmin(monthly_gen)
    m_start = month_starts[min_month]
    m_end = month_starts[min_month + 1]

    bat_soc = FIXED_CAP_BAT * 0.5
    tank_soc = FIXED_CAP_TANK * 0.5

    sum_h2_prod, sum_nh3_prod = 0.0, 0.0
    sum_grid_buy, sum_bat_dis, sum_bat_chg, sum_curtailment = 0.0, 0.0, 0.0, 0.0
    sum_el_power, sum_nh3_power = 0.0, 0.0

    h2_demand_per_hour = FIXED_CAP_NH3 * TECH['nh3_h2_req']
    p_nh3_req = FIXED_CAP_NH3 * TECH['nh3_elec_req']

    if return_series:
        arr_load = np.zeros(steps)
        arr_curtail = np.zeros(steps)
        arr_grid_buy = np.zeros(steps)

    for i in range(steps):
        p_available = p_green[i]

        is_maintenance = (m_start <= i < m_end)

        if is_maintenance:

            charge = min(p_available, FIXED_CAP_BAT - bat_soc)
            bat_soc += charge
            curtail = p_available - charge
            sum_bat_chg += charge
            sum_curtailment += curtail
            if return_series:
                arr_curtail[i] = curtail
                arr_load[i] = charge
            continue


        dis, charge, grid_buy, curtail, p_el = 0.0, 0.0, 0.0, 0.0, 0.0

        p_rem = p_available - p_nh3_req
        if p_rem < 0:
            deficit_nh3 = -p_rem
            dis_nh3 = min(bat_soc, deficit_nh3, FIXED_CAP_BAT * 0.5)
            bat_soc -= dis_nh3
            dis += dis_nh3
            grid_buy += (deficit_nh3 - dis_nh3)
            p_rem = 0.0

        if p_rem > 0:
            p_el = min(p_rem, FIXED_CAP_EL)
            p_rem -= p_el
            el_deficit = FIXED_CAP_EL - p_el
            if el_deficit > 0 and bat_soc > 0:
                dis_el = min(bat_soc, el_deficit, FIXED_CAP_BAT * 0.5 - dis)
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
        sum_nh3_prod += FIXED_CAP_NH3

        if p_rem > 0:
            charge = min(p_rem, FIXED_CAP_BAT - bat_soc)
            bat_soc += charge
            curtail = p_rem - charge

        if tank_soc > FIXED_CAP_TANK:
            tank_soc = FIXED_CAP_TANK

        sum_h2_prod += (p_el / TECH['el_eff'])
        sum_nh3_power += p_nh3_req
        sum_el_power += p_el
        sum_grid_buy += grid_buy
        sum_bat_dis += dis
        sum_bat_chg += charge
        sum_curtailment += curtail

        if return_series:

            arr_load[i] = p_el + p_nh3_req + charge
            arr_curtail[i] = curtail
            arr_grid_buy[i] = grid_buy


    sum_water_tons = sum_h2_prod * TECH['water_req'] / 1000.0

    capex = (wind_kw * COST['wind'] + solar_kw * COST['solar'] +
             FIXED_CAP_EL * COST['el'] + FIXED_CAP_BAT * COST['bat'] +
             FIXED_CAP_TANK * COST['tank'] + FIXED_CAP_NH3 * COST['nh3'])

    annual_cost = capex * TECH['crf'] + sum_grid_buy * COST['grid'] + sum_water_tons * COST['water']
    lcoa = annual_cost / sum_nh3_prod if sum_nh3_prod > 0 else float('inf')

    res_dict = {
        'lcoa': lcoa,
        'curtailment': sum_curtailment,
        'curtail_rate': sum_curtailment / np.sum(p_green) if np.sum(p_green) > 0 else 1.0,
        'grid_buy': sum_grid_buy,
        'bat_dis': sum_bat_dis,
        'p_green_total': np.sum(p_green)
    }

    if return_series:
        res_dict.update({'arr_load': arr_load, 'arr_curtail': arr_curtail, 'arr_grid_buy': arr_grid_buy})

    return res_dict



def optimize_verification_ratio(cf_wind, cf_solar):
    """
    固定化工设备规模，重新寻找使得综合成本(LCOA)最低的真实风光配比
    """

    def objective(x):
        wind_kw, solar_kw = x
        res = simulate_validation(wind_kw, solar_kw, cf_wind, cf_solar, return_series=False)
        return res['lcoa']

    # 初始猜测值为风光上下限范围的中值
    x0 = [100000, 50000]
    bounds = [(10000, 300000), (0, 200000)]

    print("开始反向验证寻优：锁定化工与储能规模，寻找最适配当前负荷的真实风光配比...")
    result = minimize(objective, x0, method='L-BFGS-B', bounds=bounds)
    return result.x



if __name__ == "__main__":
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    cf_w, cf_s, times = load_resource_data()


    opt_wind_kw, opt_solar_kw = optimize_verification_ratio(cf_w, cf_s)
    new_ratio = opt_wind_kw / opt_solar_kw if opt_solar_kw > 0 else float('inf')


    val_stats = simulate_validation(opt_wind_kw, opt_solar_kw, cf_w, cf_s, return_series=True)

    print("\n=== 模块三：反向验证与电量平衡核算结果 ===")
    print(f"原模块一纯自然互补配比 (风:光): {M1_RATIO:.2f} : 1")
    print(f"验证后结合设备约束配比 (风:光): {new_ratio:.2f} : 1")
    print("-" * 40)
    print(f"验证后风电规模: {opt_wind_kw / 1000:.2f} MW")
    print(f"验证后光伏规模: {opt_solar_kw / 1000:.2f} MW")
    print(f"验证后绿氨单位成本 (LCOA): {val_stats['lcoa']:.2f} 元/kg")
    print(f"验证后系统弃电率: {val_stats['curtail_rate'] * 100:.2f}%")


    result_df = pd.DataFrame({
        '原风光配比(风比光)': [M1_RATIO],
        '验证后风光配比': [new_ratio],
        '验证后风电规模(kW)': [opt_wind_kw],
        '验证后光伏规模(kW)': [opt_solar_kw],
        '验证后LCOA(元/kg)': [val_stats['lcoa']],
        '验证后全年弃电率(%)': [val_stats['curtail_rate'] * 100],
        '全年弃电量(kWh)': [val_stats['curtailment']],
        '全年电网下电量(kWh)': [val_stats['grid_buy']]
    })
    excel_path = os.path.join(OUTPUT_DIR, "validation_result.xlsx")
    result_df.to_excel(excel_path, index=False)
    print(f"\n[数据已保存] 验证结果已输出至: {excel_path}")


    plot_start = 4000
    plot_end = plot_start + 168

    t_plot = times[plot_start:plot_end]
    p_green_plot = (opt_wind_kw * cf_w[plot_start:plot_end] + opt_solar_kw * cf_s[plot_start:plot_end])
    p_load_plot = val_stats['arr_load'][plot_start:plot_end]
    p_curtail_plot = val_stats['arr_curtail'][plot_start:plot_end]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6), gridspec_kw={'width_ratios': [3, 1]})


    ax1.plot(t_plot, p_green_plot / 1000, label='验证后可用绿电总出力', color='#2ca02c', linewidth=1.5)
    ax1.fill_between(t_plot, 0, p_load_plot / 1000, color='#1f77b4', alpha=0.5, label='实际系统用电负荷(制氢+制氨+电池充电)')
    ax1.fill_between(t_plot, p_load_plot / 1000, (p_load_plot + p_curtail_plot) / 1000, color='gray', alpha=0.4,
                     hatch='//', label='系统弃电量')

    ax1.set_title(
        f'反向验证电量平衡时序图 (截取单周)\n弃电率: {val_stats["curtail_rate"] * 100:.2f}% | LCOA: {val_stats["lcoa"]:.2f} 元/kg',
        fontsize=14)
    ax1.set_ylabel('功率 (MW)')
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
    ax1.tick_params(axis='x', rotation=45)
    ax1.legend(loc='upper right')
    ax1.grid(True, linestyle=':', alpha=0.6)


    labels = ['模块一\n(理论自然互补)', '模块三\n(设备约束验证)']
    ratio_values = [M1_RATIO, new_ratio]

    bars = ax2.bar(labels, ratio_values, color=['#ff7f0e', '#d62728'], width=0.5)
    ax2.set_title('风光配比 (风电/光伏) 对比验证', fontsize=14)
    ax2.set_ylabel('配比比值')
    for bar in bars:
        yval = bar.get_height()
        ax2.text(bar.get_x() + bar.get_width() / 2, yval + 0.05, f'{yval:.2f}', ha='center', va='bottom', fontsize=12,
                 fontweight='bold')

    plt.tight_layout()
    img_path = os.path.join(OUTPUT_DIR, "validation_balance_plot.png")
    plt.savefig(img_path, dpi=300)
    print(f"[图像已保存] 验证电量平衡对比图已输出至: {img_path}")

    plt.show()