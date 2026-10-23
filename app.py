"""
IMF Real Exchange Rate Explorer
Python 3.12 | Streamlit + Plotly

Definition:
    S(t) = units of Country 2 currency per 1 unit of Country 1 currency
    RER(t) = S(t) * CPI1(t) / CPI2(t)
    RER* = arithmetic mean of valid RER observations in selected sample
    Real Index(t) = 100 * RER(t) / RER*
"""
from __future__ import annotations

import io
import json
import re
import time
from datetime import date
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import pycountry
import requests
import streamlit as st
from plotly.subplots import make_subplots

IMF_BASE = "https://api.imf.org/external/sdmx/3.0"
START_DEFAULT = date(1976, 1, 1)
REQUEST_TIMEOUT = 35

st.set_page_config(page_title="IMF Real Exchange Rate Explorer", page_icon="🌐", layout="wide")
st.title("🌐 Real Exchange Rate Explorer")
st.caption("Monthly IMF CPI and exchange-rate data. Select two economies and a sample period to compare real and nominal exchange rates.")

# Euro area is an IMF aggregate; the IMF key uses G163.
EURO_AREA = {"name": "Euro area", "iso3": "G163", "currency": "EUR"}
COUNTRIES: list[dict[str, str]] = []
for c in pycountry.countries:
    if getattr(c, "alpha_3", None):
        COUNTRIES.append({
            "name": c.name,
            "iso3": c.alpha_3,
            "currency": "",
        })
COUNTRIES.append(EURO_AREA)
COUNTRIES.sort(key=lambda x: x["name"].casefold())
COUNTRY_BY_NAME = {c["name"]: c for c in COUNTRIES}
COUNTRY_NAMES = [c["name"] for c in COUNTRIES]

# Common currency codes. For currencies not in this map, the code attempts to use
# pycountry's currency database only as a display hint; the IMF series is the authority.
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

# IMF's public SDMX API has changed response formats/dataflow naming over time.
# Try the likely IFS flow and common API representations, then parse the response
# generically. Errors are surfaced in the UI rather than silently fabricating data.
SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/vnd.sdmx.data+json;version=2.0.0, application/vnd.sdmx.data+csv;version=2.0.0, application/json, text/csv"})


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        x = float(value)
        return x if pd.notna(x) else None
    except (TypeError, ValueError):
        return None


def _period_to_timestamp(value: Any) -> pd.Timestamp | None:
    """Parse common SDMX monthly and daily period strings into month-start timestamps."""
    if value is None:
        return None
    s = str(value).strip()
    for fmt in ("%Y-%m", "%Y-%m-%d", "%Y%m", "%b-%Y", "%Y"):
        try:
            ts = pd.to_datetime(s, format=fmt, errors="raise")
            return pd.Timestamp(year=ts.year, month=ts.month, day=1)
        except Exception:
            pass
    # Common SDMX forms such as 1976-M01 or 1976M01.
    m = re.fullmatch(r"(\d{4})-?M(\d{1,2})", s, re.I)
    if m:
        return pd.Timestamp(int(m.group(1)), int(m.group(2)), 1)
    return None


def _parse_csv_payload(payload: str) -> pd.DataFrame:
    try:
        df = pd.read_csv(io.StringIO(payload))
    except Exception:
        return pd.DataFrame(columns=["date", "value"])
    if df.empty:
        return pd.DataFrame(columns=["date", "value"])
    date_col = next((c for c in df.columns if str(c).strip().lower() in {"time_period", "time", "period", "date", "observation_period"}), None)
    value_col = next((c for c in df.columns if str(c).strip().lower() in {"obs_value", "value", "observation_value", "obsvalue"}), None)
    if date_col is None:
        date_col = next((c for c in df.columns if "time" in str(c).lower() or "period" in str(c).lower()), None)
    if value_col is None:
        value_col = next((c for c in df.columns if "obs_value" in str(c).lower() or str(c).lower() == "value"), None)
    if date_col is None or value_col is None:
        return pd.DataFrame(columns=["date", "value"])
    out = pd.DataFrame({
        "date": df[date_col].map(_period_to_timestamp),
        "value": df[value_col].map(_as_float),
    }).dropna()
    return out


def _parse_sdmx_json(payload: dict[str, Any]) -> pd.DataFrame:
    """Parse SDMX-JSON 2.0 observation arrays and a few common nested variants."""
    data = payload.get("data", payload)
    if isinstance(data, dict) and "dataSets" in data:
        datasets = data.get("dataSets") or []
        structure = data.get("structure", {}) or {}
        dimensions = structure.get("dimensions", {}) or {}
        obs_dims = dimensions.get("observation", []) or []
        time_values: list[str] = []
        for dim in obs_dims:
            if str(dim.get("id", "")).upper() in {"TIME_PERIOD", "TIME", "PERIOD"}:
                time_values = [str(v.get("id", v.get("name", ""))) for v in dim.get("values", [])]
                break
        observations: dict[str, Any] = {}
        if datasets:
            observations = datasets[0].get("observations", {}) or {}
        rows = []
        for key, item in observations.items():
            # Observation keys index the observation dimensions; the time dimension is normally first.
            try:
                idx = int(str(key).split(":")[0])
                period = time_values[idx] if idx < len(time_values) else None
                val = item[0] if isinstance(item, (list, tuple)) and item else item
                rows.append((_period_to_timestamp(period), _as_float(val)))
            except Exception:
                continue
        if rows:
            return pd.DataFrame(rows, columns=["date", "value"]).dropna()

    # Some API deployments return a series-oriented or list-oriented JSON payload.
    if isinstance(data, dict):
        for key in ("observations", "values", "data"):
            obj = data.get(key)
            if isinstance(obj, list):
                rows = []
                for item in obj:
                    if not isinstance(item, dict):
                        continue
                    period = item.get("TIME_PERIOD", item.get("time", item.get("period", item.get("date"))))
                    val = item.get("OBS_VALUE", item.get("value", item.get("obs_value")))
                    rows.append((_period_to_timestamp(period), _as_float(val)))
                parsed = pd.DataFrame(rows, columns=["date", "value"]).dropna()
                if not parsed.empty:
                    return parsed
    return pd.DataFrame(columns=["date", "value"])


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def fetch_imf_series(iso3: str, series_type: str) -> tuple[pd.DataFrame, str]:
    """
    Fetch a monthly IMF series.

    CPI key: {ISO3}.CPI._T.IX.M
    FX key:  {ISO3}.XDC_USD.PA_RT.M (local currency units per USD)

    The IMF API's dataflow ID may vary by portal release, so try common IFS names.
    The request range begins in 1976, but the API may only return years for which
    observations exist.
    """
    if series_type == "CPI":
        key = f"{iso3}.CPI._T.IX.M"
    elif series_type == "FX":
        key = f"{iso3}.XDC_USD.PA_RT.M"
    else:
        raise ValueError("Unknown series type")

    errors = []
    # IFS is the commonly used IMF International Financial Statistics dataflow.
    paths = [
        f"/data/dataflow/IMF/IFS/1.0/{key}",
        f"/data/dataflow/IMF/IFS/+/ {key}".replace(" ", ""),
        f"/data/IMF/IFS/{key}",
    ]
    for path in paths:
        url = IMF_BASE + path
        params = {"startPeriod": "1976-01", "endPeriod": date.today().strftime("%Y-%m")}
        for fmt in ("csvfile", "sdmx-json", None):
            p = dict(params)
            if fmt:
                p["format"] = fmt
            try:
                response = SESSION.get(url, params=p, timeout=REQUEST_TIMEOUT)
                if response.status_code == 429:
                    time.sleep(1.5)
                    response = SESSION.get(url, params=p, timeout=REQUEST_TIMEOUT)
                if response.status_code >= 400:
                    errors.append(f"{response.status_code} at {response.url}")
                    continue
                content_type = response.headers.get("content-type", "").lower()
                text = response.text.strip()
                if not text:
                    errors.append(f"Empty response at {response.url}")
                    continue
                parsed = pd.DataFrame(columns=["date", "value"])
                if "json" in content_type or text.startswith("{") or text.startswith("["):
                    try:
                        parsed = _parse_sdmx_json(response.json())
                    except Exception:
                        parsed = pd.DataFrame(columns=["date", "value"])
                if parsed.empty:
                    parsed = _parse_csv_payload(text)
                if not parsed.empty:
                    parsed = parsed.dropna().drop_duplicates("date").sort_values("date")
                    parsed = parsed.loc[parsed["value"] > 0].reset_index(drop=True)
                    if not parsed.empty:
                        return parsed, f"IMF API ({path}, {fmt or 'default format'})"
                errors.append(f"Could not identify observations at {response.url}")
            except requests.RequestException as exc:
                errors.append(f"{url}: {exc}")
    details = "\n".join(f"• {e}" for e in errors[-6:])
    raise RuntimeError(
        f"No usable {series_type} observations were returned for {iso3}. "
        "The IMF may not publish this series for this economy, or the API endpoint/dataflow may have changed.\n"
        f"{details}\n\nCheck the IMF Data API documentation or try another country."
    )


def currency_code(country: dict[str, str]) -> str:
    return country.get("currency") or CURRENCY_BY_COUNTRY.get(country["iso3"], country["iso3"])


def load_country_data(country: dict[str, str]) -> tuple[pd.DataFrame, list[str]]:
    iso3 = country["iso3"]
    cpi, cpi_source = fetch_imf_series(iso3, "CPI")
    fx, fx_source = fetch_imf_series(iso3, "FX")
    cpi = cpi.rename(columns={"value": "cpi"})
    fx = fx.rename(columns={"value": "local_per_usd"})
    merged = pd.merge(cpi, fx, on="date", how="outer").sort_values("date")
    return merged, [cpi_source, fx_source]


def build_sample(c1: dict[str, str], c2: dict[str, str], start: date, end: date) -> tuple[pd.DataFrame, list[str]]:
    df1, src1 = load_country_data(c1)
    df2, src2 = load_country_data(c2)
    df1 = df1.rename(columns={"cpi": "cpi_1", "local_per_usd": "local_per_usd_1"})
    df2 = df2.rename(columns={"cpi": "cpi_2", "local_per_usd": "local_per_usd_2"})
    data = pd.merge(df1[["date", "cpi_1", "local_per_usd_1"]],
                    df2[["date", "cpi_2", "local_per_usd_2"]], on="date", how="outer")
    start_ts = pd.Timestamp(start.year, start.month, 1)
    end_ts = pd.Timestamp(end.year, end.month, 1)
    data = data.loc[data["date"].between(start_ts, end_ts)].sort_values("date").copy()

    # FX source is local-currency units per USD. Therefore:
    # (currency 2 / USD) / (currency 1 / USD) = currency 2 per currency 1.
    # If Country 1 is the Euro area, use the IMF aggregate's FX series when available.
    data["S_country2_per_country1"] = data["local_per_usd_2"] / data["local_per_usd_1"]
    data.loc[data["local_per_usd_1"].isna() | data["local_per_usd_2"].isna(), "S_country2_per_country1"] = float("nan")
    data["RER"] = data["S_country2_per_country1"] * data["cpi_1"] / data["cpi_2"]
    valid_rer = data["RER"].replace([float("inf"), -float("inf")], float("nan")).dropna()
    valid_rer = valid_rer[valid_rer > 0]
    rer_star = valid_rer.mean() if not valid_rer.empty else float("nan")
    data["RER_star"] = rer_star
    data["Real_Index"] = 100 * data["RER"] / rer_star if pd.notna(rer_star) and rer_star != 0 else float("nan")
    data["Country_1"] = c1["name"]
    data["Country_2"] = c2["name"]
    data["Currency_1"] = currency_code(c1)
    data["Currency_2"] = currency_code(c2)
    data["Sample_RER_Mean"] = rer_star
    return data, src1 + src2


with st.sidebar:
    st.header("Comparison settings")
    default_1 = COUNTRY_NAMES.index("United States") if "United States" in COUNTRY_NAMES else 0
    default_2 = COUNTRY_NAMES.index("India") if "India" in COUNTRY_NAMES else min(1, len(COUNTRY_NAMES)-1)
    country_1_name = st.selectbox("Country 1 (numerator CPI)", COUNTRY_NAMES, index=default_1)
    options_2 = [n for n in COUNTRY_NAMES if n != country_1_name]
    default_2_name = "India" if country_1_name != "India" and "India" in options_2 else options_2[0]
    country_2_name = st.selectbox("Country 2 (denominator CPI)", options_2, index=options_2.index(default_2_name))
    today = date.today()
    start_date = st.date_input("Start month", value=date(1976, 1, 1), min_value=date(1976, 1, 1), max_value=today)
    end_date = st.date_input("End month", value=date(today.year, today.month, 1), min_value=date(1976, 1, 1), max_value=today)
    chart_mode = st.radio("Chart view", ["Both", "Real", "Nominal"], index=0)
    st.caption("Dates are interpreted as months; the app uses the year and month selected.")

if start_date > end_date:
    st.error("The start month must be on or before the end month.")
    st.stop()

c1 = COUNTRY_BY_NAME[country_1_name]
c2 = COUNTRY_BY_NAME[country_2_name]
if c1["iso3"] == c2["iso3"]:
    st.warning("Choose two different economies.")
    st.stop()

with st.spinner("Retrieving monthly CPI and exchange-rate series from the IMF…"):
    try:
        data, sources = build_sample(c1, c2, start_date, end_date)
    except Exception as exc:
        st.error("Could not build the comparison from the current IMF API response.")
        st.code(str(exc))
        st.markdown("You can check the official urlIMF Data API documentationhttps://data.imf.org/en/Resource-Pages/IMF-API. The app intentionally reports missing coverage instead of inventing values.")
        st.stop()

if data.empty:
    st.warning("There are no observations in the selected period.")
    st.stop()

valid_nominal = data["S_country2_per_country1"].notna().sum()
valid_real = data["Real_Index"].notna().sum()
rer_star = data["Sample_RER_Mean"].iloc[0] if len(data) else float("nan")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Months in sample", f"{len(data):,}")
m2.metric("Valid nominal FX months", f"{valid_nominal:,}")
m3.metric("Valid real-index months", f"{valid_real:,}")
m4.metric("Sample mean RER (RER*)", f"{rer_star:,.4g}" if pd.notna(rer_star) else "Unavailable")

cur1, cur2 = currency_code(c1), currency_code(c2)
st.subheader(f"{c1['name']} → {c2['name']}")
st.markdown(
    f"**Nominal exchange rate:** units of {cur2} per 1 {cur1}. "
    "The nominal series is shown in actual currency units and is not normalized."
)
st.markdown(
    "**Real index:** `100 × RER(t) / RER*`, where `RER*` is the arithmetic mean of valid RER observations "
    "within the selected sample. Changing the dates recalculates this benchmark."
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

with st.expander("Data coverage and source details"):
    st.write("Series are requested from January 1976 onward, subject to IMF availability.")
    st.write("Country 1 data sources:", *src1 if False else [])
    for source in sources:
        st.write(f"- {source}")
    st.write("A missing CPI or exchange-rate observation makes the relevant calculated value unavailable for that month.")
    st.write("The nominal exchange rate is calculated as (Country 2 local currency units per USD) divided by (Country 1 local currency units per USD).")
    st.write("This cross-rate conversion is valid only where both IMF local-currency-per-USD observations are available and comparable.")

st.subheader("Observations and calculations")
display_cols = [
    "date", "cpi_1", "cpi_2", "local_per_usd_1", "local_per_usd_2",
    "S_country2_per_country1", "RER", "RER_star", "Real_Index",
]
display = data[display_cols].copy()
display["date"] = display["date"].dt.strftime("%Y-%m")
display = display.rename(columns={
    "date": "month",
    "cpi_1": f"CPI_1_{c1['iso3']}",
    "cpi_2": f"CPI_2_{c2['iso3']}",
    "local_per_usd_1": f"Country1_local_currency_per_USD_{c1['iso3']}",
    "local_per_usd_2": f"Country2_local_currency_per_USD_{c2['iso3']}",
    "S_country2_per_country1": f"Nominal_S_{cur2}_per_{cur1}",
    "RER": "RER",
    "RER_star": "RER_star_sample_mean",
    "Real_Index": "Real_Index_sample_mean_100",
})
st.dataframe(display, use_container_width=True, hide_index=True)

csv_bytes = display.to_csv(index=False, na_rep="").encode("utf-8-sig")
filename = f"imf_rer_{c1['iso3']}_{c2['iso3']}_{start_date:%Y%m}_{end_date:%Y%m}.csv"
st.download_button(
    label="⬇️ Download all observations and calculations (CSV)",
    data=csv_bytes,
    file_name=filename,
    mime="text/csv",
    use_container_width=False,
)

st.caption("Data: IMF SDMX API. Results depend on IMF series coverage, revisions, and API availability.")
