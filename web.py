# -*- coding: utf-8 -*-
"""柔性绿氨全生命周期智能规划平台 V1.2 - 功能增强版 Web主程序"""
import io
import warnings
import hashlib
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import streamlit as st

from model1 import load_resource_data, optimize_complementarity
from model2 import (
    DEFAULT_COST, DEFAULT_TECH, optimize_engineering_scale,
    optimize_verification_ratio, simulate_system,
)
from model4_economics import calculate_economics
from model5_carbon import calculate_carbon

warnings.filterwarnings("ignore")
plt.rcParams["font.sans-serif"] = [
    "Microsoft YaHei", "SimHei", "WenQuanYi Micro Hei",
    "Noto Sans CJK SC", "Noto Sans CJK JP", "Arial Unicode MS", "DejaVu Sans"
]
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
.small-note {font-size:12px;color:#777;}
</style>
""", unsafe_allow_html=True)


def to_excel(df, summary=None, extra_sheets=None):
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="时序明细")
        if summary is not None:
            summary.to_excel(writer, index=False, sheet_name="汇总")
        if extra_sheets:
            for name, data in extra_sheets.items():
                data.to_excel(writer, index=False, sheet_name=name[:31])
    return out.getvalue()


def operating_violations(stats, target_ton, max_grid, max_curtail, full_green_h2=False):
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
    if full_green_h2 and not stats.get("h2_green_compliant", False):
        errors.append("已启用全绿电制氢约束，但仍检测到网电可能进入制氢系统。")
    return errors


@st.cache_data(show_spinner=False)
def load_uploaded_profiles(wind_bytes, solar_bytes):
    return load_resource_data(io.BytesIO(wind_bytes), io.BytesIO(solar_bytes))


def month_meta():
    month_names = [f"{i}月" for i in range(1, 13)]
    hrs = np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]) * 24
    starts = np.r_[0, np.cumsum(hrs)]
    return month_names, starts


def monthly_sum(arr):
    names, starts = month_meta()
    arr = np.asarray(arr, float)
    return pd.DataFrame({
        "月份": names,
        "数值": [float(arr[starts[i]:starts[i+1]].sum()) for i in range(12)]
    })


def monthly_mean(arr):
    names, starts = month_meta()
    arr = np.asarray(arr, float)
    return pd.DataFrame({
        "月份": names,
        "数值": [float(arr[starts[i]:starts[i+1]].mean()) for i in range(12)]
    })


def evaluate_module1_case(cf_wind, cf_solar, wind_bounds, solar_bounds, target_load_kw, max_shortage, max_curtail):
    try:
        res = optimize_complementarity(
            cf_wind, cf_solar, wind_bounds, solar_bounds,
            target_load_kw, max_shortage, max_curtail
        )
        return {
            "success": True,
            "wind_kw": float(res.x[0]),
            "solar_kw": float(res.x[1]),
            "shortage": float(res.shortage_rate),
            "curtail": float(res.curtail_rate),
        }
    except Exception as exc:
        return {"success": False, "message": str(exc)}


st.markdown("<h1 class='main-title'>🌱 柔性绿氨全生命周期智能规划平台</h1>", unsafe_allow_html=True)
st.markdown("<div class='sub-title'>DeepGreen-NH3 Life-Cycle Optimization System · V1.3 绿电定价与财务评价版</div>", unsafe_allow_html=True)

with st.sidebar:
    st.header("⚙️ 全局参数配置")
    st.subheader("1. 资源数据")
    file_wind = st.file_uploader("上传风资源Excel", type=["xlsx"])
    file_solar = st.file_uploader("上传光资源Excel", type=["xlsx"])
    st.caption("系统读取风、光文件前8760小时；两份文件的时间戳必须逐小时一致。")
    st.divider()

    st.subheader("2. 风光互补边界（模块一）")
    c1, c2 = st.columns(2)
    with c1:
        wind_min = st.number_input("风电下限(MW)", min_value=0.0, value=10.0, step=1.0)
        solar_min = st.number_input("光伏下限(MW)", min_value=0.0, value=0.0, step=1.0)
    with c2:
        wind_max = st.number_input("风电上限(MW)", min_value=0.1, value=200.0, step=1.0)
        solar_max = st.number_input("光伏上限(MW)", min_value=0.1, value=100.0, step=1.0)
    target_load_mw = st.number_input("模块一目标供电基准线(MW)", min_value=0.1, value=50.0, step=1.0)
    max_shortage = st.slider("模块一最大允许资源缺额率(%)", 0.0, 30.0, 10.0, 1.0) / 100.0
    m1_max_curtail_rate = st.slider("模块一最大允许理论弃电率(%)", 0.0, 100.0, 40.0, 1.0) / 100.0
    st.caption("模块一的理论弃电率单独设置，不再与模块二/模块三‘全年最大允许弃电率’共用同一指标。")
    st.divider()

    st.subheader("3. 项目生产及运行约束（模块二/三）")
    target_nh3_ton = st.number_input(
        "最低年产绿氨目标(吨/年)", min_value=0.0, value=0.0, step=1000.0,
        help="模块二、三要求实际年产量不低于该值；若资源充足，实际产量可以高于目标。"
    )
    max_grid_rate = st.slider("最大允许下网电率(%)", 0.0, 100.0, 100.0, 1.0) / 100.0
    max_curtail_rate = st.slider("全年最大允许弃电率(%)", 0.0, 100.0, 20.0, 1.0) / 100.0
    full_green_h2 = st.checkbox("制氢是否要求全绿电", value=False, help="勾选后，系统增加可选约束：网电不进入制氢系统，仅允许进入其他用电环节。")
    st.caption("下网电率=全年网购电量/(制氢耗电+制氨耗电)；弃电率=全年弃电量/全年风光发电量。")
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

# 输入变化时清空旧结果
signature_payload = repr((
    target_load_mw, max_shortage, m1_max_curtail_rate, target_nh3_ton, max_grid_rate, max_curtail_rate,
    price_nh3, discount_rate, int(project_life), om_rate, COST, TECH,
    wind_min, wind_max, solar_min, solar_max, ef_grid, ef_wind, ef_solar,
    ef_baseline, iscc_threshold, full_green_h2,
)).encode("utf-8")
input_signature = hashlib.sha256(file_wind.getvalue() + file_solar.getvalue() + signature_payload).hexdigest()
if st.session_state.get("_input_signature") != input_signature:
    for key in (
        "opt_wind_kw", "opt_solar_kw", "ratio_m1", "m1_shortage", "m1_curtail", "m1_case_df",
        "opt_caps_m2", "f_stats", "m2_success", "m2_message", "m3_result",
        "m3_success", "econ_result", "carbon_result",
    ):
        st.session_state.pop(key, None)
    st.session_state["_input_signature"] = input_signature


# ----------------- 页面布局 -----------------
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 1. 风光互补寻优", "🏭 2. 设备规模与调度",
    "🔄 3. 反向验证与修正", "💰 4. 三套全生命周期财务测算",
    "🌍 5. 碳足迹与减排预判",
])


with tab1:
    st.markdown("<div class='module-intro'>模块一基于8760小时风光资源数据开展风光装机组合初选，输出资源侧最优风光配比；并补充“仅风电”和“仅光伏”情形用于横向对比。模块一的理论弃电率使用独立约束，不受模块二/三全年最大允许弃电率指标影响。</div>", unsafe_allow_html=True)
    if st.button("▶ 运行风光互补寻优", type="primary", key="run_m1"):
        for key in ("opt_caps_m2", "f_stats", "m2_success", "m2_message", "m3_result",
                    "m3_success", "econ_result", "carbon_result"):
            st.session_state.pop(key, None)
        try:
            res = optimize_complementarity(
                cf_wind, cf_solar,
                (wind_min * 1000, wind_max * 1000),
                (solar_min * 1000, solar_max * 1000),
                target_load_mw * 1000, max_shortage, m1_max_curtail_rate,
            )
            opt_w, opt_s = map(float, res.x)
            st.session_state["opt_wind_kw"] = opt_w
            st.session_state["opt_solar_kw"] = opt_s
            st.session_state["ratio_m1"] = opt_w / opt_s if opt_s > 0 else np.inf
            st.session_state["m1_shortage"] = res.shortage_rate
            st.session_state["m1_curtail"] = res.curtail_rate

            cases = []
            combo = evaluate_module1_case(cf_wind, cf_solar, (wind_min*1000, wind_max*1000), (solar_min*1000, solar_max*1000), target_load_mw*1000, max_shortage, m1_max_curtail_rate)
            wind_only = evaluate_module1_case(cf_wind, cf_solar, (wind_min*1000, wind_max*1000), (0.0, 0.0), target_load_mw*1000, max_shortage, 1.0)
            solar_only = evaluate_module1_case(cf_wind, cf_solar, (0.0, 0.0), (solar_min*1000, solar_max*1000), target_load_mw*1000, max_shortage, 1.0)
            for name, case in [("风光协同最优", combo), ("仅风电", wind_only), ("仅光伏", solar_only)]:
                if case["success"]:
                    ratio_txt = "仅单一资源" if case["solar_kw"] == 0 or case["wind_kw"] == 0 else f"{case['wind_kw']/case['solar_kw']:.2f}:1"
                    cases.append({
                        "方案": name,
                        "风电装机(MW)": case["wind_kw"] / 1000,
                        "光伏装机(MW)": case["solar_kw"] / 1000,
                        "风光配比": ratio_txt,
                        "资源缺额率(%)": case["shortage"] * 100,
                        "理论弃电率(%)": case["curtail"] * 100,
                        "可行性": "可行",
                    })
                else:
                    cases.append({
                        "方案": name,
                        "风电装机(MW)": np.nan,
                        "光伏装机(MW)": np.nan,
                        "风光配比": "-",
                        "资源缺额率(%)": np.nan,
                        "理论弃电率(%)": np.nan,
                        "可行性": "不可行",
                    })
            st.session_state["m1_case_df"] = pd.DataFrame(cases)
            st.success("模块一优化完成。")
        except Exception as exc:
            st.error(str(exc))

    if "opt_wind_kw" in st.session_state:
        opt_w, opt_s = st.session_state["opt_wind_kw"], st.session_state["opt_solar_kw"]
        ratio = opt_w / opt_s if opt_s > 0 else np.inf
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("最优风电装机", f"{opt_w/1000:.2f} MW")
        c2.metric("最优光伏装机", f"{opt_s/1000:.2f} MW")
        c3.metric("风光配比(风:光)", f"{ratio:.2f}:1" if np.isfinite(ratio) else "仅风电")
        c4.metric("理论弃电率", f"{st.session_state['m1_curtail']:.2%}")

        p_w = opt_w * cf_wind
        p_s = opt_s * cf_solar
        p = p_w + p_s
        fig, ax = plt.subplots(figsize=(12, 4.5))
        sl = slice(0, 168)
        ax.plot(time_series[sl], p_w[sl]/1000, label="风电出力", linewidth=1.8)
        ax.plot(time_series[sl], p_s[sl]/1000, label="光伏出力", linewidth=1.8)
        ax.plot(time_series[sl], p[sl]/1000, label="风光综合出力", linewidth=2.0)
        ax.axhline(target_load_mw, color="red", linestyle="--", label="目标供电基准线")
        ax.set_ylabel("功率(MW)")
        ax.set_title("模块一前7日风电、光伏及综合出力")
        ax.grid(alpha=.25)
        ax.legend(ncol=4)
        st.pyplot(fig)
        plt.close(fig)

        if "m1_case_df" in st.session_state:
            st.subheader("风光协同与单一资源对比")
            st.dataframe(st.session_state["m1_case_df"], use_container_width=True, hide_index=True)

        df = pd.DataFrame([{
            "风电装机(MW)": opt_w/1000, "光伏装机(MW)": opt_s/1000,
            "风光配比": ratio, "资源缺额率(%)": st.session_state["m1_shortage"]*100,
            "理论弃电率(%)": st.session_state["m1_curtail"]*100,
            "模块一最大允许理论弃电率(%)": m1_max_curtail_rate*100,
        }])
        extra = {"风光与单一资源对比": st.session_state.get("m1_case_df", pd.DataFrame())}
        st.download_button("下载模块一结果Excel", to_excel(df, extra_sheets=extra), file_name="Module1_Ratio.xlsx")


with tab2:
    st.markdown("<div class='module-intro'>模块二固定模块一风光装机，按8760小时调度优化电解槽、储能、储氢及合成氨装置。新增检修月份可视化，并明确设备规模对应的风电/光伏资源条件；同时新增“全绿电制氢”可选约束。</div>", unsafe_allow_html=True)
    if st.button("▶ 运行设备规模与8760小时调度", type="primary", key="run_m2"):
        if "opt_wind_kw" not in st.session_state:
            st.error("请先运行模块一。")
        elif target_nh3_ton <= 0:
            st.error("请在左侧输入目标年产绿氨量。")
        else:
            for key in ("f_stats", "opt_caps_m2", "m2_success", "m2_message", "m3_result",
                        "m3_success", "econ_result", "carbon_result"):
                st.session_state.pop(key, None)
            with st.spinner("正在开展设备规模寻优及8760小时调度..."):
                try:
                    caps, stats, success, message = optimize_engineering_scale(
                        st.session_state["opt_wind_kw"], st.session_state["opt_solar_kw"],
                        cf_wind, cf_solar, target_nh3_ton, max_grid_rate, max_curtail_rate,
                        cost=COST, tech=TECH, discount_rate=discount_rate,
                        project_life=int(project_life), om_rate=om_rate, return_series=True,
                        full_green_h2=full_green_h2,
                    )
                    violations = operating_violations(stats, target_nh3_ton, max_grid_rate, max_curtail_rate, full_green_h2)
                    if violations:
                        raise ValueError("最终方案未通过校核：" + "；".join(violations))
                    st.session_state["opt_caps_m2"] = caps
                    st.session_state["f_stats"] = stats
                    st.session_state["m2_success"] = success
                    st.session_state["m2_message"] = message
                    st.success("设备规模与年度调度计算完成。")
                except Exception as exc:
                    st.error(str(exc))

    if "f_stats" in st.session_state and "opt_caps_m2" in st.session_state:
        fs = st.session_state["f_stats"]
        caps = st.session_state["opt_caps_m2"]
        if not st.session_state.get("m2_success", True):
            st.warning("优化器未报告正常收敛，但当前结果已通过独立约束校核；请复核设备边界。")

        st.subheader("资源条件与设备规模")
        c = st.columns(4)
        c[0].metric("风电装机", f"{fs['wind_kw']/1000:.2f} MW")
        c[1].metric("光伏装机", f"{fs['solar_kw']/1000:.2f} MW")
        c[2].metric("年风电发电量", f"{fs['wind_total']/1e6:.2f} 百万kWh")
        c[3].metric("年光伏发电量", f"{fs['solar_total']/1e6:.2f} 百万kWh")

        c = st.columns(4)
        c[0].metric("电解槽装机", f"{caps[0]/1000:.2f} MW")
        c[1].metric("储能容量", f"{caps[1]/1000:.2f} MWh")
        c[2].metric("储氢容量", f"{caps[2]/1000:.0f} 吨")
        c[3].metric("合成氨额定产能", f"{caps[3]:.0f} kg/h")
        if (caps[0] >= 299700 or caps[1] >= 499500 or caps[2] >= 199800 or caps[3] >= 49950):
            st.warning("至少一项设备规模触及当前搜索上限，当前结果可能受设备边界限制；建议扩大对应上限后重新寻优。")

        c = st.columns(5)
        c[0].metric("全年绿氨产量", f"{fs['nh3_total']/1000:.2f} 吨")
        c[1].metric("全年绿氢产量", f"{fs['h2_total']/1000:.2f} 吨")
        c[2].metric("全年下网电率", f"{fs['grid_rate']:.2%}", delta=f"上限 {max_grid_rate:.0%}")
        c[3].metric("全年弃电率", f"{fs['curtail_rate']:.2%}", delta=f"上限 {max_curtail_rate:.0%}")
        c[4].metric("全绿电制氢", "是" if full_green_h2 else "否", delta=("合规" if fs.get("h2_green_compliant", False) else "待核验") if full_green_h2 else "未启用")
        if full_green_h2:
            st.caption(f"制氢绿电比例下限（估算）为 {fs.get('h2_green_ratio_lower', 0.0):.2%}。")

        c = st.columns(4)
        c[0].metric("全年网购电量", f"{fs['grid_buy']/1e6:.2f} 百万kWh")
        c[1].metric("全年系统用电量", f"{fs['system_total_consumption']/1e6:.2f} 百万kWh")
        c[2].metric("全年弃电量", f"{fs['curtailment']/1e6:.2f} 百万kWh")
        c[3].metric("LCOA", f"{fs['lcoa']:.3f} 元/kg")

        st.subheader("物理平衡校核")
        c = st.columns(4)
        c[0].metric("系统边界输入", f"{fs['energy_in']/1e6:.3f} 百万kWh")
        c[1].metric("系统边界输出", f"{fs['energy_out']/1e6:.3f} 百万kWh")
        c[2].metric("年度电量偏差", f"{fs['balance_diff']:.6f} kWh")
        c[3].metric("氢气物料偏差", f"{fs['h2_balance_diff']:.6f} kg")
        if not fs["state_cycles_converged"]:
            st.warning("年度库存迭代未完全收敛：请增加储能/储氢容量或复核资源曲线。")
        st.caption(
            f"检修月份：{fs['maintenance_month']}月；电池期初/期末库存：{fs['battery_soc_start']:.2f}/{fs['battery_soc_end']:.2f} kWh；"
            f"储氢期初/期末库存：{fs['tank_soc_start']:.2f}/{fs['tank_soc_end']:.2f} kg。"
        )

        # 检修月可视化
        st.subheader("检修月份可视化")
        monthly_green = monthly_sum(fs["arr_p_green"])
        fig, ax = plt.subplots(figsize=(10, 4))
        bars = ax.bar(monthly_green["月份"], monthly_green["数值"] / 1e6)
        for i, b in enumerate(bars, start=1):
            if i == fs["maintenance_month"]:
                b.set_alpha(0.9)
                b.set_hatch("//")
        ax.set_ylabel("百万kWh")
        ax.set_title("各月风光发电量及检修月份")
        ax.grid(axis="y", alpha=.2)
        st.pyplot(fig)
        plt.close(fig)

        # 8760时序可视化
        st.subheader("8760小时运行可视化（示例窗口）")
        fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
        sl = slice(4000, 4336)
        tt = time_series[sl]
        ax1.plot(tt, fs["arr_p_wind"][sl]/1000, label="风电出力")
        ax1.plot(tt, fs["arr_p_solar"][sl]/1000, label="光伏出力")
        ax1.plot(tt, fs["arr_p_green"][sl]/1000, label="风光总出力", linewidth=2)
        ax1.plot(tt, fs["arr_grid_buy"][sl]/1000, label="网电购入", linestyle="--")
        ax1.set_ylabel("MW")
        ax1.set_title("风电、光伏及网电购入")
        ax1.legend(ncol=4)
        ax1.grid(alpha=.2)

        ax2.plot(tt, fs["arr_bat_soc"][sl]/1000, label="电池SOC")
        ax2_t = ax2.twinx()
        ax2_t.plot(tt, fs["arr_bat_chg"][sl]/1000, label="充电功率")
        ax2_t.plot(tt, -fs["arr_bat_dis"][sl]/1000, label="放电功率")
        ax2.set_ylabel("MWh")
        ax2_t.set_ylabel("MW")
        ax2.set_title("储能运行状态")
        ax2.legend(loc="upper left")
        ax2_t.legend(loc="upper right")
        ax2.grid(alpha=.2)

        ax3.plot(tt, fs["arr_tank_soc"][sl], label="储氢库存")
        ax3_t = ax3.twinx()
        ax3_t.plot(tt, fs["arr_h2_prod"][sl], label="产氢量")
        ax3_t.plot(tt, -fs["arr_h2_cons"][sl], label="耗氢量")
        ax3.set_ylabel("kg")
        ax3_t.set_ylabel("kg/h")
        ax3.set_title("储氢库存与产耗氢")
        ax3.legend(loc="upper left")
        ax3_t.legend(loc="upper right")
        ax3.grid(alpha=.2)
        fig.tight_layout()
        st.pyplot(fig)
        plt.close(fig)

        summary = pd.DataFrame([{
            "风电装机(MW)": fs["wind_kw"]/1000, "光伏装机(MW)": fs["solar_kw"]/1000,
            "年风电发电量(百万kWh)": fs["wind_total"]/1e6, "年光伏发电量(百万kWh)": fs["solar_total"]/1e6,
            "年产绿氨(吨)": fs["nh3_total"]/1000, "年产绿氢(吨)": fs["h2_total"]/1000,
            "下网电率(%)": fs["grid_rate"]*100, "下网电率上限(%)": max_grid_rate*100,
            "弃电率(%)": fs["curtail_rate"]*100, "弃电率上限(%)": max_curtail_rate*100,
            "全绿电制氢": "是" if full_green_h2 else "否", "LCOA(元/kg)": fs["lcoa"],
        }])
        detail = pd.DataFrame({
            "时间": time_series, "风电出力(kW)": fs["arr_p_wind"], "光伏出力(kW)": fs["arr_p_solar"],
            "风光出力(kW)": fs["arr_p_green"], "电解槽耗电(kW)": fs["arr_p_el"],
            "合成氨耗电(kW)": fs["arr_p_nh3"], "合成氨产量(kg/h)": fs["arr_nh3_prod"],
            "电网购电(kW)": fs["arr_grid_buy"], "电池充电(kW)": fs["arr_bat_chg"],
            "电池放电(kW)": fs["arr_bat_dis"], "电池SOC(kWh)": fs["arr_bat_soc"],
            "储氢库存(kg)": fs["arr_tank_soc"], "氢气产量(kg/h)": fs["arr_h2_prod"],
            "氢气消耗(kg/h)": fs["arr_h2_cons"], "弃电(kW)": fs["arr_curtail"],
        })
        extra = {"模块二汇总": summary, "模块二逐时结果": detail}
        st.download_button("下载模块二8760小时调度Excel", to_excel(detail, summary, extra), file_name="Module2_8760_Dispatch.xlsx")


with tab3:
    st.markdown("<div class='module-intro'>模块三固定模块二已确定的电解槽、储能、储氢和合成氨设备规模，反向优化风光装机，并增加修正后电量平衡的详细拆分与可视化：总输入按风光绿电、网电、储能放电拆分，总消耗按制氢、制氨、储能充电、弃电拆分。</div>", unsafe_allow_html=True)
    if st.button("▶ 运行反向验证与风光修正", type="primary", key="run_m3"):
        if "opt_caps_m2" not in st.session_state:
            st.error("请先完成模块二。")
        elif target_nh3_ton <= 0:
            st.error("请在左侧输入目标年产绿氨量。")
        else:
            for key in ("econ_result", "carbon_result"):
                st.session_state.pop(key, None)
            with st.spinner("正在固定设备规模、反向修正风光装机..."):
                try:
                    old_w, old_s = st.session_state["opt_wind_kw"], st.session_state["opt_solar_kw"]
                    val_w, val_s, val_stats, success, message = optimize_verification_ratio(
                        cf_wind, cf_solar, st.session_state["opt_caps_m2"], old_w, old_s,
                        target_nh3_ton, max_grid_rate, max_curtail_rate,
                        cost=COST, tech=TECH, discount_rate=discount_rate,
                        project_life=int(project_life), om_rate=om_rate,
                        wind_bounds=(wind_min*1000, wind_max*1000),
                        solar_bounds=(solar_min*1000, solar_max*1000),
                        return_series=True, full_green_h2=full_green_h2,
                    )
                    violations = operating_violations(val_stats, target_nh3_ton, max_grid_rate, max_curtail_rate, full_green_h2)
                    if violations:
                        raise ValueError("反向验证未通过：" + "；".join(violations))
                    st.session_state["f_stats"] = val_stats
                    st.session_state["opt_wind_kw"] = val_w
                    st.session_state["opt_solar_kw"] = val_s
                    st.session_state["m3_result"] = (old_w, old_s, val_w, val_s)
                    st.session_state["m3_success"] = success
                    st.success("反向验证完成，风光装机已更新。")
                except Exception as exc:
                    st.error(str(exc))

    if "m3_result" in st.session_state:
        old_w, old_s, val_w, val_s = st.session_state["m3_result"]
        fs = st.session_state["f_stats"]
        c = st.columns(4)
        c[0].metric("修正后风电", f"{val_w/1000:.2f} MW")
        c[1].metric("修正后光伏", f"{val_s/1000:.2f} MW")
        c[2].metric("全年下网电率", f"{fs['grid_rate']:.2%}")
        c[3].metric("全年弃电率", f"{fs['curtail_rate']:.2%}")
        if (val_w <= wind_min*1000 + 1 or val_w >= wind_max*1000 - 1 or
                val_s <= solar_min*1000 + 1 or val_s >= solar_max*1000 - 1):
            st.warning("修正后的风光装机触及当前搜索边界，建议扩大对应装机上下限后重新验证。")
        st.write(f"风光装机调整：风电 {old_w/1000:.2f} → {val_w/1000:.2f} MW；光伏 {old_s/1000:.2f} → {val_s/1000:.2f} MW。")
        st.caption("模块三固定模块二设备规模。若风光装机修正幅度较大，建议将修正后的风光规模作为新输入重新运行模块二，复核设备容量是否仍为最优。")

        st.subheader("修正后的电量平衡详细拆分")
        input_df = pd.DataFrame({
            "项目": ["风电出力", "光伏出力", "网电购入", "储能放电"],
            "电量(kWh)": [fs["wind_total"], fs["solar_total"], fs["grid_buy"], fs["bat_dis"]],
        })
        output_df = pd.DataFrame({
            "项目": ["制氢耗电", "制氨耗电", "储能充电", "弃电"],
            "电量(kWh)": [fs["el_power"], fs["nh3_power"], fs["bat_chg"], fs["curtailment"]],
        })
        c1, c2 = st.columns(2)
        c1.dataframe(input_df.assign(**{"电量(百万kWh)": input_df["电量(kWh)"]/1e6})[["项目", "电量(百万kWh)"]], hide_index=True, use_container_width=True)
        c2.dataframe(output_df.assign(**{"电量(百万kWh)": output_df["电量(kWh)"]/1e6})[["项目", "电量(百万kWh)"]], hide_index=True, use_container_width=True)

        fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
        axes[0].bar(input_df["项目"], input_df["电量(kWh)"]/1e6)
        axes[0].set_title("总输入构成")
        axes[0].set_ylabel("百万kWh")
        axes[0].grid(axis="y", alpha=.2)
        axes[1].bar(output_df["项目"], output_df["电量(kWh)"]/1e6)
        axes[1].set_title("总消耗/去向构成")
        axes[1].set_ylabel("百万kWh")
        axes[1].grid(axis="y", alpha=.2)
        fig.tight_layout()
        st.pyplot(fig)
        plt.close(fig)

        df = pd.DataFrame([{
            "修正前风电(MW)": old_w/1000, "修正前光伏(MW)": old_s/1000,
            "修正后风电(MW)": val_w/1000, "修正后光伏(MW)": val_s/1000,
            "全年绿氨(吨)": fs["nh3_total"]/1000, "下网电率(%)": fs["grid_rate"]*100,
            "弃电率(%)": fs["curtail_rate"]*100, "LCOA(元/kg)": fs["lcoa"],
        }])
        extra = {"总输入拆分": input_df, "总消耗拆分": output_df}
        st.download_button("下载模块三反向验证结果", to_excel(df, extra_sheets=extra), file_name="Module3_Validation.xlsx")


with tab4:
    st.markdown("<div class='module-intro'>模块四输出三套全生命周期财务测算：①整体项目详细测算；②绿电端按LCOE成本电价与全投资IRR6%反推售价两情景；③化工端分别按上述两种绿电输入价格测算LCOA、NPV、IRR和回收期。</div>", unsafe_allow_html=True)
    st.caption("财务边界：全投资、税前、无融资。建设投资在第0年投入，运营期每年按当前8760小时调度成果重复测算；暂不含增值税、所得税、税盾、融资成本、补贴及残值。绿电端资产为风电+光伏，储能归化工端。")
    if st.button("▶ 生成模块四三套全生命周期财务测算", type="primary", key="run_m4"):
        if "f_stats" not in st.session_state:
            st.error("请先完成模块二或模块三。")
        else:
            try:
                econ = calculate_economics(
                    st.session_state["f_stats"], COST, price_nh3,
                    discount_rate, int(project_life), om_rate,
                    target_green_irr=0.06,
                )
                st.session_state["econ_result"] = econ
                st.success("三套测算已完成：整体项目、绿电端双电价、化工端双电价。")
            except Exception as exc:
                st.error(f"模块四计算失败：{exc}")

    if "econ_result" in st.session_state:
        e = st.session_state["econ_result"]
        st.subheader("三套测算结果总览")
        st.dataframe(e["three_model_summary"], use_container_width=True, hide_index=True)
        one, two, three = st.tabs([
            "一｜整体项目财务详表",
            "二｜绿电端LCOE及IRR6%",
            "三｜化工端双电价收益",
        ])

        with one:
            st.markdown("**第一套：整体项目全生命周期测算（合并抵销内部绿电交易）**")
            c = st.columns(5)
            c[0].metric("项目总投资", f"{e['capex_total']/1e8:.2f} 亿元")
            c[1].metric("整体LCOA", f"{e['lcoa_yuan_per_kg']:.3f} 元/kg")
            c[2].metric("整体NPV", f"{e['npv']/1e8:.2f} 亿元")
            c[3].metric("整体全投资IRR", f"{e['irr']*100:.2f}%" if np.isfinite(e["irr"]) else "无有效解")
            c[4].metric("动态回收期", f"{e['payback_year']:.2f} 年" if np.isfinite(e["payback_year"]) else "期内未回收")
            st.markdown("**1. 设备投资明细**")
            st.dataframe(e["capex_breakdown"], use_container_width=True, hide_index=True)
            st.markdown("**2. 能源消耗与产量输入明细**")
            st.dataframe(e["detail_energy_table"], use_container_width=True, hide_index=True)
            st.markdown("**3. 整体与分部年化成本明细**")
            st.dataframe(e["detail_cost_table"], use_container_width=True, hide_index=True)
            st.markdown("**4. 整体项目全生命周期年度现金流详表**")
            st.dataframe(e["overall_lifecycle_detail"], use_container_width=True, hide_index=True)
            cash_chart = e["overall_lifecycle_detail"].set_index("年份")[["年度折现净现金流(元)", "累计折现净现金流(元)"]] / 1e8
            cash_chart.columns = ["当年折现现金流（亿元）", "累计折现现金流（亿元）"]
            st.line_chart(cash_chart)
            st.caption("整体项目收益不受内部绿电交易结算价格影响；内部售电收入与化工端购电费用在合并时抵销。")

        with two:
            st.markdown("**第二套：绿电端全生命周期财务测算（两种销售电价）**")
            a, b, c = st.columns(3)
            a.metric("风光生产LCOE", f"{e['lcoe_green_yuan_per_kwh']:.4f} 元/kWh")
            b.metric("按全投资IRR 6%反推售电价", f"{e['irr6_sale_price_yuan_per_kwh']:.4f} 元/kWh")
            c.metric("可售绿电量", f"{e['green_sales_kwh']/1e6:.2f} 百万kWh/年")
            st.caption("LCOE按全年风光总发电量计算；售电收入与IRR6%倒推价格按扣除弃电后的实际可交付电量计算。因弃电存在，两者分母不同，已额外展示可售电量等效LCOE。")
            st.metric("可售电量等效LCOE", f"{e['lcoe_sale_equivalent_yuan_per_kwh']:.4f} 元/kWh")
            st.dataframe(e["green_price_comparison"], use_container_width=True, hide_index=True)
            with st.expander("绿电端情景A：按LCOE成本电价售电——全生命周期测算详表", expanded=True):
                st.dataframe(e["green_lcoe_lifecycle_detail"], use_container_width=True, hide_index=True)
            with st.expander("绿电端情景B：按全投资IRR6%反推售电价——全生命周期测算详表", expanded=True):
                st.dataframe(e["green_irr6_lifecycle_detail"], use_container_width=True, hide_index=True)
            green_irr_actual = e["green_scenarios"]["绿电端IRR6%售电价"]["irr"]
            st.caption(f"反推校验：情景B绿电端实际全投资IRR = {green_irr_actual:.6%}；目标 = 6.000000%。")

        with three:
            st.markdown("**第三套：化工端（制氢+合成氨+储能储氢）双绿电价格收益测算**")
            st.caption("化工端资本成本仅包含电解槽、储能、储氢、合成氨装置；按实际可交付绿电量乘相应电价计入采购成本，另计网电费、水费及化工端运维费。")
            st.dataframe(e["chemical_price_comparison"], use_container_width=True, hide_index=True)
            with st.expander("化工端情景A：按绿电LCOE成本电价购电——全生命周期测算详表", expanded=True):
                st.dataframe(e["chemical_lcoe_lifecycle_detail"], use_container_width=True, hide_index=True)
            with st.expander("化工端情景B：按绿电端IRR6%售电价购电——全生命周期测算详表", expanded=True):
                st.dataframe(e["chemical_irr6_lifecycle_detail"], use_container_width=True, hide_index=True)
            comparison = e["chemical_price_comparison"].set_index("电价方案")
            st.markdown("**两种电价下化工端单位成本对比**")
            st.bar_chart(comparison[["化工端LCOA(元/kg)"]])
            st.markdown("**两种电价下化工端全投资IRR对比**")
            st.bar_chart(comparison[["化工端全投资IRR(%)"]])

        st.subheader("下载三套全生命周期经济测算（含五种逐年现金流详表）")
        finance_parameters = pd.DataFrame([
            {"参数": "模型财务边界", "取值": "全投资税前、无融资、建设投资第0年投入", "说明": "不含增值税、所得税、折旧抵税、残值、补贴和融资成本"},
            {"参数": "运营期", "取值": int(project_life), "说明": "年"},
            {"参数": "现金流贴现率", "取值": discount_rate * 100, "说明": "%"},
            {"参数": "绿电端目标全投资IRR", "取值": 6.0, "说明": "%"},
            {"参数": "绿氨销售单价", "取值": price_nh3, "说明": "元/吨"},
            {"参数": "年运维费率", "取值": om_rate * 100, "说明": "%"},
            {"参数": "全年风光总发电量", "取值": e["green_generation_kwh"], "说明": "kWh/年，绿电生产LCOE分母"},
            {"参数": "全年可售绿电量", "取值": e["green_sales_kwh"], "说明": "kWh/年，售电收入及IRR6%反推电价分母"},
            {"参数": "绿电生产LCOE", "取值": e["lcoe_green_yuan_per_kwh"], "说明": "元/kWh"},
            {"参数": "绿电端IRR6%反推售电价", "取值": e["irr6_sale_price_yuan_per_kwh"], "说明": "元/kWh"},
        ])
        extra = {
            "测算参数及边界": finance_parameters,
            "三套测算综合汇总": e["three_model_summary"],
            "设备投资明细": e["capex_breakdown"],
            "能源及产量输入": e["detail_energy_table"],
            "整体与分部年成本": e["detail_cost_table"],
            "绿电端两种售价": e["green_price_comparison"],
            "化工端两种售价收益": e["chemical_price_comparison"],
            **e["lifecycle_models"],
        }
        st.download_button(
            "下载模块四三套全生命周期完整详表（Excel）",
            to_excel(e["three_model_summary"], extra_sheets=extra),
            file_name="Module4_Three_Lifecycle_Financial_Models.xlsx",
            key="module4_download_three_models",
        )


with tab5:
    st.markdown("<div class='module-intro'>模块五在原有碳足迹与减排率测算基础上，增加结果可视化，分别展示风电、光伏和网电对应的排放贡献，以及减排率对比情况。</div>", unsafe_allow_html=True)
    if st.button("▶ 计算碳足迹与减排率", type="primary", key="run_m5"):
        if "f_stats" not in st.session_state:
            st.error("请先完成模块二或模块三。")
        else:
            try:
                carbon = calculate_carbon(
                    cf_wind, cf_solar, st.session_state["f_stats"],
                    ef_grid, ef_wind, ef_solar, ef_baseline, iscc_threshold
                )
                st.session_state["carbon_result"] = carbon
                st.success("碳足迹计算完成。")
            except Exception as exc:
                st.error(str(exc))

    if "carbon_result" in st.session_state:
        cdata = st.session_state["carbon_result"]
        c = st.columns(4)
        c[0].metric("风光生命周期排放", f"{cdata['emissions_green_kg']/1e6:.3f} 千吨CO₂e")
        c[1].metric("网电排放", f"{cdata['emissions_grid_kg']/1e6:.3f} 千吨CO₂e")
        c[2].metric("绿氨碳强度", f"{cdata['carbon_intensity_kg_per_kg']:.3f} kgCO₂e/kg")
        c[3].metric("减排率", f"{cdata['reduction_pct']:.1f}%")
        if cdata["precheck_pass"]:
            st.success(f"简化边界减排率达到设定阈值 {cdata['threshold_pct']:.0f}% ，仅为预判结果。")
        else:
            st.warning(f"简化边界减排率未达到设定阈值 {cdata['threshold_pct']:.0f}%。")

        fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
        axes[0].bar(["风电排放", "光伏排放", "网电排放"], [
            cdata.get("emissions_wind_kg", 0.0)/1000,
            cdata.get("emissions_solar_kg", 0.0)/1000,
            cdata.get("emissions_grid_kg", 0.0)/1000,
        ])
        axes[0].set_title("排放来源构成")
        axes[0].set_ylabel("吨CO₂e")
        axes[0].grid(axis="y", alpha=.2)

        axes[1].bar(["灰氨基准", "本项目绿氨"], [cdata["baseline_kg_per_kg"], cdata["carbon_intensity_kg_per_kg"]])
        axes[1].set_title("单位产品碳强度对比")
        axes[1].set_ylabel("kgCO₂e/kg NH₃")
        axes[1].grid(axis="y", alpha=.2)
        fig.tight_layout()
        st.pyplot(fig)
        plt.close(fig)

        df = pd.DataFrame([{
            "风光发电量(kWh)": cdata["wind_generation_kwh"]+cdata["solar_generation_kwh"],
            "风电LCA排放(吨CO2e)": cdata.get("emissions_wind_kg", 0.0)/1000,
            "光伏LCA排放(吨CO2e)": cdata.get("emissions_solar_kg", 0.0)/1000,
            "网电排放(吨CO2e)": cdata["emissions_grid_kg"]/1000,
            "总排放(吨CO2e)": cdata["total_emissions_kg"]/1000,
            "绿氨碳强度(kgCO2e/kg)": cdata["carbon_intensity_kg_per_kg"],
            "灰氨基准(kgCO2e/kg)": cdata["baseline_kg_per_kg"],
            "减排率(%)": cdata["reduction_pct"],
            "是否达到预判阈值": "是" if cdata["precheck_pass"] else "否",
        }])
        st.download_button("下载模块五碳足迹结果", to_excel(df), file_name="Module5_Carbon_LCA.xlsx")
