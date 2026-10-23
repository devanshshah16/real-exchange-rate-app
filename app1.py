"""
IMF Real Exchange Rate Explorer
Python 3.12 | Streamlit + Plotly

Uses the IMF SDMX 3.0 REST API directly:
  https://api.imf.org/external/sdmx/3.0/data/dataflow/{agency}/{dataset}/{version}/{key}

CPI dataflow/key are based on the IMF_DATA examples:
  Dataset: CPI
  Key: {ISO3}.CPI._T.IX.M

Exchange rates are requested from the ER dataset. The ER dataset's
dimension order/codes can differ from the assignment's earlier IFS-style
key, so this app tries documented ER key patterns and validates that a
response contains observations. If none work, it displays the attempted keys
rather than treating the series as genuinely missing.
"""
from __future__ import annotations

import io
from datetime import date
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import pycountry
import requests
import streamlit as st
from plotly.subplots import make_subplots

API_BASE = "https://api.imf.org/external/sdmx/3.0/data/dataflow"
AGENCY = "IMF.STA"
TIME_FILTER = "c[TIME_PERIOD]=ge:1976-M01"
TIMEOUT = 40

st.set_page_config(page_title="IMF Real Exchange Rate Explorer", page_icon="🌐", layout="wide")
st.title("🌐 Real Exchange Rate Explorer")
st.caption("A simple guide to how exchange rates change over time, after accounting for inflation.")
with st.expander("How to use this app (start here)", expanded=True):
    st.markdown(
        """
        1. **Choose two countries.** The exchange rate is shown as Country 2's currency for 1 unit of Country 1's currency.
        2. **Choose the dates.** The app recalculates the real-exchange-rate benchmark for those months.
        3. **Read the chart.** The nominal rate is the market exchange rate; the real index adjusts it using each country's CPI.
        4. **Download the CSV** to inspect the data and calculations.

        **Example:** If Country 1 is the United Kingdom and Country 2 is the United States, the nominal rate is **USD per GBP**.
        """
    )

EURO_AREA = {"name": "Euro area", "iso3": "G163", "currency": "EUR"}
COUNTRIES = [
    {"name": c.name, "iso3": c.alpha_3, "currency": ""}
    for c in pycountry.countries if getattr(c, "alpha_3", None)
]
COUNTRIES.append(EURO_AREA)
COUNTRIES.sort(key=lambda x: x["name"].casefold())
COUNTRY_BY_NAME = {c["name"]: c for c in COUNTRIES}
COUNTRY_NAMES = [c["name"] for c in COUNTRIES]

CURRENCY_BY_COUNTRY = {
    "USA": "USD", "GBR": "GBP", "JPN": "JPY", "CHN": "CNY", "IND": "INR",
    "CAN": "CAD", "AUS": "AUD", "CHE": "CHF", "NZL": "NZD", "MEX": "MXN",
    "BRA": "BRL", "ZAF": "ZAR", "KOR": "KRW", "SGP": "SGD", "HKG": "HKD",
    "TUR": "TRY", "RUS": "RUB", "IDN": "IDR", "THA": "THB", "MYS": "MYR",
    "PHL": "PHP", "ISR": "ILS", "SAU": "SAR", "ARE": "AED", "NOR": "NOK",
    "SWE": "SEK", "DNK": "DKK", "POL": "PLN", "CZE": "CZK", "HUN": "HUF",
    "ROU": "RON", "ISL": "ISK", "TWN": "TWD", "PAK": "PKR", "BGD": "BDT",
    "LKA": "LKR", "NPL": "NPR", "EGY": "EGP", "NGA": "NGN", "KEN": "KES",
    "GHA": "GHS", "MAR": "MAD", "TUN": "TND", "ARG": "ARS", "CHL": "CLP",
    "COL": "COP", "PER": "PEN", "URY": "UYU", "UKR": "UAH", "VNM": "VND",
    "MMR": "MMK", "KHM": "KHR", "LAO": "LAK", "ETH": "ETB", "TZA": "TZS",
    "UGA": "UGX", "DZA": "DZD", "IRN": "IRR", "IRQ": "IQD", "QAT": "QAR",
    "KWT": "KWD", "BHR": "BHD", "OMN": "OMR", "JOR": "JOD", "LBN": "LBP",
    "HRV": "EUR", "SVN": "EUR", "SVK": "EUR", "EST": "EUR", "LVA": "EUR",
    "LTU": "EUR", "FIN": "EUR", "IRL": "EUR", "ITA": "EUR", "ESP": "EUR",
    "PRT": "EUR", "GRC": "EUR", "FRA": "EUR", "DEU": "EUR", "AUT": "EUR",
    "BEL": "EUR", "NLD": "EUR", "LUX": "EUR", "CYP": "EUR", "MLT": "EUR",
    "G163": "EUR",
}

SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "text/csv, application/vnd.sdmx.data+csv;version=2.0.0, application/json"
})


def currency_code(country: dict[str, str]) -> str:
    return country.get("currency") or CURRENCY_BY_COUNTRY.get(country["iso3"], country["iso3"])


def parse_month(value: Any) -> pd.Timestamp | pd.NaT:
    """Parse IMF periods such as 2024-M01, 2024-01, or 2024."""
    if pd.isna(value):
        return pd.NaT
    text = str(value).strip()
    text = text.replace("-M", "-")
    try:
        dt = pd.to_datetime(text, errors="coerce")
        if pd.isna(dt):
            return pd.NaT
        return pd.Timestamp(dt.year, dt.month, 1)
    except Exception:
        return pd.NaT


def parse_csv(text: str) -> pd.DataFrame:
    try:
        raw = pd.read_csv(io.StringIO(text))
    except Exception:
        return pd.DataFrame(columns=["date", "value"])
    if raw.empty:
        return pd.DataFrame(columns=["date", "value"])
    raw.columns = [str(c).strip().upper() for c in raw.columns]
    date_col = next((c for c in raw.columns if c in {"TIME_PERIOD", "TIME", "PERIOD", "DATE"}), None)
    value_col = next((c for c in raw.columns if c in {"OBS_VALUE", "VALUE"}), None)
    if date_col is None or value_col is None:
        return pd.DataFrame(columns=["date", "value"])
    out = pd.DataFrame({
        "date": raw[date_col].map(parse_month),
        "value": pd.to_numeric(raw[value_col], errors="coerce"),
    }).dropna()
    out = out.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    return out


def api_csv(dataset: str, key: str) -> tuple[pd.DataFrame, str]:
    """
    Query IMF SDMX 3.0 directly. This follows the documented path pattern:
    /data/dataflow/{agency}/{dataflow}/{version}/{key}
    with '~' meaning latest version.
    """
    url = f"{API_BASE}/{AGENCY}/{dataset}/~/{key}"
    params = {"c[TIME_PERIOD]": "ge:1976-M01"}
    response = SESSION.get(url, params=params, headers={"Accept": "text/csv"}, timeout=TIMEOUT)
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code} for {response.url}: {response.text[:250]}")
    result = parse_csv(response.text)
    if result.empty:
        raise RuntimeError(f"No parseable observations returned for {response.url}. Response: {response.text[:250]}")
    return result, response.url


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def fetch_cpi(iso3: str) -> tuple[pd.DataFrame, str]:
    # CPI key pattern documented by IMF_DATA examples.
    key = f"{iso3}.CPI._T.IX.M"
    df, source = api_csv("CPI", key)
    df = df[df["value"] > 0].rename(columns={"value": "cpi"})
    if df.empty:
        raise RuntimeError(f"CPI returned no positive values for {iso3}; verify series availability.")
    return df, source


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def fetch_fx(iso3: str) -> tuple[pd.DataFrame, str]:
    """
    Query the IMF ER dataset.

    ER has a separate data structure from the CPI dataset. The candidate key
    patterns below are attempted in order because the precise ER dimension
    codes may vary from the older IFS-style key supplied in the assignment.
    Every successful response is parsed and returned; failed patterns are
    reported to the user if none work.
    """
    candidates = [
        f"{iso3}.USD.XT.M",
        f"{iso3}.USD.XDC.M",
        f"{iso3}.USD.XDC_USD.PA_RT.M",
        # Assignment-provided key shape, in case this series is exposed in ER.
        f"{iso3}.XDC_USD.PA_RT.M",
    ]
    errors = []
    for key in candidates:
        try:
            df, source = api_csv("ER", key)
            df = df[df["value"] > 0].rename(columns={"value": "local_per_usd"})
            if not df.empty:
                return df, source
        except Exception as exc:
            errors.append(f"{key}: {exc}")
    raise RuntimeError(
        f"Could not retrieve a usable ER series for {iso3}. "
        "The CPI dataset and ER dataset use different dimension structures. "
        "Verify the ER dataset's current key dimensions in the IMF API portal.\n\n"
        + "\n".join(errors)
    )


def load_country_data(country: dict[str, str]) -> tuple[pd.DataFrame, list[str]]:
    cpi, cpi_source = fetch_cpi(country["iso3"])
    fx, fx_source = fetch_fx(country["iso3"])
    merged = pd.merge(cpi, fx, on="date", how="outer").sort_values("date")
    return merged, [cpi_source, fx_source]


def build_sample(c1: dict[str, str], c2: dict[str, str], start: date, end: date):
    df1, src1 = load_country_data(c1)
    df2, src2 = load_country_data(c2)
    df1 = df1.rename(columns={"cpi": "cpi_1", "local_per_usd": "local_per_usd_1"})
    df2 = df2.rename(columns={"cpi": "cpi_2", "local_per_usd": "local_per_usd_2"})
    data = pd.merge(
        df1[["date", "cpi_1", "local_per_usd_1"]],
        df2[["date", "cpi_2", "local_per_usd_2"]],
        on="date", how="outer"
    ).sort_values("date")

    start_ts = pd.Timestamp(start.year, start.month, 1)
    end_ts = pd.Timestamp(end.year, end.month, 1)
    data = data.loc[data["date"].between(start_ts, end_ts)].copy()

    # Local-currency units per USD -> units of currency 2 per currency 1.
    data["S_country2_per_country1"] = data["local_per_usd_2"] / data["local_per_usd_1"]
    data.loc[
        data["local_per_usd_1"].isna() | data["local_per_usd_2"].isna(),
        "S_country2_per_country1"
    ] = float("nan")
    data["RER"] = data["S_country2_per_country1"] * data["cpi_1"] / data["cpi_2"]
    valid = data["RER"].replace([float("inf"), -float("inf")], float("nan")).dropna()
    valid = valid[valid > 0]
    rer_star = valid.mean() if not valid.empty else float("nan")
    data["RER_star"] = rer_star
    data["Real_Index"] = 100 * data["RER"] / rer_star if pd.notna(rer_star) and rer_star != 0 else float("nan")
    return data, src1 + src2, rer_star


with st.sidebar:
    st.header("Comparison settings")
    default_1 = COUNTRY_NAMES.index("United Kingdom") if "United Kingdom" in COUNTRY_NAMES else 0
    default_2 = COUNTRY_NAMES.index("United States") if "United States" in COUNTRY_NAMES else min(1, len(COUNTRY_NAMES) - 1)
    c1_name = st.selectbox("Country 1 (numerator CPI)", COUNTRY_NAMES, index=default_1)
    options_2 = [name for name in COUNTRY_NAMES if name != c1_name]
    default_2_name = "United States" if c1_name != "United States" and "United States" in options_2 else options_2[0]
    c2_name = st.selectbox("Country 2 (denominator CPI)", options_2, index=options_2.index(default_2_name))
    today = date.today()
    start_date = st.date_input("Start month", value=date(1976, 1, 1), min_value=date(1976, 1, 1), max_value=today)
    end_date = st.date_input("End month", value=date(today.year, today.month, 1), min_value=date(1976, 1, 1), max_value=today)
    chart_mode = st.radio("Chart view", ["Both", "Real", "Nominal"], index=0)
    st.caption("Date inputs are interpreted as calendar months.")

if start_date > end_date:
    st.error("The start month must be on or before the end month.")
    st.stop()

c1, c2 = COUNTRY_BY_NAME[c1_name], COUNTRY_BY_NAME[c2_name]
with st.spinner("Retrieving monthly CPI and exchange-rate series from the IMF…"):
    try:
        data, sources, rer_star = build_sample(c1, c2, start_date, end_date)
    except Exception as exc:
        st.error("Could not retrieve the requested IMF series.")
        st.code(str(exc))
        st.markdown("Check the IMF's official [API documentation](https://data.imf.org/en/Resource-Pages/IMF-API) and verify the dataset dimensions and series keys.")
        st.stop()

if data.empty:
    st.warning("No observations exist in the selected sample period.")
    st.stop()

cur1, cur2 = currency_code(c1), currency_code(c2)
st.subheader(f"{c1['name']} → {c2['name']}")
st.info(
    f"**Reading the currency pair:** this app shows how many {cur2} you get for 1 {cur1}. "
    f"For {c1['name']} as Country 1 and {c2['name']} as Country 2, that means "
    f"**{cur2} per {cur1}**. The nominal rate is kept in actual currency units."
)

valid_nominal_count = int(data["S_country2_per_country1"].notna().sum())
valid_real_count = int(data["Real_Index"].notna().sum())
total_months = int(len(data))
index_mean = data["Real_Index"].dropna().mean() if valid_real_count else float("nan")
index_mean_text = f"{index_mean:.2f}" if pd.notna(index_mean) else "not available"

m1, m2, m3, m4 = st.columns(4)
m1.metric("Months selected", f"{total_months:,}")
m2.metric("Months with exchange-rate data", f"{valid_nominal_count:,}")
m3.metric("Months usable for real index", f"{valid_real_count:,}")
m4.metric("Average RER benchmark (RER*)", f"{rer_star:,.5g}" if pd.notna(rer_star) else "Unavailable")

st.markdown(
    f"**What this means:** You selected {total_months} calendar months. "
    f"The nominal exchange rate is available for {valid_nominal_count} of them. "
    f"After requiring both countries' CPI and exchange-rate inputs, {valid_real_count} months can be used "
    f"for the real index. Missing months are left blank, not guessed."
)
if pd.notna(rer_star):
    st.markdown(
        f"**About RER\*:** {rer_star:,.5g} is the average real-exchange-rate calculation "
        f"over the {valid_real_count} valid months in this selected date range. "
        "It is the benchmark used to set the real index to an average of 100."
    )
else:
    st.warning("A real-exchange-rate benchmark cannot be calculated because this sample has no valid RER observations.")

st.markdown(
    "**How to read the real index:** 100 means equal to the selected-period average. "
    "Above 100 means the calculated RER is above that average; below 100 means it is below that average. "
    "This is a relative index, not a percentage change in the nominal exchange rate."
)

if chart_mode == "Both":
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(x=data["date"], y=data["Real_Index"], name="Real Index (sample mean = 100)", mode="lines"), secondary_y=False)
    fig.add_trace(go.Scatter(x=data["date"], y=data["S_country2_per_country1"], name=f"Nominal FX ({cur2} per {cur1})", mode="lines"), secondary_y=True)
    fig.update_yaxes(title_text="Real exchange-rate index", secondary_y=False)
    fig.update_yaxes(title_text=f"Nominal FX ({cur2} per {cur1})", secondary_y=True)
elif chart_mode == "Real":
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=data["date"], y=data["Real_Index"], name="Real Index (sample mean = 100)", mode="lines"))
    fig.update_yaxes(title_text="Real exchange-rate index")
else:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=data["date"], y=data["S_country2_per_country1"], name=f"Nominal FX ({cur2} per {cur1})", mode="lines"))
    fig.update_yaxes(title_text=f"Nominal FX ({cur2} per {cur1})")

fig.update_layout(
    title=f"Monthly exchange rates: {c1['name']} vs. {c2['name']}",
    xaxis_title="Month",
    hovermode="x unified",
    legend_title="Series",
    margin=dict(l=20, r=20, t=60, b=20),
)
st.plotly_chart(fig, use_container_width=True)

# A quick explanation tied to the chart currently shown.
if chart_mode in ("Real", "Both") and valid_real_count:
    latest_real = data.dropna(subset=["Real_Index"]).iloc[-1]
    latest_value = float(latest_real["Real_Index"])
    latest_month = latest_real["date"].strftime("%B %Y")
    if latest_value > 100:
        real_interpretation = f"The latest available real index is **{latest_value:.1f}** ({latest_month}), above the selected-period average of 100."
    elif latest_value < 100:
        real_interpretation = f"The latest available real index is **{latest_value:.1f}** ({latest_month}), below the selected-period average of 100."
    else:
        real_interpretation = f"The latest available real index is **{latest_value:.1f}** ({latest_month}), approximately equal to the selected-period average of 100."
    st.markdown("**What the real-index line suggests:** " + real_interpretation)

if chart_mode in ("Nominal", "Both") and valid_nominal_count:
    latest_nominal = data.dropna(subset=["S_country2_per_country1"]).iloc[-1]
    nominal_value = float(latest_nominal["S_country2_per_country1"])
    nominal_month = latest_nominal["date"].strftime("%B %Y")
    st.markdown(
        f"**What the nominal-rate line says:** In {nominal_month}, 1 {cur1} was equivalent to "
        f"approximately **{nominal_value:,.4g} {cur2}** in the IMF series. "
        "This is the nominal rate, so it does not adjust for differences in consumer prices."
    )

with st.expander("Data coverage and source details"):
    st.write("Data are requested from January 1976 onward, subject to series availability.")
    for source in sources:
        st.write(f"- `{source}`")
    st.write("Missing observations are not interpolated.")
    st.write("The nominal cross-rate is calculated as Country 2 local-currency units per USD divided by Country 1 local-currency units per USD.")
    st.warning("The IMF ER series key must be confirmed against the current ER data structure. If all candidate keys fail, the error panel lists the attempted keys.")

st.subheader("Quick checks: does the calculation make sense?")

# The mean of the index should be 100 over the valid observations used to compute RER*.
if valid_real_count:
    if abs(index_mean - 100) < 0.05:
        st.success(f"**Sample-average check passed:** the real index averages {index_mean:.2f} across valid months (expected: 100).")
    else:
        st.warning(f"The real index averages {index_mean:.2f}, not 100. Check the RER inputs and missing values.")
else:
    st.warning("The average-index check cannot run because there are no valid real-index observations.")

# Show one transparent worked example using an observation with every required input.
calculation_rows = data.dropna(subset=[
    "cpi_1", "cpi_2", "local_per_usd_1", "local_per_usd_2",
    "S_country2_per_country1", "RER", "Real_Index"
])
if not calculation_rows.empty:
    example = calculation_rows.iloc[0]
    example_month = example["date"].strftime("%B %Y")
    st.markdown(f"**One worked example ({example_month})**")
    st.markdown(
        f"- Country 1 CPI: **{example['cpi_1']:,.5g}**\n"
        f"- Country 2 CPI: **{example['cpi_2']:,.5g}**\n"
        f"- Country 1 local-currency units per USD: **{example['local_per_usd_1']:,.5g}**\n"
        f"- Country 2 local-currency units per USD: **{example['local_per_usd_2']:,.5g}**"
    )
    st.code(
        f"S = Country 2 local currency per USD / Country 1 local currency per USD\n"
        f"  = {example['local_per_usd_2']:,.8g} / {example['local_per_usd_1']:,.8g}\n"
        f"  = {example['S_country2_per_country1']:,.8g} {cur2} per {cur1}\n\n"
        f"RER = S × CPI1 / CPI2\n"
        f"    = {example['S_country2_per_country1']:,.8g} × {example['cpi_1']:,.8g} / {example['cpi_2']:,.8g}\n"
        f"    = {example['RER']:,.8g}\n\n"
        f"Real Index = 100 × RER / RER*\n"
        f"           = 100 × {example['RER']:,.8g} / {rer_star:,.8g}\n"
        f"           = {example['Real_Index']:,.4f}"
    )
    st.caption(
        "This worked example uses the same CPI and FX inputs that appear in the CSV below. "
        "The cross-rate formula assumes both FX series are local-currency units per USD."
    )
else:
    st.info("A worked example will appear when the selected period contains a month with all CPI and FX inputs.")

display = data[[
    "date", "cpi_1", "cpi_2", "local_per_usd_1", "local_per_usd_2",
    "S_country2_per_country1", "RER", "RER_star", "Real_Index"
]].copy()
display["date"] = display["date"].dt.strftime("%Y-%m")
display = display.rename(columns={
    "date": "month",
    "cpi_1": f"CPI_1_{c1['iso3']}",
    "cpi_2": f"CPI_2_{c2['iso3']}",
    "local_per_usd_1": f"Country1_local_currency_per_USD_{c1['iso3']}",
    "local_per_usd_2": f"Country2_local_currency_per_USD_{c2['iso3']}",
    "S_country2_per_country1": f"Nominal_S_{cur2}_per_{cur1}",
    "RER_star": "RER_star_sample_mean",
    "Real_Index": "Real_Index_sample_mean_100",
})
st.subheader("Observations and calculations")
st.dataframe(display, use_container_width=True, hide_index=True)
csv_bytes = display.to_csv(index=False, na_rep="").encode("utf-8-sig")
filename = f"imf_rer_{c1['iso3']}_{c2['iso3']}_{start_date:%Y%m}_{end_date:%Y%m}.csv"
st.download_button(
    "⬇️ Download all observations and calculations (CSV)",
    data=csv_bytes,
    file_name=filename,
    mime="text/csv",
)
st.caption("Source: IMF SDMX API. Check source-series definitions and coverage before interpreting results.")
