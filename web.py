# -*- coding: utf-8 -*-
"""柔性绿氨全生命周期智能规划平台 V1.1 - Streamlit Web主程序"""
import io
import warnings
import hashlib
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import streamlit as st

from model1 import load_resource_data, optimize_complementarity
from model2 import (
    DEFAULT_COST, DEFAULT_TECH, optimize_engineering_scale,
    optimize_verification_ratio, simulate_system, save_dispatch_result,
)
from model4_economics import calculate_economics
from model5_carbon import calculate_carbon

warnings.filterwarnings("ignore")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

st.set_page_config(
    page_title="柔性绿氨全生命周期智能规划平台",
    layout="wide",
    page_icon="🌱",
)

st.markdown("""
<style>
.main-title {text-align:center; margin-bottom:0!important;}
.sub-title {text-align:center;color:#888;margin-top:4px;font-size:17px;}
.module-intro {font-size:14px;color:#555;padding:10px;background:#f7f9fa;
border-left:4px solid #2ca02c;border-radius:4px;margin-bottom:12px;}
</style>
""", unsafe_allow_html=True)


def to_excel(df, summary=None, extra_sheets=None):
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="时序明细")
        if summary is not None:
            summary.to_excel(writer, index=False, sheet_name="约束校核")
        if extra_sheets:
            for name, data in extra_sheets.items():
                data.to_excel(writer, index=False, sheet_name=name[:31])
    return out.getvalue()


def operating_violations(stats, target_ton, max_grid, max_curtail):
    errors = []
    if stats["grid_rate"] > max_grid + 1e-5:
        errors.append(f"下网电率{stats['grid_rate']:.2%}超过上限{max_grid:.2%}")
    if stats["curtail_rate"] > max_curtail + 1e-5:
        errors.append(f"弃电率{stats['curtail_rate']:.2%}超过上限{max_curtail:.2%}")
    if stats["nh3_total"] < target_ton * 1000 - max(1.0, target_ton * 1000 * 1e-6):
        errors.append(f"年产氨量{stats['nh3_total']/1000:.2f}吨低于目标{target_ton:.2f}吨")
    if stats["balance_diff"] > max(0.1, stats["energy_in"] * 1e-8):
        errors.append(f"全年电量平衡偏差{stats['balance_diff']:.4f}kWh")
    if stats["h2_balance_diff"] > max(0.01, stats["h2_total"] * 1e-7):
        errors.append(f"氢气物料平衡偏差{stats['h2_balance_diff']:.4f}kg")
    if stats["max_power_balance_error"] > 1e-4:
        errors.append(f"逐时电力平衡偏差{stats['max_power_balance_error']:.6f}kW")
    return errors


@st.cache_data(show_spinner=False)
def load_uploaded_profiles(wind_bytes, solar_bytes):
    return load_resource_data(io.BytesIO(wind_bytes), io.BytesIO(solar_bytes))


st.markdown("<h1 class='main-title'>🌱 柔性绿氨全生命周期智能规划平台</h1>", unsafe_allow_html=True)
st.markdown("<div class='sub-title'>DeepGreen-NH3 Life-Cycle Optimization System · V1.1</div>", unsafe_allow_html=True)

with st.sidebar:
    st.header("⚙️ 全局参数配置")
    st.subheader("1. 资源数据")
    file_wind = st.file_uploader("上传风资源Excel", type=["xlsx"])
    file_solar = st.file_uploader("上传光资源Excel", type=["xlsx"])
    st.caption("系统读取风、光文件前8760小时；两份文件的时间戳必须逐小时一致。")
    st.divider()

    st.subheader("2. 风光互补边界")
    c1, c2 = st.columns(2)
    with c1:
        wind_min = st.number_input("风电下限(MW)", min_value=0.0, value=10.0, step=1.0)
        solar_min = st.number_input("光伏下限(MW)", min_value=0.0, value=0.0, step=1.0)
    with c2:
        wind_max = st.number_input("风电上限(MW)", min_value=0.1, value=200.0, step=1.0)
        solar_max = st.number_input("光伏上限(MW)", min_value=0.1, value=100.0, step=1.0)
    target_load_mw = st.number_input("模块一目标供电基准线(MW)", min_value=0.1, value=50.0, step=1.0)
    max_shortage = st.slider("模块一最大允许资源缺额率(%)", 0.0, 30.0, 10.0, 1.0) / 100.0
    st.divider()

    st.subheader("3. 项目生产及运行约束")
    target_nh3_ton = st.number_input(
        "最低年产绿氨目标(吨/年)", min_value=0.0, value=0.0, step=1000.0,
        help="必填设计输入。模块二、三要求实际年产量不低于该值；若资源充足，实际产量可以高于目标。"
    )
    max_grid_rate = st.slider("最大允许下网电率(%)", 0.0, 100.0, 100.0, 1.0) / 100.0
    max_curtail_rate = st.slider("全年最大允许弃电率(%)", 0.0, 100.0, 20.0, 1.0) / 100.0
    st.caption("下网电率=全年网购电量/(制氢耗电+制氨耗电)，不含储能充电。弃电率=全年弃电量/全年风光发电量。")
    st.divider()

    st.subheader("4. 经济评价参数")
    price_nh3 = st.number_input("绿氨售价(元/吨)", min_value=0.0, value=4500.0, step=100.0)
    discount_rate = st.slider("贴现率(%)", 0.0, 20.0, 8.0, 0.5) / 100.0
    project_life = st.number_input("项目生命周期(年)", min_value=1, max_value=50, value=20, step=1)
    om_rate = st.number_input("年度运维费率(%)", min_value=0.0, max_value=20.0, value=2.0, step=0.5) / 100.0
    st.divider()

    with st.expander("🛠️ 设备造价与技术参数", expanded=False):
        cost_wind = st.number_input("风电单价(元/kW)", min_value=0.0, value=4000.0)
        cost_solar = st.number_input("光伏单价(元/kW)", min_value=0.0, value=3000.0)
        cost_el = st.number_input("电解槽单价(元/kW)", min_value=0.0, value=1500.0)
        cost_bat = st.number_input("储能单价(元/kWh)", min_value=0.0, value=1000.0)
        cost_tank = st.number_input("储氢单价(元/kg)", min_value=0.0, value=300.0)
        cost_nh3 = st.number_input("合成氨装置单价(元/(kg/h))", min_value=0.0, value=15000.0)
        cost_grid = st.number_input("网电电价(元/kWh)", min_value=0.0, value=0.6, step=0.05)
        cost_water = st.number_input("工业水价(元/吨)", min_value=0.0, value=4.0, step=0.5)
        st.markdown("**工艺参数**")
        tech_el_eff = st.number_input("电解制氢电耗(kWh/kg H2)", min_value=1.0, value=50.0)
        tech_water = st.number_input("制氢水耗(kg/kg H2)", min_value=0.0, value=15.0)
        tech_nh3_elec = st.number_input("合成氨耗电(kWh/kg NH3)", min_value=0.0, value=1.0)
        tech_nh3_h2 = st.number_input("合成氨耗氢(kg/kg NH3)", min_value=0.001, value=0.177, format="%.3f")
        eta_charge = st.number_input("电池充电效率(%)", min_value=50.0, max_value=100.0, value=95.0) / 100.0
        eta_discharge = st.number_input("电池放电效率(%)", min_value=50.0, max_value=100.0, value=95.0) / 100.0
        bat_power_rate = st.number_input("储能功率倍率(C)", min_value=0.1, max_value=2.0, value=0.5, step=0.1)
    st.divider()

    with st.expander("🌍 碳足迹参数", expanded=False):
        ef_grid = st.number_input("电网排放因子(kgCO2e/kWh)", min_value=0.0, value=0.5703, format="%.4f")
        ef_wind = st.number_input("风电LCA排放因子(kgCO2e/kWh)", min_value=0.0, value=0.011, format="%.3f")
        ef_solar = st.number_input("光伏LCA排放因子(kgCO2e/kWh)", min_value=0.0, value=0.040, format="%.3f")
        ef_baseline = st.number_input("灰氨基准排放强度(kgCO2e/kgNH3)", min_value=0.01, value=2.50)
        iscc_threshold = st.slider("减排率预判阈值(%)", 0, 100, 70) / 100.0

COST = {
    "wind": cost_wind, "solar": cost_solar, "el": cost_el, "bat": cost_bat,
    "tank": cost_tank, "nh3": cost_nh3, "grid": cost_grid, "water": cost_water,
}
TECH = {
    "el_eff": tech_el_eff, "nh3_elec_req": tech_nh3_elec,
    "nh3_h2_req": tech_nh3_h2, "water_req": tech_water,
    "eta_charge": eta_charge, "eta_discharge": eta_discharge,
    "bat_power_rate": bat_power_rate,
}

if not file_wind or not file_solar:
    st.info("请先在左侧上传风资源和光资源Excel文件。")
    st.stop()

try:
    cf_wind, cf_solar, time_series = load_uploaded_profiles(file_wind.getvalue(), file_solar.getvalue())
except Exception as exc:
    st.error(f"资源数据读取失败：{exc}")
    st.stop()

if wind_min > wind_max or solar_min > solar_max:
    st.error("风光装机下限不能大于上限。")
    st.stop()

# 输入参数或资源文件变化时，清除旧计算结果，防止财务/碳足迹引用过期方案
signature_payload = repr((
    target_load_mw, max_shortage, target_nh3_ton, max_grid_rate, max_curtail_rate,
    price_nh3, discount_rate, int(project_life), om_rate, COST, TECH,
    wind_min, wind_max, solar_min, solar_max, ef_grid, ef_wind, ef_solar,
    ef_baseline, iscc_threshold,
)).encode("utf-8")
input_signature = hashlib.sha256(
    file_wind.getvalue() + file_solar.getvalue() + signature_payload
).hexdigest()
if st.session_state.get("_input_signature") != input_signature:
    for key in (
        "opt_wind_kw", "opt_solar_kw", "ratio_m1", "m1_shortage", "m1_curtail",
        "opt_caps_m2", "f_stats", "m2_success", "m2_message", "m3_result",
        "m3_success", "econ_result", "carbon_result",
    ):
        st.session_state.pop(key, None)
    st.session_state["_input_signature"] = input_signature

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 1. 风光互补寻优", "🏭 2. 设备规模与调度",
    "🔄 3. 反向验证与修正", "💰 4. 吨氨单耗与财务评估",
    "🌍 5. 碳足迹与减排预判",
])

with tab1:
    st.markdown("<div class='module-intro'>基于8760小时风光时序数据，开展风光装机组合初选。模块一缺额率是资源匹配指标，不等同于系统实际下网电率。</div>", unsafe_allow_html=True)
    if st.button("▶ 运行风光互补寻优", type="primary", key="run_m1"):
        for key in ("opt_caps_m2","f_stats","m2_success","m2_message","m3_result",
                    "m3_success","econ_result","carbon_result"):
            st.session_state.pop(key,None)
        try:
            res = optimize_complementarity(
                cf_wind, cf_solar,
                (wind_min * 1000, wind_max * 1000),
                (solar_min * 1000, solar_max * 1000),
                target_load_mw * 1000, max_shortage, max_curtail_rate,
            )
            opt_w, opt_s = map(float, res.x)
            st.session_state["opt_wind_kw"] = opt_w
            st.session_state["opt_solar_kw"] = opt_s
            st.session_state["ratio_m1"] = opt_w / opt_s if opt_s > 0 else np.inf
            st.session_state["m1_shortage"] = res.shortage_rate
            st.session_state["m1_curtail"] = res.curtail_rate
            st.success("模块一优化完成。")
        except Exception as exc:
            st.error(str(exc))

    if "opt_wind_kw" in st.session_state:
        opt_w, opt_s = st.session_state["opt_wind_kw"], st.session_state["opt_solar_kw"]
        ratio = opt_w / opt_s if opt_s > 0 else np.inf
        c1,c2,c3,c4=st.columns(4)
        c1.metric("最优风电装机",f"{opt_w/1000:.2f} MW")
        c2.metric("最优光伏装机",f"{opt_s/1000:.2f} MW")
        c3.metric("风光配比(风:光)",f"{ratio:.2f}:1")
        c4.metric("理论弃电率",f"{st.session_state['m1_curtail']:.2%}")
        p=opt_w*cf_wind+opt_s*cf_solar
        fig,ax=plt.subplots(figsize=(12,4))
        ax.plot(time_series[:168],p[:168]/1000,label="风光综合出力")
        ax.axhline(target_load_mw,color="red",ls="--",label="资源匹配基准线")
        ax.set_ylabel("功率(MW)");ax.grid(alpha=.25);ax.legend()
        st.pyplot(fig);plt.close(fig)
        df=pd.DataFrame([{
            "风电装机(MW)":opt_w/1000,"光伏装机(MW)":opt_s/1000,
            "风光配比":ratio,"资源缺额率(%)":st.session_state["m1_shortage"]*100,
            "理论弃电率(%)":st.session_state["m1_curtail"]*100,
        }])
        st.download_button("下载模块一结果Excel",to_excel(df),file_name="Module1_Ratio.xlsx")


with tab2:
    st.markdown("<div class='module-intro'>固定模块一风光装机，按8760小时调度优化电解槽、储能、储氢及合成氨装置。合成氨允许0至额定产能柔性调节，年产量不得低于最低目标值，实际产量允许高于目标。</div>",unsafe_allow_html=True)
    if st.button("▶ 运行设备规模与8760小时调度",type="primary",key="run_m2"):
        if "opt_wind_kw" not in st.session_state:
            st.error("请先运行模块一。")
        elif target_nh3_ton <= 0:
            st.error("请在左侧输入目标年产绿氨量。")
        else:
            for key in ("f_stats","opt_caps_m2","m2_success","m2_message","m3_result",
                        "m3_success","econ_result","carbon_result"):
                st.session_state.pop(key,None)
            with st.spinner("正在开展设备规模寻优及8760小时调度..."):
                try:
                    caps,stats,success,message=optimize_engineering_scale(
                        st.session_state["opt_wind_kw"],st.session_state["opt_solar_kw"],
                        cf_wind,cf_solar,target_nh3_ton,max_grid_rate,max_curtail_rate,
                        cost=COST,tech=TECH,discount_rate=discount_rate,
                        project_life=int(project_life),om_rate=om_rate,return_series=True)
                    violations=operating_violations(stats,target_nh3_ton,max_grid_rate,max_curtail_rate)
                    if violations:
                        raise ValueError("最终方案未通过校核："+ "；".join(violations))
                    st.session_state["opt_caps_m2"]=caps
                    st.session_state["f_stats"]=stats
                    st.session_state["m2_success"]=success
                    st.session_state["m2_message"]=message
                    st.success("设备规模与年度调度计算完成。")
                except Exception as exc:
                    st.error(str(exc))

    if "f_stats" in st.session_state and "opt_caps_m2" in st.session_state:
        fs=st.session_state["f_stats"];caps=st.session_state["opt_caps_m2"]
        if not st.session_state.get("m2_success",True):
            st.warning("优化器未报告正常收敛，但当前结果已通过独立约束校核；请复核设备边界。")
        c=st.columns(4)
        c[0].metric("电解槽装机",f"{caps[0]/1000:.2f} MW")
        c[1].metric("储能容量",f"{caps[1]/1000:.2f} MWh")
        c[2].metric("储氢容量",f"{caps[2]/1000:.0f} kg")
        c[3].metric("合成氨额定产能",f"{caps[3]:.0f} kg/h")
        if (caps[0] >= 299700 or caps[1] >= 499500 or caps[2] >= 199800 or caps[3] >= 49950):
            st.warning("至少一项设备规模触及当前搜索上限，当前结果可能受设备边界限制；建议扩大对应上限后重新寻优。")
        c=st.columns(4)
        c[0].metric("全年绿氨产量",f"{fs['nh3_total']/1000:.2f} 吨")
        c[1].metric("全年绿氢产量",f"{fs['h2_total']/1000:.2f} 吨")
        c[2].metric("全年下网电率",f"{fs['grid_rate']:.2%}",delta=f"上限 {max_grid_rate:.0%}")
        c[3].metric("全年弃电率",f"{fs['curtail_rate']:.2%}",delta=f"上限 {max_curtail_rate:.0%}")
        c=st.columns(4)
        c[0].metric("全年网购电量",f"{fs['grid_buy']/1e6:.2f} 百万kWh")
        c[1].metric("全年系统用电量",f"{fs['system_total_consumption']/1e6:.2f} 百万kWh")
        c[2].metric("全年弃电量",f"{fs['curtailment']/1e6:.2f} 百万kWh")
        c[3].metric("LCOA",f"{fs['lcoa']:.3f} 元/kg")
        st.subheader("物理平衡校核")
        c=st.columns(4)
        c[0].metric("系统边界输入",f"{fs['energy_in']/1e6:.3f} 百万kWh")
        c[1].metric("系统边界输出",f"{fs['energy_out']/1e6:.3f} 百万kWh")
        c[2].metric("年度电量偏差",f"{fs['balance_diff']:.6f} kWh")
        c[3].metric("氢气物料偏差",f"{fs['h2_balance_diff']:.6f} kg")
        if not fs["state_cycles_converged"]:
            st.warning("年度库存迭代未完全收敛：请增加储能/储氢容量或复核资源曲线。")
        st.caption(
            f"检修月份：{fs['maintenance_month']}月；电池期初/期末库存："
            f"{fs['battery_soc_start']:.2f}/{fs['battery_soc_end']:.2f} kWh；"
            f"储氢期初/期末库存：{fs['tank_soc_start']:.2f}/{fs['tank_soc_end']:.2f} kg。"
        )
        fig,(ax1,ax2,ax3)=plt.subplots(3,1,figsize=(14,10),sharex=True)
        sl=slice(4000,4336)
        tt=time_series[sl]
        green=fs["arr_p_green"][sl]/1000
        load=(fs["arr_p_el"][sl]+fs["arr_p_nh3"][sl])/1000
        supply=(fs["arr_p_green"][sl]+fs["arr_grid_buy"][sl]+fs["arr_bat_dis"][sl])/1000
        ax1.plot(tt,green,label="风光出力")
        ax1.plot(tt,supply,label="综合供电",ls="--")
        ax1.plot(tt,load,label="工艺负荷")
        ax1.fill_between(tt,load,(fs["arr_p_el"][sl]+fs["arr_p_nh3"][sl]+fs["arr_bat_chg"][sl])/1000,
                         alpha=.25,label="储能充电")
        ax1.set_ylabel("MW");ax1.legend(ncol=4);ax1.grid(alpha=.2)
        ax2.plot(tt,fs["arr_bat_soc"][sl]/1000,label="电池SOC(MWh)")
        ax2_t=ax2.twinx();ax2_t.plot(tt,fs["arr_bat_chg"][sl]/1000,label="充电(MW)",alpha=.6)
        ax2_t.plot(tt,-fs["arr_bat_dis"][sl]/1000,label="放电(MW)",alpha=.6)
        ax2.set_ylabel("MWh");ax2_t.set_ylabel("MW");ax2.legend(loc="upper left")
        ax2_t.legend(loc="upper right");ax2.grid(alpha=.2)
        ax3.plot(tt,fs["arr_tank_soc"][sl],label="储氢库存(kg)")
        ax3_t=ax3.twinx();ax3_t.plot(tt,fs["arr_h2_prod"][sl],label="产氢(kg/h)",alpha=.6)
        ax3_t.plot(tt,-fs["arr_h2_cons"][sl],label="耗氢(kg/h)",alpha=.6)
        ax3.set_ylabel("kg");ax3_t.set_ylabel("kg/h");ax3.legend(loc="upper left")
        ax3_t.legend(loc="upper right");ax3.grid(alpha=.2)
        fig.tight_layout();st.pyplot(fig);plt.close(fig)
        summary=pd.DataFrame([{
            "年产绿氨(吨)":fs["nh3_total"]/1000,"年产绿氢(吨)":fs["h2_total"]/1000,
            "下网电率(%)":fs["grid_rate"]*100,"下网电率上限(%)":max_grid_rate*100,
            "弃电率(%)":fs["curtail_rate"]*100,"弃电率上限(%)":max_curtail_rate*100,
            "电量平衡偏差(kWh)":fs["balance_diff"],"氢气物料平衡偏差(kg)":fs["h2_balance_diff"],
            "LCOA(元/kg)":fs["lcoa"],
        }])
        detail=pd.DataFrame({
            "时间":time_series,"风光出力(kW)":fs["arr_p_green"],
            "电解槽耗电(kW)":fs["arr_p_el"],"合成氨耗电(kW)":fs["arr_p_nh3"],
            "合成氨产量(kg/h)":fs["arr_nh3_prod"],"电网购电(kW)":fs["arr_grid_buy"],
            "电池充电(kW)":fs["arr_bat_chg"],"电池放电(kW)":fs["arr_bat_dis"],
            "电池SOC(kWh)":fs["arr_bat_soc"],"储氢库存(kg)":fs["arr_tank_soc"],
            "氢气产量(kg/h)":fs["arr_h2_prod"],"氢气消耗(kg/h)":fs["arr_h2_cons"],
            "弃电(kW)":fs["arr_curtail"],
        })
        st.download_button("下载模块二8760小时调度Excel",
                           to_excel(detail,summary),file_name="Module2_8760_Dispatch.xlsx")


with tab3:
    st.markdown("<div class='module-intro'>固定模块二已确定的电解槽、储能、储氢和合成氨设备规模，反向优化风光装机，并用同一套8760小时调度逻辑重新校核。</div>",unsafe_allow_html=True)
    if st.button("▶ 运行反向验证与风光修正",type="primary",key="run_m3"):
        if "opt_caps_m2" not in st.session_state:
            st.error("请先完成模块二。")
        elif target_nh3_ton<=0:
            st.error("请在左侧输入目标年产绿氨量。")
        else:
            for key in ("econ_result","carbon_result"):
                st.session_state.pop(key,None)
            with st.spinner("正在固定设备规模、反向修正风光装机..."):
                try:
                    old_w,old_s=st.session_state["opt_wind_kw"],st.session_state["opt_solar_kw"]
                    val_w,val_s,val_stats,success,message=optimize_verification_ratio(
                        cf_wind,cf_solar,st.session_state["opt_caps_m2"],old_w,old_s,
                        target_nh3_ton,max_grid_rate,max_curtail_rate,
                        cost=COST,tech=TECH,discount_rate=discount_rate,
                        project_life=int(project_life),om_rate=om_rate,
                        wind_bounds=(wind_min*1000,wind_max*1000),
                        solar_bounds=(solar_min*1000,solar_max*1000),
                        return_series=True)
                    violations=operating_violations(val_stats,target_nh3_ton,max_grid_rate,max_curtail_rate)
                    if violations:
                        raise ValueError("反向验证未通过："+ "；".join(violations))
                    st.session_state["f_stats"]=val_stats
                    st.session_state["opt_wind_kw"]=val_w
                    st.session_state["opt_solar_kw"]=val_s
                    st.session_state["m3_result"]=(old_w,old_s,val_w,val_s)
                    st.session_state["m3_success"]=success
                    st.success("反向验证完成，风光装机已更新。")
                except Exception as exc:
                    st.error(str(exc))
    if "m3_result" in st.session_state:
        old_w,old_s,val_w,val_s=st.session_state["m3_result"]
        fs=st.session_state["f_stats"]
        c=st.columns(4)
        c[0].metric("修正后风电",f"{val_w/1000:.2f} MW")
        c[1].metric("修正后光伏",f"{val_s/1000:.2f} MW")
        c[2].metric("全年下网电率",f"{fs['grid_rate']:.2%}")
        c[3].metric("全年弃电率",f"{fs['curtail_rate']:.2%}")
        if (val_w <= wind_min*1000 + 1 or val_w >= wind_max*1000 - 1
                or val_s <= solar_min*1000 + 1 or val_s >= solar_max*1000 - 1):
            st.warning("修正后的风光装机触及当前搜索边界，建议扩大对应装机上下限后重新验证。")
        st.write(f"风光装机调整：风电{old_w/1000:.2f}→{val_w/1000:.2f}MW；光伏{old_s/1000:.2f}→{val_s/1000:.2f}MW。")
        st.caption("模块三固定模块二设备规模。若风光装机修正幅度较大，建议将修正后的风光规模作为新输入重新运行模块二，复核设备容量是否仍为最优。")
        df=pd.DataFrame([{
            "修正前风电(MW)":old_w/1000,"修正前光伏(MW)":old_s/1000,
            "修正后风电(MW)":val_w/1000,"修正后光伏(MW)":val_s/1000,
            "全年绿氨(吨)":fs["nh3_total"]/1000,"下网电率(%)":fs["grid_rate"]*100,
            "弃电率(%)":fs["curtail_rate"]*100,"LCOA(元/kg)":fs["lcoa"],
        }])
        st.download_button("下载模块三反向验证结果",to_excel(df),file_name="Module3_Validation.xlsx")


with tab4:
    st.markdown("<div class='module-intro'>统一使用模块二/三最终调度结果，按年化投资、运维费、网电费和水费计算LCOA，并开展现金流评价。</div>",unsafe_allow_html=True)
    if st.button("▶ 生成吨氨单耗与财务评价",type="primary",key="run_m4"):
        if "f_stats" not in st.session_state:
            st.error("请先完成模块二或模块三。")
        else:
            try:
                econ=calculate_economics(
                    st.session_state["f_stats"],COST,price_nh3,
                    discount_rate,int(project_life),om_rate)
                st.session_state["econ_result"]=econ
                st.success("财务评价完成。")
            except Exception as exc:
                st.error(str(exc))
    if "econ_result" in st.session_state:
        e=st.session_state["econ_result"]
        c=st.columns(4)
        c[0].metric("吨氨综合电耗",f"{e['unit_electricity_kwh_per_ton']:.0f} kWh/t")
        c[1].metric("吨氨水耗",f"{e['unit_water_ton_per_ton']:.2f} t/t")
        c[2].metric("LCOA",f"{e['lcoa_yuan_per_kg']:.3f} 元/kg")
        c[3].metric("LCOH",f"{e['lcoh_yuan_per_kg']:.3f} 元/kg")
        c=st.columns(4)
        c[0].metric("总投资",f"{e['capex_total']/1e8:.2f} 亿元")
        c[1].metric("年总成本",f"{e['annual_total_cost']/1e4:.0f} 万元")
        c[2].metric("NPV",f"{e['npv']/1e8:.2f} 亿元")
        c[3].metric("IRR",f"{e['irr']*100:.2f}%" if np.isfinite(e["irr"]) else "无有效解")
        st.metric("动态投资回收期",f"{e['payback_year']:.2f} 年" if np.isfinite(e["payback_year"]) else "项目期内未回收")
        fig,ax=plt.subplots(figsize=(11,4))
        years=np.arange(len(e["discounted_cash_flows"]))
        ax.bar(years,np.array(e["discounted_cash_flows"])/1e8,label="年度折现净现金流")
        ax.plot(years,np.array(e["cumulative_discounted_cash_flows"])/1e8,marker="o",label="累计折现现金流")
        ax.axhline(0,color="black",ls="--",lw=.8);ax.set_xlabel("运营年份");ax.set_ylabel("亿元")
        ax.legend();ax.grid(alpha=.2);st.pyplot(fig);plt.close(fig)
        out=pd.DataFrame([{
            "CAPEX(亿元)":e["capex_total"]/1e8,"年OPEX(万元)":e["annual_opex"]/1e4,
            "LCOA(元/kg)":e["lcoa_yuan_per_kg"],"LCOH(元/kg)":e["lcoh_yuan_per_kg"],
            "吨氨电耗(kWh/t)":e["unit_electricity_kwh_per_ton"],
            "吨氨水耗(t/t)":e["unit_water_ton_per_ton"],
            "NPV(亿元)":e["npv"]/1e8,"IRR(%)":e["irr"]*100 if np.isfinite(e["irr"]) else np.nan,
            "动态回收期(年)":e["payback_year"],
        }])
        st.download_button("下载模块四经济评价结果",to_excel(out),file_name="Module4_Financials.xlsx")


with tab5:
    st.markdown("<div class='module-intro'>依据风光设备生命周期排放因子和网电排放因子，核算简化边界下的绿氨碳强度与减排率预判；该计算不替代正式ISCC-EU认证。</div>",unsafe_allow_html=True)
    if st.button("▶ 计算碳足迹与减排率",type="primary",key="run_m5"):
        if "f_stats" not in st.session_state:
            st.error("请先完成模块二或模块三。")
        else:
            try:
                carbon=calculate_carbon(
                    cf_wind,cf_solar,st.session_state["f_stats"],
                    ef_grid,ef_wind,ef_solar,ef_baseline,iscc_threshold)
                st.session_state["carbon_result"]=carbon
                st.success("碳足迹计算完成。")
            except Exception as exc:
                st.error(str(exc))
    if "carbon_result" in st.session_state:
        cdata=st.session_state["carbon_result"]
        c=st.columns(4)
        c[0].metric("风光生命周期排放",f"{cdata['emissions_green_kg']/1e6:.3f} 千吨CO₂e")
        c[1].metric("网电排放",f"{cdata['emissions_grid_kg']/1e6:.3f} 千吨CO₂e")
        c[2].metric("绿氨碳强度",f"{cdata['carbon_intensity_kg_per_kg']:.3f} kgCO₂e/kg")
        c[3].metric("减排率",f"{cdata['reduction_pct']:.1f}%")
        if cdata["precheck_pass"]:
            st.success(f"简化边界减排率达到设定阈值{cdata['threshold_pct']:.0f}%，仅为预判结果。")
        else:
            st.warning(f"简化边界减排率未达到设定阈值{cdata['threshold_pct']:.0f}%。")
        df=pd.DataFrame([{
            "风光发电量(kWh)":cdata["wind_generation_kwh"]+cdata["solar_generation_kwh"],
            "风光LCA排放(吨CO2e)":cdata["emissions_green_kg"]/1000,
            "网电排放(吨CO2e)":cdata["emissions_grid_kg"]/1000,
            "总排放(吨CO2e)":cdata["total_emissions_kg"]/1000,
            "绿氨碳强度(kgCO2e/kg)":cdata["carbon_intensity_kg_per_kg"],
            "灰氨基准(kgCO2e/kg)":cdata["baseline_kg_per_kg"],
            "减排率(%)":cdata["reduction_pct"],
            "是否达到预判阈值":"是" if cdata["precheck_pass"] else "否",
        }])
        st.download_button("下载模块五碳足迹结果",to_excel(df),file_name="Module5_Carbon_LCA.xlsx")
