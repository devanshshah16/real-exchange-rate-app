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

st.set_page_config(page_title="Exchange Rate Explorer", page_icon="↗", layout="wide")
st.markdown(
    """
    <style>
      .block-container {max-width: 1180px; padding-top: 2rem; padding-bottom: 3rem;}
      h1, h2, h3 {letter-spacing: -0.035em;}
      div[data-testid="stMetric"] {background: rgba(128,128,128,.07); border: 1px solid rgba(128,128,128,.16); padding: 14px 16px; border-radius: 12px;}
      div[data-testid="stForm"] {border: 1px solid rgba(128,128,128,.20); padding: 18px 20px; border-radius: 14px;}
      div[data-testid="stExpander"] {border: 1px solid rgba(128,128,128,.16); border-radius: 12px;}
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("Exchange Rate Explorer")
st.caption("Understand how currencies move — and how inflation changes the picture.")
with st.expander("How this works", expanded=False):
    st.markdown(
        """
        - **Nominal rate:** how much Country 2 currency buys one unit of Country 1 currency.
        - **Real index:** the nominal rate adjusted using both countries' CPI.
        - **100 is the sample average:** above 100 is above the selected-period average; below 100 is below it.
        - Changing the dates recalculates the sample average. Missing observations are not filled in.

        **Example:** UK as Country 1 and US as Country 2 means the nominal rate is USD per GBP.
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


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def _natural_earth_geojson() -> dict | None:
    """Load simplified public country outlines once per day for decorative use."""
    url = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


def _country_outline_svg(iso3: str) -> str | None:
    """Create a faint SVG outline for a country; no effect on calculations."""
    if iso3 == "G163":  # Euro area is a currency area, not one country polygon.
        return None
    geo = _natural_earth_geojson()
    if not geo:
        return None

    feature = None
    for item in geo.get("features", []):
        props = item.get("properties", {})
        codes = {
            str(props.get("ADM0_A3", "")), str(props.get("ISO_A3", "")),
            str(props.get("ISO_A3_EH", "")), str(props.get("SOV_A3", "")),
        }
        if iso3 in codes:
            feature = item
            break
    if feature is None:
        return None

    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []
    polygons = coords if geom.get("type") == "MultiPolygon" else [coords]
    rings, points = [], []
    for polygon in polygons:
        if not polygon:
            continue
        ring = polygon[0]
        if len(ring) >= 3:
            rings.append(ring)
            points.extend(ring)
    if not points:
        return None

    min_lon, max_lon = min(p[0] for p in points), max(p[0] for p in points)
    min_lat, max_lat = min(p[1] for p in points), max(p[1] for p in points)
    lon_span, lat_span = max_lon - min_lon, max_lat - min_lat
    if lon_span <= 0 or lat_span <= 0:
        return None

    vw, vh, pad = 320, 220, 10
    scale = min((vw - 2 * pad) / lon_span, (vh - 2 * pad) / lat_span)
    ox = (vw - lon_span * scale) / 2
    oy = (vh - lat_span * scale) / 2
    path_parts = []
    for ring in rings:
        commands = []
        for idx, (lon, lat, *_) in enumerate(ring):
            x = ox + (lon - min_lon) * scale
            y = oy + (max_lat - lat) * scale
            commands.append(("M" if idx == 0 else "L") + f"{x:.1f},{y:.1f}")
        commands.append("Z")
        path_parts.append(" ".join(commands))
    path_data = " ".join(path_parts)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 220" preserveAspectRatio="xMidYMid meet">'
        f'<path d="{path_data}" fill="currentColor" fill-opacity="0.025" '
        'stroke="currentColor" stroke-opacity="0.105" stroke-width="1.5" '
        'stroke-linejoin="round" fill-rule="evenodd"/></svg>'
    )


def show_country_silhouettes(country1: dict[str, str], country2: dict[str, str]) -> None:
    """Display selected-country silhouettes subtly, after the user submits the form."""
    import base64

    def background(svg: str | None) -> str:
        if not svg:
            return "none"
        encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
        return f"url('data:image/svg+xml;base64,{encoded}')"

    svg1 = _country_outline_svg(country1["iso3"])
    svg2 = _country_outline_svg(country2["iso3"])
    if not svg1 and not svg2:
        st.caption("Country outlines are temporarily unavailable; the analysis is unaffected.")
        return

    st.markdown(
        f"""
        <style>
          .country-silhouette-pair {{
            display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr);
            gap:1rem; margin:.35rem 0 1rem; pointer-events:none;
          }}
          .country-silhouette-item {{ min-width:0; position:relative; overflow:hidden;
            border-radius:12px; min-height:112px; display:flex; align-items:flex-end;
            justify-content:center; border:1px solid rgba(128,128,128,.08); }}
          .country-silhouette-art {{ position:absolute; inset:0; background-repeat:no-repeat;
            background-position:center; background-size:contain; opacity:1; }}
          .country-silhouette-name {{ position:relative; z-index:1; font-size:.72rem;
            letter-spacing:.09em; text-transform:uppercase; opacity:.58;
            padding:.45rem .7rem; text-align:center; }}
          @media (max-width:640px) {{ .country-silhouette-item {{min-height:82px;}} }}
        </style>
        <div class="country-silhouette-pair" aria-hidden="true">
          <div class="country-silhouette-item"><div class="country-silhouette-art" style="background-image:{background(svg1)}"></div><div class="country-silhouette-name">{country1['name']}</div></div>
          <div class="country-silhouette-item"><div class="country-silhouette-art" style="background-image:{background(svg2)}"></div><div class="country-silhouette-name">{country2['name']}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


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


st.subheader("Build a comparison")
with st.form("comparison_form", clear_on_submit=False):
    col1, col2 = st.columns(2)
    with col1:
        c1_name = st.selectbox(
            "Country 1", COUNTRY_NAMES, index=None,
            placeholder="Choose the first country",
            help="The CPI for this country is P1.",
        )
    with col2:
        # Keep Country 2's options stable inside the form. Streamlit forms only
        # rerun after submission, so dynamically changing this list based on
        # Country 1 can reset Country 2 on the first submit.
        c2_name = st.selectbox(
            "Country 2", COUNTRY_NAMES, index=None,
            placeholder="Choose the second country",
            help="The exchange rate is quoted as Country 2 currency per 1 unit of Country 1 currency.",
        )
    date1, date2 = st.columns(2)
    with date1:
        start_date = st.date_input(
            "Start month", value=date(2015, 1, 1),
            min_value=date(1976, 1, 1), max_value=date.today(),
            help="First month to include.",
        )
    with date2:
        end_date = st.date_input(
            "End month", value=date.today().replace(day=1),
            min_value=date(1976, 1, 1), max_value=date.today(),
            help="Last month to include.",
        )
    submitted = st.form_submit_button("Analyze exchange rates", type="primary", use_container_width=True)

if not submitted:
    st.info("Choose both countries and the date range, then select **Analyze exchange rates**. Results will appear here.")
    st.stop()
if not c1_name or not c2_name:
    st.warning("Please choose both countries.")
    st.stop()
if c1_name == c2_name:
    st.warning("Please choose two different countries.")
    st.stop()
if start_date > end_date:
    st.warning("The start month must be on or before the end month.")
    st.stop()

chart_mode = st.segmented_control(
    "Chart view", ["Overview", "Real index", "Nominal rate"],
    default="Overview",
    help="Overview shows both series using separate axes.",
) or "Overview"
c1, c2 = COUNTRY_BY_NAME[c1_name], COUNTRY_BY_NAME[c2_name]
show_country_silhouettes(c1, c2)

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
valid_nominal_count = int(data["S_country2_per_country1"].notna().sum())
valid_real_count = int(data["Real_Index"].notna().sum())
total_months = int(len(data))
index_mean = data["Real_Index"].dropna().mean() if valid_real_count else float("nan")

st.divider()
st.subheader(f"{c1['name']} → {c2['name']}")
st.caption(f"{start_date:%b %Y} – {end_date:%b %Y}  ·  Quote: {cur2} per {cur1}  ·  Monthly observations")

m1, m2, m3 = st.columns(3)
m1.metric("Months analyzed", f"{total_months:,}")
m2.metric("FX observations", f"{valid_nominal_count:,}", help="Months with both countries' FX observations.")
m3.metric("Real-index observations", f"{valid_real_count:,}", help="Months with the CPI and FX inputs needed for RER.")

with st.expander("What do these numbers mean?", expanded=False):
    st.markdown(
        f"- **Months analyzed — {total_months:,}:** calendar months in your chosen date range.\n"
        f"- **FX observations — {valid_nominal_count:,}:** months where both countries have exchange-rate observations.\n"
        f"- **Real-index observations — {valid_real_count:,}:** months with the CPI and FX inputs needed to calculate RER."
    )
    st.caption("Missing observations are left blank rather than estimated or filled in.")

if pd.notna(rer_star):
    st.metric("Selected-period RER benchmark", f"{rer_star:,.5g}")
    with st.expander("What is the RER benchmark?", expanded=False):
        st.write(
            f"The benchmark is the average of the {valid_real_count:,} valid RER observations in this date range. "
            "It sets the real index's average to 100. Changing the dates recalculates the benchmark."
        )
else:
    st.warning("The real-exchange-rate benchmark is unavailable because this sample has no valid RER observations.")

with st.expander("Exchange-rate chart", expanded=True):
    st.caption("The real index adjusts the exchange rate for CPI. The nominal rate stays in actual currency units.")
    if chart_mode == "Overview":
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Scatter(x=data["date"], y=data["Real_Index"], name="Real index", mode="lines"), secondary_y=False)
        fig.add_trace(go.Scatter(x=data["date"], y=data["S_country2_per_country1"], name=f"Nominal rate ({cur2}/{cur1})", mode="lines"), secondary_y=True)
        fig.update_yaxes(title_text="Real index (average = 100)", secondary_y=False)
        fig.update_yaxes(title_text=f"{cur2} per {cur1}", secondary_y=True)
    elif chart_mode == "Real index":
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=data["date"], y=data["Real_Index"], name="Real index", mode="lines"))
        fig.update_yaxes(title_text="Real index (average = 100)")
    else:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=data["date"], y=data["S_country2_per_country1"], name=f"Nominal rate ({cur2}/{cur1})", mode="lines"))
        fig.update_yaxes(title_text=f"{cur2} per {cur1}")
    fig.update_layout(title=None, xaxis_title=None, hovermode="x unified", legend_title=None, margin=dict(l=12, r=12, t=20, b=10), height=420)
    st.plotly_chart(fig, use_container_width=True)

    if chart_mode in ("Overview", "Real index") and valid_real_count:
        latest_real = data.dropna(subset=["Real_Index"]).iloc[-1]
        val = float(latest_real["Real_Index"])
        month = latest_real["date"].strftime("%b %Y")
        relation = "above" if val > 100 else "below" if val < 100 else "at"
        st.markdown(f"**In plain English:** The latest real index is **{val:.1f}** ({month}), {relation} the selected-period average of 100.")
    if chart_mode in ("Overview", "Nominal rate") and valid_nominal_count:
        latest_fx = data.dropna(subset=["S_country2_per_country1"]).iloc[-1]
        st.markdown(
            f"**Latest nominal rate:** In {latest_fx['date']:%b %Y}, 1 {cur1} was approximately "
            f"**{float(latest_fx['S_country2_per_country1']):,.4g} {cur2}** in the source data."
        )

with st.expander("Calculation check", expanded=False):
    if valid_real_count and pd.notna(index_mean):
        if abs(index_mean - 100) < 0.05:
            st.success(f"Check passed: the real index averages {index_mean:.2f} across valid months (target: 100).")
        else:
            st.warning(f"The real index averages {index_mean:.2f}, not 100. Review the data and calculations.")
    else:
        st.info("This check needs at least one valid real-index observation.")
    example_rows = data.dropna(subset=["cpi_1", "cpi_2", "local_per_usd_1", "local_per_usd_2", "S_country2_per_country1", "RER", "Real_Index"])
    if not example_rows.empty:
        ex = example_rows.iloc[0]
        st.markdown(f"**Worked example: {ex['date']:%B %Y}**")
        st.code(
            f"S = Country 2 FX / Country 1 FX\n"
            f"  = {ex['local_per_usd_2']:,.8g} / {ex['local_per_usd_1']:,.8g}\n"
            f"  = {ex['S_country2_per_country1']:,.8g} {cur2} per {cur1}\n\n"
            f"RER = S × CPI1 / CPI2\n"
            f"    = {ex['S_country2_per_country1']:,.8g} × {ex['cpi_1']:,.8g} / {ex['cpi_2']:,.8g}\n"
            f"    = {ex['RER']:,.8g}\n\n"
            f"Real index = 100 × RER / RER*\n"
            f"           = 100 × {ex['RER']:,.8g} / {rer_star:,.8g}\n"
            f"           = {ex['Real_Index']:,.4f}"
        )
        st.caption("Compare these inputs with the same month's row in the CSV.")
    else:
        st.info("A worked example appears when the selected period contains all required CPI and FX inputs.")

with st.expander("Data coverage and source details", expanded=False):
    st.write("Data are requested from January 1976 onward, subject to IMF series availability.")
    for source in sources:
        st.write(f"- `{source}`")
    st.write(
        "The nominal cross-rate is calculated as Country 2 local-currency units per USD divided by "
        "Country 1 local-currency units per USD. This is valid only if both source series use that convention."
    )
    st.warning("A successful API response does not by itself confirm the economic definition of the series. Verify source definitions and units before using results in coursework.")

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

with st.expander("View data table", expanded=False):
    st.caption("Raw observations and calculations for the selected sample.")
    st.dataframe(display, use_container_width=True, hide_index=True)

with st.expander("Download data", expanded=True):
    st.write("Download the full selected sample, including source inputs and calculations.")
    csv_bytes = display.to_csv(index=False, na_rep="").encode("utf-8-sig")
    filename = f"imf_rer_{c1['iso3']}_{c2['iso3']}_{start_date:%Y%m}_{end_date:%Y%m}.csv"
    st.download_button("Download CSV", data=csv_bytes, file_name=filename, mime="text/csv", use_container_width=True)

st.caption("IMF coverage varies by country and period. Missing values are not interpolated.")
