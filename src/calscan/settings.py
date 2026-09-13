"""Loads config.yaml (thresholds/products) + .env (secrets) into typed settings.

Domain code should depend on AppConfig, not on this module, so it stays
network- and filesystem-free and testable without a real config.yaml.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class ProductConfig(BaseModel):
    source: Literal["schwab", "alpaca"]
    symbol: str
    multiplier: int
    style: Literal["european", "american"]
    settlement: Literal["cash", "physical"]


class RatesConfig(BaseModel):
    risk_free: float
    dividend_yield: float


class ExpirySelectionConfig(BaseModel):
    front_dte_min: int
    front_dte_max: int
    ratio_min: float
    ratio_max: float
    prefer_pm_settled: bool


class StrikesConfig(BaseModel):
    target_deltas: list[int]
    sides: list[Literal["call", "put"]]


class RegimeConfig(BaseModel):
    ivts_carry_max: float
    ivts_backwardation: float
    ivts_backwardation_strong: float
    slope_z_window_days: int
    slope_z_avoid_top_pct: float
    vix_lookback_median_days: int
    rv_window_short: int
    rv_window_long: int


class VolShockAlphaConfig(BaseModel):
    baseline: float
    stress_cases: list[float]


class GatesConfig(BaseModel):
    max_spread_pct_of_mid: float
    min_open_interest: int
    max_debit_pct_of_nav: float
    rv10_must_be_below_breakeven: bool


class PlaybookTargets(BaseModel):
    profit_pct: float
    stop_pct: float
    distance_stop_sigma: float
    time_stop_dte: int


class TargetsConfig(BaseModel):
    playbook_A: PlaybookTargets
    playbook_B: PlaybookTargets


class ScenarioGridConfig(BaseModel):
    spot_moves_sigma: list[float]
    iv_shifts_front: list[float]
    iv_shifts_back: list[float]


class ScheduleConfig(BaseModel):
    run_times_et: list[str]


class AlertsConfig(BaseModel):
    enabled: bool
    channel: Literal["none", "email", "pushover", "slack"]


class AppConfig(BaseModel):
    products: dict[str, ProductConfig]
    rates: RatesConfig
    expiry_selection: ExpirySelectionConfig
    strikes: StrikesConfig
    regime: RegimeConfig
    vol_shock_alpha: VolShockAlphaConfig
    gates: GatesConfig
    targets: TargetsConfig
    scenario_grid: ScenarioGridConfig
    schedule: ScheduleConfig
    nav: float
    alerts: AlertsConfig

    @classmethod
    def load(cls, path: Path | str = REPO_ROOT / "config.yaml") -> AppConfig:
        raw = yaml.safe_load(Path(path).read_text())
        return cls.model_validate(raw)


class Secrets(BaseSettings):
    """Loaded from .env. Never logged."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    schwab_app_key: str = ""
    schwab_app_secret: str = ""
    schwab_callback_url: str = "https://127.0.0.1:8182"
    schwab_token_path: str = "data/schwab_token.json"

    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_data_feed: str = "iex"


class Settings(BaseModel):
    config: AppConfig
    secrets: Secrets

    @classmethod
    def load(cls, config_path: Path | str = REPO_ROOT / "config.yaml") -> Settings:
        return cls(config=AppConfig.load(config_path), secrets=Secrets())
