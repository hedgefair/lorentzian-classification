"""Settings-parsing utilities for the Lorentzian Classification CLI."""

from __future__ import annotations

import argparse
from dataclasses import fields

from .core import Settings
from ._types import (
    BOOLEAN_SETTING_FIELDS,
    FEATURE_SLOTS,
    FEATURE_TYPES,
    FLOAT_SETTING_FIELDS,
    INTEGER_SETTING_FIELDS,
    PINE_SETTING_BOUNDS,
    VALID_SOURCES,
)


def parse_feature(value: str) -> tuple[str, int, int]:
    parts = value.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("feature must have form TYPE:PARAM_A:PARAM_B, e.g. RSI:14:1")
    kind = parts[0].upper()
    if kind not in FEATURE_TYPES:
        raise argparse.ArgumentTypeError("feature type must be one of RSI, WT, CCI, ADX")
    try:
        return kind, int(parts[1]), int(parts[2])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("feature params must be integers") from exc


def bounded_int(name: str, minimum: int | None = None, maximum: int | None = None):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
        if minimum is not None and parsed < minimum:
            raise argparse.ArgumentTypeError(f"{name} must be >= {minimum}")
        if maximum is not None and parsed > maximum:
            raise argparse.ArgumentTypeError(f"{name} must be <= {maximum}")
        return parsed

    return parse


def bounded_float(name: str, minimum: float | None = None, maximum: float | None = None):
    def parse(value: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be a number") from exc
        if minimum is not None and parsed < minimum:
            raise argparse.ArgumentTypeError(f"{name} must be >= {minimum:g}")
        if maximum is not None and parsed > maximum:
            raise argparse.ArgumentTypeError(f"{name} must be <= {maximum:g}")
        return parsed

    return parse


def is_strict_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def coerce_feature(value: object, key: str) -> tuple[str, int, int]:
    if isinstance(value, str):
        try:
            return parse_feature(value)
        except argparse.ArgumentTypeError as exc:
            raise ValueError(f"{key}: {exc}") from exc
    if isinstance(value, list | tuple) and len(value) == 3:
        kind = str(value[0]).upper()
        if kind not in FEATURE_TYPES:
            raise ValueError(f"{key}: feature type must be one of RSI, WT, CCI, ADX")
        if not is_strict_int(value[1]) or not is_strict_int(value[2]):
            raise ValueError(f"{key}: feature params must be integers")
        return kind, value[1], value[2]
    raise ValueError(f"{key}: feature must be a TYPE:PARAM_A:PARAM_B string or 3-item list")


def validate_settings(settings: Settings) -> Settings:
    for key in BOOLEAN_SETTING_FIELDS:
        if not isinstance(getattr(settings, key), bool):
            raise ValueError(f"{key}: must be a boolean")

    for key in INTEGER_SETTING_FIELDS:
        if not is_strict_int(getattr(settings, key)):
            raise ValueError(f"{key}: must be an integer")

    for key in FLOAT_SETTING_FIELDS:
        value = getattr(settings, key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"{key}: must be a number")

    if not isinstance(settings.source, str):
        raise ValueError("source: must be a string")
    if settings.source not in VALID_SOURCES:
        raise ValueError(f"source: must be one of {', '.join(VALID_SOURCES)}")

    for key, (minimum, maximum) in PINE_SETTING_BOUNDS.items():
        value = getattr(settings, key)
        if minimum is not None and value < minimum:
            raise ValueError(f"{key}: must be >= {minimum:g}")
        if maximum is not None and value > maximum:
            raise ValueError(f"{key}: must be <= {maximum:g}")

    for key in FEATURE_SLOTS:
        feature = getattr(settings, key)
        if not isinstance(feature, tuple | list) or len(feature) != 3:
            raise ValueError(f"{key}: feature must be a 3-item tuple")
        kind, param_a, param_b = feature
        if kind not in FEATURE_TYPES:
            raise ValueError(f"{key}: feature type must be one of RSI, WT, CCI, ADX")
        if not is_strict_int(param_a) or not is_strict_int(param_b):
            raise ValueError(f"{key}: feature params must be integers")

    return settings


def settings_from_mapping(overrides: dict[str, object]) -> Settings:
    field_names = {field.name for field in fields(Settings)}
    unknown = sorted(set(overrides) - field_names)
    if unknown:
        raise ValueError(f"unknown settings keys: {', '.join(unknown)}")

    values = {field.name: getattr(Settings(), field.name) for field in fields(Settings)}
    for key, value in overrides.items():
        values[key] = coerce_feature(value, key) if key in {"f1", "f2", "f3", "f4", "f5"} else value
    return validate_settings(Settings(**values))


def add_settings_args(parser: argparse.ArgumentParser) -> None:
    defaults = Settings()
    parser.add_argument("--source", choices=VALID_SOURCES, default=defaults.source)
    parser.add_argument("--neighbors-count", type=bounded_int("neighbors-count", 1, 100), default=defaults.neighbors_count)
    parser.add_argument("--max-bars-back", type=int, default=defaults.max_bars_back)
    parser.add_argument("--feature-count", type=int, default=defaults.feature_count, choices=range(2, 6))
    parser.add_argument("--color-compression", type=bounded_int("color-compression", 1, 10), default=defaults.color_compression)
    parser.add_argument("--include-full-history", action="store_true", default=defaults.include_full_history)
    parser.add_argument("--use-volatility-filter", action=argparse.BooleanOptionalAction, default=defaults.use_volatility_filter)
    parser.add_argument("--use-regime-filter", action=argparse.BooleanOptionalAction, default=defaults.use_regime_filter)
    parser.add_argument("--use-adx-filter", action=argparse.BooleanOptionalAction, default=defaults.use_adx_filter)
    parser.add_argument("--regime-threshold", type=bounded_float("regime-threshold", -10, 10), default=defaults.regime_threshold)
    parser.add_argument("--adx-threshold", type=bounded_int("adx-threshold", 0, 100), default=defaults.adx_threshold)
    parser.add_argument("--use-ema-filter", action=argparse.BooleanOptionalAction, default=defaults.use_ema_filter)
    parser.add_argument("--ema-period", type=bounded_int("ema-period", 1), default=defaults.ema_period)
    parser.add_argument("--use-sma-filter", action=argparse.BooleanOptionalAction, default=defaults.use_sma_filter)
    parser.add_argument("--sma-period", type=bounded_int("sma-period", 1), default=defaults.sma_period)
    parser.add_argument("--use-kernel-filter", action=argparse.BooleanOptionalAction, default=defaults.use_kernel_filter)
    parser.add_argument("--use-kernel-smoothing", action=argparse.BooleanOptionalAction, default=defaults.use_kernel_smoothing)
    parser.add_argument("--use-dynamic-exits", action=argparse.BooleanOptionalAction, default=defaults.use_dynamic_exits)
    parser.add_argument("--show-exits", action=argparse.BooleanOptionalAction, default=defaults.show_exits)
    parser.add_argument("--use-worst-case", action=argparse.BooleanOptionalAction, default=defaults.use_worst_case)
    parser.add_argument("--kernel-h", type=bounded_int("kernel-h", 3), default=defaults.kernel_h)
    parser.add_argument("--kernel-r", type=float, default=defaults.kernel_r)
    parser.add_argument("--kernel-x", type=int, default=defaults.kernel_x)
    parser.add_argument("--kernel-lag", type=int, default=defaults.kernel_lag)
    parser.add_argument("--show-kernel-estimate", action=argparse.BooleanOptionalAction, default=defaults.show_kernel_estimate)
    parser.add_argument("--show-bar-colors", action=argparse.BooleanOptionalAction, default=defaults.show_bar_colors)
    parser.add_argument("--show-bar-predictions", action=argparse.BooleanOptionalAction, default=defaults.show_bar_predictions)
    parser.add_argument("--use-atr-offset", action=argparse.BooleanOptionalAction, default=defaults.use_atr_offset)
    parser.add_argument("--bar-predictions-offset", type=bounded_float("bar-predictions-offset", 0), default=defaults.bar_predictions_offset)
    parser.add_argument("--use-confidence-gradient", action=argparse.BooleanOptionalAction, default=defaults.use_confidence_gradient)
    parser.add_argument("--show-trade-stats", action=argparse.BooleanOptionalAction, default=defaults.show_trade_stats)
    parser.add_argument("--f1", type=parse_feature, default=defaults.f1)
    parser.add_argument("--f2", type=parse_feature, default=defaults.f2)
    parser.add_argument("--f3", type=parse_feature, default=defaults.f3)
    parser.add_argument("--f4", type=parse_feature, default=defaults.f4)
    parser.add_argument("--f5", type=parse_feature, default=defaults.f5)


def settings_from_args(args: argparse.Namespace) -> Settings:
    return validate_settings(Settings(
        source=args.source,
        neighbors_count=args.neighbors_count,
        max_bars_back=args.max_bars_back,
        feature_count=args.feature_count,
        color_compression=args.color_compression,
        include_full_history=args.include_full_history,
        use_volatility_filter=args.use_volatility_filter,
        use_regime_filter=args.use_regime_filter,
        use_adx_filter=args.use_adx_filter,
        regime_threshold=args.regime_threshold,
        adx_threshold=args.adx_threshold,
        use_ema_filter=args.use_ema_filter,
        ema_period=args.ema_period,
        use_sma_filter=args.use_sma_filter,
        sma_period=args.sma_period,
        use_kernel_filter=args.use_kernel_filter,
        use_kernel_smoothing=args.use_kernel_smoothing,
        use_dynamic_exits=args.use_dynamic_exits,
        show_exits=args.show_exits,
        use_worst_case=args.use_worst_case,
        kernel_h=args.kernel_h,
        kernel_r=args.kernel_r,
        kernel_x=args.kernel_x,
        kernel_lag=args.kernel_lag,
        show_kernel_estimate=args.show_kernel_estimate,
        show_bar_colors=args.show_bar_colors,
        show_bar_predictions=args.show_bar_predictions,
        use_atr_offset=args.use_atr_offset,
        bar_predictions_offset=args.bar_predictions_offset,
        use_confidence_gradient=args.use_confidence_gradient,
        show_trade_stats=args.show_trade_stats,
        f1=args.f1,
        f2=args.f2,
        f3=args.f3,
        f4=args.f4,
        f5=args.f5,
    ))
