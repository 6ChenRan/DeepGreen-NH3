import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from scipy.optimize import minimize
import numpy_financial as npf
import warnings
import io

warnings.filterwarnings('ignore')

plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False


st.set_page_config(page_title="柔性绿氨全生命周期智能规划平台", layout="wide", page_icon="🌱")


st.markdown("""
<style>
    /* 主标题与副标题 */
    .main-title { text-align: center; margin-bottom: 0px !important; padding-bottom: 0px !important; }
    .sub-title { text-align: center; color: #888888; margin-top: 5px !important; font-weight: 300; font-size: 18px; }

    /* 模块简介说明文字 */
    .module-intro { font-size: 14px; color: #666666; font-weight: normal; margin-bottom: 15px; padding: 10px; background-color: #f8f9fa; border-left: 4px solid #2ca02c; border-radius: 4px; }

    /* 数据卡片的标签字体大小与颜色 */
    div[data-testid="stMetricLabel"] > div > div > p { font-size: 14px !important; color: #555555 !important; font-weight: normal !important; }

    /* st.subheader 字体大小 */
    h3 { font-size: 20px !important; margin-top: 1.5rem !important; margin-bottom: 0.5rem !important; }
</style>
""", unsafe_allow_html=True)


def to_excel(df):
    """将 DataFrame 转换为 Excel 二进制流，供下载使用"""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Sheet1')
    return output.getvalue()



st.markdown("<h1 class='main-title'>🌱 柔性绿氨全生命周期智能规划平台</h1>", unsafe_allow_html=True)
st.markdown("<h3 class='sub-title'>DeepGreen-NH3 Life-Cycle Optimization System</h3>", unsafe_allow_html=True)
st.markdown("<br>", unsafe_allow_html=True)


with st.sidebar:
    st.header("⚙️ 全局参数配置")

    st.subheader("1. 资源数据上传")
    file_wind = st.file_uploader("上传8760h风资源 (Excel)", type=["xlsx"])
    file_solar = st.file_uploader("上传8760h光资源 (Excel)", type=["xlsx"])
    st.divider()

    st.subheader("2. 风光互补边界")
    colA, colB = st.columns(2)
    with colA:
        wind_min = st.number_input("风电下限(MW)", value=10.0)
        solar_min = st.number_input("光伏下限(MW)", value=0.0)
    with colB:
        wind_max = st.number_input("风电上限(MW)", value=200.0)
        solar_max = st.number_input("光伏上限(MW)", value=100.0)

    target_load = st.number_input("目标供电基准线 (MW)", value=50.0)
    max_shortage = st.slider("最大允许缺电率 (%)", min_value=1.0, max_value=20.0, value=10.0) / 100.0
    st.divider()

    st.subheader("3. 经济评估边界")
    price_nh3 = st.number_input("绿氨售价 (元/吨)", value=4500.0)
    discount_rate = st.slider("贴现率 (%)", min_value=1.0, max_value=15.0, value=8.0) / 100.0
    project_life = st.number_input("项目生命周期 (年)", value=20)
    st.divider()


    with st.expander("🌍 LCA 碳足迹与 ISCC-EU 认证参数", expanded=True):
        st.markdown("**全生命周期排放因子**")
        ef_grid = st.number_input("电网网电排放因子 (kgCO2eq/kWh)", value=0.5703, format="%.4f")
        ef_wind = st.number_input("风电LCA排放因子 (kgCO2eq/kWh)", value=0.011, format="%.3f")
        ef_solar = st.number_input("光伏LCA排放因子 (kgCO2eq/kWh)", value=0.040, format="%.3f")
        ef_baseline = st.number_input("化石灰氨排放基线 (kgCO2eq/kgNH3)", value=2.50, format="%.2f")
        iscc_threshold = st.slider("ISCC-EU 减排阈值 (%)", min_value=50, max_value=100, value=70)
    st.divider()

    with st.expander("🛠️ 高级参数设置 (造价与技术指标)"):
        st.markdown("**设备与能源造价**")
        cost_wind = st.number_input("风电单价 (元/kW)", value=4000)
        cost_solar = st.number_input("光伏单价 (元/kW)", value=3000)
        cost_el = st.number_input("电解槽单价 (元/kW)", value=1500)
        cost_bat = st.number_input("储能单价 (元/kWh)", value=1000)
        cost_tank = st.number_input("高压储氢罐单价 (元/kg)", value=300)
        cost_nh3 = st.number_input("合成氨装置单价 (元/(kg/h))", value=15000)
        cost_grid = st.number_input("电网下电电价 (元/kWh)", value=0.6)
        cost_water = st.number_input("工业用水单价 (元/吨)", value=4.0)
        om_rate = st.number_input("年度固定运维费率 (%)", value=2.0) / 100.0

        st.markdown("**核心技术指标**")
        tech_el_eff = st.number_input("电解槽制氢电耗 (kWh/kg H2)", value=50.0)
        tech_water_h2 = st.number_input("制氢水耗 (kg水/kg H2)", value=15.0)
        tech_nh3_elec = st.number_input("合成氨本体耗电 (kWh/kg NH3)", value=1.0)
        tech_nh3_h2 = st.number_input("吨氨耗氢量 (kg H2/kg NH3)", value=0.177, format="%.3f")


COST = {'wind': cost_wind, 'solar': cost_solar, 'el': cost_el, 'bat': cost_bat, 'tank': cost_tank, 'nh3': cost_nh3,
        'grid': cost_grid, 'water': cost_water}
TECH = {'el_eff': tech_el_eff, 'nh3_elec_req': tech_nh3_elec, 'nh3_h2_req': tech_nh3_h2, 'water_req': tech_water_h2,
        'crf': (discount_rate * (1 + discount_rate) ** project_life) / ((1 + discount_rate) ** project_life - 1)}

if not file_wind or not file_solar:
    st.info("👈 请在左侧边栏上传风、光资源数据文件 (Excel格式) 以启动计算系统。")
    st.stop()


@st.cache_data
def load_resource_data(wind_file, solar_file):
    df_solar = pd.read_excel(solar_file)
    df_wind = pd.read_excel(wind_file)
    df_solar['time'] = pd.to_datetime(df_solar['time'])
    cf_solar = df_solar['electricity/KW'].values[:8760] / df_solar['electricity/KW'].max()
    cf_wind = df_wind['electricity/KW'].values[:8760] / df_wind['electricity/KW'].max()
    time_series = df_solar['time'].values[:8760]
    return cf_wind, cf_solar, time_series


try:
    cf_wind, cf_solar, time_series = load_resource_data(file_wind, file_solar)
except Exception as e:
    st.error(f"数据读取失败，请检查文件格式。错误: {e}")
    st.stop()



def optimize_complementarity(cf_w, cf_s, bounds_w, bounds_s, t_load, m_shortage):
    def objective(x):
        cap_w, cap_s = x
        power = cap_w * cf_w + cap_s * cf_s
        if np.sum(power) == 0: return 1.0
        curtailment = np.maximum(power - t_load, 0)
        return np.sum(curtailment) / np.sum(power)

    def constraint_shortage(x):
        cap_w, cap_s = x
        power = cap_w * cf_w + cap_s * cf_s
        shortage = np.maximum(t_load - power, 0)
        shortage_rate = np.sum(shortage) / (t_load * 8760)
        return m_shortage - shortage_rate

    x0 = [(bounds_w[0] + bounds_w[1]) / 2, (bounds_s[0] + bounds_s[1]) / 2]
    cons = [{'type': 'ineq', 'fun': constraint_shortage}]
    res = minimize(objective, x0, method='SLSQP', bounds=[bounds_w, bounds_s], constraints=cons)
    return res.x, res.fun


def simulate_system_app(caps, opt_wind, opt_solar, cf_w, cf_s, return_series=False):
    cap_el, cap_bat, cap_tank, cap_nh3 = caps
    steps = 8760
    p_green = opt_wind * cf_w + opt_solar * cf_s

    days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    month_starts = [0]
    for d in days_in_month: month_starts.append(month_starts[-1] + d * 24)
    monthly_gen = [np.sum(p_green[month_starts[m]:month_starts[m + 1]]) for m in range(12)]
    min_month = np.argmin(monthly_gen)
    m_start, m_end = month_starts[min_month], month_starts[min_month + 1]

    bat_soc, tank_soc = cap_bat * 0.5, cap_tank * 0.5
    sum_h2_prod, sum_nh3_prod, sum_grid_buy = 0.0, 0.0, 0.0
    sum_bat_dis, sum_bat_chg, sum_curtailment = 0.0, 0.0, 0.0
    sum_el_power, sum_nh3_power = 0.0, 0.0
    h2_demand_per_hour = cap_nh3 * TECH['nh3_h2_req']
    p_nh3_req = cap_nh3 * TECH['nh3_elec_req']

    if return_series:
        arr_p_el, arr_p_nh3 = np.zeros(steps), np.zeros(steps)
        arr_grid_buy, arr_curtail = np.zeros(steps), np.zeros(steps)
        arr_bat_soc, arr_tank_soc = np.zeros(steps), np.zeros(steps)
        arr_bat_dis, arr_bat_chg = np.zeros(steps), np.zeros(steps)
        arr_h2_prod, arr_h2_cons = np.zeros(steps), np.zeros(steps)
        arr_load = np.zeros(steps)

    for i in range(steps):
        p_available = p_green[i]
        if m_start <= i < m_end:
            charge = min(p_available, cap_bat - bat_soc)
            bat_soc += charge
            curtail = p_available - charge
            sum_bat_chg += charge
            sum_curtailment += curtail
            if return_series:
                arr_curtail[i], arr_bat_chg[i] = curtail, charge
                arr_bat_soc[i], arr_tank_soc[i] = bat_soc, tank_soc
                arr_load[i] = charge
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

        if tank_soc > cap_tank: tank_soc = cap_tank

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
            arr_h2_prod[i], arr_h2_cons[i] = p_el / TECH['el_eff'], h2_demand_per_hour
            arr_load[i] = p_el + p_nh3_req + charge

    sum_water_tons = sum_h2_prod * TECH['water_req'] / 1000.0
    capex_total = (opt_wind * COST['wind'] + opt_solar * COST['solar'] +
                   cap_el * COST['el'] + cap_bat * COST['bat'] + cap_tank * COST['tank'] + cap_nh3 * COST['nh3'])
    annual_cost = capex_total * TECH['crf'] + sum_grid_buy * COST['grid'] + sum_water_tons * COST['water']

    energy_in = np.sum(p_green) + sum_grid_buy + sum_bat_dis
    energy_out = sum_el_power + sum_nh3_power + sum_bat_chg + sum_curtailment

    res = {
        'lcoa': annual_cost / sum_nh3_prod if sum_nh3_prod > 0 else float('inf'),
        'nh3_total': sum_nh3_prod, 'h2_total': sum_h2_prod, 'water_tons': sum_water_tons,
        'grid_buy': sum_grid_buy, 'bat_dis': sum_bat_dis, 'bat_chg': sum_bat_chg,
        'p_green_total': np.sum(p_green), 'curtailment': sum_curtailment,
        'el_power': sum_el_power, 'nh3_power': sum_nh3_power,
        'm_start': m_start, 'm_end': m_end, 'm_month': min_month + 1,
        'capex_total': capex_total,
        'energy_in': energy_in, 'energy_out': energy_out, 'balance_diff': abs(energy_in - energy_out)
    }
    if return_series:
        res.update({
            'arr_p_green': p_green, 'arr_p_el': arr_p_el, 'arr_p_nh3': arr_p_nh3,
            'arr_grid_buy': arr_grid_buy, 'arr_curtail': arr_curtail, 'arr_bat_soc': arr_bat_soc,
            'arr_tank_soc': arr_tank_soc, 'arr_bat_dis': arr_bat_dis, 'arr_bat_chg': arr_bat_chg,
            'arr_h2_prod': arr_h2_prod, 'arr_h2_cons': arr_h2_cons, 'arr_load': arr_load
        })
    return res


def optimize_verification_ratio(cf_w, cf_s, fixed_caps):
    def objective(x):
        wind_kw, solar_kw = x
        res = simulate_system_app(fixed_caps, wind_kw, solar_kw, cf_w, cf_s, return_series=False)
        return res['lcoa']

    x0 = [st.session_state.get('opt_wind_kw', 100000), st.session_state.get('opt_solar_kw', 50000)]
    bounds = [(wind_min * 1000, wind_max * 1000), (solar_min * 1000, solar_max * 1000)]
    result = minimize(objective, x0, method='L-BFGS-B', bounds=bounds)
    return result.x



tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 1. 风光互补寻优",
    "🏭 2. 物理规模与调度",
    "🔄 3. 反向验证与修正",
    "💰 4. 吨氨单耗与财务评估",
    "🌍 5. 碳足迹与 ISCC-EU 认证"
])


with tab1:
    st.markdown("<div class='module-intro'>📌 基于当地天然风光资源的时序特性，以供电安全(最大缺电率)为底线，寻找使弃电率最低的最佳装机配比。</div>",
                unsafe_allow_html=True)

    if st.button("▶ 运行风光互补寻优", type="primary"):
        with st.spinner("正在求解约束最佳互补配比..."):
            bounds_w = (wind_min * 1000, wind_max * 1000)
            bounds_s = (solar_min * 1000, solar_max * 1000)
            t_load = target_load * 1000

            opt_caps, curtail_rate = optimize_complementarity(cf_wind, cf_solar, bounds_w, bounds_s, t_load,
                                                              max_shortage)
            opt_wind_kw, opt_solar_kw = opt_caps
            ratio = opt_wind_kw / opt_solar_kw if opt_solar_kw > 0 else float('inf')

            st.session_state['opt_wind_kw'] = opt_wind_kw
            st.session_state['opt_solar_kw'] = opt_solar_kw
            st.session_state['ratio_m1'] = ratio
            st.success("优化完成！")

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("最优风电规模", f"{opt_wind_kw / 1000:.2f} MW")
            col2.metric("最优光伏规模", f"{opt_solar_kw / 1000:.2f} MW")
            col3.metric("最佳风光配比", f"{ratio:.2f} : 1")
            col4.metric("基准目标弃电率", f"{curtail_rate * 100:.2f}%")

            fig, ax = plt.subplots(figsize=(12, 5))
            total_power = opt_wind_kw * cf_wind + opt_solar_kw * cf_solar
            ax.plot(time_series[:168], total_power[:168] / 1000, label='综合出力', color='green')
            ax.plot(time_series[:168], opt_wind_kw * cf_wind[:168] / 1000, label='风电出力', alpha=0.6)
            ax.plot(time_series[:168], opt_solar_kw * cf_solar[:168] / 1000, label='光伏出力', alpha=0.6)
            ax.axhline(y=t_load / 1000, color='red', linestyle='--', label=f'实际基准线 ({target_load} MW)')
            ax.set_title(f'风光互补出力与基准线匹配 (首周 168小时)', fontsize=14)
            ax.set_ylabel('功率 (MW)')
            ax.legend(loc='upper right')
            ax.grid(True, alpha=0.3)
            st.pyplot(fig)
            plt.close(fig)

            res_df = pd.DataFrame({'风电规模(MW)': [opt_wind_kw / 1000], '光伏规模(MW)': [opt_solar_kw / 1000], '风光配比': [ratio],
                                   '弃电率': [curtail_rate]})
            st.download_button("📥 下载互补配比结果 (Excel)", data=to_excel(res_df), file_name='Module1_Ratio.xlsx',
                               mime='application/vnd.ms-excel')


with tab2:
    st.markdown("<div class='module-intro'>📌 嵌入“电池+储氢罐”双缓冲逻辑及化工年度检修约束，精准输出最优设备容量与全年水耗及电量平衡。</div>",
                unsafe_allow_html=True)

    if st.button("▶ 运行全局规模寻优", type="primary", key="btn_m2"):
        if 'opt_wind_kw' not in st.session_state:
            st.error("请先在「风光互补配比寻优」中运行计算！")
        else:
            with st.spinner("正在进行 8760 小时时序全局仿真与规模寻优 (约耗时 10-30 秒)..."):
                opt_w = st.session_state['opt_wind_kw']
                opt_s = st.session_state['opt_solar_kw']


                def objective_m2(x): return simulate_system_app(x, opt_w, opt_s, cf_wind, cf_solar)['lcoa']


                x0 = [opt_w * 0.6, opt_w * 0.2, opt_w * 0.1, opt_w * 0.05]
                bounds = [(10000, 300000), (0, 500000), (1000, 200000), (100, 50000)]
                res_m2 = minimize(objective_m2, x0, method='L-BFGS-B', bounds=bounds)

                opt_caps_m2 = res_m2.x
                f_stats = simulate_system_app(opt_caps_m2, opt_w, opt_s, cf_wind, cf_solar, return_series=True)

                st.session_state['f_stats'] = f_stats
                st.session_state['opt_caps_m2'] = opt_caps_m2

                st.success(f"物理规模锁定！自动识别出发电低谷（第 {f_stats['m_month']} 月）作为大修期。")

                st.subheader("🏭 最优物理规模配置")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("电解槽总功率", f"{opt_caps_m2[0] / 1000:.2f} MW")
                c2.metric("储能系统容量", f"{opt_caps_m2[1] / 1000:.2f} MWh")
                c3.metric("高压储氢容量", f"{opt_caps_m2[2]:.0f} kg")
                c4.metric("合成氨额定产能", f"{opt_caps_m2[3]:.0f} kg/h")

                st.subheader("💧 产出与资源耗用")
                cx1, cx2, cx3 = st.columns(3)
                cx1.metric("全年绿氨总产量", f"{f_stats['nh3_total'] / 10000:.2f} 万kg")
                cx2.metric("全年绿氢总产量", f"{f_stats['h2_total'] / 10000:.2f} 万kg")
                cx3.metric("全年纯水总耗用", f"{f_stats['water_tons'] / 10000:.2f} 万吨")

                st.subheader("⚡ 闭环物理电量平衡校验")
                cb1, cb2, cb3 = st.columns(3)
                cb1.metric("总输入能量 (风光+网电+放电)", f"{f_stats['energy_in'] / 10000:.1f} 万kWh")
                cb2.metric("总消耗能量 (制氢+制氨+充电+弃电)", f"{f_stats['energy_out'] / 10000:.1f} 万kWh")
                cb3.metric("闭环守恒偏差 (须趋于 0)", f"{f_stats['balance_diff']:.4f} kWh")

                st.subheader("📈 宏观：全年综合出力与化工检修期智能落位 (8760 小时)")
                fig1, ax1 = plt.subplots(figsize=(15, 4))
                ax1.plot(time_series, f_stats['arr_p_green'] / 1000, color='#2ca02c', linewidth=0.5, label='风光综合出力')
                ax1.axvspan(time_series[f_stats['m_start']], time_series[f_stats['m_end'] - 1], color='red', alpha=0.3,
                            label=f'第{f_stats["m_month"]}月 化工检修停产期')
                ax1.set_ylabel('功率 (MW)')
                ax1.legend(loc='upper right')
                ax1.grid(True, linestyle=':', alpha=0.6)
                st.pyplot(fig1)
                plt.close(fig1)

                st.subheader("📊 微观：微电网能量平衡与储能调度细节 (截取典型双周)")
                p_start = 4000
                if f_stats['m_start'] <= p_start < f_stats['m_end']: p_start = (f_stats['m_end'] + 1000) % 8760
                p_end = p_start + 336
                t_p = time_series[p_start:p_end]

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

                fig2, (ax_bal, axA, axB) = plt.subplots(3, 1, figsize=(15, 14), sharex=True)

                p_nh3_mw = p_nh3_plot / 1000
                p_el_mw = p_el_plot / 1000
                bat_chg_mw = bat_chg_plot / 1000

                ax_bal.fill_between(t_p, 0, p_nh3_mw, label='制氨耗电', color='#e377c2')
                ax_bal.fill_between(t_p, p_nh3_mw, p_nh3_mw + p_el_mw, label='制氢耗电', color='#1f77b4')
                ax_bal.fill_between(t_p, p_nh3_mw + p_el_mw, p_nh3_mw + p_el_mw + bat_chg_mw, label='储能充电',
                                    color='#9467bd')

                supply_total = (p_green_plot + grid_buy_plot + bat_dis_plot) / 1000
                ax_bal.plot(t_p, p_green_plot / 1000, label='可用风光绿电', color='#2ca02c', linewidth=2)
                ax_bal.plot(t_p, supply_total, label='综合供电(含网电与放电)', color='red', linestyle='--', linewidth=1.5)

                ax_bal.fill_between(t_p, p_nh3_mw + p_el_mw + bat_chg_mw, supply_total,
                                    where=(supply_total > (p_nh3_mw + p_el_mw + bat_chg_mw)),
                                    color='gray', alpha=0.5, hatch='//', label='系统弃电量')

                ax_bal.set_title('微电网物理电量流向与供需平衡监控图', fontsize=14)
                ax_bal.set_ylabel('功率 (MW)')
                ax_bal.legend(loc='upper right', ncol=2)
                ax_bal.grid(True, linestyle=':', alpha=0.6)

                axA.bar(t_p, bat_chg_plot / 1000, width=0.04, color='blue', alpha=0.6, label='储能充电 (+)')
                axA.bar(t_p, -bat_dis_plot / 1000, width=0.04, color='red', alpha=0.6, label='储能放电 (-)')
                axA.set_ylabel('充放电 (MW)')
                axA_t = axA.twinx()
                axA_t.plot(t_p, bat_soc_plot, color='purple', lw=2, label='电池电量SOC')
                axA_t.set_ylabel('电量 (MWh)', color='purple')
                axA.legend(loc='upper left');
                axA_t.legend(loc='upper right')
                axA.set_title('微电网储能动态跟踪', fontsize=12)

                axB.bar(t_p, h2_prod_plot, width=0.04, color='teal', alpha=0.6, label='制氢入罐 (+)')
                axB.bar(t_p, -h2_cons_plot, width=0.04, color='darkorange', alpha=0.6, label='耗氢出罐 (-)')
                axB.set_ylabel('氢气流量 (kg/h)')
                axB_t = axB.twinx()
                axB_t.plot(t_p, tank_soc_plot, color='brown', lw=2, linestyle='-.', label='高压储氢罐库存')
                axB_t.set_ylabel('库存 (kg)', color='brown')
                axB.legend(loc='upper left');
                axB_t.legend(loc='upper right')
                axB.set_title('管网储氢量动态跟踪', fontsize=12)
                axB.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))

                plt.tight_layout()
                st.pyplot(fig2)
                plt.close(fig2)

                res_df_m2 = pd.DataFrame({
                    '时间': time_series, '可用绿电(kW)': f_stats['arr_p_green'], '用电负荷(kW)': f_stats['arr_load'],
                    '电网下电(kW)': f_stats['arr_grid_buy'], '系统弃电(kW)': f_stats['arr_curtail'],
                    '电池SOC(kWh)': f_stats['arr_bat_soc'], '储氢罐SOC(kg)': f_stats['arr_tank_soc']
                })
                st.download_button("📥 下载 8760小时时序平衡数据 (Excel)", data=to_excel(res_df_m2),
                                   file_name='Module2_8760_Dispatch.xlsx', mime='application/vnd.ms-excel')


with tab3:
    st.markdown("<div class='module-intro'>📌 利用模块二锁定的化工设备规模，反算真实用电负荷，重新验证并修正最佳风光配比。</div>", unsafe_allow_html=True)

    if st.button("▶ 运行刚性负荷反向验证", type="primary", key="btn_m_val"):
        if 'opt_caps_m2' not in st.session_state:
            st.error("请先完成「模块二」以锁定化工设备物理规模！")
        else:
            with st.spinner("正在基于刚性设备约束，反向寻优真实风光配比..."):
                fixed_caps = st.session_state['opt_caps_m2']
                val_wind, val_solar = optimize_verification_ratio(cf_wind, cf_solar, fixed_caps)
                val_ratio = val_wind / val_solar if val_solar > 0 else float('inf')

                val_stats = simulate_system_app(fixed_caps, val_wind, val_solar, cf_wind, cf_solar, return_series=True)
                m1_ratio = st.session_state.get('ratio_m1', 0)

                st.session_state['f_stats'] = val_stats
                st.session_state['opt_wind_kw'] = val_wind
                st.session_state['opt_solar_kw'] = val_solar
                st.success("反向验证完成！系统已完成装机修正，并应用至后续测算。")

                vc1, vc2, vc3, vc4 = st.columns(4)
                vc1.metric("自然配比 (模块一)", f"{m1_ratio:.2f} : 1")
                vc2.metric("修正后真实配比", f"{val_ratio:.2f} : 1", delta=f"{val_ratio - m1_ratio:.2f}", delta_color="off")
                vc3.metric("修正后风电规模", f"{val_wind / 1000:.2f} MW")
                vc4.metric("修正后光伏规模", f"{val_solar / 1000:.2f} MW")

                fig_val, (v_ax1, v_ax2) = plt.subplots(1, 2, figsize=(16, 6), gridspec_kw={'width_ratios': [3, 1]})
                p_start = 4000
                p_end = p_start + 168
                t_plot = time_series[p_start:p_end]
                p_green_plot = val_stats['arr_p_green'][p_start:p_end]
                p_load_plot = val_stats['arr_load'][p_start:p_end]
                p_curtail_plot = val_stats['arr_curtail'][p_start:p_end]

                v_ax1.plot(t_plot, p_green_plot / 1000, label='修正后总出力', color='#2ca02c', linewidth=1.5)
                v_ax1.fill_between(t_plot, 0, p_load_plot / 1000, color='#1f77b4', alpha=0.5, label='真实负荷(化工+充电)')
                v_ax1.fill_between(t_plot, p_load_plot / 1000, (p_load_plot + p_curtail_plot) / 1000, color='gray',
                                   alpha=0.4, hatch='//', label='系统弃电量')
                v_ax1.set_title('反向验证电量平衡时序图 (截取单周)', fontsize=14)
                v_ax1.set_ylabel('功率 (MW)')
                v_ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
                v_ax1.legend(loc='upper right')
                v_ax1.grid(True, linestyle=':', alpha=0.6)

                labels = ['理论自然配比', '设备约束验证配比']
                bars = v_ax2.bar(labels, [m1_ratio, val_ratio], color=['#ff7f0e', '#d62728'], width=0.5)
                v_ax2.set_title('风光配比修正对比', fontsize=14)
                v_ax2.set_ylabel('配比比值')
                for bar in bars:
                    yval = bar.get_height()
                    v_ax2.text(bar.get_x() + bar.get_width() / 2, yval + 0.05, f'{yval:.2f}', ha='center', va='bottom',
                               fontsize=12, fontweight='bold')

                st.pyplot(fig_val)
                plt.close(fig_val)

                res_df_m3 = pd.DataFrame({'原配比': [m1_ratio], '修正配比': [val_ratio], '验证后风电(MW)': [val_wind / 1000],
                                          '验证后光伏(MW)': [val_solar / 1000]})
                st.download_button("📥 下载验证修正结果 (Excel)", data=to_excel(res_df_m3), file_name='Module3_Validation.xlsx',
                                   mime='application/vnd.ms-excel')


with tab4:
    st.markdown("<div class='module-intro'>📌 依据系统产能、水/电能耗及贴现率，输出权威的吨氨单耗及 LCOA、NPV 等财务指标，并提供关键参数的经济效益灵敏度分析。</div>",
                unsafe_allow_html=True)

    if st.button("▶ 生成财务瀑布图及报告", type="primary", key="btn_m4"):
        if 'f_stats' not in st.session_state:
            st.error("请先按顺序依次运行前述模块锁定规模！")
        else:
            with st.spinner("正在核算单耗与全生命周期现金流..."):
                fs = st.session_state['f_stats']
                caps = st.session_state['opt_caps_m2']
                opt_w = st.session_state['opt_wind_kw']
                opt_s = st.session_state['opt_solar_kw']

                cx_w, cx_s = opt_w * COST['wind'], opt_s * COST['solar']
                cx_el, cx_bat = caps[0] * COST['el'], caps[1] * COST['bat']
                cx_tank, cx_nh3 = caps[2] * COST['tank'], caps[3] * COST['nh3']
                capex_total = cx_w + cx_s + cx_el + cx_bat + cx_tank + cx_nh3
                capex_h2_part = cx_w + cx_s + cx_el + cx_bat

                nh3_tons = fs['nh3_total'] / 1000
                h2_tons = fs['h2_total'] / 1000
                ann_rev = nh3_tons * price_nh3
                ann_grid = fs['grid_buy'] * COST['grid']
                ann_water = fs['water_tons'] * COST['water']
                ann_om = capex_total * om_rate
                ann_opex = ann_grid + ann_water + ann_om

                crf = TECH['crf']
                lcoa = (capex_total * crf + ann_opex) / (nh3_tons * 1000) if nh3_tons > 0 else 0
                lcoh = (capex_h2_part * crf) / (h2_tons * 1000) if h2_tons > 0 else 0

                cf = [-capex_total] + [ann_rev - ann_opex] * int(project_life)
                npv = npf.npv(discount_rate, cf)
                irr = npf.irr(cf)

                dis_cf = [-capex_total] + [(ann_rev - ann_opex) / (1 + discount_rate) ** t for t in
                                           range(1, int(project_life) + 1)]
                cum_dcf = np.cumsum(dis_cf)

                pb_period = -1
                for i in range(1, len(cum_dcf)):
                    if cum_dcf[i] >= 0:
                        pb_period = i - 1 + abs(cum_dcf[i - 1]) / dis_cf[i]
                        break

                st.success("评估完成！单耗分解、瀑布图及灵敏度分析已生成。")

                st.subheader("📊 吨氨单耗与成本拆解")
                tc1, tc2, tc3, tc4 = st.columns(4)
                tc1.metric("吨氨综合电耗", f"{(fs['el_power'] + fs['nh3_power']) / nh3_tons:.0f} kWh/t")
                tc2.metric("吨氨综合水耗", f"{fs['water_tons'] / nh3_tons:.2f} 吨/t")
                tc3.metric("吨氨网电成本", f"{ann_grid / nh3_tons:.0f} 元/t")
                tc4.metric("吨氨折旧运维", f"{(capex_total * crf + ann_om) / nh3_tons:.0f} 元/t")

                st.subheader("💰 宏观经济评价指标")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("平准化绿氨成本 (LCOA)", f"{lcoa:.2f} 元/kg")
                c2.metric("总投资 (CAPEX)", f"{capex_total / 1e8:.2f} 亿元")
                c3.metric("年营运总成本 (OPEX)", f"{ann_opex / 1e4:.0f} 万元")
                c4.metric("年营业总收入", f"{ann_rev / 1e4:.0f} 万元")

                c5, c6, c7, c8 = st.columns(4)
                c5.metric("平准化绿氢成本 (LCOH)", f"{lcoh:.2f} 元/kg")
                c6.metric("净现值 (NPV)", f"{npv / 1e8:.2f} 亿元")
                c7.metric("内部收益率 (IRR)", f"{irr * 100:.2f} %")
                c8.metric("动态投资回收期", f"{pb_period:.2f} 年" if pb_period > 0 else "> 20年")

                st.subheader("📈 全生命周期折现现金流模型")
                fig3, ax3 = plt.subplots(figsize=(12, 5))
                years = np.arange(project_life + 1)
                ax3.bar(years, np.array(dis_cf) / 1e8, color=['red'] + ['green'] * int(project_life), label='年度折现净现金流')
                ax3.plot(years, cum_dcf / 1e8, color='blue', marker='o', lw=2, label='累计折现现金流')
                ax3.axhline(0, color='black', ls='--', lw=1)
                if pb_period > 0:
                    ax3.axvline(x=pb_period, color='orange', ls=':', lw=2, label=f'回收期 {pb_period:.1f}年')
                ax3.set_xlabel('项目运营年份', fontsize=12)
                ax3.set_ylabel('金额 (亿元)', fontsize=12)
                ax3.legend()
                ax3.grid(True, ls=':', alpha=0.6)
                st.pyplot(fig3)
                plt.close(fig3)

                # ==========================
                # 新增：核心参数灵敏度分析
                # ==========================
                st.subheader("🕸️ 经济效益灵敏度分析 (Sensitivity Analysis)")
                variations = [-0.2, -0.1, 0.0, 0.1, 0.2]
                var_labels = ['-20%', '-10%', '0%', '+10%', '+20%']
                irr_price = []
                irr_grid = []

                for v in variations:
                    # 绿氨售价变动
                    test_price = price_nh3 * (1 + v)
                    test_rev = nh3_tons * test_price
                    test_cf_price = [-capex_total] + [test_rev - ann_opex] * int(project_life)
                    irr_price.append(npf.irr(test_cf_price) * 100 if npf.irr(test_cf_price) else 0)

                    # 网电价格变动
                    test_grid_cost = fs['grid_buy'] * COST['grid'] * (1 + v)
                    test_opex = test_grid_cost + ann_water + ann_om
                    test_cf_grid = [-capex_total] + [ann_rev - test_opex] * int(project_life)
                    irr_grid.append(npf.irr(test_cf_grid) * 100 if npf.irr(test_cf_grid) else 0)

                fig4, ax4 = plt.subplots(figsize=(10, 5))
                ax4.plot(var_labels, irr_price, marker='o', linewidth=2, label='绿氨售价变化', color='#d62728')
                ax4.plot(var_labels, irr_grid, marker='s', linewidth=2, label='网电价格变化', color='#1f77b4')
                ax4.axhline(y=discount_rate * 100, color='gray', linestyle='--',
                            label=f'基准折现率 ({discount_rate * 100:.0f}%)')
                ax4.set_xlabel('参数变动比例 (%)', fontsize=12)
                ax4.set_ylabel('内部收益率 IRR (%)', fontsize=12)
                ax4.set_title('核心参数对项目 IRR 的影响分析图', fontsize=14)
                ax4.legend()
                ax4.grid(True, linestyle=':', alpha=0.6)
                st.pyplot(fig4)
                plt.close(fig4)

                res_df_m4 = pd.DataFrame({'年份': years, '年度折现净现金流(元)': dis_cf, '累计折现现金流(元)': cum_dcf})
                st.download_button("📥 下载生命周期现金流报表 (Excel)", data=to_excel(res_df_m4),
                                   file_name='Module4_Financials.xlsx', mime='application/vnd.ms-excel')


with tab5:
    st.markdown("<div class='module-intro'>📌 严格依据 ISCC-EU RFNBO 准则，计入风光绿电基建排放与网电分摊惩罚，核算最终产品全生命周期碳减排合规性。</div>",
                unsafe_allow_html=True)

    if st.button("▶ 进行碳足迹追溯与合规认证", type="primary", key="btn_m5"):
        if 'f_stats' not in st.session_state:
            st.error("请先完成物理模型运行与定容！")
        else:
            with st.spinner("正在进行全生命周期碳排放溯源分析..."):
                fs = st.session_state['f_stats']
                opt_w = st.session_state['opt_wind_kw']
                opt_s = st.session_state['opt_solar_kw']
                nh3_kg = fs['nh3_total']

                wind_gen = np.sum(opt_w * cf_wind)
                solar_gen = np.sum(opt_s * cf_solar)
                emissions_green = (wind_gen * ef_wind) + (solar_gen * ef_solar)

                el_power = fs['el_power']
                nh3_power = fs['nh3_power']
                total_process_power = el_power + nh3_power
                ratio_el = el_power / total_process_power if total_process_power > 0 else 0
                ratio_nh3 = nh3_power / total_process_power if total_process_power > 0 else 0

                emissions_grid_total = fs['grid_buy'] * ef_grid
                emissions_grid_el = emissions_grid_total * ratio_el
                emissions_grid_nh3 = emissions_grid_total * ratio_nh3

                total_emissions = emissions_green + emissions_grid_total
                se_project = total_emissions / nh3_kg if nh3_kg > 0 else 0
                se_baseline = ef_baseline

                ghg_saving_pct = ((se_baseline - se_project) / se_baseline) * 100 if se_baseline > 0 else 0
                is_compliant = ghg_saving_pct >= iscc_threshold

                st.success("LCA 碳足迹计算完毕！已计入风光设备基建排碳与逐时网电溯源惩罚。")

                if is_compliant:
                    st.success(
                        f"🏅 **ISCC-EU 认证预判：通过！** 本项目减排率达到了 {ghg_saving_pct:.1f}%，已超越欧盟 RFNBO 法规要求的 {iscc_threshold}% 阈值。")
                else:
                    st.error(
                        f"⚠️ **ISCC-EU 认证预判：未通过！** 本项目减排率仅为 {ghg_saving_pct:.1f}%，未达到 RFNBO 要求的 {iscc_threshold}% 阈值，建议扩建风光或储能！")

                st.subheader("📊 碳足迹核心拆解指标")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("风光绿电生命周期排放", f"{emissions_green / 1000:.0f} 吨CO₂e")
                c2.metric("全厂工艺网电补电排放", f"{emissions_grid_total / 1000:.0f} 吨CO₂e")
                c3.metric("项目绿氨碳排强度", f"{se_project:.3f} kgCO₂e/kg")
                c4.metric("灰氨基准线对比", f"{se_baseline:.3f} kgCO₂e/kg")

                fig_carbon, (c_ax1, c_ax2) = plt.subplots(1, 2, figsize=(14, 6))

                labels_c = ['化石基灰氨 (基准线)', '本项目绿氨']
                values_c = [se_baseline, se_project]
                bars_c = c_ax1.bar(labels_c, values_c, color=['#7f7f7f', '#2ca02c'], width=0.5)
                c_ax1.set_ylabel('碳排放强度 (kgCO$_2$eq / kgNH$_3$)', fontsize=12)
                c_ax1.set_title(f'绿氨产品全生命周期减排效益 (减排 {ghg_saving_pct:.1f}%)', fontsize=14)
                c_ax1.axhline(y=se_baseline * (1 - iscc_threshold / 100), color='red', linestyle='--',
                              label=f'ISCC-EU {iscc_threshold}% 减排红线')
                for bar in bars_c:
                    yval = bar.get_height()
                    c_ax1.text(bar.get_x() + bar.get_width() / 2, yval + 0.05, f'{yval:.3f}', ha='center', va='bottom',
                               fontsize=12, fontweight='bold')
                c_ax1.legend(loc='upper right')
                c_ax1.grid(axis='y', linestyle=':', alpha=0.6)

                pie_labels = ['风光基建LCA溯源', '电网补电(制氢分摊)', '电网补电(制氨分摊)']
                pie_sizes = [emissions_green, emissions_grid_el, emissions_grid_nh3]

                if sum(pie_sizes) > 0:
                    c_ax2.pie([x for x in pie_sizes if x > 0],
                              labels=[l for l, x in zip(pie_labels, pie_sizes) if x > 0],
                              autopct='%1.1f%%', startangle=90, colors=['#8c564b', '#1f77b4', '#e377c2'])
                    c_ax2.set_title('全生命周期边界残余碳排 LCA 溯源拆解', fontsize=14)

                st.pyplot(fig_carbon)
                plt.close(fig_carbon)

                res_df_m5 = pd.DataFrame({
                    '项目绿氨碳强(kgCO2/kg)': [se_project], '灰氨基准碳强(kgCO2/kg)': [se_baseline],
                    '减排率(%)': [ghg_saving_pct], '是否合规ISCC': ["是" if is_compliant else "否"],
                    '风光基建LCA碳排(吨)': [emissions_green / 1000], '电解网电碳排(吨)': [emissions_grid_el / 1000],
                    '制氨网电碳排(吨)': [emissions_grid_nh3 / 1000]
                })
                st.download_button("📥 下载碳足迹 LCA 认证核算报告 (Excel)", data=to_excel(res_df_m5),
                                   file_name='Module5_Carbon_LCA.xlsx', mime='application/vnd.ms-excel')