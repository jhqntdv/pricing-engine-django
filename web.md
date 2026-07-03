# Django MVP: Pricing Engine Web Calculator — Integration Specification

This document provides the complete technical specification for integrating the quantitative pricing engine into the existing **`bloombgg`** Django MVP template. It is designed so that a developer can implement the full integration without needing to understand the underlying financial mathematics.

---

## 0. Required Steps to Plugin the Pricing Engine

To successfully plugin the pricing engine into the Django app, follow these steps before starting the development server:

1. **Copy Engine Components**: Copy the `kernel/`, `utils/`, and `data/` directories from the pricing engine repository directly into the Django project root (at the same level as `manage.py`).
2. **Add Engine Dependencies**: The pricing engine requires mathematical libraries that are not in the default Django template. Add them to your Django project's environment by running:
   ```bash
   uv add numpy pandas scipy
   ```
3. **Sync Environment**: Ensure your `uv` virtual environment is up to date:
   ```bash
   uv sync
   ```
4. **Implement Integration Logic**: Update `core/views.py` and create `core/services.py` to handle the pricing logic (detailed in Section 3).
5. **Run Migrations**: Initialize Django's SQLite database (required for session storage and admin):
   ```bash
   python manage.py migrate
   ```

---

## 1. Existing Architecture Summary

### 1.1. The `bloombgg` Django Project
- **Django 6.0** project using `uv` for dependency management.
- Project config lives in `bloombgg/config/` (settings, urls, wsgi).
- Single app: `bloombgg/core/` with views, urls, and templates.
- **Session-based auth** using signed cookies (password: `1995`). Calculator pages are gated behind authentication.
- **Static files** served by WhiteNoise middleware.
- **Deployment**: Docker → Cloud Run via `cloudbuild.yaml`. Container specs: 2 vCPU, 4 GiB RAM.
- **No database needed**: Sessions use `signed_cookies` engine. The app is fully stateless.

### 1.2. Existing Routes & Templates
| Route | View Function | Template | Purpose |
|:---|:---|:---|:---|
| `/` | `index` | `index.html` | Landing / docs page |
| `/login/` | `login_view` | `calculator_auth.html` | Auth gate |
| `/logout/` | `logout_view` | — | Session flush |
| `/options/` | `options_calculator` | `options.html` | Vanilla options calc |
| `/exotics/` | `exotics_calculator` | `exotics.html` | Exotic options calc |
| `/elns/` | `elns_calculator` | `elns.html` | ELN (autocall) calc |

### 1.3. The Pricing Engine Integration
For this integration, we assume the pricing engine source code has been copied directly into the `bloombgg` Django project directory (at the same level as `manage.py`). Here is the expected folder structure outlining the integration:

```text
bloombgg/
├── .venv/                      # uv virtual environment (contains django + numpy/scipy/pandas)
├── pyproject.toml              # Django app dependencies + engine dependencies
├── manage.py                   # Django CLI entry point
├── config/                     # Django project settings
├── core/                       # Django main app
│   ├── templates/core/         # HTML template files (options.html, exotics.html, etc.)
│   ├── services.py             # [NEW] Orchestration & Market caching logic
│   └── views.py                # [MODIFIED] API endpoints calling the engine
├── kernel/                     # [PLUGIN] Core pricing engine logic
├── utils/                      # [PLUGIN] Engine settings & results dataclasses
└── data/                       # [PLUGIN] Static CSV market data
```

**Why this is optimal:**
1. **No PYTHONPATH hacks:** Django automatically adds the `manage.py` directory to the Python path, so `from kernel...` imports work natively.
2. **Data Loader Paths:** The `MarketDataLoader` dynamically resolves paths three levels up from its location (`kernel/market_data/data_loader.py`). This perfectly resolves to the `bloombgg/data/` folder without needing to rewrite engine internals.
3. **No Dockerfile COPY changes:** The standard `COPY . /app/` will copy the Django app, the engine, and the CSV data all at once.

---

## 2. Scope of MVP

### In Scope
- Wire up the 3 existing calculator pages to actually call the pricing engine and display numeric results.
- Support only: **Vanilla** (European Call/Put), **Exotic** (Barrier + American), **ELN** (Phoenix + Eagle autocall).
- Support only: **SPX** and **AAPL** underlyings.
- Model choice: **Black-Scholes** or **Heston**.
- Fixed simulation: **10,000 paths**, **252 steps**.
- Always compute **Greeks** (delta, gamma, vega, theta, rho).
- Output: Price, Greeks, 95% CI bounds, and (for ELN solving mode) implied yield.
- Frontend blocking/spinner to prevent duplicate submissions.

### Out of Scope
- No charting / visualization. Results are numeric only.
- No documentation page integration (the `docs/` folder is still WIP).
- No Celery / task queue / async workers.
- No database.

---

## 3. Implementation Plan

### 3.1. Backend: New API Endpoint

#### File Changes
| File | Action |
|:---|:---|
| `bloombgg/core/services.py` | **[NEW]** Market caching singleton + pricing orchestration logic |
| `bloombgg/core/views.py` | **[MODIFY]** Add `api_calculate` view |
| `bloombgg/core/urls.py` | **[MODIFY]** Add `path('api/calculate/', views.api_calculate, name='api_calculate')` |

#### Market Data Caching (`services.py`)

The `Market` object initialization reads CSVs and calibrates vol surfaces (~1-3 seconds). It must be cached in memory.

```python
# bloombgg/core/services.py
import copy
from kernel.market_data.market import Market
from kernel.market_data.data_loader import MarketDataLoader
from kernel.market_data.volatility_surface.enums_volatility import VolatilitySurfaceType
from kernel.market_data.rate_curve_data.enums_interpolators import InterpolationType
from kernel.tools import CalendarConvention, ObservationFrequency, RateCurveType, Model
from kernel.models.pricing_engines.enum_pricing_engine import PricingEngineType
from kernel.pricing_launcher import PricingLauncher
from utils.pricing_settings import PricingSettings

# Product imports
from kernel.products.options.vanilla_options import EuropeanCallOption, EuropeanPutOption
from kernel.products.options.barrier_options import DownAndInCallOption, UpAndOutPutOption
from kernel.products.options.american_options import AmericanCallOption, AmericanPutOption
from kernel.products.structured_products.autocall_products import Phoenix, Eagle

_MARKET_CACHE = {}

def get_market(underlying_name: str, vol_surface_type: VolatilitySurfaceType) -> Market:
    """Get or create a cached Market instance.
    
    Cache key includes vol_surface_type because the Market calibrates
    the surface on init, so SVI vs SSVI produce different objects.
    """
    cache_key = (underlying_name, vol_surface_type.name)
    if cache_key not in _MARKET_CACHE:
        loader = MarketDataLoader()
        _MARKET_CACHE[cache_key] = Market(
            underlying_name=underlying_name,
            yield_curve_data=loader.get_yield_curve(RateCurveType.RF_US_TREASURY.value),
            underlying_data=loader.get_underlying_info(underlying_name),
            option_data=loader.get_option_data(underlying_name),
            rate_curve_type=RateCurveType.RF_US_TREASURY,
            interpolation_type=InterpolationType.CUBIC,
            volatility_surface_type=vol_surface_type,
            calendar_convention=CalendarConvention.ACT_360,
            obs_frequency=ObservationFrequency.ANNUAL,
        )
    return _MARKET_CACHE[cache_key]
```

> **NOTE on cache key**: The `Market` object bakes in the `VolatilitySurfaceType` at init time. If the user picks SVI for vanilla options but SSVI for ELNs, these are different `Market` instances. The cache must key on `(underlying, vol_surface)` — not just `underlying`.

#### Pricing Orchestration

```python
def run_pricing(payload: dict) -> dict:
    """Map a frontend JSON payload to engine calls and return results."""
    # 1. Determine vol surface
    vol_surface_str = payload.get("volatility_surface", "SVI")
    vol_surface = VolatilitySurfaceType[vol_surface_str]   # SVI, SSVI, or LOCAL
    
    # 2. Get cached market
    underlying = payload["underlying"]  # "SPX" or "AAPL"
    market = get_market(underlying, vol_surface)
    
    # 3. Build PricingSettings
    settings = PricingSettings()
    settings.underlying_name = underlying
    settings.rate_curve_type = RateCurveType.RF_US_TREASURY
    settings.interpolation_type = InterpolationType.CUBIC
    settings.volatility_surface_type = vol_surface
    settings.day_count_convention = CalendarConvention.ACT_360
    settings.obs_frequency = ObservationFrequency.ANNUAL
    settings.model = Model[payload["model"]]  # BLACK_SCHOLES or HESTON
    settings.nb_paths = 10000
    settings.nb_steps = 252
    settings.compute_greeks = True
    
    # 4. Determine engine type and build product
    category = payload["category"]    # "vanilla", "exotic", "eln"
    product_type = payload["product_type"]
    
    # --- See Section 4 for the full product_type → class + engine mapping ---
    
    # 5. Execute
    launcher = PricingLauncher(pricing_settings=settings, market=market)
    product = build_product(payload)   # instantiate the correct product class
    result = launcher.calculate(copy.deepcopy(product))
    
    # 6. Serialize PricingResults
    return {
        "price": round(result.price, 4) if result.price is not None else None,
        "coupon_callable": round(result.coupon_callable, 4) if result.coupon_callable is not None else None,
        "greeks": {k: round(v, 6) for k, v in result.greeks.items()} if result.greeks else None,
        "ci_lower": round(result.lower_bound, 4) if result.lower_bound is not None else None,
        "ci_upper": round(result.upper_bound, 4) if result.upper_bound is not None else None,
        "std_dev": round(result.std_dev, 6) if result.std_dev is not None else None,
    }
```

> **IMPORTANT — `copy.deepcopy(product)`**: The README warns that `PricingLauncher.calculate()` mutates the `derivative` object (sets `initial_spot`, `price`, etc.). Always pass a deepcopy.

### 3.2. Frontend: Overwrite the 3 Calculator Templates

We will **directly overwrite** the existing template files:
- `bloombgg/core/templates/core/options.html`
- `bloombgg/core/templates/core/exotics.html`
- `bloombgg/core/templates/core/elns.html`

All templates will:
1. Extend `core/base.html` (inheriting the terminal aesthetic, nav, clock, auth state).
2. Use `<select>` dropdowns (not free-text inputs) for Product Type, Underlying, Model, Vol Surface.
3. Use JavaScript to show/hide fields dynamically based on the selected product type.
4. POST via `fetch()` to `/api/calculate/` as JSON.
5. Display a **blocking overlay + spinner** during calculation (disable submit, overlay the page).
6. Render numeric results into the existing `[ RESULTS OUTPUT ]` table.

#### CSS Additions to `base.html`
The existing `base.html` styles only cover `input[type="text"]` and `input[type="password"]`. We need to add styles for:
- `<select>` dropdowns (same terminal aesthetic: black bg, green text, amber border).
- `input[type="number"]` fields.
- `.spinner` / `.overlay` classes for the loading state.
- `.result-value` class for highlighting computed results.

---

## 4. Product Type → Class & Engine Mapping (Complete Reference)

This is the exact mapping the backend must use to instantiate the correct product class and engine type.

### 4.1. Vanilla Options (`/options/` page) — Engine: `MC`

| `product_type` value | Python Class | Constructor Args | UI Fields |
|:---|:---|:---|:---|
| `EuropeanCallOption` | `EuropeanCallOption` | `maturity: float, strike: float` | Maturity (Y), Strike |
| `EuropeanPutOption` | `EuropeanPutOption` | `maturity: float, strike: float` | Maturity (Y), Strike |

**Constructor signature** (from `abstract_option.py`): `AbstractOption(maturity, strike)`.
- `strike` is an **absolute price** (e.g., 5768 for SPX ATM), NOT a percentage.
- The current `options.html` template has a "STRIKE (%)" label and hardcodes `100.00`. For the MVP, the backend should convert: `strike_absolute = spot * (strike_pct / 100)`, where `spot = market.underlying_asset.last_price`.

**Vol Surface**: Default to `SVI`. User can pick `SVI`, `SSVI`, or `LOCAL`.

### 4.2. Exotic Options (`/exotics/` page) — Engine: `MC` or `AMERICAN_MC`

| `product_type` value | Python Class | Constructor Args | Engine | UI Fields |
|:---|:---|:---|:---|:---|
| `DownAndInCallOption` | `DownAndInCallOption` | `maturity, strike, barrier` | `MC` | Maturity, Strike, Barrier |
| `UpAndOutPutOption` | `UpAndOutPutOption` | `maturity, strike, barrier` | `MC` | Maturity, Strike, Barrier |
| `AmericanCallOption` | `AmericanCallOption` | `strike, maturity` | `AMERICAN_MC` | Maturity, Strike |
| `AmericanPutOption` | `AmericanPutOption` | `strike, maturity` | `AMERICAN_MC` | Maturity, Strike |

**Critical constructor differences:**
- Barrier options: `__init__(self, maturity, strike, barrier)` — maturity comes first.
- American options: `__init__(self, strike, maturity)` — **strike comes first** (different order!).
- Barrier validation: `DownBarrierOption` requires `barrier < strike`. `UpBarrierOption` requires `barrier > strike`. The backend must validate or catch `ValueError`.

**Barrier input**: The existing `exotics.html` has a "BARRIER LEVEL" input with default `80.00`. This likely represents a **percentage of spot** (80%). The backend should convert: `barrier_absolute = spot * (barrier_pct / 100)`.

**Vol Surface**: Default to `SVI`. User can pick `SVI`, `SSVI`, or `LOCAL`.

### 4.3. ELN / Autocall (`/elns/` page) — Engine: `MC` or `CALLABLE_MC`

| `product_type` value | Python Class | Constructor Args | UI Fields |
|:---|:---|:---|:---|
| `Phoenix` | `Phoenix` | `maturity, observation_frequency, capital_barrier, autocall_barrier, coupon_rate, coupon_barrier, is_security, is_plus` | Maturity, Obs Freq, Capital Barrier, Autocall Barrier, Coupon Rate, Coupon Barrier, Is Security, Is Plus |
| `Eagle` | `Eagle` | `Eagle` | `maturity, observation_frequency, capital_barrier, autocall_barrier, coupon_rate, is_security, is_plus` | Same as Phoenix **minus** Coupon Barrier |

**Two operating modes** controlled by a UI toggle:
1. **Pricing Mode** (`solve_yield = false`):
   - User provides `coupon_rate` (e.g., `8.0` meaning 8% per period).
   - Engine type: `PricingEngineType.MC` (or `CALLABLE_MC` — both work).
   - `settings.compute_callable_coupons = False`.
   - Result: `price` is populated.
2. **Solving Mode** (`solve_yield = true`):
   - `coupon_rate` is set to `0.0` in the product constructor.
   - Engine type: `PricingEngineType.CALLABLE_MC`.
   - `settings.compute_callable_coupons = True`.
   - Result: `coupon_callable` is populated (the implied yield in %).

**Observation Frequency**: The `ObservationFrequency` enum maps as:
| UI Label | Enum | Value |
|:---|:---|:---|
| Annual | `ANNUAL` | 1 |
| Semi-Annual | `SEMIANNUAL` | 2 |
| Quarterly | `QUARTERLY` | 4 |
| Monthly | `MONTHLY` | 12 |

**Barrier inputs are percentages** (not absolute prices). The engine internally normalizes paths to 100-based. So `capital_barrier=70` means 70% of initial spot.

**Vol Surface**: Default to **SSVI** for ELN products (long-dated simulations require no-arbitrage guarantee). Show a warning if user selects SVI.

---

## 5. API Contract

### 5.1. Endpoint
`POST /api/calculate/`

Must include the CSRF token. When using `fetch()`, read it from the `csrftoken` cookie (Django's default) and set the `X-CSRFToken` header.

### 5.2. Request Payload

```json
{
  "category": "exotic",
  "product_type": "DownAndInCallOption",
  "underlying": "SPX",
  "model": "BLACK_SCHOLES",
  "volatility_surface": "SVI",
  
  "maturity": 1.0,
  "strike_pct": 100.0,
  "barrier_pct": 80.0,
  
  "observation_frequency": "ANNUAL",
  "capital_barrier": 70.0,
  "autocall_barrier": 100.0,
  "coupon_barrier": 80.0,
  "coupon_rate": 8.0,
  "is_security": false,
  "is_plus": false,
  "solve_yield": false
}
```

**Field presence rules:**
| Field | Vanilla | Exotic (Barrier) | Exotic (American) | ELN |
|:---|:---|:---|:---|:---|
| `maturity` | ✅ | ✅ | ✅ | ✅ |
| `strike_pct` | ✅ | ✅ | ✅ | ❌ |
| `barrier_pct` | ❌ | ✅ | ❌ | ❌ |
| `observation_frequency` | ❌ | ❌ | ❌ | ✅ |
| `capital_barrier` | ❌ | ❌ | ❌ | ✅ |
| `autocall_barrier` | ❌ | ❌ | ❌ | ✅ |
| `coupon_barrier` | ❌ | ❌ | ❌ | ✅ (Phoenix only) |
| `coupon_rate` | ❌ | ❌ | ❌ | ✅ (if `solve_yield=false`) |
| `is_security` | ❌ | ❌ | ❌ | ✅ (optional, default false) |
| `is_plus` | ❌ | ❌ | ❌ | ✅ (optional, default false) |
| `solve_yield` | ❌ | ❌ | ❌ | ✅ |

### 5.3. Response Payload

**Success (200):**
```json
{
  "status": "success",
  "data": {
    "price": 102.2410,
    "coupon_callable": null,
    "greeks": {
      "delta": 0.520000,
      "gamma": 0.012000,
      "vega": 1.250000,
      "theta": -0.040000,
      "rho": 0.030000
    },
    "ci_lower": 101.5000,
    "ci_upper": 103.2000,
    "std_dev": 0.443000
  }
}
```

**Error (400 / 500):**
```json
{
  "status": "error",
  "error_type": "InvalidProductInputError",
  "message": "The barrier must be lower than the strike for a low barrier."
}
```

Error types to catch from `kernel/exceptions.py`:
- `PricingEngineError` (catch-all base class)
  - `UnsupportedModelError`
  - `UnsupportedEngineTypeError`
  - `UnsupportedProductError`
  - `InvalidProductInputError`
  - `IndeterminateValuationError`
  - `CalibrationError`
- Standard `ValueError` from product constructors (e.g., barrier validation).

---

## 6. Frontend Behavior Specification

### 6.1. Submit Blocking (All 3 Pages)
```javascript
const form = document.getElementById('calc-form');
const overlay = document.getElementById('overlay');
const submitBtn = document.getElementById('submit-btn');

form.addEventListener('submit', async (e) => {
    e.preventDefault();
    submitBtn.disabled = true;
    overlay.style.display = 'flex';  // Show spinner overlay
    
    try {
        const resp = await fetch('/api/calculate/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken'),
            },
            body: JSON.stringify(buildPayload()),
        });
        const data = await resp.json();
        renderResults(data);
    } catch (err) {
        renderError(err.message);
    } finally {
        submitBtn.disabled = false;
        overlay.style.display = 'none';
    }
});
```

### 6.2. Dynamic Field Visibility

**`/options/` page:**
- Product Type dropdown: `EuropeanCallOption`, `EuropeanPutOption`.
- Fields always visible: Underlying, Model, Vol Surface, Maturity, Strike (%).

**`/exotics/` page:**
- Product Type dropdown: `DownAndInCallOption`, `UpAndOutPutOption`, `AmericanCallOption`, `AmericanPutOption`.
- When Barrier product is selected → show Barrier (%) field.
- When American product is selected → hide Barrier (%) field.

**`/elns/` page:**
- Product Type dropdown: `Phoenix`, `Eagle`.
- When `Phoenix` is selected → show `Coupon Barrier` field.
- When `Eagle` is selected → hide `Coupon Barrier` field.
- Mode toggle: "Pricing Mode" / "Solving Mode".
  - Pricing Mode → `Coupon Rate` field is editable.
  - Solving Mode → `Coupon Rate` field is disabled, set to `0.0`.
- Vol Surface defaults to **SSVI**. If user selects SVI → show amber warning text.
- If user selects **Heston** model → show amber warning: "Heston model is significantly slower (~6x)."

### 6.3. Results Table (All 3 Pages)
The results table must include all of these rows (populated from the JSON response):

| Metric | JSON Field |
|:---|:---|
| PRICE (NPV) | `data.price` |
| IMPLIED YIELD (%) | `data.coupon_callable` (ELN only) |
| DELTA | `data.greeks.delta` |
| GAMMA | `data.greeks.gamma` |
| VEGA | `data.greeks.vega` |
| THETA | `data.greeks.theta` |
| RHO | `data.greeks.rho` |
| 95% CI LOWER | `data.ci_lower` |
| 95% CI UPPER | `data.ci_upper` |
| STD DEV | `data.std_dev` |

For ELN in Solving Mode, `price` will be `null` and `coupon_callable` will be populated. The UI should handle both cases gracefully (show `---` for null fields).

---

## 7. Deployment Considerations

### 7.1. Package Dependencies
The bloombgg app currently only has Django/gunicorn. We must add the pricing engine's data science packages to `bloombgg/pyproject.toml`:
```toml
dependencies = [
    "django>=6.0.6",
    "gunicorn>=26.0.0",
    "python-dotenv>=1.2.2",
    "whitenoise>=6.12.0",
    "numpy>=1.26.0",
    "scipy>=1.11.0",
    "pandas>=2.1.0",
]
```

### 7.2. Cloud Run Container Specs
Current `cloudbuild.yaml` allocates **2 vCPU, 4 GiB RAM**. Per the README resource estimates:
- 10,000 paths × 252 steps × BS + Greeks ≈ 6 simulations × ~16 MB each ≈ ~100 MB peak.
- Heston doubles this (~200 MB).
- This fits comfortably within 4 GiB.

### 7.3. Gunicorn Timeout
The current Dockerfile uses `--timeout 0` (infinite), which is correct. Greeks calculations with Heston can take 15-30+ seconds.

### 7.4. Workers
Current config: `--workers 1 --threads 8`. Since MC pricing is CPU-bound and NumPy releases the GIL during array ops, this is acceptable. With submit blocking on the frontend, only one calculation runs at a time per user anyway.
