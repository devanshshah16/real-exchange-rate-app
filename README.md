# IMF Real Exchange Rate Explorer

A Streamlit application for an international finance class. It compares monthly CPI-adjusted real exchange rates and nominal exchange rates for two IMF economies, including the Euro area aggregate.

## Requirements

- Python 3.12
- Internet access to the IMF SDMX API
- The three files in this folder: `app.py`, `requirements.txt`, and `README.md`

## Run locally

```bash
python3.12 -m venv .venv
```

Activate the environment:

**macOS / Linux**
```bash
source .venv/bin/activate
```

**Windows PowerShell**
```powershell
.venv\Scripts\Activate.ps1
```

Install dependencies and launch:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run app.py
```

Streamlit will print a local URL, usually `http://localhost:8501`.

## App functionality

- Choose Country 1 and Country 2 from a country list, plus the Euro area (`G163`).
- Requests monthly CPI and exchange-rate series from January 1976 onward, where available.
- Allows selection of start and end months.
- Displays Real, Nominal, or Both chart views. In Both mode, real and nominal series use separate y-axes.
- Recalculates the sample-average real exchange-rate benchmark when the selected dates change.
- Downloads observations and calculations in the selected sample as CSV.

## Definitions and units

The app defines:

- `S(t)`: units of Country 2 currency per one unit of Country 1 currency.
- `P1(t)`: Country 1 monthly CPI.
- `P2(t)`: Country 2 monthly CPI.
- `RER(t) = S(t) × P1(t) / P2(t)`.
- `RER*`: arithmetic mean of valid `RER(t)` observations inside the selected sample.
- `Real Index(t) = 100 × RER(t) / RER*`.

The nominal exchange rate is **not normalized**.

The IMF exchange-rate key is expressed as local-currency units per USD. The app converts it to the requested direction using:

`S(t) = (Country 2 local-currency units per USD) / (Country 1 local-currency units per USD)`

This assumes both local-per-USD observations are available and comparable for that month. CPI values and exchange rates must both be present for a real exchange-rate observation to be calculated. Missing values are left missing rather than interpolated.

## IMF API notes and troubleshooting

The app uses the IMF SDMX 3.0 base endpoint:

`https://api.imf.org/external/sdmx/3.0`

Series keys used:

- CPI: `{ISO3}.CPI._T.IX.M`
- Exchange rate: `{ISO3}.XDC_USD.PA_RT.M`
- Euro area code: `G163`

The IMF API may change dataflow identifiers, response formats, or access behavior. The application tries common IFS dataflow URL patterns and CSV/JSON representations, but the exact endpoint and payload schema should be validated against the current IMF API documentation:

https://data.imf.org/en/Resource-Pages/IMF-API

If the app reports that no usable observations were returned:

1. Check that the series is available for that economy in the IMF data portal.
2. Open the official API documentation and confirm the current dataflow ID and series-key structure.
3. Update the `paths` list in `fetch_imf_series()` if the IMF has changed its dataflow route.
4. Inspect the response format if the IMF has changed the CSV/JSON representation.
5. Try a different country. Data may not be available back to 1976 for every series.

The country selector uses ISO-3166 country codes from `pycountry`, plus the IMF Euro area aggregate. It is a practical selector list, not a guarantee that every selected economy has observations for both requested IMF series.

## Methodological notes

- The sample average `RER*` is computed only from positive, valid RER observations within the selected period.
- The index is 100 on average over the valid RER observations in the selected sample, subject to floating-point rounding.
- Changing the start or end month recalculates `RER*` and the index.
- The CSV contains raw CPI observations, both countries' local-currency-per-USD rates, the converted nominal cross-rate, RER, sample RER mean, and real index.
- The nominal exchange rate is not normalized to 100.
- The application is for educational use and is not a substitute for checking the source series and methodology.

## Files

- `app.py` — Streamlit app, IMF requests, calculations, chart, and CSV export.
- `requirements.txt` — Python dependencies.
- `README.md` — setup and methodology.
