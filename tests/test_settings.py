from datetime import date

from calscan.adapters.cboe import parse_daily_closes
from calscan.settings import AppConfig


def test_config_yaml_loads() -> None:
    config = AppConfig.load()
    assert config.products["SPX"].symbol == "$SPX"
    assert config.regime.ivts_carry_max == 0.92
    assert config.targets.playbook_A.profit_pct == 0.25


def test_parse_daily_closes() -> None:
    csv_text = "DATE,OPEN,HIGH,LOW,CLOSE\n01/02/1990,17.24,17.24,17.24,17.24\n"
    rows = parse_daily_closes(csv_text)
    assert rows == [(date(1990, 1, 2), 17.24)]
