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
    """Get or create a cached Market instance."""
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

def _resolve_engine_type(category: str, product_type: str, solve_yield: bool) -> PricingEngineType:
    """Pick the correct engine for a product."""
    if category == "exotic" and product_type.startswith("American"):
        return PricingEngineType.AMERICAN_MC
    if category == "eln":
        return PricingEngineType.CALLABLE_MC if solve_yield else PricingEngineType.MC
    return PricingEngineType.MC

def build_product(payload: dict, spot: float):
    product_type = payload["product_type"]
    category = payload["category"]
    maturity = float(payload.get("maturity", 1.0))
    
    if category == "vanilla":
        strike_pct = float(payload["strike_pct"])
        strike = spot * (strike_pct / 100.0)
        if product_type == "EuropeanCallOption":
            return EuropeanCallOption(maturity, strike)
        elif product_type == "EuropeanPutOption":
            return EuropeanPutOption(maturity, strike)
            
    elif category == "exotic":
        if product_type.startswith("American"):
            strike_pct = float(payload["strike_pct"])
            strike = spot * (strike_pct / 100.0)
            if product_type == "AmericanCallOption":
                return AmericanCallOption(strike, maturity)
            elif product_type == "AmericanPutOption":
                return AmericanPutOption(strike, maturity)
        else: # Barrier
            strike_pct = float(payload["strike_pct"])
            strike = spot * (strike_pct / 100.0)
            barrier_pct = float(payload["barrier_pct"])
            barrier = spot * (barrier_pct / 100.0)
            if product_type == "DownAndInCallOption":
                return DownAndInCallOption(maturity, strike, barrier)
            elif product_type == "UpAndOutPutOption":
                return UpAndOutPutOption(maturity, strike, barrier)
                
    elif category == "eln":
        obs_freq_str = payload.get("observation_frequency", "ANNUAL")
        obs_freq = ObservationFrequency[obs_freq_str]
        capital_barrier = float(payload["capital_barrier"])
        autocall_barrier = float(payload["autocall_barrier"])
        is_security = payload.get("is_security", False)
        is_plus = payload.get("is_plus", False)
        
        solve_yield = payload.get("solve_yield", False)
        if solve_yield:
            coupon_rate = 0.0
        else:
            coupon_rate = float(payload["coupon_rate"])
            
        if product_type == "Phoenix":
            coupon_barrier = float(payload["coupon_barrier"])
            return Phoenix(maturity, obs_freq, capital_barrier, autocall_barrier, coupon_rate, coupon_barrier, is_security, is_plus)
        elif product_type == "Eagle":
            return Eagle(maturity, obs_freq, capital_barrier, autocall_barrier, coupon_rate, is_security, is_plus)
            
    raise ValueError(f"Unknown product type or category: {category} / {product_type}")

def run_pricing(payload: dict) -> dict:
    """Map a frontend JSON payload to engine calls and return results."""
    vol_surface_str = payload.get("volatility_surface", "SVI")
    vol_surface = VolatilitySurfaceType[vol_surface_str]
    
    underlying = payload["underlying"]
    market = get_market(underlying, vol_surface)
    
    settings = PricingSettings()
    settings.underlying_name = underlying
    settings.rate_curve_type = RateCurveType.RF_US_TREASURY
    settings.interpolation_type = InterpolationType.CUBIC
    settings.volatility_surface_type = vol_surface
    settings.day_count_convention = CalendarConvention.ACT_360
    settings.obs_frequency = ObservationFrequency.ANNUAL
    settings.model = Model[payload["model"]]
    settings.nb_paths = 10000
    settings.nb_steps = 252
    settings.compute_greeks = True
    
    category = payload["category"]
    product_type = payload["product_type"]
    solve_yield = payload.get("solve_yield", False)

    settings.pricing_engine_type = _resolve_engine_type(category, product_type, solve_yield)
    if category == "eln" and solve_yield:
        settings.compute_callable_coupons = True

    spot = market.underlying_asset.last_price
    product = build_product(payload, spot)
    
    launcher = PricingLauncher(pricing_settings=settings, market=market)
    result = launcher.calculate(copy.deepcopy(product))
    
    return {
        "price": round(result.price, 4) if result.price is not None else None,
        "coupon_callable": round(result.coupon_callable, 4) if result.coupon_callable is not None else None,
        "greeks": {k: round(v, 6) for k, v in result.greeks.items()} if result.greeks else None,
        "ci_lower": round(result.lower_bound, 4) if result.lower_bound is not None else None,
        "ci_upper": round(result.upper_bound, 4) if result.upper_bound is not None else None,
        "std_dev": round(result.std_dev, 6) if result.std_dev is not None else None,
    }
