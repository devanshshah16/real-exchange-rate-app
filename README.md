# IMF Real Exchange Rate Explorer

## Run with Python 3.12

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run app.py
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`.

## Data API

The application uses the IMF SDMX 3.0 URL structure:

`https://api.imf.org/external/sdmx/3.0/data/dataflow/{agency}/{dataset}/{version}/{key}`

The CPI request uses agency `IMF.STA`, dataset `CPI`, version `~` (latest), and key `{ISO3}.CPI._T.IX.M`. This follows the IMF API guide's documented example for the CPI dataset.

The exchange-rate series are in the IMF `ER` dataset, which has its own data structure. The app tries candidate keys from the online examples and assignment hints, but the correct ER dimensions should be checked against the current IMF data portal if requests fail. A failed key is not proof that data do not exist.

## Calculations

- `S(t)`: units of Country 2 currency per one unit of Country 1 currency.
- `RER(t) = S(t) × CPI1(t) / CPI2(t)`.
- `RER*`: mean of valid RER observations in the selected date range.
- `Real Index(t) = 100 × RER(t) / RER*`.

Nominal FX is not normalized. In this version, cross-rates are formed from local-currency units per USD, so the correct ER key must return that convention for both economies. Verify the ER observation definition before relying on the result.

## Troubleshooting

If CPI requests fail, check the IMF `CPI` dataflow and country series key. If ER requests fail, inspect the ER dataflow dimensions and valid codelists in the IMF portal; do not assume the CPI key format applies to ER.

Official IMF API information: https://data.imf.org/en/Resource-Pages/IMF-API


## Beginner-friendly UI updates

The interface defaults to United Kingdom as Country 1 and United States as Country 2, so the nominal quote should be USD per GBP when the returned FX series have the stated local-currency-per-USD convention.

The app explains the number of months selected, data coverage, sample-average RER benchmark, and how to interpret the real index. It also displays:
- a plain-English interpretation of the latest point in the chart;
- a check that the real index averages 100 over valid observations;
- one worked RER calculation using the selected sample's CPI and FX inputs;
- a CSV download of the inputs and calculations.

The index-average check should be approximately 100, subject to rounding. The worked calculation is a useful manual verification, but it does not independently validate whether the IMF source series have been mapped to the correct economic definitions. Verify the ER series dimensions and currency conventions against the IMF source.


## Minimal UI behavior

- The app shows only the comparison form until the user clicks **Analyze exchange rates**.
- Results are grouped into collapsible sections: chart, calculation check, source details, data table, and CSV download.
- The chart view can be Overview, Real index, or Nominal rate.
- The date controls default to January 2015 through the current month; change them before analysis as needed.
