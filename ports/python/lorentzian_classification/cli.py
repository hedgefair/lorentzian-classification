"""Command-line interface for the Python Lorentzian Classification port."""

from __future__ import annotations

import argparse
import contextlib
import csv
from dataclasses import fields
import hashlib
import io
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import shlex

from . import __version__
from .core import (
    RESULT_FIELDNAMES,
    Settings,
    TvRow,
    calculate,
    format_result_float,
    is_missing,
    read_tradingview_csv,
    write_result_csv,
)
from ._types import (
    DEBUG_MARKER_RE,
    DEFAULT_FEATURE_EXPORT_COLUMNS,
    EXPORT_PACK_ACCEPTANCE_FIELDNAMES,
    EXPORT_PACK_HEADER,
    MINIMUM_PINE_EXPORT_COLUMNS,
    PARITY_FIXTURES,
    PINE_EXPORT_SERIES,
    SETTINGS_FINGERPRINT_COLUMN,
    SHA256_HEX_RE,
    ParityFixture,
    PineSourceSpec,
    externalParityReportSpec,
    external_FEATURE_ENUMS,
    external_PARITY_REPORT_COLUMNS,
    external_PARITY_SCRIPT_INPUTS,
)
from ._settings import (
    add_settings_args,
    coerce_feature,
    settings_from_args,
    settings_from_mapping,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
# Default parity source is the repo-local baseline set. Point at a local
# external_runtime 5 external workspace with the LORENTZIAN_external_ROOT environment variable
# (or use --fixture-dir / LORENTZIAN_PARITY_FIXTURE_DIR for a direct directory).
DEFAULT_external_ROOT = Path(
    os.environ.get("LORENTZIAN_external_ROOT")
    or REPO_ROOT / "tests" / "parity" / "baselines"
)
DEFAULT_FIXTURE_DIR = DEFAULT_external_ROOT / "Files"
FIXTURE_DIR_ENV_VAR = "LORENTZIAN_PARITY_FIXTURE_DIR"
DEFAULT_MANIFEST = REPO_ROOT / "tests" / "parity" / "fixtures_manifest.json"
DEFAULT_EXPORT_PACK_OUTPUT = "/tmp/lorentzian-export-pack"
DEFAULT_PINE_EXPORT_SOURCE_DIR = "/path/to/downloaded-pine-exports"
DEFAULT_external_RUNNER_PACK_OUTPUT = "/tmp/lorentzian-external-runner-pack"
DEFAULT_READINESS_ARTIFACTS_OUTPUT = "/tmp/lorentzian-readiness-artifacts"


def fmt_float(value: float, digits: int = 16) -> str:
    return format_result_float(value, digits)


def output_path_error(path: Path) -> str | None:
    parent = path.parent
    if parent and not parent.exists():
        return f"output directory not found: {parent}"
    if parent and not parent.is_dir():
        return f"output parent is not a directory: {parent}"
    if path.exists() and not path.is_file():
        return f"output path is not a file: {path}"
    return None


def output_directory_error(path: Path) -> str | None:
    for parent in path.parents:
        if parent.exists() and not parent.is_dir():
            return f"output directory parent is not a directory: {parent}"
    if path.exists() and not path.is_dir():
        return f"output directory path is not a directory: {path}"
    return None


def write_results(path: Path, rows) -> None:
    write_result_csv(path, rows)


def feature_export_column(slot: int, feature: tuple[str, int, int]) -> str:
    default_feature = getattr(Settings(), f"f{slot}")
    if feature == default_feature:
        return DEFAULT_FEATURE_EXPORT_COLUMNS[slot]
    return f"F{slot}_{feature[0].upper()}"


def feature_export_columns(settings: Settings) -> dict[str, str]:
    return {
        f"F{slot}": feature_export_column(slot, getattr(settings, f"f{slot}"))
        for slot in range(1, 6)
    }


def pine_export_columns_for_settings(settings: Settings, include_full: bool) -> list[str]:
    selected = RESULT_FIELDNAMES if include_full else MINIMUM_PINE_EXPORT_COLUMNS
    feature_columns = feature_export_columns(settings)
    return [
        feature_columns.get(column.split("_", 1)[0], column)
        if column.startswith(("F1_", "F2_", "F3_", "F4_", "F5_"))
        else column
        for column in selected
    ]


def pine_export_series_for_settings(settings: Settings, include_full: bool) -> list[dict[str, object]]:
    feature_columns = feature_export_columns(settings)
    rows = []
    selected_columns = set(RESULT_FIELDNAMES if include_full else MINIMUM_PINE_EXPORT_COLUMNS)
    for row in PINE_EXPORT_SERIES:
        if row["column"] not in selected_columns:
            continue
        updated = dict(row)
        column = str(updated["column"])
        if column.startswith(("F1_", "F2_", "F3_", "F4_", "F5_")):
            updated["column"] = feature_columns[column.split("_", 1)[0]]
        rows.append(updated)
    return rows


def builtin_parity_fixtures() -> list[ParityFixture]:
    return [
        ParityFixture(
            name=filename,
            filename=filename,
            settings=Settings(include_full_history=include_full_history),
        )
        for filename, include_full_history in PARITY_FIXTURES
    ]


def duplicate_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def casefold_collision_groups(values: list[str]) -> list[str]:
    groups: dict[str, list[str]] = {}
    for value in values:
        key = value.casefold()
        if value not in groups.setdefault(key, []):
            groups[key].append(value)
    return [", ".join(group) for group in groups.values() if len(group) > 1]


def object_rows(payload: dict[str, object], section: str) -> list[dict[str, object]]:
    rows = payload.get(section, [])
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ValueError(f"{section} must be a list")
    return [row for row in rows if isinstance(row, dict)]


def manifest_relative_path_error(value: str) -> str | None:
    normalized = value.replace("\\", "/")
    if Path(value).is_absolute() or PureWindowsPath(value).is_absolute() or normalized.startswith("/"):
        return "must be a relative path"
    if PureWindowsPath(value).drive:
        return "must be a relative path"
    if any(part == ".." for part in normalized.split("/")):
        return "must not contain '..'"
    return None


def windows_artifact_path_error(value: str) -> str | None:
    windows_path = PureWindowsPath(value)
    if windows_path.is_absolute() or windows_path.drive:
        return "must not be a Windows absolute path"
    return None


def validate_manifest_integrity(payload: dict[str, object]) -> None:
    for section in ["fixtures", "required_uncovered_fixture_cases"]:
        rows = object_rows(payload, section)
        for key in ["name", "filename"]:
            values = [str(row[key]) for row in rows if isinstance(row.get(key), str) and row.get(key)]
            duplicates = duplicate_values(values)
            if duplicates:
                raise ValueError(f"{section}: duplicate {key} values: {', '.join(duplicates)}")
            if key == "filename":
                casefold_duplicates = casefold_collision_groups(values)
                if casefold_duplicates:
                    raise ValueError(
                        f"{section}: case-insensitive duplicate {key} values: "
                        + "; ".join(casefold_duplicates)
                    )

    for section in ["external_parity_reports"]:
        rows = object_rows(payload, section)
        for key in ["name", "filename"]:
            values = [str(row[key]) for row in rows if isinstance(row.get(key), str) and row.get(key)]
            duplicates = duplicate_values(values)
            if duplicates:
                raise ValueError(f"{section}: duplicate {key} values: {', '.join(duplicates)}")
            if key == "filename":
                casefold_duplicates = casefold_collision_groups(values)
                if casefold_duplicates:
                    raise ValueError(
                        f"{section}: case-insensitive duplicate {key} values: "
                        + "; ".join(casefold_duplicates)
                    )

    ignored_rows = object_rows(payload, "ignored_csv_candidates")
    ignored_filenames = [
        str(row["filename"])
        for row in ignored_rows
        if isinstance(row.get("filename"), str) and row.get("filename")
    ]
    duplicates = duplicate_values(ignored_filenames)
    if duplicates:
        raise ValueError(f"ignored_csv_candidates: duplicate filename values: {', '.join(duplicates)}")
    casefold_duplicates = casefold_collision_groups(ignored_filenames)
    if casefold_duplicates:
        raise ValueError(
            "ignored_csv_candidates: case-insensitive duplicate filename values: "
            + "; ".join(casefold_duplicates)
        )

    for section in ["pine_sources", "external_sources"]:
        sources = object_rows(payload, section)
        for key in ["name", "path"]:
            values = [str(row[key]) for row in sources if isinstance(row.get(key), str) and row.get(key)]
            duplicates = duplicate_values(values)
            if duplicates:
                raise ValueError(f"{section}: duplicate {key} values: {', '.join(duplicates)}")
            if key == "path":
                casefold_duplicates = casefold_collision_groups(values)
                if casefold_duplicates:
                    raise ValueError(
                        f"{section}: case-insensitive duplicate {key} values: "
                        + "; ".join(casefold_duplicates)
                    )

    fixture_filenames = {
        str(row["filename"])
        for row in object_rows(payload, "fixtures")
        if isinstance(row.get("filename"), str) and row.get("filename")
    }
    required_filenames = {
        str(row["filename"])
        for row in object_rows(payload, "required_uncovered_fixture_cases")
        if isinstance(row.get("filename"), str) and row.get("filename")
    }
    external_report_filenames = {
        str(row["filename"])
        for row in object_rows(payload, "external_parity_reports")
        if isinstance(row.get("filename"), str) and row.get("filename")
    }
    overlap = sorted(fixture_filenames & required_filenames)
    if overlap:
        raise ValueError(f"fixtures and required_uncovered_fixture_cases share filenames: {', '.join(overlap)}")
    fixture_filenames_by_key = {filename.casefold(): filename for filename in fixture_filenames}
    required_filenames_by_key = {filename.casefold(): filename for filename in required_filenames}
    casefold_overlap = sorted(set(fixture_filenames_by_key) & set(required_filenames_by_key))
    if casefold_overlap:
        overlap_labels = [
            f"{fixture_filenames_by_key[key]} / {required_filenames_by_key[key]}" for key in casefold_overlap
        ]
        raise ValueError(
            "fixtures and required_uncovered_fixture_cases share filenames ignoring case: "
            + ", ".join(overlap_labels)
        )
    evidence_by_key = {
        filename.casefold(): filename for filename in fixture_filenames | required_filenames | external_report_filenames
    }
    ignored_by_key = {filename.casefold(): filename for filename in ignored_filenames}
    ignored_overlap = sorted(set(evidence_by_key) & set(ignored_by_key))
    if ignored_overlap:
        overlap_labels = [
            f"{evidence_by_key[key]} / {ignored_by_key[key]}" for key in ignored_overlap
        ]
        raise ValueError(
            "ignored_csv_candidates overlap tracked, required, or external parity report filenames ignoring case: "
            + ", ".join(overlap_labels)
        )
    tracked_required_ignored_by_key = {
        filename.casefold(): filename
        for filename in fixture_filenames | required_filenames | set(ignored_filenames)
    }
    external_report_by_key = {filename.casefold(): filename for filename in external_report_filenames}
    external_report_overlap = sorted(set(tracked_required_ignored_by_key) & set(external_report_by_key))
    if external_report_overlap:
        overlap_labels = [
            f"{tracked_required_ignored_by_key[key]} / {external_report_by_key[key]}" for key in external_report_overlap
        ]
        raise ValueError(
            "external_parity_reports overlap tracked, required, or ignored filenames ignoring case: "
            + ", ".join(overlap_labels)
        )

    required_selectors: dict[str, str] = {}
    selector_collisions: list[str] = []
    for row in object_rows(payload, "required_uncovered_fixture_cases"):
        name = row.get("name")
        filename = row.get("filename")
        if not isinstance(name, str) or not isinstance(filename, str):
            continue
        for selector in [name, filename]:
            owner = required_selectors.get(selector)
            if owner is not None and owner != name:
                selector_collisions.append(selector)
            required_selectors[selector] = name
    if selector_collisions:
        raise ValueError(
            "required_uncovered_fixture_cases: ambiguous name/filename selectors: "
            + ", ".join(sorted(set(selector_collisions)))
        )

    required_cases = payload.get("required_uncovered_fixture_cases", [])
    if not isinstance(required_cases, list):
        raise ValueError("required_uncovered_fixture_cases must be a list")
    for index, case in enumerate(required_cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"required_uncovered_fixture_cases {index}: expected object")
        label = uncovered_case_label(case)
        proves = case.get("proves")
        if not isinstance(proves, list) or not proves:
            raise ValueError(f"{label}: proves must be a non-empty list")
        if not all(isinstance(item, str) and item for item in proves):
            raise ValueError(f"{label}: proves must contain non-empty strings")
        python_smoke_fixture = case.get("python_smoke_fixture")
        if not isinstance(python_smoke_fixture, str) or not python_smoke_fixture:
            raise ValueError(f"{label}: python_smoke_fixture must be a non-empty string")
        if smoke_fixture_error := manifest_relative_path_error(python_smoke_fixture):
            raise ValueError(f"{label}: python_smoke_fixture {smoke_fixture_error}")

    for index, row in enumerate(ignored_rows, start=1):
        filename = row.get("filename")
        if not isinstance(filename, str) or not filename:
            raise ValueError(f"ignored_csv_candidates {index}: filename must be a non-empty string")
        if filename_error := manifest_relative_path_error(filename):
            raise ValueError(f"ignored_csv_candidates {index}: filename {filename_error}")
        if not filename.lower().endswith(".csv"):
            raise ValueError(f"ignored_csv_candidates {index}: filename must end with .csv")
        reason = row.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"ignored_csv_candidates {index}: reason must be a non-empty string")

    for index, row in enumerate(object_rows(payload, "external_parity_reports"), start=1):
        filename = row.get("filename")
        if not isinstance(filename, str) or not filename:
            raise ValueError(f"external_parity_reports {index}: filename must be a non-empty string")
        if filename_error := manifest_relative_path_error(filename):
            raise ValueError(f"external_parity_reports {index}: filename {filename_error}")
        if not filename.lower().endswith(".csv"):
            raise ValueError(f"external_parity_reports {index}: filename must end with .csv")
        name = row.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"external_parity_reports {index}: name must be a non-empty string")
        role = row.get("role")
        if not isinstance(role, str) or not role.strip():
            raise ValueError(f"external_parity_reports {index}: role must be a non-empty string")
        input_filename = row.get("input_filename")
        if not isinstance(input_filename, str) or not input_filename:
            raise ValueError(f"external_parity_reports {index}: input_filename must be a non-empty string")
        if input_error := manifest_relative_path_error(input_filename):
            raise ValueError(f"external_parity_reports {index}: input_filename {input_error}")
        if not input_filename.lower().endswith(".csv"):
            raise ValueError(f"external_parity_reports {index}: input_filename must end with .csv")
        include_full_history = row.get("include_full_history")
        if not isinstance(include_full_history, bool):
            raise ValueError(f"external_parity_reports {index}: include_full_history must be a boolean")
        script_path = row.get("script_path", "Scripts/LorentzianParityCheck.external_src")
        if not isinstance(script_path, str) or not script_path:
            raise ValueError(f"external_parity_reports {index}: script_path must be a non-empty string")
        if script_error := manifest_relative_path_error(script_path):
            raise ValueError(f"external_parity_reports {index}: script_path {script_error}")
        if not script_path.lower().endswith(".external_src"):
            raise ValueError(f"external_parity_reports {index}: script_path must end with .external_src")
        required = row.get("required", True)
        if not isinstance(required, bool):
            raise ValueError(f"external_parity_reports {index}: required must be a boolean")
        tolerance = row.get("tolerance")
        if tolerance is not None and (isinstance(tolerance, bool) or not isinstance(tolerance, int | float)):
            raise ValueError(f"external_parity_reports {index}: tolerance must be a number")


def load_manifest_payload(path: str | Path) -> dict[str, object]:
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise ValueError(f"manifest not found: {manifest_path}")
    if not manifest_path.is_file():
        raise ValueError(f"manifest path is not a file: {manifest_path}")
    with manifest_path.open() as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("manifest must be a JSON object")
    validate_manifest_integrity(payload)
    return payload


def parity_fixture_from_mapping(fixture: dict[str, object], index: int, section: str) -> ParityFixture:
    filename = fixture.get("filename")
    if not isinstance(filename, str) or not filename:
        raise ValueError(f"{section} {index}: filename is required")
    filename_error = manifest_relative_path_error(filename)
    if filename_error:
        raise ValueError(f"{section} {index}: filename {filename_error}")
    name = fixture.get("name", filename)
    if not isinstance(name, str) or not name:
        raise ValueError(f"{section} {index}: name must be a non-empty string")
    raw_settings = fixture.get("settings", {})
    if not isinstance(raw_settings, dict):
        raise ValueError(f"{name}: settings must be an object")
    tolerance = fixture.get("tolerance")
    if tolerance is not None:
        tolerance = float(tolerance)
    return ParityFixture(
        name=name,
        filename=filename,
        settings=settings_from_mapping(raw_settings),
        tolerance=tolerance,
    )


def load_parity_manifest(path: str | Path) -> list[ParityFixture]:
    payload = load_manifest_payload(path)
    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, list):
        raise ValueError("manifest must contain a fixtures list")

    specs: list[ParityFixture] = []
    for index, fixture in enumerate(fixtures, start=1):
        if not isinstance(fixture, dict):
            raise ValueError(f"fixture {index}: expected object")
        specs.append(parity_fixture_from_mapping(fixture, index, "fixture"))
    return specs


def load_required_fixture_cases(payload: dict[str, object]) -> tuple[list[ParityFixture], list[str]]:
    cases = payload.get("required_uncovered_fixture_cases", [])
    if not isinstance(cases, list):
        raise ValueError("required_uncovered_fixture_cases must be a list")

    specs: list[ParityFixture] = []
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"required_uncovered_fixture_cases {index}: expected object")
        specs.append(parity_fixture_from_mapping(case, index, "required_uncovered_fixture_cases"))
    return specs, []


def required_fixture_case_by_name(payload: dict[str, object], name: str) -> ParityFixture:
    cases = payload.get("required_uncovered_fixture_cases", [])
    if not isinstance(cases, list):
        raise ValueError("required_uncovered_fixture_cases must be a list")

    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            continue
        spec = parity_fixture_from_mapping(case, index, "required_uncovered_fixture_cases")
        if spec.name == name or spec.filename == name:
            return spec
    raise ValueError(f"required uncovered fixture case not found: {name}")


def load_source_specs(payload: dict[str, object], section: str) -> list[PineSourceSpec]:
    sources = payload.get(section, [])
    if not isinstance(sources, list):
        raise ValueError(f"{section} must be a list")

    specs: list[PineSourceSpec] = []
    for index, source in enumerate(sources, start=1):
        if not isinstance(source, dict):
            raise ValueError(f"{section} {index}: expected object")
        name = source.get("name")
        path = source.get("path")
        role = source.get("role", "")
        sha256 = source.get("sha256")
        code_sha256 = source.get("code_sha256")
        allow_debug_markers = source.get("allow_debug_markers", True)
        if not isinstance(name, str) or not name:
            raise ValueError(f"{section} {index}: name is required")
        if not isinstance(path, str) or not path:
            raise ValueError(f"{section} {index}: path is required")
        path_error = manifest_relative_path_error(path)
        if path_error:
            raise ValueError(f"{section} {index}: path {path_error}")
        if not isinstance(role, str):
            raise ValueError(f"{section} {index}: role must be a string")
        if sha256 is not None and (not isinstance(sha256, str) or not is_sha256_hex(sha256)):
            raise ValueError(f"{section} {index}: sha256 must be a 64-character lowercase hex string")
        if code_sha256 is not None and (not isinstance(code_sha256, str) or not is_sha256_hex(code_sha256)):
            raise ValueError(f"{section} {index}: code_sha256 must be a 64-character lowercase hex string")
        if not isinstance(allow_debug_markers, bool):
            raise ValueError(f"{section} {index}: allow_debug_markers must be a boolean")
        specs.append(
            PineSourceSpec(
                name=name,
                path=path,
                role=role,
                sha256=sha256,
                code_sha256=code_sha256,
                allow_debug_markers=allow_debug_markers,
            )
        )
    return specs


def load_pine_source_specs(payload: dict[str, object]) -> list[PineSourceSpec]:
    return load_source_specs(payload, "pine_sources")


def load_external_source_specs(payload: dict[str, object]) -> list[PineSourceSpec]:
    return load_source_specs(payload, "external_sources")


def load_external_parity_report_specs(payload: dict[str, object]) -> list[externalParityReportSpec]:
    rows = payload.get("external_parity_reports", [])
    if not isinstance(rows, list):
        raise ValueError("external_parity_reports must be a list")

    specs: list[externalParityReportSpec] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"external_parity_reports {index}: expected object")
        name = row.get("name")
        filename = row.get("filename")
        role = row.get("role")
        input_filename = row.get("input_filename")
        include_full_history = row.get("include_full_history")
        script_path = row.get("script_path", "Scripts/LorentzianParityCheck.external_src")
        required = row.get("required", True)
        tolerance = row.get("tolerance")
        if not isinstance(name, str) or not name:
            raise ValueError(f"external_parity_reports {index}: name must be a non-empty string")
        if not isinstance(filename, str) or not filename:
            raise ValueError(f"external_parity_reports {index}: filename must be a non-empty string")
        if filename_error := manifest_relative_path_error(filename):
            raise ValueError(f"external_parity_reports {index}: filename {filename_error}")
        if not isinstance(role, str) or not role.strip():
            raise ValueError(f"external_parity_reports {index}: role must be a non-empty string")
        if not isinstance(input_filename, str) or not input_filename:
            raise ValueError(f"external_parity_reports {index}: input_filename must be a non-empty string")
        if input_error := manifest_relative_path_error(input_filename):
            raise ValueError(f"external_parity_reports {index}: input_filename {input_error}")
        if not isinstance(include_full_history, bool):
            raise ValueError(f"external_parity_reports {index}: include_full_history must be a boolean")
        if not isinstance(script_path, str) or not script_path:
            raise ValueError(f"external_parity_reports {index}: script_path must be a non-empty string")
        if script_error := manifest_relative_path_error(script_path):
            raise ValueError(f"external_parity_reports {index}: script_path {script_error}")
        if not isinstance(required, bool):
            raise ValueError(f"external_parity_reports {index}: required must be a boolean")
        if tolerance is not None and (isinstance(tolerance, bool) or not isinstance(tolerance, int | float)):
            raise ValueError(f"external_parity_reports {index}: tolerance must be a number")
        specs.append(
            externalParityReportSpec(
                name=name,
                filename=filename,
                role=role.strip(),
                input_filename=input_filename,
                include_full_history=include_full_history,
                script_path=script_path,
                required=required,
                tolerance=float(tolerance) if tolerance is not None else None,
            )
        )
    return specs


def manifest_path_from_arg(value: str | None) -> Path | None:
    if value:
        return Path(value)
    if DEFAULT_MANIFEST.exists():
        return DEFAULT_MANIFEST
    return None


def parity_fixtures_from_arg(value: str | None) -> list[ParityFixture]:
    manifest_path = manifest_path_from_arg(value)
    if manifest_path is not None:
        return load_parity_manifest(manifest_path)
    return builtin_parity_fixtures()


def uncovered_case_label(case: object) -> str:
    if isinstance(case, str):
        return case
    if isinstance(case, dict):
        name = case.get("name")
        filename = case.get("filename")
        if isinstance(name, str) and isinstance(filename, str):
            return f"{name} ({filename})"
        if isinstance(name, str):
            return name
        if isinstance(filename, str):
            return filename
    return str(case)


def parity_summary(
    tv_rows: list[TvRow], py_rows, tolerance: float, settings: Settings | None = None
) -> tuple[dict[str, object], list[dict[str, str]]]:
    settings = settings or Settings()
    last_bar_index = len(tv_rows) - 1
    max_bars_back_index = last_bar_index - settings.max_bars_back if last_bar_index >= settings.max_bars_back else 0
    max_diff = {key: 0.0 for key in ["f1", "f2", "f3", "f4", "f5", "kernel"]}
    compared = {key: 0 for key in max_diff}
    numeric_mismatches = {key: 0 for key in max_diff}
    row_count_mismatch = len(tv_rows) != len(py_rows)
    pred_mismatch = 0
    dir_mismatch = 0
    buy_mismatch = 0
    sell_mismatch = 0
    stop_buy_mismatch = 0
    stop_sell_mismatch = 0
    stream_mismatch = 0
    optional_compared = {
        key: 0
        for key in [
            "open_position_alert",
            "open_long_alert",
            "close_long_alert",
            "open_short_alert",
            "close_short_alert",
            "close_position_alert",
            "kernel_bullish_alert",
            "kernel_bearish_alert",
            "kernel_plot_color",
            "prediction_label",
            "prediction_label_y",
            "prediction_label_color",
            "bar_color",
            "trade_stats_visible",
            "trade_stats_header",
            "total_wins",
            "total_losses",
            "total_early_signal_flips",
            "total_trades",
            "win_loss_ratio",
            "table_wl_ratio",
            "win_rate",
        ]
    }
    optional_mismatches = {key: 0 for key in optional_compared}
    details: list[dict[str, str]] = []
    for i, (tv, py) in enumerate(zip(tv_rows, py_rows)):
        row_numeric_mismatches: list[str] = []
        row_reasons: list[str] = []
        for key in ["f1", "f2", "f3", "f4", "f5", "kernel"]:
            tv_value = getattr(tv, key)
            py_value = getattr(py, key)
            if math.isnan(tv_value):
                continue
            compared[key] += 1
            if math.isnan(py_value):
                numeric_mismatches[key] += 1
                row_numeric_mismatches.append(key)
                continue
            diff = abs(py_value - tv_value)
            max_diff[key] = max(max_diff[key], diff)
            if diff > tolerance:
                numeric_mismatches[key] += 1
                row_numeric_mismatches.append(key)
        if i >= max_bars_back_index:
            if tv.prediction != py.prediction:
                pred_mismatch += 1
                row_reasons.append("prediction")
            if tv.direction != py.direction:
                dir_mismatch += 1
                row_reasons.append("direction")
            if tv.buy != py.buy:
                buy_mismatch += 1
                row_reasons.append("buy")
            if tv.sell != py.sell:
                sell_mismatch += 1
                row_reasons.append("sell")
            if tv.exit_buy != py.stop_buy:
                stop_buy_mismatch += 1
                row_reasons.append("stop_buy")
            if tv.exit_sell != py.stop_sell:
                stop_sell_mismatch += 1
                row_reasons.append("stop_sell")
            if tv.backtest_stream is not None and tv.backtest_stream != py.backtest_stream:
                stream_mismatch += 1
                row_reasons.append("backtest_stream")
            for key in [
                "open_position_alert",
                "open_long_alert",
                "close_long_alert",
                "open_short_alert",
                "close_short_alert",
                "close_position_alert",
                "kernel_bullish_alert",
                "kernel_bearish_alert",
                "kernel_plot_color",
                "prediction_label",
                "prediction_label_color",
                "bar_color",
                "trade_stats_visible",
                "trade_stats_header",
                "total_wins",
                "total_losses",
                "total_early_signal_flips",
                "total_trades",
            ]:
                tv_value = getattr(tv, key)
                if tv_value is None:
                    continue
                optional_compared[key] += 1
                if tv_value != getattr(py, key):
                    optional_mismatches[key] += 1
                    row_reasons.append(key)
            for key in ["prediction_label_y", "win_loss_ratio", "table_wl_ratio", "win_rate"]:
                tv_value = getattr(tv, key)
                if tv_value is None:
                    continue
                py_value = getattr(py, key)
                optional_compared[key] += 1
                if math.isnan(py_value) or abs(tv_value - py_value) > tolerance:
                    optional_mismatches[key] += 1
                    row_reasons.append(key)
        row_reasons.extend(row_numeric_mismatches)
        if row_reasons:
            details.append(
                {
                    "time": tv.bar.time,
                    "mismatch_reasons": ";".join(row_reasons),
                    "tv_prediction": str(tv.prediction),
                    "python_prediction": str(py.prediction),
                    "tv_direction": str(tv.direction),
                    "python_direction": str(py.direction),
                    "tv_buy": "1" if tv.buy else "",
                    "python_buy": "1" if py.buy else "",
                    "tv_sell": "1" if tv.sell else "",
                    "python_sell": "1" if py.sell else "",
                    "tv_stop_buy": "1" if tv.exit_buy else "",
                    "python_stop_buy": "1" if py.stop_buy else "",
                    "tv_stop_sell": "1" if tv.exit_sell else "",
                    "python_stop_sell": "1" if py.stop_sell else "",
                    "tv_backtest_stream": "" if tv.backtest_stream is None else str(tv.backtest_stream),
                    "python_backtest_stream": str(py.backtest_stream),
                }
            )
    pass_features = all(count == 0 for count in numeric_mismatches.values())
    pass_signals = (
        pred_mismatch
        == dir_mismatch
        == buy_mismatch
        == sell_mismatch
        == stop_buy_mismatch
        == stop_sell_mismatch
        == stream_mismatch
        == 0
    )
    pass_optional = all(count == 0 for count in optional_mismatches.values())
    return (
        {
            "rows": len(tv_rows),
            "max_bars_back_index": max_bars_back_index,
            "compared": compared,
            "max_diff": max_diff,
            "row_count_mismatch": row_count_mismatch,
            "python_rows": len(py_rows),
            "numeric_mismatches": numeric_mismatches,
            "prediction_mismatches": pred_mismatch,
            "direction_mismatches": dir_mismatch,
            "buy_mismatches": buy_mismatch,
            "sell_mismatches": sell_mismatch,
            "stop_buy_mismatches": stop_buy_mismatch,
            "stop_sell_mismatches": stop_sell_mismatch,
            "backtest_stream_mismatches": stream_mismatch,
            "optional_compared": optional_compared,
            "optional_mismatches": optional_mismatches,
            "pass": not row_count_mismatch and pass_features and pass_signals and pass_optional,
        },
        details,
    )


def print_optional_parity(summary: dict[str, object]) -> None:
    compared = summary["optional_compared"]
    mismatches = summary["optional_mismatches"]
    assert isinstance(compared, dict)
    assert isinstance(mismatches, dict)
    for key in compared:
        if compared[key]:
            print(f"{key} mismatches: {mismatches[key]} over {compared[key]} rows")


def fixture_path_for(fixture_dirs: list[Path], filename: str) -> Path | None:
    for fixture_dir in fixture_dirs:
        candidate = fixture_dir / filename
        if candidate.is_file():
            return candidate
    return None


def csv_header_fields(path: Path) -> list[str]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        first_line = handle.readline().strip()
    if not first_line:
        return []
    delimiter = "\t" if "\t" in first_line and "," not in first_line else ","
    return [field.strip() for field in next(csv.reader([first_line], delimiter=delimiter), [])]


def full_numeric_export_columns_for_settings(settings: Settings) -> list[str]:
    return [
        str(row["column"])
        for row in pine_export_series_for_settings(settings, include_full=True)
        if row["export_mode"] != "encoded_helper_required"
    ]


def settings_fingerprint(settings: Settings) -> int:
    payload = {
        field.name: getattr(settings, field.name)
        for field in fields(Settings)
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        first_line = handle.readline()
    delimiter = "\t" if "\t" in first_line and "," not in first_line else ","
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def pine_export_schema(
    path: Path,
    settings: Settings | None = None,
    require_full_numeric: bool = False,
    expected_settings_fingerprint: int | None = None,
) -> dict[str, object]:
    if not path.exists():
        return {
            "valid": False,
            "present": False,
            "is_file": False,
            "fields": [],
            "row_count": 0,
            "duplicate_columns": [],
            "missing_required_columns": [],
            "missing_full_numeric_columns": [],
            "full_numeric_required_columns": None,
            "settings_fingerprint": None,
            "required_columns": pine_export_columns_for_settings(settings, include_full=False)
            if settings
            else MINIMUM_PINE_EXPORT_COLUMNS,
            "expected_feature_columns": feature_export_columns(settings) if settings else None,
        }
    if not path.is_file():
        return {
            "valid": False,
            "present": True,
            "is_file": False,
            "fields": [],
            "row_count": 0,
            "duplicate_columns": [],
            "missing_required_columns": [],
            "missing_full_numeric_columns": [],
            "full_numeric_required_columns": None,
            "settings_fingerprint": None,
            "required_columns": pine_export_columns_for_settings(settings, include_full=False)
            if settings
            else MINIMUM_PINE_EXPORT_COLUMNS,
            "expected_feature_columns": feature_export_columns(settings) if settings else None,
        }
    fields = csv_header_fields(path)
    field_set = set(fields)
    duplicate_columns = duplicate_values(fields)
    data_rows = csv_rows(path) if fields else []
    row_count = len(data_rows)
    missing: list[str] = []
    settings_fingerprint_record: dict[str, object] | None = None
    for column in [
        "time",
        "open",
        "high",
        "low",
        "close",
        "Kernel Regression Estimate",
        "Prediction",
        "Direction",
        "Buy",
        "Sell",
        "StopBuy",
        "StopSell",
    ]:
        if column not in field_set:
            missing.append(column)
    expected_feature_columns = feature_export_columns(settings) if settings else {}
    upper_fields = [field.upper() for field in fields]
    for slot in ["F1", "F2", "F3", "F4", "F5"]:
        expected_column = expected_feature_columns.get(slot)
        if expected_column:
            if expected_column not in field_set:
                missing.append(expected_column)
        elif not any(field.startswith(f"{slot}_") for field in upper_fields):
            missing.append(f"{slot}_*")
    missing_full_numeric: list[str] = []
    full_numeric_required_columns: list[str] | None = None
    if require_full_numeric:
        full_numeric_required_columns = full_numeric_export_columns_for_settings(settings or Settings())
        missing_full_numeric = [column for column in full_numeric_required_columns if column not in field_set]
        missing.extend(column for column in missing_full_numeric if column not in missing)
        if expected_settings_fingerprint is not None:
            fingerprint_values: list[int | None] = []
            mismatch_count = 0
            if SETTINGS_FINGERPRINT_COLUMN not in field_set:
                missing.append(SETTINGS_FINGERPRINT_COLUMN)
            else:
                for row in data_rows:
                    raw_value = row.get(SETTINGS_FINGERPRINT_COLUMN)
                    if raw_value in ("", None):
                        fingerprint_values.append(None)
                        mismatch_count += 1
                        continue
                    try:
                        value = int(round(float(raw_value)))
                    except ValueError:
                        value = None
                    fingerprint_values.append(value)
                    if value != expected_settings_fingerprint:
                        mismatch_count += 1
            settings_fingerprint_record = {
                "column": SETTINGS_FINGERPRINT_COLUMN,
                "expected": expected_settings_fingerprint,
                "present": SETTINGS_FINGERPRINT_COLUMN in field_set,
                "mismatch_count": mismatch_count,
                "values": fingerprint_values[:10],
                "valid": SETTINGS_FINGERPRINT_COLUMN in field_set and mismatch_count == 0,
            }
    return {
        "valid": row_count > 0 and not duplicate_columns and not missing and (
            settings_fingerprint_record is None or bool(settings_fingerprint_record["valid"])
        ),
        "present": True,
        "is_file": True,
        "fields": fields,
        "row_count": row_count,
        "duplicate_columns": duplicate_columns,
        "missing_required_columns": missing,
        "missing_full_numeric_columns": missing_full_numeric,
        "full_numeric_required_columns": full_numeric_required_columns,
        "settings_fingerprint": settings_fingerprint_record,
        "required_columns": pine_export_columns_for_settings(settings, include_full=False)
        if settings
        else MINIMUM_PINE_EXPORT_COLUMNS,
        "expected_feature_columns": expected_feature_columns or None,
    }


def input_csv_schema(path: Path) -> dict[str, object]:
    if not path.exists():
        return {
            "valid": False,
            "present": False,
            "is_file": False,
            "fields": [],
            "row_count": 0,
            "duplicate_columns": [],
            "missing_required_columns": [],
            "required_columns": ["time", "open", "high", "low", "close"],
        }
    if not path.is_file():
        return {
            "valid": False,
            "present": True,
            "is_file": False,
            "fields": [],
            "row_count": 0,
            "duplicate_columns": [],
            "missing_required_columns": [],
            "required_columns": ["time", "open", "high", "low", "close"],
        }
    fields = csv_header_fields(path)
    field_set = set(fields)
    duplicate_columns = duplicate_values(fields)
    data_rows = csv_rows(path) if fields else []
    row_count = len(data_rows)
    required_columns = ["time", "open", "high", "low", "close"]
    missing = [column for column in required_columns if column not in field_set]
    return {
        "valid": row_count > 0 and not duplicate_columns and not missing,
        "present": True,
        "is_file": True,
        "fields": fields,
        "row_count": row_count,
        "duplicate_columns": duplicate_columns,
        "missing_required_columns": missing,
        "required_columns": required_columns,
    }


def cmd_run(args: argparse.Namespace) -> int:
    output_path = Path(args.output)
    error = output_path_error(output_path)
    if error:
        print(f"invalid output path: {error}")
        return 1
    schema = input_csv_schema(Path(args.input))
    if not schema["valid"]:
        print("invalid input CSV")
        if not schema.get("present", True):
            print(f"input file not found: {args.input}")
            return 1
        if not schema.get("is_file", True):
            print(f"input path is not a file: {args.input}")
            return 1
        missing = schema["missing_required_columns"]
        assert isinstance(missing, list)
        if missing:
            print("missing required input columns:")
            for column in missing:
                print(f"  - {column}")
        duplicate_columns = schema["duplicate_columns"]
        assert isinstance(duplicate_columns, list)
        if duplicate_columns:
            print("duplicate input columns:")
            for column in duplicate_columns:
                print(f"  - {column}")
        if schema["row_count"] == 0:
            print("no data rows")
        return 1
    settings = settings_from_args(args)
    try:
        tv_rows, price_scale = read_tradingview_csv(args.input)
        results = calculate(tv_rows, settings=settings, price_scale=price_scale)
    except (KeyError, ValueError, csv.Error) as exc:
        print(f"invalid input CSV: {exc}")
        return 1
    write_results(output_path, results)
    return 0


def cmd_parity(args: argparse.Namespace) -> int:
    settings = settings_from_args(args)
    schema = pine_export_schema(Path(args.input), settings)
    if not schema["valid"]:
        print("invalid Pine export schema")
        if not schema.get("present", True):
            print(f"input file not found: {args.input}")
            return 1
        if not schema.get("is_file", True):
            print(f"input path is not a file: {args.input}")
            return 1
        missing = schema["missing_required_columns"]
        assert isinstance(missing, list)
        if missing:
            print("missing required export columns:")
            for column in missing:
                print(f"  - {column}")
        duplicate_columns = schema["duplicate_columns"]
        assert isinstance(duplicate_columns, list)
        if duplicate_columns:
            print("duplicate export columns:")
            for column in duplicate_columns:
                print(f"  - {column}")
        if schema["row_count"] == 0:
            print("no data rows")
        return 1
    try:
        tv_rows, price_scale = read_tradingview_csv(args.input, feature_columns=feature_export_columns(settings))
        results = calculate(tv_rows, settings=settings, price_scale=price_scale)
    except (KeyError, ValueError, csv.Error) as exc:
        print(f"invalid Pine export data: {exc}")
        return 1
    summary, mismatches = parity_summary(tv_rows, results, args.tolerance, settings)
    if args.output:
        output_path = Path(args.output)
        error = output_path_error(output_path)
        if error:
            print(f"invalid output path: {error}")
            return 1
        with output_path.open("w", newline="") as handle:
            fieldnames = [
                "time",
                "mismatch_reasons",
                "tv_prediction",
                "python_prediction",
                "tv_direction",
                "python_direction",
                "tv_buy",
                "python_buy",
                "tv_sell",
                "python_sell",
                "tv_stop_buy",
                "python_stop_buy",
                "tv_stop_sell",
                "python_stop_sell",
                "tv_backtest_stream",
                "python_backtest_stream",
            ]
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(mismatches)
    print(f"rows: {summary['rows']}")
    if summary["row_count_mismatch"]:
        print(f"python rows: {summary['python_rows']}")
    print(f"stats window starts at index: {summary['max_bars_back_index']}")
    for key in ["f1", "f2", "f3", "f4", "f5", "kernel"]:
        print(f"{key} max diff: {summary['max_diff'][key]:.3e} over {summary['compared'][key]} rows")
        if summary["numeric_mismatches"][key]:
            print(f"{key} numeric mismatches: {summary['numeric_mismatches'][key]}")
    print(f"prediction mismatches: {summary['prediction_mismatches']}")
    print(f"direction mismatches: {summary['direction_mismatches']}")
    print(f"buy mismatches: {summary['buy_mismatches']}")
    print(f"sell mismatches: {summary['sell_mismatches']}")
    print(f"stop-buy mismatches: {summary['stop_buy_mismatches']}")
    print(f"stop-sell mismatches: {summary['stop_sell_mismatches']}")
    print(f"backtest stream mismatches: {summary['backtest_stream_mismatches']}")
    print_optional_parity(summary)
    return 0 if summary["pass"] else 1


def validate_fixture_spec(
    fixture_dirs: list[Path],
    spec: ParityFixture,
    default_tolerance: float,
    output_mismatches: str | None,
    require_full_numeric: bool = False,
) -> tuple[bool, bool]:
    record, mismatches = validate_fixture_record(fixture_dirs, spec, default_tolerance, require_full_numeric)
    if not record["present"]:
        return False, False

    print_fixture_record(record)

    if mismatches and output_mismatches:
        path = Path(str(record["path"]))
        out_path = Path(output_mismatches)
        error = output_directory_error(out_path)
        if error:
            print(f"invalid output mismatch directory: {error}")
            return True, False
        out_path.mkdir(parents=True, exist_ok=True)
        mismatch_path = out_path / f"{path.stem}.mismatches.csv"
        with mismatch_path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(mismatches[0]))
            writer.writeheader()
            writer.writerows(mismatches)
        print(f"  wrote mismatches: {mismatch_path}")
    return True, bool(record["passed"])


def validate_fixture_record(
    fixture_dirs: list[Path],
    spec: ParityFixture,
    default_tolerance: float,
    require_full_numeric: bool = False,
) -> tuple[dict[str, object], list[dict[str, str]]]:
    path = fixture_path_for(fixture_dirs, spec.filename)
    tolerance = spec.tolerance if spec.tolerance is not None else default_tolerance
    record: dict[str, object] = {
        "name": spec.name,
        "filename": spec.filename,
        "present": path is not None,
        "path": str(path) if path else None,
        "passed": False,
        "tolerance": tolerance,
        "summary": None,
        "mismatch_count": None,
        "schema": None,
        "error": None,
    }
    if path is None:
        return record, []

    schema = pine_export_schema(
        path,
        spec.settings,
        require_full_numeric,
        settings_fingerprint(spec.settings) if require_full_numeric else None,
    )
    record["schema"] = schema
    if not schema["valid"]:
        duplicate_columns = schema.get("duplicate_columns")
        if duplicate_columns:
            record["error"] = "duplicate export columns: " + ", ".join(
                str(column) for column in duplicate_columns
            )
            return record, []
        missing_columns = schema["missing_required_columns"]
        if missing_columns:
            record["error"] = "missing required export columns: " + ", ".join(
                str(column) for column in missing_columns
            )
        elif schema.get("row_count") == 0:
            record["error"] = "no data rows"
        else:
            fingerprint = schema.get("settings_fingerprint")
            if isinstance(fingerprint, dict):
                record["error"] = (
                    "settings fingerprint mismatch: "
                    f"expected {fingerprint['expected']}, "
                    f"mismatched rows {fingerprint['mismatch_count']}"
                )
            else:
                record["error"] = "invalid Pine export schema"
        return record, []

    try:
        tv_rows, price_scale = read_tradingview_csv(path, feature_columns=feature_export_columns(spec.settings))
        results = calculate(tv_rows, settings=spec.settings, price_scale=price_scale)
    except (KeyError, ValueError, csv.Error) as exc:
        record["error"] = f"invalid Pine export data: {exc}"
        return record, []
    summary, mismatches = parity_summary(tv_rows, results, tolerance, spec.settings)
    record["passed"] = bool(summary["pass"])
    record["summary"] = summary
    record["mismatch_count"] = len(mismatches)
    return record, mismatches


def print_fixture_record(record: dict[str, object]) -> None:
    summary = record["summary"]
    if summary is None:
        status = "PASS" if record["passed"] else "FAIL"
        print(f"{status} {record['filename']}: {record.get('error') or 'not validated'}")
        return
    assert isinstance(summary, dict)
    status = "PASS" if record["passed"] else "FAIL"
    print(
        f"{status} {record['filename']}: rows={summary['rows']} "
        f"python_rows={summary['python_rows']} "
        f"prediction_mismatches={summary['prediction_mismatches']} "
        f"direction_mismatches={summary['direction_mismatches']} "
        f"buy_mismatches={summary['buy_mismatches']} "
        f"sell_mismatches={summary['sell_mismatches']} "
        f"stop_buy_mismatches={summary['stop_buy_mismatches']} "
        f"stop_sell_mismatches={summary['stop_sell_mismatches']} "
        f"backtest_stream_mismatches={summary['backtest_stream_mismatches']}"
    )
    for key in ["f1", "f2", "f3", "f4", "f5", "kernel"]:
        print(
            f"  {key}: max_diff={summary['max_diff'][key]:.3e}, "
            f"compared={summary['compared'][key]}, mismatches={summary['numeric_mismatches'][key]}"
        )
    print_optional_parity(summary)


def default_fixture_root() -> Path:
    env_value = os.environ.get(FIXTURE_DIR_ENV_VAR)
    return Path(env_value) if env_value else DEFAULT_external_ROOT


def fixture_dir_from_arg(value: str | None) -> Path:
    path = Path(value) if value else default_fixture_root()
    files_path = path / "Files"
    if files_path.is_dir():
        return files_path
    return path


def fixture_search_dirs_from_arg(value: str | None) -> list[Path]:
    path = Path(value) if value else default_fixture_root()
    candidates = [path]
    files_path = path / "Files"
    if files_path.is_dir():
        candidates.insert(0, files_path)

    search_dirs: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            search_dirs.append(candidate)
            seen.add(resolved)
    return search_dirs


def fixture_directory_status(fixture_dirs: list[Path]) -> tuple[list[Path], str | None]:
    existing_dirs = [path for path in fixture_dirs if path.is_dir()]
    if existing_dirs:
        return existing_dirs, None
    existing_non_dir = next((path for path in fixture_dirs if path.exists()), None)
    if existing_non_dir is not None:
        return [], f"fixture path is not a directory: {existing_non_dir}"
    return [], f"fixture directory not found: {fixture_dirs[0]}"


def ignored_csv_candidate_rows(
    payload: dict[str, object], fixture_dirs: list[Path]
) -> list[dict[str, object]]:
    rows = object_rows(payload, "ignored_csv_candidates")
    ignored: list[dict[str, object]] = []
    for row in rows:
        filename = str(row["filename"])
        path = fixture_path_for(fixture_dirs, filename)
        ignored.append(
            {
                "filename": filename,
                "reason": str(row["reason"]).strip(),
                "present": path is not None,
                "path": str(path) if path else None,
                "classification": classify_csv_candidate(path) if path else "missing_csv",
            }
        )
    return ignored


def untracked_csv_candidate_rows(
    fixture_dirs: list[Path], manifest_filenames: set[str]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen_csvs: set[Path] = set()
    for fixture_dir in fixture_dirs:
        for path in sorted(fixture_dir.glob("*.csv")):
            resolved = path.resolve()
            if resolved in seen_csvs:
                continue
            seen_csvs.add(resolved)
            if path.name in manifest_filenames:
                continue
            rows.append(
                {"path": str(path), "filename": path.name, "classification": classify_csv_candidate(path)}
            )
    return rows


def external_parity_report_action(spec: externalParityReportSpec) -> dict[str, object]:
    windows_script_path = spec.script_path.replace("/", "\\")
    return {
        "name": spec.name,
        "script_path": spec.script_path,
        "compile_command": [
            "external-compiler",
            f'/compile:"{windows_script_path}"',
            '/inc:"."',
            "/log",
        ],
        "script_inputs": {
            "InpInputFile": spec.input_filename,
            "InpOutputFile": spec.filename,
            "InpIncludeFullHist": spec.include_full_history,
        },
        "output_file": spec.filename,
        "manual_run": (
            "Attach the compiled script to any external_runtime chart; the chart data is ignored because "
            "the script reads InpInputFile from external/Files."
        ),
    }


def external_bool(value: object) -> str:
    return "true" if value is True else "false"


def shell_command_string(command: list[object]) -> str:
    return shlex.join(str(item) for item in command)


def external_runner_slug(value: object) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(value)).strip("_").lower()
    return slug or "parity_report"


def external_runner_record(row: dict[str, object], output_dir: Path, symbol: str, period: str) -> dict[str, object]:
    name = str(row["name"])
    slug = external_runner_slug(name)
    script_path = str(row.get("script_path") or "Scripts/LorentzianParityCheck.external_src")
    script_name = PureWindowsPath(script_path.replace("/", "\\")).stem
    preset_name = f"lorentzian_parity_{slug}.set"
    ini_name = f"run_parity_{slug}.ini"
    preset_relative = Path("Presets") / preset_name
    ini_relative = Path("Scripts") / "parity_debug" / ini_name
    terminal_config = PureWindowsPath("external") / PureWindowsPath(str(ini_relative).replace("/", "\\"))
    preset_text = "\n".join(
        [
            f"InpInputFile={row['input_filename']}",
            f"InpOutputFile={row['filename']}",
            f"InpIncludeFullHist={external_bool(row['include_full_history'])}",
            "",
        ]
    )
    ini_text = "\n".join(
        [
            "[StartUp]",
            f"Script={script_name}",
            f"ScriptParameters={preset_name}",
            f"Symbol={symbol}",
            f"Period={period}",
            "ShutdownTerminal=1",
            "",
        ]
    )
    return {
        "name": name,
        "report_filename": row["filename"],
        "input_filename": row["input_filename"],
        "include_full_history": row["include_full_history"],
        "script_path": script_path,
        "status": "pending" if row.get("passed") is None else ("passed" if row.get("passed") else "failed"),
        "error": row.get("error"),
        "stale": row.get("stale"),
        "preset_file": str(preset_relative),
        "startup_config": str(ini_relative),
        "preset_path": str(output_dir / preset_relative),
        "startup_config_path": str(output_dir / ini_relative),
        "terminal_command": [
            "external_runtime.exe",
            "/portable",
            f'/config:"{terminal_config}"',
        ],
        "preset_text": preset_text,
        "startup_config_text": ini_text,
    }


def external_runner_pack_records(
    payload: dict[str, object],
    fixture_dirs: list[Path],
    output_dir: Path,
    cases: list[str] | None,
    tolerance: float,
    only_failing: bool,
    symbol: str,
    period: str,
) -> list[dict[str, object]]:
    if only_failing:
        rows, _, _ = external_report_checklist(payload, fixture_dirs, tolerance)
    else:
        rows = [
            {
                "name": spec.name,
                "filename": spec.filename,
                "role": spec.role,
                "input_filename": spec.input_filename,
                "include_full_history": spec.include_full_history,
                "script_path": spec.script_path,
                "required": spec.required,
                "passed": None,
                "error": None,
                "stale": None,
            }
            for spec in load_external_parity_report_specs(payload)
        ]
    rows = filter_external_parity_report_records(rows, cases)
    if only_failing:
        rows = [row for row in rows if not row.get("passed")]
    return [external_runner_record(row, output_dir, symbol, period) for row in rows]


def write_external_runner_pack(records: list[dict[str, object]], output_dir: Path) -> None:
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError(f"invalid output path: output directory not found: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "Presets").mkdir(parents=True, exist_ok=True)
    (output_dir / "Scripts" / "parity_debug").mkdir(parents=True, exist_ok=True)

    for record in records:
        preset_path = Path(str(record["preset_path"]))
        startup_path = Path(str(record["startup_config_path"]))
        preset_path.write_text(str(record["preset_text"]))
        startup_path.write_text(str(record["startup_config_text"]))

    manifest_records = [
        {key: value for key, value in record.items() if key not in {"preset_text", "startup_config_text"}}
        for record in records
    ]
    (output_dir / "external_runner_pack.json").write_text(
        json.dumps({"records": manifest_records}, indent=2, sort_keys=True) + "\n"
    )


def parse_simple_key_value_file(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("[") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def external_runner_pack_verification(input_dir: Path) -> dict[str, object]:
    errors: list[str] = []
    index_path = input_dir / "external_runner_pack.json"
    if not input_dir.exists():
        return {"input": str(input_dir), "valid": False, "record_count": 0, "errors": [f"input directory not found: {input_dir}"]}
    if not input_dir.is_dir():
        return {"input": str(input_dir), "valid": False, "record_count": 0, "errors": [f"runner pack input is not a directory: {input_dir}"]}
    if not index_path.exists():
        return {"input": str(input_dir), "valid": False, "record_count": 0, "errors": [f"runner pack index not found: {index_path}"]}
    if not index_path.is_file():
        return {"input": str(input_dir), "valid": False, "record_count": 0, "errors": [f"invalid runner pack index: path is not a file: {index_path}"]}

    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"input": str(input_dir), "valid": False, "record_count": 0, "errors": [f"invalid runner pack index: {exc}"]}

    records = payload.get("records")
    if not isinstance(records, list):
        return {"input": str(input_dir), "valid": False, "record_count": 0, "errors": ["invalid runner pack index: records must be a list"]}

    seen_names: set[str] = set()
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            errors.append(f"record {index}: expected object")
            continue
        name = str(record.get("name") or f"record {index}")
        if name in seen_names:
            errors.append(f"{name}: duplicate runner-pack record name")
        seen_names.add(name)
        for key in ["report_filename", "input_filename", "script_path"]:
            if not isinstance(record.get(key), str) or not record.get(key):
                errors.append(f"{name}: missing {key}")
        if not isinstance(record.get("include_full_history"), bool):
            errors.append(f"{name}: include_full_history must be a boolean")

        preset_file = record.get("preset_file")
        startup_config = record.get("startup_config")
        if not isinstance(preset_file, str) or not preset_file:
            errors.append(f"{name}: missing preset_file")
            continue
        if not isinstance(startup_config, str) or not startup_config:
            errors.append(f"{name}: missing startup_config")
            continue
        if windows_artifact_path_error(preset_file):
            errors.append(f"{name}: invalid preset_file path: {preset_file}")
            continue
        if windows_artifact_path_error(startup_config):
            errors.append(f"{name}: invalid startup_config path: {startup_config}")
            continue

        preset_path = input_dir / preset_file
        startup_path = input_dir / startup_config
        if not is_path_within(preset_path, input_dir):
            errors.append(f"{name}: preset_file escapes runner pack: {preset_file}")
            continue
        if not is_path_within(startup_path, input_dir):
            errors.append(f"{name}: startup_config escapes runner pack: {startup_config}")
            continue
        if not preset_path.is_file():
            errors.append(f"{name}: preset file not found: {preset_path}")
            continue
        if not startup_path.is_file():
            errors.append(f"{name}: startup config not found: {startup_path}")
            continue

        preset_values = parse_simple_key_value_file(preset_path.read_text(encoding="utf-8"))
        startup_values = parse_simple_key_value_file(startup_path.read_text(encoding="utf-8"))
        expected_include = external_bool(record.get("include_full_history"))
        expected_preset_values = {
            "InpInputFile": str(record.get("input_filename")),
            "InpOutputFile": str(record.get("report_filename")),
            "InpIncludeFullHist": expected_include,
        }
        for key, expected in expected_preset_values.items():
            if preset_values.get(key) != expected:
                errors.append(f"{name}: {preset_file} {key} expected {expected!r}, got {preset_values.get(key)!r}")

        script_path = str(record.get("script_path") or "Scripts/LorentzianParityCheck.external_src")
        expected_script = PureWindowsPath(script_path.replace("/", "\\")).stem
        expected_preset_name = Path(preset_file).name
        expected_startup_values = {
            "Script": expected_script,
            "ScriptParameters": expected_preset_name,
            "ShutdownTerminal": "1",
        }
        for key, expected in expected_startup_values.items():
            if startup_values.get(key) != expected:
                errors.append(f"{name}: {startup_config} {key} expected {expected!r}, got {startup_values.get(key)!r}")

        terminal_command = record.get("terminal_command")
        if not isinstance(terminal_command, list) or "external_runtime.exe" not in [str(item) for item in terminal_command]:
            errors.append(f"{name}: terminal_command missing external_runtime.exe")

    return {"input": str(input_dir), "valid": not errors, "record_count": len(records), "errors": errors}


def external_parity_script_artifact_records(
    fixture_dirs: list[Path], specs: list[externalParityReportSpec]
) -> list[dict[str, object]]:
    workspaces = workspace_roots(fixture_dirs)
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for spec in specs:
        script_path = spec.script_path
        if script_path in seen:
            continue
        seen.add(script_path)
        source_path = source_path_for(workspaces, script_path)
        artifact_path = source_path.with_suffix(".compiled") if source_path else None
        source_mtime = source_path.stat().st_mtime if source_path else None
        artifact_present = artifact_path.exists() if artifact_path else False
        artifact_mtime = artifact_path.stat().st_mtime if artifact_present and artifact_path else None
        passed = (
            source_path is not None
            and artifact_path is not None
            and artifact_present
            and artifact_mtime is not None
            and source_mtime is not None
            and artifact_mtime >= source_mtime
        )
        if source_path is None:
            detail = "external parity script source is missing"
        elif not artifact_present:
            detail = "external parity script .compiled artifact is missing"
        elif not passed:
            detail = f"external parity script .compiled is older than {source_path}"
        else:
            detail = "external parity script .compiled artifact is present and newer than its source"
        records.append(
            {
                "script_path": script_path,
                "source_path": str(source_path) if source_path else None,
                "artifact_path": str(artifact_path) if artifact_path else None,
                "source_mtime": source_mtime,
                "artifact_present": artifact_present,
                "artifact_mtime": artifact_mtime,
                "passed": passed,
                "detail": detail,
            }
        )
    return records


def external_parity_script_contract_records(
    fixture_dirs: list[Path], specs: list[externalParityReportSpec]
) -> list[dict[str, object]]:
    workspaces = workspace_roots(fixture_dirs)
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for spec in specs:
        script_path = spec.script_path
        if script_path in seen:
            continue
        seen.add(script_path)
        source_path = source_path_for(workspaces, script_path)
        record: dict[str, object] = {
            "script_path": script_path,
            "source_path": str(source_path) if source_path else None,
            "present": source_path is not None,
            "passed": False,
            "missing_inputs": [],
            "missing_usage": [],
            "missing_report_columns": [],
            "errors": [],
        }
        if source_path is None:
            record["errors"] = ["external parity script source is missing"]
            records.append(record)
            continue

        source = source_path.read_text(encoding="utf-8", errors="replace")
        missing_inputs = [
            name
            for name, declaration in external_PARITY_SCRIPT_INPUTS.items()
            if re.search(rf"\b{re.escape(declaration)}\s+{re.escape(name)}\s*=", source) is None
        ]
        usage_checks = {
            "script_show_inputs": "#property script_show_inputs" in source,
            "read_input_file": re.search(r"\bReadCSV\s*\(\s*InpInputFile\s*\)", source) is not None,
            "write_output_file": re.search(r"\bFileOpen\s*\(\s*InpOutputFile\s*,\s*FILE_WRITE\b", source) is not None,
            "include_full_history": "InpIncludeFullHist" in source and re.search(r"\bInpIncludeFullHist\b", source) is not None,
        }
        missing_usage = [name for name, passed in usage_checks.items() if not passed]
        missing_columns = [column for column in external_PARITY_REPORT_COLUMNS if f'"{column}"' not in source]
        errors = []
        if missing_inputs:
            errors.append("missing external parity script inputs: " + ", ".join(missing_inputs))
        if missing_usage:
            errors.append("missing external parity script usage: " + ", ".join(missing_usage))
        if missing_columns:
            errors.append("missing external parity report header columns: " + ", ".join(missing_columns))
        record["missing_inputs"] = missing_inputs
        record["missing_usage"] = missing_usage
        record["missing_report_columns"] = missing_columns
        record["errors"] = errors
        record["passed"] = not errors
        records.append(record)
    return records


def external_parity_script_contract_errors(records: list[dict[str, object]]) -> list[str]:
    errors: list[str] = []
    for record in records:
        if record.get("passed"):
            continue
        for error in record.get("errors", []):
            errors.append(f"{record['script_path']}: {error}")
    return errors


def external_parity_report_records(
    fixture_dirs: list[Path],
    specs: list[externalParityReportSpec],
    compiled_records: list[dict[str, object]],
    default_tolerance: float,
    script_artifact_records: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    script_records_by_path = {
        str(record["script_path"]): record for record in script_artifact_records or []
    }
    freshest_artifact_mtime = max(
        (
            float(record["artifact_mtime"])
            for record in compiled_records
            if record.get("passed") and record.get("artifact_mtime") is not None
        ),
        default=None,
    )
    freshest_script_artifact_mtime = max(
        (
            float(record["artifact_mtime"])
            for record in script_records_by_path.values()
            if record.get("passed") and record.get("artifact_mtime") is not None
        ),
        default=None,
    )
    freshest_dependency_mtime = max(
        (mtime for mtime in [freshest_artifact_mtime, freshest_script_artifact_mtime] if mtime is not None),
        default=None,
    )
    records: list[dict[str, object]] = []
    for spec in specs:
        tolerance = spec.tolerance if spec.tolerance is not None else default_tolerance
        path = fixture_path_for(fixture_dirs, spec.filename)
        script_record = script_records_by_path.get(spec.script_path)
        record: dict[str, object] = {
            "name": spec.name,
            "filename": spec.filename,
            "role": spec.role,
            "input_filename": spec.input_filename,
            "include_full_history": spec.include_full_history,
            "script_path": spec.script_path,
            "script_artifact": script_record,
            "regeneration_action": external_parity_report_action(spec),
            "required": spec.required,
            "present": path is not None,
            "path": str(path) if path else None,
            "passed": False,
            "tolerance": tolerance,
            "rows": 0,
            "compared_prediction_rows": 0,
            "prediction_mismatches": 0,
            "compared_direction_rows": 0,
            "direction_mismatches": 0,
            "buy_mismatches": 0,
            "sell_mismatches": 0,
            "max_abs_diff": {},
            "numeric_mismatches": {},
            "stale": None,
            "report_mtime": None,
            "freshest_compiled_artifact_mtime": freshest_artifact_mtime,
            "freshest_script_artifact_mtime": freshest_script_artifact_mtime,
            "freshest_dependency_mtime": freshest_dependency_mtime,
            "error": None,
        }
        if path is None:
            record["error"] = "missing external parity report"
            records.append(record)
            continue
        if not path.is_file():
            record["error"] = "external parity report path is not a file"
            records.append(record)
            continue

        report_mtime = path.stat().st_mtime
        record["report_mtime"] = report_mtime
        if freshest_dependency_mtime is not None:
            record["stale"] = report_mtime < freshest_dependency_mtime

        fields = csv_header_fields(path)
        required_columns = ["time", "pred_match", "dir_match"]
        missing_columns = [column for column in required_columns if column not in fields]
        if missing_columns:
            record["error"] = "missing external parity report columns: " + ", ".join(missing_columns)
            records.append(record)
            continue

        max_abs_diff: dict[str, float] = {
            column: 0.0 for column in fields if column.endswith("_diff")
        }
        numeric_mismatches = {column: 0 for column in max_abs_diff}
        try:
            with path.open(newline="") as handle:
                for row in csv.DictReader(handle):
                    record["rows"] = int(record["rows"]) + 1
                    pred_match = row.get("pred_match", "")
                    if pred_match in {"0", "1"}:
                        record["compared_prediction_rows"] = int(record["compared_prediction_rows"]) + 1
                        if pred_match == "0":
                            record["prediction_mismatches"] = int(record["prediction_mismatches"]) + 1
                    dir_match = row.get("dir_match", "")
                    if dir_match in {"0", "1"}:
                        record["compared_direction_rows"] = int(record["compared_direction_rows"]) + 1
                        if dir_match == "0":
                            record["direction_mismatches"] = int(record["direction_mismatches"]) + 1
                    if row.get("tv_buy", "") != row.get("external_buy", ""):
                        record["buy_mismatches"] = int(record["buy_mismatches"]) + 1
                    if row.get("tv_sell", "") != row.get("external_sell", ""):
                        record["sell_mismatches"] = int(record["sell_mismatches"]) + 1
                    for column in max_abs_diff:
                        value = row.get(column, "")
                        if value in ("", None):
                            continue
                        diff = abs(float(value))
                        max_abs_diff[column] = max(max_abs_diff[column], diff)
                        if diff > tolerance:
                            numeric_mismatches[column] += 1
        except (csv.Error, ValueError) as exc:
            record["error"] = f"invalid external parity report data: {exc}"
            records.append(record)
            continue

        record["max_abs_diff"] = max_abs_diff
        record["numeric_mismatches"] = numeric_mismatches
        if not record["rows"]:
            record["error"] = "external parity report has no data rows"
        elif script_record is not None and not script_record["passed"]:
            record["error"] = "external parity script artifact is missing or stale"
        elif record["stale"]:
            if freshest_script_artifact_mtime is None:
                record["error"] = "external parity report is older than the compiled indicator artifact"
            else:
                record["error"] = "external parity report is older than a compiled external dependency artifact"
        elif record["prediction_mismatches"] or record["direction_mismatches"]:
            record["error"] = "external parity report contains prediction or direction mismatches"
        elif record["buy_mismatches"] or record["sell_mismatches"]:
            record["error"] = "external parity report contains buy/sell signal mismatches"
        elif any(count for count in numeric_mismatches.values()):
            record["error"] = "external parity report contains numeric diffs above tolerance"
        record["passed"] = record["error"] is None
        records.append(record)
    return records


def filter_external_parity_report_records(
    rows: list[dict[str, object]], cases: list[str] | None
) -> list[dict[str, object]]:
    if not cases:
        return rows

    selected: list[dict[str, object]] = []
    for case in cases:
        matches = [
            row
            for row in rows
            if row.get("name") == case or row.get("filename") == case or row.get("input_filename") == case
        ]
        if not matches:
            raise ValueError(f"external parity report case not found: {case}")
        selected.extend(matches)

    deduped: list[dict[str, object]] = []
    seen: set[tuple[object, object]] = set()
    for row in selected:
        key = (row.get("name"), row.get("filename"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def external_report_checklist(
    payload: dict[str, object], fixture_dirs: list[Path], tolerance: float
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    external_source_specs = load_external_source_specs(payload)
    external_report_specs = load_external_parity_report_specs(payload)
    external_source_rows = source_records(fixture_dirs, external_source_specs)
    compiled_rows = external_compiled_artifact_records(external_source_rows)
    script_artifact_rows = external_parity_script_artifact_records(fixture_dirs, external_report_specs)
    report_rows = external_parity_report_records(
        fixture_dirs, external_report_specs, compiled_rows, tolerance, script_artifact_rows
    )
    return report_rows, compiled_rows, script_artifact_rows


def build_fixture_validation_report(
    fixture_dirs: list[Path],
    manifest_path: Path | None,
    tolerance: float,
    require_full_coverage: bool,
    pine_export_source_dir: str = DEFAULT_PINE_EXPORT_SOURCE_DIR,
) -> dict[str, object]:
    fixture_specs = load_parity_manifest(manifest_path) if manifest_path else builtin_parity_fixtures()
    payload = load_manifest_payload(manifest_path) if manifest_path else {}
    pine_records, pine_errors = validate_manifest_pine_sources(fixture_dirs, manifest_path)
    pine_input_contract = pine_input_contract_records(pine_records) if manifest_path else []
    pine_input_contract_error_rows = pine_input_contract_errors(pine_input_contract)
    pine_output_contract = pine_output_contract_records(pine_records) if manifest_path else []
    pine_output_contract_error_rows = pine_output_contract_errors(pine_output_contract)
    external_records, external_errors = validate_manifest_external_sources(fixture_dirs, manifest_path)
    external_indicator_input_contract = external_indicator_input_contract_records(external_records) if manifest_path else []
    external_indicator_input_contract_error_rows = external_indicator_input_contract_errors(
        external_indicator_input_contract
    )
    external_sanity_records = external_source_sanity_records(external_records)
    external_compiled_records = external_compiled_artifact_records(external_records)
    external_parity_report_specs = load_external_parity_report_specs(payload) if manifest_path else []
    external_parity_script_artifact_records_out = external_parity_script_artifact_records(
        fixture_dirs, external_parity_report_specs
    )
    external_parity_script_contract_records_out = external_parity_script_contract_records(
        fixture_dirs, external_parity_report_specs
    )
    external_parity_report_records_out = external_parity_report_records(
        fixture_dirs,
        external_parity_report_specs,
        external_compiled_records,
        tolerance,
        external_parity_script_artifact_records_out,
    )
    source_errors = pine_errors + external_errors

    fixture_records = []
    for spec in fixture_specs:
        record, _mismatches = validate_fixture_record(fixture_dirs, spec, tolerance)
        fixture_records.append(record)

    required_records = []
    required_action_items: list[dict[str, object]] = []
    required_settings_smoke: list[dict[str, object]] = []
    missing_required: list[str] = []
    labels_without_fixture: list[str] = []
    coverage_error: str | None = None
    required_export_workflow: dict[str, object] | None = None
    external_runner_workflow = external_runner_pack_workflow_commands(manifest_path, fixture_dirs) if manifest_path else None
    readiness_artifacts_workflow = (
        readiness_artifacts_workflow_commands(manifest_path, fixture_dirs) if manifest_path else None
    )
    ignored_candidates = ignored_csv_candidate_rows(payload, fixture_dirs) if manifest_path else []
    ignored_filenames = {str(row["filename"]) for row in ignored_candidates}
    external_report_filenames = {spec.filename for spec in external_parity_report_specs}
    manifest_filenames = {spec.filename for spec in fixture_specs} | ignored_filenames | external_report_filenames
    if manifest_path is not None:
        required_specs_for_inventory, labels_for_inventory = load_required_fixture_cases(payload)
        manifest_filenames |= {spec.filename for spec in required_specs_for_inventory}
        manifest_filenames |= set(labels_for_inventory)
    untracked_candidates = untracked_csv_candidate_rows(fixture_dirs, manifest_filenames)
    unexpected_pine_candidates = [
        row for row in untracked_candidates if row["classification"] == "tradingview_pine_export_candidate"
    ]

    if require_full_coverage:
        if manifest_path is None:
            coverage_error = "no parity manifest available"
        else:
            required_export_workflow = export_pack_workflow_commands(
                manifest_path,
                fixture_dirs,
                pine_export_source_dir=pine_export_source_dir,
            )
            required_specs, labels_without_fixture = load_required_fixture_cases(payload)
            checklist_rows = export_checklist_rows(payload, fixture_dirs, manifest_path)
            required_settings_smoke = required_settings_smoke_records(payload, fixture_dirs)
            checklist_by_filename = {
                row["filename"]: row for row in checklist_rows if isinstance(row.get("filename"), str)
            }
            missing_required = list(labels_without_fixture)

            for spec in required_specs:
                record, _mismatches = validate_fixture_record(
                    fixture_dirs, spec, tolerance, require_full_numeric=True
                )
                required_records.append(record)
                if not record["present"]:
                    missing_required.append(uncovered_case_label({"name": spec.name, "filename": spec.filename}))
                    action_item = dict(checklist_by_filename.get(spec.filename, {}))
                    if action_item:
                        action_item["status"] = "missing"
                        required_action_items.append(action_item)
                elif not record["passed"]:
                    action_item = dict(checklist_by_filename.get(spec.filename, {}))
                    if action_item:
                        action_item["status"] = "failing"
                        required_action_items.append(action_item)

    fixtures_passed = all(record["present"] and record["passed"] for record in fixture_records)
    required_passed = (
        coverage_error is None
        and not missing_required
        and all(record["present"] and record["passed"] for record in required_records)
    )
    required_settings_smoke_passed = all(record["passed"] for record in required_settings_smoke)
    external_source_sanity_passed = all(record["passed"] for record in external_sanity_records)
    pine_input_contract_passed = all(record["passed"] for record in pine_input_contract)
    pine_output_contract_passed = all(record["passed"] for record in pine_output_contract)
    external_indicator_input_contract_passed = all(
        record["passed"] for record in external_indicator_input_contract
    )
    external_compiled_artifacts_passed = all(record["passed"] for record in external_compiled_records)
    external_parity_script_artifacts_passed = all(
        record["passed"] for record in external_parity_script_artifact_records_out
    )
    external_parity_script_contract_passed = all(
        record["passed"] for record in external_parity_script_contract_records_out
    )
    required_external_parity_reports_passed = all(
        (not record["required"]) or record["passed"] for record in external_parity_report_records_out
    )
    passed = (
        not source_errors
        and pine_input_contract_passed
        and pine_output_contract_passed
        and external_indicator_input_contract_passed
        and external_source_sanity_passed
        and external_compiled_artifacts_passed
        and external_parity_script_artifacts_passed
        and external_parity_script_contract_passed
        and required_external_parity_reports_passed
        and not unexpected_pine_candidates
        and fixtures_passed
        and (required_passed and required_settings_smoke_passed if require_full_coverage else True)
    )

    summary = {
        "pine_sources_present": sum(1 for record in pine_records if record["present"]),
        "pine_sources_invalid": sum(1 for record in pine_records if not record["valid"]),
        "pine_input_contract_total": len(pine_input_contract),
        "pine_input_contract_failed": sum(1 for record in pine_input_contract if not record["passed"]),
        "pine_output_contract_total": len(pine_output_contract),
        "pine_output_contract_failed": sum(1 for record in pine_output_contract if not record["passed"]),
        "external_sources_present": sum(1 for record in external_records if record["present"]),
        "external_sources_invalid": sum(1 for record in external_records if not record["valid"]),
        "external_indicator_input_contract_total": len(external_indicator_input_contract),
        "external_indicator_input_contract_failed": sum(
            1 for record in external_indicator_input_contract if not record["passed"]
        ),
        "external_source_sanity_total": len(external_sanity_records),
        "external_source_sanity_failed": sum(1 for record in external_sanity_records if not record["passed"]),
        "external_compiled_artifacts_total": len(external_compiled_records),
        "external_compiled_artifacts_failed": sum(1 for record in external_compiled_records if not record["passed"]),
        "external_parity_script_artifacts_total": len(external_parity_script_artifact_records_out),
        "external_parity_script_artifacts_failed": sum(
            1 for record in external_parity_script_artifact_records_out if not record["passed"]
        ),
        "external_parity_script_contract_total": len(external_parity_script_contract_records_out),
        "external_parity_script_contract_failed": sum(
            1 for record in external_parity_script_contract_records_out if not record["passed"]
        ),
        "external_parity_reports_total": len(external_parity_report_records_out),
        "external_parity_reports_required": sum(1 for record in external_parity_report_records_out if record["required"]),
        "external_parity_reports_failed": sum(1 for record in external_parity_report_records_out if not record["passed"]),
        "external_parity_reports_required_failed": sum(
            1 for record in external_parity_report_records_out if record["required"] and not record["passed"]
        ),
        "external_parity_reports_stale": sum(1 for record in external_parity_report_records_out if record["stale"]),
        "ignored_csv_candidates": len(ignored_candidates),
        "untracked_csv_candidates": len(untracked_candidates),
        "unexpected_pine_export_candidates": len(unexpected_pine_candidates),
        "fixtures_total": len(fixture_records),
        "fixtures_present": sum(1 for record in fixture_records if record["present"]),
        "fixtures_missing": sum(1 for record in fixture_records if not record["present"]),
        "fixtures_passed": sum(1 for record in fixture_records if record["passed"]),
        "fixtures_failed": sum(1 for record in fixture_records if record["present"] and not record["passed"]),
        "required_total": len(required_records) + len(labels_without_fixture),
        "required_present": sum(1 for record in required_records if record["present"]),
        "required_missing": len(missing_required),
        "required_passed": sum(1 for record in required_records if record["passed"]),
        "required_failed": sum(1 for record in required_records if record["present"] and not record["passed"]),
        "required_settings_smoke_total": len(required_settings_smoke),
        "required_settings_smoke_passed": sum(1 for record in required_settings_smoke if record["passed"]),
        "required_settings_smoke_failed": sum(1 for record in required_settings_smoke if not record["passed"]),
    }

    return {
        "fixture_directories": [str(path) for path in fixture_dirs],
        "manifest": str(manifest_path) if manifest_path else None,
        "require_full_coverage": require_full_coverage,
        "passed": passed,
        "coverage_error": coverage_error,
        "pine_sources": pine_records,
        "pine_input_contract": pine_input_contract,
        "pine_output_contract": pine_output_contract,
        "external_sources": external_records,
        "external_indicator_input_contract": external_indicator_input_contract,
        "external_source_sanity": external_sanity_records,
        "external_compiled_artifacts": external_compiled_records,
        "external_parity_script_artifacts": external_parity_script_artifact_records_out,
        "external_parity_script_contract": external_parity_script_contract_records_out,
        "external_parity_reports": external_parity_report_records_out,
        "external_source_sanity_errors": [
            f"{record['source']}:{record['check']}: {record['detail']}"
            for record in external_sanity_records
            if not record["passed"]
        ],
        "pine_input_contract_errors": pine_input_contract_error_rows,
        "pine_output_contract_errors": pine_output_contract_error_rows,
        "external_indicator_input_contract_errors": external_indicator_input_contract_error_rows,
        "external_compiled_artifact_errors": [
            f"{record['source']}: {record['detail']}"
            for record in external_compiled_records
            if not record["passed"]
        ],
        "external_parity_script_artifact_errors": [
            f"{record['script_path']}: {record['detail']}"
            for record in external_parity_script_artifact_records_out
            if not record["passed"]
        ],
        "external_parity_script_contract_errors": external_parity_script_contract_errors(
            external_parity_script_contract_records_out
        ),
        "external_parity_report_errors": [
            f"{record['name']}: {record['error']}"
            for record in external_parity_report_records_out
            if record["required"] and not record["passed"]
        ],
        "external_parity_report_actions": [
            record["regeneration_action"]
            for record in external_parity_report_records_out
            if record["required"] and not record["passed"]
        ],
        "ignored_csv_candidates": ignored_candidates,
        "untracked_csv_candidates": untracked_candidates,
        "unexpected_pine_export_candidates": unexpected_pine_candidates,
        "source_errors": source_errors,
        "fixtures": fixture_records,
        "required_uncovered_fixtures": required_records,
        "required_settings_smoke": required_settings_smoke,
        "missing_required": missing_required,
        "required_action_items": required_action_items,
        "required_export_workflow": required_export_workflow,
        "external_runner_workflow": external_runner_workflow,
        "readiness_artifacts_workflow": readiness_artifacts_workflow,
        "summary": summary,
    }


def cmd_validate_fixtures(args: argparse.Namespace) -> int:
    fixture_dirs = fixture_search_dirs_from_arg(args.fixture_dir)
    existing_fixture_dirs, fixture_error = fixture_directory_status(fixture_dirs)
    if fixture_error:
        print(fixture_error)
        return 1
    manifest_path = manifest_path_from_arg(args.manifest)
    if args.json:
        try:
            payload = build_fixture_validation_report(
                existing_fixture_dirs,
                manifest_path,
                args.tolerance,
                args.require_full_coverage,
                pine_export_source_dir=args.pine_export_source_dir,
            )
        except ValueError as exc:
            print(f"invalid parity manifest: {exc}")
            return 1
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["passed"] else 1

    try:
        fixture_specs = load_parity_manifest(manifest_path) if manifest_path else builtin_parity_fixtures()
    except ValueError as exc:
        print(f"invalid parity manifest: {exc}")
        return 1

    all_passed = True
    missing_required: list[str] = []
    required_action_items: list[dict[str, object]] = []

    print("fixture directories:")
    for fixture_dir in existing_fixture_dirs:
        print(f"  - {fixture_dir}")
    if manifest_path:
        print(f"manifest: {manifest_path}")
    if manifest_path:
        try:
            payload = load_manifest_payload(manifest_path)
            pine_records, pine_errors = validate_manifest_pine_sources(existing_fixture_dirs, manifest_path)
            pine_contract = pine_input_contract_records(pine_records)
            pine_contract_errors = pine_input_contract_errors(pine_contract)
            pine_output_contract = pine_output_contract_records(pine_records)
            pine_output_contract_error_rows = pine_output_contract_errors(pine_output_contract)
            external_records, external_errors = validate_manifest_external_sources(existing_fixture_dirs, manifest_path)
            external_indicator_input_contract = external_indicator_input_contract_records(external_records)
            external_indicator_input_contract_error_rows = external_indicator_input_contract_errors(
                external_indicator_input_contract
            )
            source_errors = pine_errors + external_errors
            external_sanity = external_source_sanity_records(external_records)
            external_compiled = external_compiled_artifact_records(external_records)
            required_specs, labels_without_fixture = load_required_fixture_cases(payload)
            external_parity_specs = load_external_parity_report_specs(payload)
            external_parity_scripts = external_parity_script_artifact_records(existing_fixture_dirs, external_parity_specs)
            external_parity_script_contract = external_parity_script_contract_records(existing_fixture_dirs, external_parity_specs)
            external_parity_script_contract_error_rows = external_parity_script_contract_errors(
                external_parity_script_contract
            )
            external_parity = external_parity_report_records(
                existing_fixture_dirs,
                external_parity_specs,
                external_compiled,
                args.tolerance,
                external_parity_scripts,
            )
            manifest_filenames = (
                {spec.filename for spec in fixture_specs}
                | {spec.filename for spec in required_specs}
                | {record.filename for record in external_parity_specs}
                | {str(row["filename"]) for row in ignored_csv_candidate_rows(payload, existing_fixture_dirs)}
                | set(labels_without_fixture)
            )
            unexpected_pine_candidates = [
                row
                for row in untracked_csv_candidate_rows(existing_fixture_dirs, manifest_filenames)
                if row["classification"] == "tradingview_pine_export_candidate"
            ]
        except ValueError as exc:
            print(f"invalid parity manifest: {exc}")
            return 1
        if source_errors:
            all_passed = False
            print("source validation failed:")
            for error in source_errors:
                print(f"  - {error}")
        if pine_contract_errors:
            all_passed = False
            print("Pine input contract failed:")
            for error in pine_contract_errors:
                print(f"  - {error}")
        if pine_output_contract_error_rows:
            all_passed = False
            print("Pine output contract failed:")
            for error in pine_output_contract_error_rows:
                print(f"  - {error}")
        if external_indicator_input_contract_error_rows:
            all_passed = False
            print("external indicator input contract failed:")
            for error in external_indicator_input_contract_error_rows:
                print(f"  - {error}")
        failed_external_sanity = [record for record in external_sanity if not record["passed"]]
        if failed_external_sanity:
            all_passed = False
            print("external source sanity failed:")
            for record in failed_external_sanity:
                print(f"  - {record['source']}:{record['check']}: {record['detail']}")
        failed_external_compiled = [record for record in external_compiled if not record["passed"]]
        if failed_external_compiled:
            all_passed = False
            print("external compiled artifact freshness failed:")
            for record in failed_external_compiled:
                print(f"  - {record['source']}: {record['detail']}")
        failed_external_parity_scripts = [record for record in external_parity_scripts if not record["passed"]]
        if failed_external_parity_scripts:
            all_passed = False
            print("external parity script artifact freshness failed:")
            for record in failed_external_parity_scripts:
                print(f"  - {record['script_path']}: {record['detail']}")
        if external_parity_script_contract_error_rows:
            all_passed = False
            print("external parity script contract failed:")
            for error in external_parity_script_contract_error_rows:
                print(f"  - {error}")
        failed_external_parity = [record for record in external_parity if record["required"] and not record["passed"]]
        if failed_external_parity:
            all_passed = False
            print("external parity report validation failed:")
            for record in failed_external_parity:
                print(f"  - {record['name']}: {record['error']}")
        if unexpected_pine_candidates:
            all_passed = False
            print("unexpected TradingView/Pine export candidates:")
            for row in unexpected_pine_candidates:
                print(f"  - {row['filename']} -> {row['path']}")
    for spec in fixture_specs:
        exists, passed = validate_fixture_spec(existing_fixture_dirs, spec, args.tolerance, args.output_mismatches)
        if not exists:
            print(f"FAIL {spec.name}: missing {spec.filename}")
            all_passed = False
        all_passed = all_passed and passed

    if args.require_full_coverage:
        if not manifest_path:
            print("coverage incomplete: no parity manifest available")
            all_passed = False
        else:
            try:
                payload = load_manifest_payload(manifest_path)
            except ValueError as exc:
                print(f"invalid parity manifest: {exc}")
                return 1
            try:
                required_specs, labels_without_fixture = load_required_fixture_cases(payload)
            except ValueError as exc:
                print(f"invalid parity manifest: {exc}")
                return 1
            try:
                checklist_rows = export_checklist_rows(payload, existing_fixture_dirs, manifest_path)
            except ValueError as exc:
                print(f"invalid parity manifest: {exc}")
                return 1
            try:
                smoke_records = required_settings_smoke_records(payload, existing_fixture_dirs)
            except ValueError as exc:
                print(f"invalid parity manifest: {exc}")
                return 1
            checklist_by_filename = {
                row["filename"]: row for row in checklist_rows if isinstance(row.get("filename"), str)
            }
            missing_required = list(labels_without_fixture)
            failed_smoke_records = [record for record in smoke_records if not record["passed"]]
            if failed_smoke_records:
                print("required Python settings smoke failed:")
                for record in failed_smoke_records:
                    print(f"  - {record['name']} using {record['python_smoke_fixture']}: {record['error']}")
                all_passed = False
            elif smoke_records:
                print(f"required Python settings smoke: PASS {len(smoke_records)}/{len(smoke_records)}")
            for spec in required_specs:
                exists, passed = validate_fixture_spec(
                    existing_fixture_dirs,
                    spec,
                    args.tolerance,
                    args.output_mismatches,
                    require_full_numeric=True,
                )
                if not exists:
                    missing_required.append(uncovered_case_label({"name": spec.name, "filename": spec.filename}))
                    action_item = dict(checklist_by_filename.get(spec.filename, {}))
                    if action_item:
                        action_item["status"] = "missing"
                        required_action_items.append(action_item)
                elif not passed:
                    action_item = dict(checklist_by_filename.get(spec.filename, {}))
                    if action_item:
                        action_item["status"] = "failing"
                        required_action_items.append(action_item)
                all_passed = all_passed and (exists and passed)
            if missing_required:
                print("coverage incomplete: missing Pine exports for:")
                for item in missing_required:
                    print(f"  - {item}")
                if required_action_items:
                    print("missing export action details:")
                    for item in required_action_items:
                        print(f"  - {item['name']} -> {item['filename']}")
                        proves = item.get("proves", [])
                        if proves:
                            print(f"    proves: {', '.join(str(value) for value in proves)}")
                        settings = item.get("settings", {})
                        if settings:
                            assert isinstance(settings, dict)
                            print("    Pine settings:")
                            for key, value in settings.items():
                                print(f"      {key}: {value}")
                        cli_flags = item.get("cli_flags", [])
                        if cli_flags:
                            print(f"    equivalent CLI flags: {' '.join(str(value) for value in cli_flags)}")
                        helper_command = item.get("pine_export_helper_command", [])
                        if helper_command:
                            assert isinstance(helper_command, list)
                            print(f"    Pine export helper command: {' '.join(str(value) for value in helper_command)}")
                        full_helper_command = item.get("pine_export_helper_command_full", [])
                        if full_helper_command:
                            assert isinstance(full_helper_command, list)
                            print(f"    full helper command: {' '.join(str(value) for value in full_helper_command)}")
                all_passed = False

    return 0 if all_passed else 1


def build_readiness_report(
    fixture_dirs: list[Path],
    manifest_path: Path | None,
    tolerance: float,
    pine_export_source_dir: str = DEFAULT_PINE_EXPORT_SOURCE_DIR,
) -> dict[str, object]:
    report = build_fixture_validation_report(
        fixture_dirs,
        manifest_path,
        tolerance,
        require_full_coverage=manifest_path is not None,
        pine_export_source_dir=pine_export_source_dir,
    )
    return {"ready": report["passed"], **report}


def build_pine_input_contract_report(fixture_dirs: list[Path], manifest_path: Path | None) -> dict[str, object]:
    if manifest_path is None:
        raise ValueError("pine-input-contract requires a parity manifest")
    pine_records, pine_errors = validate_manifest_pine_sources(fixture_dirs, manifest_path)
    records = pine_input_contract_records(pine_records)
    errors = pine_errors + pine_input_contract_errors(records)
    return {
        "passed": not errors,
        "manifest": str(manifest_path),
        "fixture_directories": [str(path) for path in fixture_dirs],
        "pine_sources": pine_records,
        "contracts": records,
        "errors": errors,
        "summary": {
            "pine_sources": len(pine_records),
            "contracts_total": len(records),
            "contracts_failed": sum(1 for record in records if not record["passed"]),
            "errors": len(errors),
        },
    }


def build_pine_output_contract_report(fixture_dirs: list[Path], manifest_path: Path | None) -> dict[str, object]:
    if manifest_path is None:
        raise ValueError("pine-output-contract requires a parity manifest")
    pine_records, pine_errors = validate_manifest_pine_sources(fixture_dirs, manifest_path)
    records = pine_output_contract_records(pine_records)
    errors = pine_errors + pine_output_contract_errors(records)
    return {
        "passed": not errors,
        "manifest": str(manifest_path),
        "fixture_directories": [str(path) for path in fixture_dirs],
        "pine_sources": pine_records,
        "contracts": records,
        "errors": errors,
        "summary": {
            "pine_sources": len(pine_records),
            "contracts_total": len(records),
            "contracts_failed": sum(1 for record in records if not record["passed"]),
            "errors": len(errors),
        },
    }


def cmd_pine_input_contract(args: argparse.Namespace) -> int:
    fixture_dirs = fixture_search_dirs_from_arg(args.fixture_dir)
    existing_fixture_dirs, fixture_error = fixture_directory_status(fixture_dirs)
    if fixture_error:
        print(fixture_error)
        return 1
    manifest_path = manifest_path_from_arg(args.manifest)
    try:
        report = build_pine_input_contract_report(existing_fixture_dirs, manifest_path)
    except ValueError as exc:
        print(f"invalid parity manifest: {exc}")
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["passed"] else 1

    print("Pine input contract: PASS" if report["passed"] else "Pine input contract: FAIL")
    print(f"manifest: {report['manifest']}")
    summary = report["summary"]
    assert isinstance(summary, dict)
    print(
        "summary: "
        f"contracts_failed={summary['contracts_failed']}/{summary['contracts_total']} "
        f"errors={summary['errors']}"
    )
    errors = report["errors"]
    assert isinstance(errors, list)
    for error in errors:
        print(f"  - {error}")
    return 0 if report["passed"] else 1


def cmd_pine_output_contract(args: argparse.Namespace) -> int:
    fixture_dirs = fixture_search_dirs_from_arg(args.fixture_dir)
    existing_fixture_dirs, fixture_error = fixture_directory_status(fixture_dirs)
    if fixture_error:
        print(fixture_error)
        return 1
    manifest_path = manifest_path_from_arg(args.manifest)
    try:
        report = build_pine_output_contract_report(existing_fixture_dirs, manifest_path)
    except ValueError as exc:
        print(f"invalid parity manifest: {exc}")
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["passed"] else 1

    print("Pine output contract: PASS" if report["passed"] else "Pine output contract: FAIL")
    print(f"manifest: {report['manifest']}")
    summary = report["summary"]
    assert isinstance(summary, dict)
    print(
        "summary: "
        f"contracts_failed={summary['contracts_failed']}/{summary['contracts_total']} "
        f"errors={summary['errors']}"
    )
    errors = report["errors"]
    assert isinstance(errors, list)
    for error in errors:
        print(f"  - {error}")
    return 0 if report["passed"] else 1


def readiness_blockers_from_report(report: dict[str, object]) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []

    source_errors = report.get("source_errors", [])
    if isinstance(source_errors, list) and source_errors:
        blockers.append({"kind": "source_errors", "count": len(source_errors), "errors": source_errors})

    pine_contract_errors = report.get("pine_input_contract_errors", [])
    if isinstance(pine_contract_errors, list) and pine_contract_errors:
        blockers.append(
            {
                "kind": "pine_input_contract",
                "count": len(pine_contract_errors),
                "errors": pine_contract_errors,
                "records": report.get("pine_input_contract", []),
            }
        )

    pine_output_errors = report.get("pine_output_contract_errors", [])
    if isinstance(pine_output_errors, list) and pine_output_errors:
        blockers.append(
            {
                "kind": "pine_output_contract",
                "count": len(pine_output_errors),
                "errors": pine_output_errors,
                "records": report.get("pine_output_contract", []),
            }
        )

    external_indicator_input_errors = report.get("external_indicator_input_contract_errors", [])
    if isinstance(external_indicator_input_errors, list) and external_indicator_input_errors:
        blockers.append(
            {
                "kind": "external_indicator_input_contract",
                "count": len(external_indicator_input_errors),
                "errors": external_indicator_input_errors,
                "records": report.get("external_indicator_input_contract", []),
            }
        )

    external_source_sanity_errors = report.get("external_source_sanity_errors", [])
    if isinstance(external_source_sanity_errors, list) and external_source_sanity_errors:
        blockers.append(
            {
                "kind": "external_source_sanity",
                "count": len(external_source_sanity_errors),
                "errors": external_source_sanity_errors,
            }
        )

    external_compiled_errors = report.get("external_compiled_artifact_errors", [])
    if isinstance(external_compiled_errors, list) and external_compiled_errors:
        blockers.append(
            {
                "kind": "external_compiled_artifacts",
                "count": len(external_compiled_errors),
                "errors": external_compiled_errors,
            }
        )

    external_script_errors = report.get("external_parity_script_artifact_errors", [])
    if isinstance(external_script_errors, list) and external_script_errors:
        blockers.append(
            {
                "kind": "external_parity_script_artifacts",
                "count": len(external_script_errors),
                "errors": external_script_errors,
            }
        )

    external_script_contract_errors = report.get("external_parity_script_contract_errors", [])
    if isinstance(external_script_contract_errors, list) and external_script_contract_errors:
        blockers.append(
            {
                "kind": "external_parity_script_contract",
                "count": len(external_script_contract_errors),
                "errors": external_script_contract_errors,
                "records": report.get("external_parity_script_contract", []),
            }
        )

    external_report_errors = report.get("external_parity_report_errors", [])
    if isinstance(external_report_errors, list) and external_report_errors:
        actions = report.get("external_parity_report_actions", [])
        reports = report.get("external_parity_reports", [])
        stale_count = 0
        if isinstance(reports, list):
            stale_count = sum(1 for record in reports if isinstance(record, dict) and record.get("stale"))
        blockers.append(
            {
                "kind": "external_parity_reports",
                "count": len(external_report_errors),
                "stale": stale_count,
                "errors": external_report_errors,
                "actions": actions if isinstance(actions, list) else [],
                "workflow": report.get("external_runner_workflow"),
            }
        )

    unexpected_pine = report.get("unexpected_pine_export_candidates", [])
    if isinstance(unexpected_pine, list) and unexpected_pine:
        blockers.append(
            {
                "kind": "unexpected_pine_export_candidates",
                "count": len(unexpected_pine),
                "candidates": unexpected_pine,
            }
        )

    missing_required = report.get("missing_required", [])
    if isinstance(missing_required, list) and missing_required:
        action_items = report.get("required_action_items", [])
        blockers.append(
            {
                "kind": "required_pine_exports",
                "count": len(missing_required),
                "missing": missing_required,
                "actions": action_items if isinstance(action_items, list) else [],
                "workflow": report.get("required_export_workflow"),
            }
        )

    required_records = report.get("required_uncovered_fixtures", [])
    if isinstance(required_records, list):
        failing_required = [
            record
            for record in required_records
            if isinstance(record, dict) and record.get("present") and not record.get("passed")
        ]
        if failing_required:
            blockers.append(
                {
                    "kind": "failing_required_pine_exports",
                    "count": len(failing_required),
                    "fixtures": failing_required,
                }
            )

    smoke_records = report.get("required_settings_smoke", [])
    if isinstance(smoke_records, list):
        failing_smoke = [
            record for record in smoke_records if isinstance(record, dict) and not record.get("passed")
        ]
        if failing_smoke:
            blockers.append(
                {
                    "kind": "required_settings_smoke",
                    "count": len(failing_smoke),
                    "failures": failing_smoke,
                }
            )

    fixture_records = report.get("fixtures", [])
    if isinstance(fixture_records, list):
        failing_fixtures = [
            record for record in fixture_records if isinstance(record, dict) and not record.get("passed")
        ]
        if failing_fixtures:
            blockers.append(
                {
                    "kind": "tracked_fixtures",
                    "count": len(failing_fixtures),
                    "fixtures": failing_fixtures,
                }
            )

    return blockers


def build_readiness_blockers_report(
    fixture_dirs: list[Path],
    manifest_path: Path | None,
    tolerance: float,
    pine_export_source_dir: str = DEFAULT_PINE_EXPORT_SOURCE_DIR,
) -> dict[str, object]:
    report = build_readiness_report(fixture_dirs, manifest_path, tolerance, pine_export_source_dir)
    blockers = readiness_blockers_from_report(report)
    return {
        "ready": report["ready"],
        "manifest": report["manifest"],
        "fixture_directories": report["fixture_directories"],
        "summary": report["summary"],
        "readiness_artifacts_workflow": report.get("readiness_artifacts_workflow"),
        "blockers": blockers,
        "blocker_count": len(blockers),
    }


def cmd_readiness(args: argparse.Namespace) -> int:
    fixture_dirs = fixture_search_dirs_from_arg(args.fixture_dir)
    existing_fixture_dirs, fixture_error = fixture_directory_status(fixture_dirs)
    if fixture_error:
        print(fixture_error)
        return 1
    manifest_path = manifest_path_from_arg(args.manifest)
    try:
        report = build_readiness_report(
            existing_fixture_dirs,
            manifest_path,
            args.tolerance,
            args.pine_export_source_dir,
        )
    except ValueError as exc:
        print(f"invalid parity manifest: {exc}")
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["ready"] else 1

    print("readiness: READY" if report["ready"] else "readiness: NOT READY")
    print(f"manifest: {report['manifest']}")
    print("fixture directories:")
    for fixture_dir in report["fixture_directories"]:
        print(f"  - {fixture_dir}")
    summary = report["summary"]
    assert isinstance(summary, dict)
    print(
        "summary: "
        f"pine_sources_invalid={summary['pine_sources_invalid']} "
        f"pine_input_contract_failed="
        f"{summary['pine_input_contract_failed']}/{summary['pine_input_contract_total']} "
        f"pine_output_contract_failed="
        f"{summary['pine_output_contract_failed']}/{summary['pine_output_contract_total']} "
        f"external_sources_invalid={summary['external_sources_invalid']} "
        f"external_indicator_input_contract_failed="
        f"{summary['external_indicator_input_contract_failed']}/"
        f"{summary['external_indicator_input_contract_total']} "
        f"external_source_sanity_failed="
        f"{summary['external_source_sanity_failed']}/{summary['external_source_sanity_total']} "
        f"external_compiled_artifacts_failed="
        f"{summary['external_compiled_artifacts_failed']}/{summary['external_compiled_artifacts_total']} "
        f"external_parity_script_artifacts_failed="
        f"{summary['external_parity_script_artifacts_failed']}/"
        f"{summary['external_parity_script_artifacts_total']} "
        f"external_parity_script_contract_failed="
        f"{summary['external_parity_script_contract_failed']}/"
        f"{summary['external_parity_script_contract_total']} "
        f"external_parity_reports_required_failed="
        f"{summary['external_parity_reports_required_failed']}/{summary['external_parity_reports_required']} "
        f"fixtures_passed={summary['fixtures_passed']}/{summary['fixtures_total']} "
        f"required_passed={summary['required_passed']}/{summary['required_total']} "
        f"required_settings_smoke_passed="
        f"{summary['required_settings_smoke_passed']}/{summary['required_settings_smoke_total']} "
        f"unexpected_pine_export_candidates={summary['unexpected_pine_export_candidates']} "
        f"required_missing={summary['required_missing']} "
        f"required_failed={summary['required_failed']}"
    )
    source_errors = report["source_errors"]
    assert isinstance(source_errors, list)
    if source_errors:
        print("source errors:")
        for error in source_errors:
            print(f"  - {error}")
    pine_contract_errors = report.get("pine_input_contract_errors", [])
    assert isinstance(pine_contract_errors, list)
    if pine_contract_errors:
        print("Pine input contract failures:")
        for error in pine_contract_errors:
            print(f"  - {error}")
    pine_output_errors = report.get("pine_output_contract_errors", [])
    assert isinstance(pine_output_errors, list)
    if pine_output_errors:
        print("Pine output contract failures:")
        for error in pine_output_errors:
            print(f"  - {error}")
    external_indicator_input_errors = report.get("external_indicator_input_contract_errors", [])
    assert isinstance(external_indicator_input_errors, list)
    if external_indicator_input_errors:
        print("external indicator input contract failures:")
        for error in external_indicator_input_errors:
            print(f"  - {error}")
    external_source_sanity = report.get("external_source_sanity", [])
    assert isinstance(external_source_sanity, list)
    failed_sanity = [record for record in external_source_sanity if isinstance(record, dict) and not record["passed"]]
    if failed_sanity:
        print("external source sanity failures:")
        for record in failed_sanity:
            print(f"  - {record['source']}:{record['check']}: {record['detail']}")
    external_compiled = report.get("external_compiled_artifacts", [])
    assert isinstance(external_compiled, list)
    failed_compiled = [record for record in external_compiled if isinstance(record, dict) and not record["passed"]]
    if failed_compiled:
        print("external compiled artifact freshness failures:")
        for record in failed_compiled:
            print(f"  - {record['source']}: {record['detail']}")
    external_scripts = report.get("external_parity_script_artifacts", [])
    assert isinstance(external_scripts, list)
    failed_scripts = [record for record in external_scripts if isinstance(record, dict) and not record["passed"]]
    if failed_scripts:
        print("external parity script artifact freshness failures:")
        for record in failed_scripts:
            print(f"  - {record['script_path']}: {record['detail']}")
    external_script_contract_errors = report.get("external_parity_script_contract_errors", [])
    assert isinstance(external_script_contract_errors, list)
    if external_script_contract_errors:
        print("external parity script contract failures:")
        for error in external_script_contract_errors:
            print(f"  - {error}")
    external_parity = report.get("external_parity_reports", [])
    assert isinstance(external_parity, list)
    failed_external_parity = [record for record in external_parity if isinstance(record, dict) and record["required"] and not record["passed"]]
    if failed_external_parity:
        print("external parity report failures:")
        for record in failed_external_parity:
            print(f"  - {record['name']}: {record['error']}")
        print("external parity report regeneration actions:")
        for record in failed_external_parity:
            action = record.get("regeneration_action", {})
            assert isinstance(action, dict)
            compile_command = action.get("compile_command", [])
            script_inputs = action.get("script_inputs", {})
            if compile_command:
                assert isinstance(compile_command, list)
                print(f"  - {record['name']} compile: {' '.join(str(item) for item in compile_command)}")
            if isinstance(script_inputs, dict):
                print(
                    f"    run inputs: InpInputFile={script_inputs.get('InpInputFile')} "
                    f"InpOutputFile={script_inputs.get('InpOutputFile')} "
                    f"InpIncludeFullHist={script_inputs.get('InpIncludeFullHist')}"
                )
    unexpected_pine_candidates = report["unexpected_pine_export_candidates"]
    assert isinstance(unexpected_pine_candidates, list)
    if unexpected_pine_candidates:
        print("unexpected TradingView/Pine export candidates:")
        for row in unexpected_pine_candidates:
            assert isinstance(row, dict)
            print(f"  - {row['filename']} -> {row['path']}")
    missing_required = report["missing_required"]
    assert isinstance(missing_required, list)
    if missing_required:
        print("missing required exports:")
        for item in missing_required:
            print(f"  - {item}")
    action_items = report["required_action_items"]
    assert isinstance(action_items, list)
    if action_items:
        print("next export actions:")
        for item in action_items:
            assert isinstance(item, dict)
            print(f"  - {item['name']} -> {item['filename']} ({item['status']})")
            cli_flags = item.get("cli_flags", [])
            if cli_flags:
                print(f"    equivalent CLI flags: {' '.join(str(value) for value in cli_flags)}")
            helper_command = item.get("pine_export_helper_command", [])
            if helper_command:
                print(f"    Pine export helper command: {' '.join(str(value) for value in helper_command)}")
            full_helper_command = item.get("pine_export_helper_command_full", [])
            if full_helper_command:
                print(f"    full helper command: {' '.join(str(value) for value in full_helper_command)}")
    smoke_records = report["required_settings_smoke"]
    assert isinstance(smoke_records, list)
    failed_smoke_records = [record for record in smoke_records if isinstance(record, dict) and not record["passed"]]
    if failed_smoke_records:
        print("required Python settings smoke failures:")
        for record in failed_smoke_records:
            print(f"  - {record['name']} using {record['python_smoke_fixture']}: {record['error']}")
    return 0 if report["ready"] else 1


def cmd_readiness_blockers(args: argparse.Namespace) -> int:
    fixture_dirs = fixture_search_dirs_from_arg(args.fixture_dir)
    existing_fixture_dirs, fixture_error = fixture_directory_status(fixture_dirs)
    if fixture_error:
        print(fixture_error)
        return 1
    manifest_path = manifest_path_from_arg(args.manifest)
    try:
        report = build_readiness_blockers_report(
            existing_fixture_dirs,
            manifest_path,
            args.tolerance,
            args.pine_export_source_dir,
        )
    except ValueError as exc:
        print(f"invalid parity manifest: {exc}")
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["ready"] else 1

    print("readiness blockers: READY" if report["ready"] else "readiness blockers: NOT READY")
    print(f"manifest: {report['manifest']}")
    print(f"blocker_count: {report['blocker_count']}")
    readiness_workflow = report.get("readiness_artifacts_workflow")
    if isinstance(readiness_workflow, dict):
        print(f"prepare artifacts: {readiness_workflow['prepare_readiness_artifacts_command_string']}")
        print(f"verify artifacts: {readiness_workflow['verify_readiness_artifacts_command_string']}")
    blockers = report["blockers"]
    assert isinstance(blockers, list)
    for blocker in blockers:
        assert isinstance(blocker, dict)
        print(f"- {blocker['kind']}: {blocker['count']}")
        if blocker["kind"] == "required_pine_exports":
            workflow = blocker.get("workflow")
            if isinstance(workflow, dict):
                print(f"  export pack: {workflow['export_pack_command_string']}")
                print(f"  verify pack: {workflow['verify_export_pack_command_string']}")
                print(f"  import exports: {workflow['import_pine_exports_command_string']}")
                print(f"  rerun readiness: {workflow['readiness_command_string']}")
            for item in blocker.get("actions", []):
                if isinstance(item, dict):
                    print(f"  missing: {item['name']} -> {item['filename']}")
        elif blocker["kind"] == "external_parity_reports":
            print(f"  stale: {blocker.get('stale', 0)}")
            workflow = blocker.get("workflow")
            if isinstance(workflow, dict):
                print(f"  runner pack: {workflow['external_runner_pack_command_string']}")
                print(f"  verify runner pack: {workflow['verify_external_runner_pack_command_string']}")
            for action in blocker.get("actions", []):
                if not isinstance(action, dict):
                    continue
                inputs = action.get("script_inputs", {})
                if isinstance(inputs, dict):
                    print(
                        f"  refresh: {action['name']} "
                        f"InpInputFile={inputs.get('InpInputFile')} "
                        f"InpOutputFile={inputs.get('InpOutputFile')} "
                        f"InpIncludeFullHist={inputs.get('InpIncludeFullHist')}"
                    )
        else:
            errors = blocker.get("errors") or blocker.get("missing") or blocker.get("candidates")
            if isinstance(errors, list):
                for item in errors:
                    print(f"  - {item}")
    return 0 if report["ready"] else 1


def pine_source_roots(fixture_dirs: list[Path]) -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()
    for fixture_dir in fixture_dirs:
        workspace = fixture_dir.parent if fixture_dir.name == "Files" else fixture_dir
        candidate = workspace / "PineScript"
        if not candidate.is_dir():
            continue
        resolved = candidate.resolve()
        if resolved not in seen:
            roots.append(candidate)
            seen.add(resolved)
    return roots


def workspace_roots(fixture_dirs: list[Path]) -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()
    for fixture_dir in fixture_dirs:
        workspace = fixture_dir.parent if fixture_dir.name == "Files" else fixture_dir
        resolved = workspace.resolve()
        if resolved not in seen:
            roots.append(workspace)
            seen.add(resolved)
    return roots


def source_path_for(workspaces: list[Path], source_path: str) -> Path | None:
    for workspace in workspaces:
        candidate = workspace / source_path
        if candidate.is_file():
            return candidate
    return None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_code_sha256(path: Path) -> str:
    lines = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        lines.append(line.rstrip())
    payload = "\n".join(lines) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_sha256_hex(value: str) -> bool:
    return bool(SHA256_HEX_RE.fullmatch(value))


def is_path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def cli_flag_group_counts(flags: list[object]) -> dict[tuple[str, ...], int] | None:
    groups: list[tuple[str, ...]] = []
    index = 0
    while index < len(flags):
        flag = flags[index]
        if not isinstance(flag, str) or not flag.startswith("--"):
            return None
        if index + 1 < len(flags) and isinstance(flags[index + 1], str) and not str(flags[index + 1]).startswith("--"):
            groups.append((flag, str(flags[index + 1])))
            index += 2
        else:
            groups.append((flag,))
            index += 1

    counts: dict[tuple[str, ...], int] = {}
    for group in groups:
        counts[group] = counts.get(group, 0) + 1
    return counts


def object_list_counts(rows: list[object]) -> dict[str, int] | None:
    counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        key = json.dumps(row, sort_keys=True, separators=(",", ":"))
        counts[key] = counts.get(key, 0) + 1
    return counts


def has_debug_markers(path: Path) -> bool:
    if "debug" in path.name.lower():
        return True
    source = path.read_text(errors="ignore")
    return DEBUG_MARKER_RE.search(source) is not None


def source_records(fixture_dirs: list[Path], specs: list[PineSourceSpec]) -> list[dict[str, object]]:
    workspaces = workspace_roots(fixture_dirs)
    records: list[dict[str, object]] = []
    for spec in specs:
        path = source_path_for(workspaces, spec.path)
        actual_sha256 = file_sha256(path) if path else None
        actual_code_sha256 = source_code_sha256(path) if path else None
        debug_markers = has_debug_markers(path) if path else None
        expected_sha256 = spec.sha256
        expected_code_sha256 = spec.code_sha256
        sha256_matches = None if expected_sha256 is None or actual_sha256 is None else actual_sha256 == expected_sha256
        code_sha256_matches = (
            None
            if expected_code_sha256 is None or actual_code_sha256 is None
            else actual_code_sha256 == expected_code_sha256
        )
        debug_allowed = spec.allow_debug_markers
        debug_ok = True if debug_markers is None else debug_allowed or not debug_markers
        hash_ok = sha256_matches is not False or code_sha256_matches is True
        records.append(
            {
                "name": spec.name,
                "path": spec.path,
                "role": spec.role,
                "present": path is not None,
                "resolved_path": str(path) if path else None,
                "sha256": actual_sha256,
                "expected_sha256": expected_sha256,
                "sha256_matches": sha256_matches,
                "code_sha256": actual_code_sha256,
                "expected_code_sha256": expected_code_sha256,
                "code_sha256_matches": code_sha256_matches,
                "debug_markers": debug_markers,
                "allow_debug_markers": debug_allowed,
                "valid": path is not None and hash_ok and debug_ok,
            }
        )
    return records


def pine_source_records(fixture_dirs: list[Path], specs: list[PineSourceSpec]) -> list[dict[str, object]]:
    return source_records(fixture_dirs, specs)


def source_lock_entries(records: list[dict[str, object]]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for record in records:
        entries.append(
            {
                "name": record["name"],
                "path": record["path"],
                "sha256": record["sha256"] or record["expected_sha256"],
            }
        )
    return entries


def source_lock_fingerprint(entries: list[dict[str, object]]) -> str:
    payload = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def external_source_sanity_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    sanity_records: list[dict[str, object]] = []
    for record in records:
        if not record.get("present") or not record.get("resolved_path"):
            continue
        name = str(record["name"])
        path = Path(str(record["resolved_path"]))
        source = path.read_text(errors="ignore")
        checks: list[dict[str, object]] = []

        if name == "LorentzianClassification":
            checks.extend(
                [
                    {
                        "name": "ann_replay_starts_from_bar_zero",
                        "passed": "int begin = 0" in source
                        and re.search(r"\bint\s+begin\s*=.*prev_calculated\s*-\s*1", source) is None,
                        "detail": "OnCalculate should replay from bar zero so ANN state cannot duplicate the prior bar.",
                    },
                    {
                        "name": "ann_state_resets_each_calculation",
                        "passed": not re.search(r"if\s*\(\s*prev_calculated\s*==\s*0\s*\)\s*\{?\s*InitANN", source),
                        "detail": "InitANN should not be gated by prev_calculated when the ANN history is replayed.",
                    },
                    {
                        "name": "signal_flip_filter_gate_matches_pine",
                        "passed": re.search(r"\bfilterAll\s*=\s*filtVol\s*&&\s*filtRegime\s*&&\s*filtAdx\b", source)
                        is not None,
                        "detail": "Only volatility, regime, and ADX filters should gate persistent signal flips.",
                    },
                ]
            )
        for check in checks:
            sanity_records.append(
                {
                    "source": name,
                    "path": record["path"],
                    "check": check["name"],
                    "passed": check["passed"],
                    "detail": check["detail"],
                }
            )
    return sanity_records


def external_literal(value: object) -> object:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, tuple | list) and len(value) == 3:
        return external_FEATURE_ENUMS[str(value[0]).upper()]
    if value == "close":
        return "PRICE_CLOSE"
    return value


def expected_external_indicator_input_contract_rows(settings: Settings | None = None) -> list[dict[str, object]]:
    settings = settings or Settings()
    input_map = {
        "source": "InpSource",
        "neighbors_count": "InpNeighborsCount",
        "max_bars_back": "InpMaxBarsBack",
        "feature_count": "InpFeatureCount",
        "color_compression": "InpColorCompression",
        "show_exits": "InpShowExits",
        "use_dynamic_exits": "InpUseDynamicExits",
        "include_full_history": "InpIncludeFullHist",
        "show_trade_stats": "InpShowTradeStats",
        "use_worst_case": "InpUseWorstCase",
        "use_volatility_filter": "InpUseVolFilter",
        "use_regime_filter": "InpUseRegimeFilter",
        "use_adx_filter": "InpUseAdxFilter",
        "regime_threshold": "InpRegimeThreshold",
        "adx_threshold": "InpAdxThreshold",
        "use_ema_filter": "InpUseEmaFilter",
        "ema_period": "InpEmaPeriod",
        "use_sma_filter": "InpUseSmaFilter",
        "sma_period": "InpSmaPeriod",
        "use_kernel_filter": "InpUseKernelFilter",
        "show_kernel_estimate": "InpShowKernelEst",
        "use_kernel_smoothing": "InpUseKernelSmoothing",
        "kernel_h": "InpKernelH",
        "kernel_r": "InpKernelR",
        "kernel_x": "InpKernelX",
        "kernel_lag": "InpKernelLag",
        "show_bar_colors": "InpShowBarColors",
        "show_bar_predictions": "InpShowBarPreds",
        "use_atr_offset": "InpUseAtrOffset",
        "bar_predictions_offset": "InpBarPredOffset",
        "use_confidence_gradient": "InpUseConfidenceGradient",
    }
    rows = [
        {"setting": field.name, "input": input_name, "expected": external_literal(getattr(settings, field.name))}
        for field in fields(Settings)
        if field.name in input_map
        for input_name in [input_map[field.name]]
    ]
    for slot in range(1, 6):
        kind, param_a, param_b = getattr(settings, f"f{slot}")
        rows.extend(
            [
                {"setting": f"f{slot}", "input": f"InpF{slot}Type", "expected": external_FEATURE_ENUMS[kind]},
                {"setting": f"f{slot}", "input": f"InpF{slot}ParamA", "expected": param_a},
                {"setting": f"f{slot}", "input": f"InpF{slot}ParamB", "expected": param_b},
            ]
        )
    return rows


def parse_external_literal(value: str) -> object:
    value = value.strip()
    if value in {"true", "false"}:
        return value
    try:
        if "." not in value:
            return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def external_input_defaults(source: str) -> dict[str, object]:
    defaults: dict[str, object] = {}
    pattern = re.compile(r"^\s*input\s+(?!group\b).+?\s+(?P<name>Inp[A-Za-z0-9_]+)\s*=\s*(?P<value>[^;]+);")
    for line in source.splitlines():
        match = pattern.match(line)
        if match:
            defaults[match.group("name")] = parse_external_literal(match.group("value"))
    return defaults


def external_indicator_input_contract_for_source(record: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {
        "source": record["name"],
        "path": record["path"],
        "resolved_path": record.get("resolved_path"),
        "present": record.get("present"),
        "passed": False,
        "expected_count": 0,
        "actual_count": 0,
        "missing": [],
        "mismatches": [],
        "errors": [],
    }
    if not record.get("present") or not record.get("resolved_path"):
        result["errors"] = [f"{record['name']}: source missing"]
        return result

    source = Path(str(record["resolved_path"])).read_text(encoding="utf-8", errors="replace")
    expected_rows = expected_external_indicator_input_contract_rows()
    actual_defaults = external_input_defaults(source)
    result["expected_count"] = len(expected_rows)
    result["actual_count"] = len(actual_defaults)
    missing: list[dict[str, object]] = []
    mismatches: list[dict[str, object]] = []
    for expected in expected_rows:
        input_name = str(expected["input"])
        if input_name not in actual_defaults:
            missing.append(expected)
            continue
        actual = actual_defaults[input_name]
        expected_value = expected["expected"]
        if actual != expected_value:
            mismatches.append(
                {
                    "setting": expected["setting"],
                    "input": input_name,
                    "expected": expected_value,
                    "actual": actual,
                }
            )
    errors = []
    if missing:
        errors.append(f"missing external indicator inputs: {len(missing)}")
    if mismatches:
        errors.append(f"external indicator input default mismatches: {len(mismatches)}")
    result["missing"] = missing
    result["mismatches"] = mismatches
    result["errors"] = errors
    result["passed"] = not errors
    return result


def external_indicator_input_contract_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    targets = [
        record
        for record in records
        if record.get("name") == "LorentzianClassification"
        and str(record.get("path", "")).endswith("LorentzianClassification.external_src")
    ]
    return [external_indicator_input_contract_for_source(record) for record in targets]


def external_indicator_input_contract_errors(records: list[dict[str, object]]) -> list[str]:
    errors: list[str] = []
    for record in records:
        if record.get("passed"):
            continue
        for error in record.get("errors", []):
            errors.append(f"{record['source']}: {error}")
        missing = record.get("missing", [])
        if isinstance(missing, list) and missing:
            missing_inputs = ", ".join(str(row["input"]) for row in missing if isinstance(row, dict))
            errors.append(f"{record['source']}: missing inputs: {missing_inputs}")
        mismatches = record.get("mismatches", [])
        if isinstance(mismatches, list):
            for mismatch in mismatches:
                if not isinstance(mismatch, dict):
                    continue
                errors.append(
                    f"{record['source']}: {mismatch['input']} expected "
                    f"{mismatch['expected']!r} for {mismatch['setting']} "
                    f"but found {mismatch['actual']!r}"
                )
    return errors


def external_compiled_artifact_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    primary = next(
        (
            record
            for record in records
            if record.get("name") == "LorentzianClassification"
            and record.get("present")
            and record.get("resolved_path")
            and str(record.get("resolved_path")).endswith(".external_src")
        ),
        None,
    )
    if primary is None:
        return []

    source_paths = [
        Path(str(record["resolved_path"]))
        for record in records
        if record.get("present") and record.get("resolved_path")
    ]
    primary_path = Path(str(primary["resolved_path"]))
    artifact_path = primary_path.with_suffix(".compiled")
    newest_source = max(source_paths, key=lambda path: path.stat().st_mtime)
    newest_source_mtime = newest_source.stat().st_mtime
    artifact_present = artifact_path.exists()
    artifact_mtime = artifact_path.stat().st_mtime if artifact_present else None
    passed = artifact_present and artifact_mtime is not None and artifact_mtime >= newest_source_mtime
    if not artifact_present:
        detail = "compiled .compiled artifact is missing"
    elif not passed:
        detail = f"compiled .compiled is older than {newest_source}"
    else:
        detail = "compiled .compiled artifact is present and newer than all manifest-pinned external sources"
    return [
        {
            "source": primary["name"],
            "source_path": str(primary_path),
            "artifact_path": str(artifact_path),
            "present": artifact_present,
            "passed": passed,
            "artifact_mtime": artifact_mtime,
            "newest_source_path": str(newest_source),
            "newest_source_mtime": newest_source_mtime,
            "detail": detail,
        }
    ]


def validate_source_records(
    fixture_dirs: list[Path], specs: list[PineSourceSpec]
) -> tuple[list[dict[str, object]], list[str]]:
    records = source_records(fixture_dirs, specs)
    errors: list[str] = []
    for record in records:
        if not record["present"]:
            errors.append(f"{record['name']}: missing {record['path']}")
        if record["sha256_matches"] is False:
            if record["code_sha256_matches"] is True:
                continue
            detail = f"{record['name']}: sha256 mismatch expected {record['expected_sha256']} got {record['sha256']}"
            if record.get("expected_code_sha256"):
                detail += (
                    f"; code_sha256 expected {record['expected_code_sha256']} got {record['code_sha256']}"
                )
            errors.append(detail)
        if record["debug_markers"] and not record["allow_debug_markers"]:
            errors.append(f"{record['name']}: debug markers are not allowed")
    return records, errors


def validate_manifest_pine_sources(
    fixture_dirs: list[Path], manifest_path: Path | None
) -> tuple[list[dict[str, object]], list[str]]:
    if manifest_path is None:
        return [], []
    payload = load_manifest_payload(manifest_path)
    specs = load_pine_source_specs(payload)
    return validate_source_records(fixture_dirs, specs)


def validate_manifest_external_sources(
    fixture_dirs: list[Path], manifest_path: Path | None
) -> tuple[list[dict[str, object]], list[str]]:
    if manifest_path is None:
        return [], []
    payload = load_manifest_payload(manifest_path)
    specs = load_external_source_specs(payload)
    return validate_source_records(fixture_dirs, specs)


def setting_to_cli_flag(key: str, value: object) -> list[str]:
    flag = f"--{key.replace('_', '-')}"
    if isinstance(value, bool):
        return [flag if value else f"--no-{key.replace('_', '-')}"]
    return [flag, str(value)]


def pine_input_row(
    key: str,
    value: object,
    title: str,
    *,
    group: str,
    variable: str | None = None,
    inline: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {"setting": key, "title": title, "group": group, "value": value}
    if variable:
        row["variable"] = variable
    if inline:
        row["inline"] = inline
    return row


def feature_to_pine_inputs(key: str, value: object) -> list[dict[str, object]]:
    kind, param_a, param_b = coerce_feature(value, key)
    slot = int(key[1:])
    string_inline = f"{slot * 2 - 1:02d}"
    param_inline = f"{slot * 2:02d}"
    return [
        pine_input_row(key, kind, f"Feature {slot}", group="Feature Engineering", variable=f"{key}_string", inline=string_inline),
        pine_input_row(key, param_a, "Parameter A", group="Feature Engineering", variable=f"{key}_paramA", inline=param_inline),
        pine_input_row(key, param_b, "Parameter B", group="Feature Engineering", variable=f"{key}_paramB", inline=param_inline),
    ]


def setting_to_pine_inputs(key: str, value: object) -> list[dict[str, object]]:
    if key in {"f1", "f2", "f3", "f4", "f5"}:
        return feature_to_pine_inputs(key, value)

    input_map: dict[str, tuple[str, str, str | None, str | None]] = {
        "source": ("Source", "General Settings", None, None),
        "neighbors_count": ("Neighbors Count", "General Settings", None, None),
        "max_bars_back": ("Max Bars Back", "General Settings", None, None),
        "feature_count": ("Feature Count", "Feature Engineering", None, None),
        "color_compression": ("Color Compression", "General Settings", None, None),
        "show_exits": ("Show Default Exits", "General Settings", None, "exits"),
        "use_dynamic_exits": ("Use Dynamic Exits", "General Settings", None, "exits"),
        "use_worst_case": ("Use Worst Case Estimates", "General Settings", "useWorstCase", None),
        "include_full_history": ("Include Full History", "General Settings", "includeFullHistory", None),
        "use_volatility_filter": ("Use Volatility Filter", "Filters", None, None),
        "use_regime_filter": ("Use Regime Filter", "Filters", None, "regime"),
        "use_adx_filter": ("Use ADX Filter", "Filters", None, "adx"),
        "regime_threshold": ("Threshold", "Filters", None, "regime"),
        "adx_threshold": ("Threshold", "Filters", None, "adx"),
        "use_ema_filter": ("Use EMA Filter", "Filters", "useEmaFilter", "ema"),
        "ema_period": ("Period", "Filters", "emaPeriod", "ema"),
        "use_sma_filter": ("Use SMA Filter", "Filters", "useSmaFilter", "sma"),
        "sma_period": ("Period", "Filters", "smaPeriod", "sma"),
        "use_kernel_filter": ("Trade with Kernel", "Kernel Settings", "useKernelFilter", "kernel"),
        "show_kernel_estimate": ("Show Kernel Estimate", "Kernel Settings", "showKernelEstimate", "kernel"),
        "use_kernel_smoothing": ("Enhance Kernel Smoothing", "Kernel Settings", "useKernelSmoothing", "1"),
        "kernel_h": ("Lookback Window", "Kernel Settings", "h", "kernel"),
        "kernel_r": ("Relative Weighting", "Kernel Settings", "r", "kernel"),
        "kernel_x": ("Regression Level", "Kernel Settings", "x", "kernel"),
        "kernel_lag": ("Lag", "Kernel Settings", "lag", "1"),
        "show_bar_colors": ("Show Bar Colors", "Display Settings", "showBarColors", None),
        "show_bar_predictions": ("Show Bar Prediction Values", "Display Settings", "showBarPredictions", None),
        "use_atr_offset": ("Use ATR Offset", "Display Settings", "useAtrOffset", None),
        "bar_predictions_offset": ("Bar Prediction Offset", "Display Settings", "barPredictionsOffset", None),
        "use_confidence_gradient": ("Use Confidence Gradient", "Display Settings", "useConfidenceGradient", None),
        "show_trade_stats": ("Show Trade Stats", "General Settings", "showTradeStats", None),
    }
    title, group, variable, inline = input_map.get(key, (key, "", None, None))
    return [pine_input_row(key, value, title, group=group, variable=variable, inline=inline)]


def split_pine_call_args(args: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    index = 0
    while index < len(args):
        char = args[index]
        if quote:
            if char == quote and (index == 0 or args[index - 1] != "\\"):
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(depth - 1, 0)
        elif char == "," and depth == 0:
            parts.append(args[start:index].strip())
            start = index + 1
        index += 1
    tail = args[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def split_pine_named_arg(arg: str) -> tuple[str | None, str]:
    quote: str | None = None
    depth = 0
    for index, char in enumerate(arg):
        if quote:
            if char == quote and (index == 0 or arg[index - 1] != "\\"):
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(depth - 1, 0)
        elif char == "=" and depth == 0:
            return arg[:index].strip(), arg[index + 1 :].strip()
    return None, arg.strip()


def parse_pine_literal(value: str) -> object:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    if value == "true":
        return True
    if value == "false":
        return False
    if value.endswith(".") and value[:-1].isdigit():
        return float(value)
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def pine_input_call_rows(source: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    pattern = re.compile(
        r"^\s*(?:(?P<variable>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*)?"
        r"input\.(?P<kind>[A-Za-z_][A-Za-z0-9_]*)\((?P<args>.*)\)\s*,?\s*$"
    )
    for line_number, line in enumerate(source.splitlines(), start=1):
        match = pattern.match(line)
        if not match:
            continue
        positional: list[object] = []
        named: dict[str, object] = {}
        for arg in split_pine_call_args(match.group("args")):
            key, value = split_pine_named_arg(arg)
            parsed = parse_pine_literal(value)
            if key is None:
                positional.append(parsed)
            else:
                named[key.replace(" ", "")] = parsed
        row: dict[str, object] = {
            "line": line_number,
            "kind": match.group("kind"),
            "variable": match.group("variable"),
            "title": named.get("title", positional[1] if len(positional) > 1 else None),
            "group": named.get("group"),
            "inline": named.get("inline"),
            "value": named.get("defval", positional[0] if positional else None),
        }
        rows.append(row)
    return rows


def pine_input_contract_key(row: dict[str, object]) -> tuple[object, ...]:
    variable = row.get("variable")
    if isinstance(variable, str) and variable:
        return ("variable", variable)
    return ("title", row.get("title"), row.get("group"), row.get("inline"))


def expected_pine_input_contract_rows() -> list[dict[str, object]]:
    defaults = Settings()
    rows: list[dict[str, object]] = []
    for field in fields(Settings):
        for row in setting_to_pine_inputs(field.name, getattr(defaults, field.name)):
            rows.append(row)
    return rows


def pine_input_contract_for_source(record: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {
        "source": record["name"],
        "path": record["path"],
        "resolved_path": record.get("resolved_path"),
        "present": record.get("present"),
        "passed": False,
        "expected_count": 0,
        "actual_count": 0,
        "missing": [],
        "unexpected": [],
        "mismatches": [],
        "errors": [],
    }
    if not record.get("present") or not record.get("resolved_path"):
        result["errors"] = [f"{record['name']}: source missing"]
        return result

    source = Path(str(record["resolved_path"])).read_text(encoding="utf-8", errors="replace")
    expected_rows = expected_pine_input_contract_rows()
    actual_rows = pine_input_call_rows(source)
    expected_by_key = {pine_input_contract_key(row): row for row in expected_rows}
    actual_by_key = {pine_input_contract_key(row): row for row in actual_rows}
    result["expected_count"] = len(expected_rows)
    result["actual_count"] = len(actual_rows)

    missing: list[dict[str, object]] = []
    unexpected: list[dict[str, object]] = []
    mismatches: list[dict[str, object]] = []
    for key, expected in expected_by_key.items():
        actual = actual_by_key.get(key)
        if actual is None:
            missing.append(expected)
            continue
        for field_name in ["title", "group", "inline", "value"]:
            if actual.get(field_name) != expected.get(field_name):
                mismatches.append(
                    {
                        "setting": expected.get("setting"),
                        "variable": expected.get("variable"),
                        "field": field_name,
                        "expected": expected.get(field_name),
                        "actual": actual.get(field_name),
                        "line": actual.get("line"),
                    }
                )
    for key, actual in actual_by_key.items():
        if key not in expected_by_key:
            unexpected.append(actual)

    errors = []
    if missing:
        errors.append(f"missing Pine inputs: {len(missing)}")
    if unexpected:
        errors.append(f"unexpected Pine inputs: {len(unexpected)}")
    if mismatches:
        errors.append(f"Pine input metadata/default mismatches: {len(mismatches)}")
    result["missing"] = missing
    result["unexpected"] = unexpected
    result["mismatches"] = mismatches
    result["errors"] = errors
    result["passed"] = not errors
    return result


def pine_input_contract_records(pine_records: list[dict[str, object]]) -> list[dict[str, object]]:
    targets = [
        record
        for record in pine_records
        if record.get("name") == "lcv6" or Path(str(record.get("path", ""))).name.casefold() == "lcv6.pine"
    ]
    return [pine_input_contract_for_source(record) for record in targets]


def pine_input_contract_errors(records: list[dict[str, object]]) -> list[str]:
    errors: list[str] = []
    for record in records:
        if record.get("passed"):
            continue
        for error in record.get("errors", []):
            errors.append(f"{record['source']}: {error}")
    return errors


def pine_call_records(source: str, call: str) -> dict[str, str]:
    records: dict[str, str] = {}
    prefix = f"{call}("
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped.startswith(prefix):
            continue
        inner = stripped[len(prefix) : stripped.rfind(")")]
        args = split_pine_call_args(inner)
        positional: list[str] = []
        named: dict[str, str] = {}
        for arg in args:
            key, value = split_pine_named_arg(arg)
            if key is None:
                positional.append(value)
            else:
                named[key.replace(" ", "")] = value

        title_value = named.get("title")
        if title_value is None and len(positional) > 1:
            title_value = positional[1]
        if title_value is None:
            continue
        parsed_title = parse_pine_literal(title_value)
        if not isinstance(parsed_title, str):
            continue
        expression = named.get("condition", positional[0] if positional else "")
        records[parsed_title] = expression.strip()
    return records


def pine_condition_head(expression: str) -> str:
    return expression.split("?", 1)[0].strip()


def expected_pine_output_surface() -> dict[str, object]:
    return {
        "plots": {
            "Kernel Regression Estimate": "Kernel Regression Estimate",
            "Backtest Stream": "Backtest Stream",
        },
        "plotshapes": {
            "Buy": "Buy",
            "Sell": "Sell",
            "StopBuy": "StopBuy",
            "StopSell": "StopSell",
        },
        "alerts": {
            "Open Long ▲": "Open Long Alert",
            "Close Long ▲": "Close Long Alert",
            "Open Short ▼": "Open Short Alert",
            "Close Short ▼": "Close Short Alert",
            "Open Position ▲▼": "Open Position Alert",
            "Close Position ▲▼": "Close Position Alert",
            "Kernel Bullish Color Change": "Kernel Bullish Alert",
            "Kernel Bearish Color Change": "Kernel Bearish Alert",
        },
        "tokens": {
            "label.new": "Prediction Label",
            "y_val": "Prediction Label Y",
            "c_label": "Prediction Label Color",
            "barcolor": "Bar Color",
            "showTradeStats": "Trade Stats Visible",
            "tradeStatsHeader": "Trade Stats Header",
            "totalWins": "Total Wins",
            "totalLosses": "Total Losses",
            "totalEarlySignalFlips": "Total Early Signal Flips",
            "totalTrades": "Total Trades",
            "winLossRatio": "Win Loss Ratio",
            "totalWins / totalLosses": "Table WL Ratio",
            "winRate": "Win Rate",
        },
    }


def pine_output_contract_for_source(record: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {
        "source": record["name"],
        "path": record["path"],
        "resolved_path": record.get("resolved_path"),
        "present": record.get("present"),
        "passed": False,
        "missing": [],
        "mismatches": [],
        "errors": [],
    }
    if not record.get("present") or not record.get("resolved_path"):
        result["errors"] = [f"{record['name']}: source missing"]
        return result

    source = Path(str(record["resolved_path"])).read_text(encoding="utf-8", errors="replace")
    export_by_column = {str(row["column"]): str(row["pine_expression"]) for row in PINE_EXPORT_SERIES}
    missing: list[dict[str, object]] = []
    mismatches: list[dict[str, object]] = []

    missing_result_columns = sorted(set(RESULT_FIELDNAMES) - set(export_by_column))
    if missing_result_columns:
        missing.append({"kind": "result_export_columns", "columns": missing_result_columns})

    expected = expected_pine_output_surface()
    for call_name, expected_key in [
        ("plot", "plots"),
        ("plotshape", "plotshapes"),
        ("alertcondition", "alerts"),
    ]:
        actual_records = pine_call_records(source, call_name)
        expected_records = expected[expected_key]
        assert isinstance(expected_records, dict)
        for pine_title, export_column in expected_records.items():
            actual_expression = actual_records.get(str(pine_title))
            expected_expression = export_by_column.get(str(export_column))
            if actual_expression is None:
                missing.append({"kind": call_name, "title": pine_title, "column": export_column})
                continue
            if expected_expression is None:
                missing.append({"kind": "export_series", "column": export_column})
                continue
            actual_head = pine_condition_head(actual_expression)
            expected_head = pine_condition_head(expected_expression)
            if actual_head != expected_head:
                mismatches.append(
                    {
                        "kind": call_name,
                        "title": pine_title,
                        "column": export_column,
                        "expected": expected_head,
                        "actual": actual_head,
                    }
                )

    token_map = expected["tokens"]
    assert isinstance(token_map, dict)
    for token, export_column in token_map.items():
        if str(token) not in source:
            missing.append({"kind": "source_token", "token": token, "column": export_column})
        if str(export_column) not in RESULT_FIELDNAMES:
            missing.append({"kind": "result_column", "column": export_column})

    errors = []
    if missing:
        errors.append(f"missing Pine output surfaces: {len(missing)}")
    if mismatches:
        errors.append(f"Pine output expression mismatches: {len(mismatches)}")
    result["missing"] = missing
    result["mismatches"] = mismatches
    result["errors"] = errors
    result["passed"] = not errors
    return result


def pine_output_contract_records(pine_records: list[dict[str, object]]) -> list[dict[str, object]]:
    targets = [
        record
        for record in pine_records
        if record.get("name") == "lcv6" or Path(str(record.get("path", ""))).name.casefold() == "lcv6.pine"
    ]
    return [pine_output_contract_for_source(record) for record in targets]


def pine_output_contract_errors(records: list[dict[str, object]]) -> list[str]:
    errors: list[str] = []
    for record in records:
        if record.get("passed"):
            continue
        for error in record.get("errors", []):
            errors.append(f"{record['source']}: {error}")
    return errors


def helper_command_for_case(manifest_path: Path | None, spec: ParityFixture, include_full: bool = False) -> list[str]:
    command = [
        "PYTHONPATH=ports/python",
        "python3",
        "-m",
        "lorentzian_classification",
        "pine-export-helper",
    ]
    if manifest_path is not None:
        command.extend(["--manifest", str(manifest_path), "--manifest-case", spec.name])
    else:
        for field in fields(Settings):
            value = getattr(spec.settings, field.name)
            default = getattr(Settings(), field.name)
            if value != default:
                command.extend(setting_to_cli_flag(field.name, value))
    if include_full:
        command.append("--full")
    return command


def export_pack_workflow_commands(
    manifest_path: Path,
    fixture_dirs: list[Path],
    output_dir: str = DEFAULT_EXPORT_PACK_OUTPUT,
    pine_export_source_dir: str = DEFAULT_PINE_EXPORT_SOURCE_DIR,
) -> dict[str, object]:
    fixture_dir = preferred_fixture_dir_arg(fixture_dirs)
    export_pack_command = [
        "PYTHONPATH=ports/python",
        "python3",
        "-m",
        "lorentzian_classification",
        "export-pack",
        "--fixture-dir",
        fixture_dir,
        "--manifest",
        str(manifest_path),
        "--output",
        output_dir,
    ]
    verify_export_pack_command = [
        "PYTHONPATH=ports/python",
        "python3",
        "-m",
        "lorentzian_classification",
        "verify-export-pack",
        output_dir,
    ]
    import_pine_exports_command = [
        "PYTHONPATH=ports/python",
        "python3",
        "-m",
        "lorentzian_classification",
        "import-pine-exports",
        pine_export_source_dir,
        "--fixture-dir",
        fixture_dir,
        "--manifest",
        str(manifest_path),
    ]
    readiness_command = [
        "PYTHONPATH=ports/python",
        "python3",
        "-m",
        "lorentzian_classification",
        "readiness",
        "--fixture-dir",
        fixture_dir,
        "--manifest",
        str(manifest_path),
    ]
    return {
        "output_dir": output_dir,
        "export_pack_command": export_pack_command,
        "export_pack_command_string": shell_command_string(export_pack_command),
        "verify_export_pack_command": verify_export_pack_command,
        "verify_export_pack_command_string": shell_command_string(verify_export_pack_command),
        "import_pine_exports_command": import_pine_exports_command,
        "import_pine_exports_command_string": shell_command_string(import_pine_exports_command),
        "readiness_command": readiness_command,
        "readiness_command_string": shell_command_string(readiness_command),
    }


def preferred_fixture_dir_arg(fixture_dirs: list[Path]) -> str:
    fixture_dir = str(fixture_dirs[0]) if fixture_dirs else str(DEFAULT_FIXTURE_DIR)
    resolved_fixture_dirs = {path.resolve() for path in fixture_dirs if path.exists()}
    for workspace in workspace_roots(fixture_dirs):
        files_dir = workspace / "Files"
        if files_dir.exists() and files_dir.resolve() in resolved_fixture_dirs:
            return str(workspace)
    return fixture_dir


def external_runner_pack_workflow_commands(
    manifest_path: Path, fixture_dirs: list[Path], output_dir: str = DEFAULT_external_RUNNER_PACK_OUTPUT
) -> dict[str, object]:
    runner_command = [
        "PYTHONPATH=ports/python",
        "python3",
        "-m",
        "lorentzian_classification",
        "external-runner-pack",
        "--fixture-dir",
        preferred_fixture_dir_arg(fixture_dirs),
        "--manifest",
        str(manifest_path),
        "--only-failing",
        "--output",
        output_dir,
    ]
    verify_command = [
        "PYTHONPATH=ports/python",
        "python3",
        "-m",
        "lorentzian_classification",
        "verify-external-runner-pack",
        output_dir,
    ]
    return {
        "output_dir": output_dir,
        "external_runner_pack_command": runner_command,
        "external_runner_pack_command_string": shell_command_string(runner_command),
        "verify_external_runner_pack_command": verify_command,
        "verify_external_runner_pack_command_string": shell_command_string(verify_command),
    }


def readiness_artifacts_workflow_commands(
    manifest_path: Path, fixture_dirs: list[Path], output_dir: str = DEFAULT_READINESS_ARTIFACTS_OUTPUT
) -> dict[str, object]:
    prepare_command = [
        "PYTHONPATH=ports/python",
        "python3",
        "-m",
        "lorentzian_classification",
        "prepare-readiness-artifacts",
        "--fixture-dir",
        preferred_fixture_dir_arg(fixture_dirs),
        "--manifest",
        str(manifest_path),
        "--output",
        output_dir,
        "--clean-stale",
    ]
    verify_command = [
        "PYTHONPATH=ports/python",
        "python3",
        "-m",
        "lorentzian_classification",
        "verify-readiness-artifacts",
        output_dir,
    ]
    return {
        "output_dir": output_dir,
        "prepare_readiness_artifacts_command": prepare_command,
        "prepare_readiness_artifacts_command_string": shell_command_string(prepare_command),
        "verify_readiness_artifacts_command": verify_command,
        "verify_readiness_artifacts_command_string": shell_command_string(verify_command),
    }


def export_checklist_rows(
    payload: dict[str, object], fixture_dirs: list[Path], manifest_path: Path | None = None
) -> list[dict[str, object]]:
    cases = payload.get("required_uncovered_fixture_cases", [])
    if not isinstance(cases, list):
        raise ValueError("required_uncovered_fixture_cases must be a list")

    rows: list[dict[str, object]] = []
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"required_uncovered_fixture_cases {index}: expected object")
        spec = parity_fixture_from_mapping(case, index, "required_uncovered_fixture_cases")
        raw_settings = case.get("settings", {})
        proves = case.get("proves", [])
        python_smoke_fixture = case.get("python_smoke_fixture")
        if not isinstance(raw_settings, dict):
            raise ValueError(f"{spec.name}: settings must be an object")
        if not isinstance(proves, list) or not proves:
            raise ValueError(f"{spec.name}: proves must be a non-empty list")
        if not isinstance(python_smoke_fixture, str) or not python_smoke_fixture:
            raise ValueError(f"{spec.name}: python_smoke_fixture must be a non-empty string")
        export_series = pine_export_series_for_settings(spec.settings, include_full=True)
        settings_fingerprint_value = settings_fingerprint(spec.settings)
        path = fixture_path_for(fixture_dirs, spec.filename)
        smoke_path = fixture_path_for(fixture_dirs, python_smoke_fixture) if python_smoke_fixture else None
        cli_flags: list[str] = []
        tradingview_settings: list[dict[str, object]] = []
        for key, value in raw_settings.items():
            cli_flags.extend(setting_to_cli_flag(key, value))
            tradingview_settings.extend(setting_to_pine_inputs(key, value))
        helper_command = helper_command_for_case(manifest_path, spec, include_full=False)
        full_helper_command = helper_command_for_case(manifest_path, spec, include_full=True)
        rows.append(
            {
                "name": spec.name,
                "filename": spec.filename,
                "present": path is not None,
                "path": str(path) if path else None,
                "python_smoke_fixture": python_smoke_fixture,
                "python_smoke_fixture_present": smoke_path is not None if python_smoke_fixture else None,
                "python_smoke_fixture_path": str(smoke_path) if smoke_path else None,
                "proves": [str(item) for item in proves],
                "settings": raw_settings,
                "tradingview_settings": tradingview_settings,
                "cli_flags": cli_flags,
                "pine_export_helper_command": helper_command,
                "pine_export_helper_command_full": full_helper_command,
                "minimum_export_columns": pine_export_columns_for_settings(spec.settings, include_full=False),
                "full_instrumented_export_columns": pine_export_columns_for_settings(spec.settings, include_full=True)
                + [SETTINGS_FINGERPRINT_COLUMN],
                "settings_fingerprint": settings_fingerprint_value,
                "settings_fingerprint_column": SETTINGS_FINGERPRINT_COLUMN,
                "pine_export_series": export_series,
            }
        )
    return rows


def filter_export_checklist_rows(rows: list[dict[str, object]], cases: list[str] | None) -> list[dict[str, object]]:
    if not cases:
        return rows

    selected: list[dict[str, object]] = []
    for case in cases:
        matches = [
            row
            for row in rows
            if row.get("name") == case or row.get("filename") == case
        ]
        if not matches:
            raise ValueError(f"export checklist case not found: {case}")
        selected.extend(matches)

    deduped: list[dict[str, object]] = []
    seen: set[tuple[object, object]] = set()
    for row in selected:
        key = (row.get("name"), row.get("filename"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def smoke_output_invariants(tv_rows: list[TvRow], results, settings: Settings) -> tuple[dict[str, object], list[str]]:
    row_count_matches = len(tv_rows) == len(results)
    bar_alignment_mismatches = sum(
        1 for tv_row, result in zip(tv_rows, results) if tv_row.bar.time != result.bar.time
    )
    invalid_prediction_count = sum(
        1 for result in results if result.prediction < -settings.neighbors_count or result.prediction > settings.neighbors_count
    )
    invalid_direction_count = sum(1 for result in results if result.direction not in {-1, 0, 1})
    invalid_backtest_stream_count = sum(1 for result in results if result.backtest_stream not in {-2, -1, 0, 1, 2})
    trade_stats_visible_mismatches = sum(
        1 for result in results if result.trade_stats_visible != settings.show_trade_stats
    )
    finite_kernel_count = sum(1 for result in results if not is_missing(result.kernel))
    active_feature_counts = {
        f"f{slot}": sum(1 for result in results if not is_missing(getattr(result, f"f{slot}")))
        for slot in range(1, settings.feature_count + 1)
    }
    signal_counts = {
        "buy": sum(1 for result in results if result.buy),
        "sell": sum(1 for result in results if result.sell),
        "stop_buy": sum(1 for result in results if result.stop_buy),
        "stop_sell": sum(1 for result in results if result.stop_sell),
        "open_long_alert": sum(1 for result in results if result.open_long_alert),
        "open_short_alert": sum(1 for result in results if result.open_short_alert),
        "close_long_alert": sum(1 for result in results if result.close_long_alert),
        "close_short_alert": sum(1 for result in results if result.close_short_alert),
    }

    failures: list[str] = []
    if not row_count_matches:
        failures.append("Python output row count does not match input rows")
    if bar_alignment_mismatches:
        failures.append(f"bar time alignment mismatches: {bar_alignment_mismatches}")
    if invalid_prediction_count:
        failures.append(f"predictions outside neighbor bounds: {invalid_prediction_count}")
    if invalid_direction_count:
        failures.append(f"directions outside Pine enum values: {invalid_direction_count}")
    if invalid_backtest_stream_count:
        failures.append(f"backtest stream values outside Pine enum values: {invalid_backtest_stream_count}")
    if trade_stats_visible_mismatches:
        failures.append(f"trade stats visibility mismatches: {trade_stats_visible_mismatches}")

    # Tiny synthetic fixtures used in unit tests can finish before warmup. Real
    # representative fixtures should produce finite active features and kernels.
    if len(tv_rows) >= 100 and finite_kernel_count == 0:
        failures.append("no finite kernel values produced")
    if len(tv_rows) >= 100:
        for feature, count in active_feature_counts.items():
            if count == 0:
                failures.append(f"no finite {feature} values produced")

    return (
        {
            "row_count_matches": row_count_matches,
            "bar_alignment_mismatches": bar_alignment_mismatches,
            "invalid_prediction_count": invalid_prediction_count,
            "invalid_direction_count": invalid_direction_count,
            "invalid_backtest_stream_count": invalid_backtest_stream_count,
            "trade_stats_visible_mismatches": trade_stats_visible_mismatches,
            "finite_kernel_count": finite_kernel_count,
            "active_feature_counts": active_feature_counts,
            "signal_counts": signal_counts,
        },
        failures,
    )


def required_settings_smoke_records(payload: dict[str, object], fixture_dirs: list[Path]) -> list[dict[str, object]]:
    cases = payload.get("required_uncovered_fixture_cases", [])
    if not isinstance(cases, list):
        raise ValueError("required_uncovered_fixture_cases must be a list")

    records: list[dict[str, object]] = []
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"required_uncovered_fixture_cases {index}: expected object")

        spec = parity_fixture_from_mapping(case, index, "required_uncovered_fixture_cases")
        python_smoke_fixture = case.get("python_smoke_fixture")
        if not isinstance(python_smoke_fixture, str) or not python_smoke_fixture:
            raise ValueError(f"{spec.name}: python_smoke_fixture must be a non-empty string")

        record: dict[str, object] = {
            "name": spec.name,
            "filename": spec.filename,
            "python_smoke_fixture": python_smoke_fixture,
            "present": False,
            "path": None,
            "passed": False,
            "rows": None,
            "python_rows": None,
            "output_summary": None,
            "invariant_failures": [],
            "error": None,
        }
        path = fixture_path_for(fixture_dirs, python_smoke_fixture)
        record["present"] = path is not None
        record["path"] = str(path) if path else None
        if path is None:
            record["error"] = f"missing python smoke fixture {python_smoke_fixture}"
            record["invariant_failures"] = [record["error"]]
            records.append(record)
            continue

        try:
            tv_rows, price_scale = read_tradingview_csv(path, feature_columns=feature_export_columns(spec.settings))
            results = calculate(tv_rows, settings=spec.settings, price_scale=price_scale)
        except Exception as exc:  # pragma: no cover - defensive report path
            record["error"] = str(exc)
            record["invariant_failures"] = [record["error"]]
            records.append(record)
            continue

        record["rows"] = len(tv_rows)
        record["python_rows"] = len(results)
        output_summary, invariant_failures = smoke_output_invariants(tv_rows, results, spec.settings)
        record["output_summary"] = output_summary
        record["invariant_failures"] = invariant_failures
        record["passed"] = not invariant_failures
        if not record["passed"]:
            record["error"] = "; ".join(invariant_failures)
        records.append(record)
    return records


def pine_export_helper_rows(include_full: bool, settings: Settings | None = None) -> list[dict[str, object]]:
    settings = settings or Settings()
    return pine_export_series_for_settings(settings, include_full)


def pine_plot_line(row: dict[str, object]) -> str:
    column = row["column"]
    expression = row["pine_expression"]
    mode = row["export_mode"]
    if mode == "chart_export_builtin":
        return f"// {column}: exported by TradingView chart OHLC/time columns"
    if mode == "encoded_helper_required":
        return f"// {column}: {row['note']} Source expression: {expression}"
    return f'plot({expression}, "{column}", display=display.data_window)'


def settings_fingerprint_plot_line(settings: Settings) -> str:
    return f'plot({settings_fingerprint(settings)}, "{SETTINGS_FINGERPRINT_COLUMN}", display=display.data_window)'


def safe_export_pack_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return safe.strip("._") or "export_case"


def duplicate_export_pack_snippet_names(rows: list[dict[str, object]]) -> dict[str, list[str]]:
    owners_by_key: dict[str, list[str]] = {}
    display_by_key: dict[str, str] = {}
    for row in rows:
        snippet_name = safe_export_pack_name(str(row.get("name", ""))) + ".pine"
        snippet_key = snippet_name.casefold()
        display_by_key.setdefault(snippet_key, snippet_name)
        owners_by_key.setdefault(snippet_key, []).append(str(row.get("name", "")))
    return {display_by_key[key]: owners for key, owners in owners_by_key.items() if len(owners) > 1}


def export_pack_snippet(row: dict[str, object]) -> str:
    settings = row.get("settings", {})
    if not isinstance(settings, dict):
        settings = {}
    parsed_settings = settings_from_mapping(settings)
    helper_rows = pine_export_series_for_settings(parsed_settings, include_full=True)
    lines = [
        EXPORT_PACK_HEADER,
        "// Lorentzian Classification Python parity export helper",
        f"// Manifest case: {row['name']}",
        f"// Target CSV filename: {row['filename']}",
        "// Use the TradingView chart export for time/open/high/low/close.",
        "// Paste these lines after the lcv6 output variables are defined, then export the chart data window.",
    ]
    proves = row.get("proves", [])
    if isinstance(proves, list) and proves:
        lines.append("// Proves: " + "; ".join(str(item) for item in proves))
    cli_flags = row.get("cli_flags", [])
    if isinstance(cli_flags, list) and cli_flags:
        lines.append("// Equivalent Python CLI flags: " + " ".join(str(item) for item in cli_flags))
    lines.append(
        f"// Settings Fingerprint: {settings_fingerprint(parsed_settings)} "
        f'({SETTINGS_FINGERPRINT_COLUMN})'
    )
    source_fingerprint = row.get("pine_source_lock_fingerprint")
    if isinstance(source_fingerprint, str) and source_fingerprint:
        lines.append(f"// Pine Source Lock Fingerprint: {source_fingerprint}")
    lines.extend(pine_plot_line(helper_row) for helper_row in helper_rows)
    lines.append(settings_fingerprint_plot_line(parsed_settings))
    return "\n".join(lines) + "\n"


def export_pack_readme_text(
    manifest: object,
    acceptance_manifest_name: str,
    pine_source_lock_fingerprint: object,
    exports: list[object],
) -> str:
    readme_lines = [
        "# Lorentzian Parity Export Pack",
        "",
        f"Manifest: `{manifest}`",
        f"Acceptance manifest: `{acceptance_manifest_name}`",
        f"Pine source lock fingerprint: `{pine_source_lock_fingerprint}`",
        "",
        "Paste each `.pine` helper snippet into the matching lcv6 TradingView script after the output variables are defined.",
        "Export chart data with the listed TradingView settings and save the CSV using the target filename.",
        "",
    ]
    for row in exports:
        if not isinstance(row, dict):
            raise TypeError("export row must be an object")
        readme_lines.extend(
            [
                f"## {row['name']}",
                "",
                f"- Target CSV: `{row['filename']}`",
                f"- Helper snippet: `{Path(str(row['helper_snippet'])).name}`",
                f"- Status: {'present' if row['present'] else 'missing'}",
                f"- Settings fingerprint: `{row['settings_fingerprint']}` in `{row['settings_fingerprint_column']}`",
                f"- Pine source lock fingerprint: `{row['pine_source_lock_fingerprint']}`",
                f"- Required full export columns: `{len(row['full_instrumented_export_columns'])}`",
            ]
        )
        settings = row.get("settings", {})
        if isinstance(settings, dict) and settings:
            readme_lines.append("- Pine settings:")
            for key, value in sorted(settings.items()):
                readme_lines.append(f"  - `{key}`: `{value}`")
        tradingview_settings = row.get("tradingview_settings", [])
        if isinstance(tradingview_settings, list) and tradingview_settings:
            readme_lines.append("- TradingView settings:")
            for item in tradingview_settings:
                if not isinstance(item, dict):
                    continue
                group = str(item.get("group", "")).strip()
                title = str(item.get("title", "")).strip()
                location = f"{group} / {title}" if group else title
                setting = item.get("setting")
                details = [f"setting `{setting}`"] if setting else []
                variable = item.get("variable")
                if variable:
                    details.append(f"variable `{variable}`")
                inline = item.get("inline")
                if inline:
                    details.append(f"inline `{inline}`")
                details_text = f" ({', '.join(details)})" if details else ""
                readme_lines.append(f"  - {location}: `{item.get('value')}`{details_text}")
        readme_lines.append("")
    return "\n".join(readme_lines).rstrip() + "\n"


def cmd_pine_export_helper(args: argparse.Namespace) -> int:
    settings = settings_from_args(args)
    manifest_case: str | None = args.manifest_case
    manifest_path = manifest_path_from_arg(args.manifest)
    if manifest_case:
        if not manifest_path:
            print("pine-export-helper --manifest-case requires a parity manifest")
            return 1
        try:
            payload = load_manifest_payload(manifest_path)
            settings = required_fixture_case_by_name(payload, manifest_case).settings
        except ValueError as exc:
            print(f"invalid parity manifest: {exc}")
            return 1

    rows = pine_export_helper_rows(args.full, settings)
    plot_lines = [pine_plot_line(row) for row in rows]
    if args.full and manifest_case:
        plot_lines.append(settings_fingerprint_plot_line(settings))
    payload = {
        "full": args.full,
        "manifest": str(manifest_path) if manifest_path else None,
        "manifest_case": manifest_case,
        "settings": {
            field.name: getattr(settings, field.name)
            for field in fields(Settings)
        },
        "minimum_export_columns": pine_export_columns_for_settings(settings, include_full=False),
        "full_instrumented_export_columns": pine_export_columns_for_settings(settings, include_full=True)
        + ([SETTINGS_FINGERPRINT_COLUMN] if manifest_case else []),
        "settings_fingerprint": settings_fingerprint(settings) if manifest_case else None,
        "settings_fingerprint_column": SETTINGS_FINGERPRINT_COLUMN if manifest_case else None,
        "rows": rows,
        "plot_lines": plot_lines,
        "summary": {
            "total": len(rows),
            "chart_builtin": sum(1 for row in rows if row["export_mode"] == "chart_export_builtin"),
            "plot_data_window": sum(
                1
                for row in rows
                if row["export_mode"] in {"plot_data_window", "plot_or_plot_data_window", "encoded_plot_data_window"}
            ),
            "encoded_helper_required": sum(1 for row in rows if row["export_mode"] == "encoded_helper_required"),
            "encoded_plot_data_window": sum(1 for row in rows if row["export_mode"] == "encoded_plot_data_window"),
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print("// Lorentzian Classification Python parity export helper")
    print("// Paste after lcv6 output variables are defined. Chart time/OHLC columns are exported by TradingView.")
    if manifest_case:
        print(f"// Manifest case: {manifest_case}")
    for line in payload["plot_lines"]:
        print(line)
    return 0


def validate_pine_export_file(path: Path, spec: ParityFixture, tolerance: float) -> tuple[dict[str, object], list[dict[str, str]]]:
    record: dict[str, object] = {
        "name": spec.name,
        "filename": spec.filename,
        "path": str(path),
        "passed": False,
        "tolerance": tolerance,
        "summary": None,
        "mismatch_count": None,
        "schema": None,
        "error": None,
    }
    schema = pine_export_schema(path, spec.settings, True, settings_fingerprint(spec.settings))
    record["schema"] = schema
    if not schema["valid"]:
        duplicate_columns = schema.get("duplicate_columns")
        if duplicate_columns:
            record["error"] = "duplicate export columns: " + ", ".join(str(column) for column in duplicate_columns)
            return record, []
        missing_columns = schema["missing_required_columns"]
        if missing_columns:
            record["error"] = "missing required export columns: " + ", ".join(str(column) for column in missing_columns)
            return record, []
        missing_full = schema["missing_full_numeric_columns"]
        if missing_full:
            record["error"] = "missing full numeric export columns: " + ", ".join(str(column) for column in missing_full)
            return record, []
        if schema.get("settings_fingerprint") != settings_fingerprint(spec.settings):
            record["error"] = (
                f"settings fingerprint mismatch: expected {settings_fingerprint(spec.settings)} "
                f"got {schema.get('settings_fingerprint')}"
            )
            return record, []
        record["error"] = "invalid Pine export schema"
        return record, []

    try:
        tv_rows, price_scale = read_tradingview_csv(path, feature_columns=feature_export_columns(spec.settings))
        results = calculate(tv_rows, settings=spec.settings, price_scale=price_scale)
    except (KeyError, ValueError, csv.Error) as exc:
        record["error"] = f"invalid Pine export data: {exc}"
        return record, []

    summary, mismatches = parity_summary(tv_rows, results, tolerance, spec.settings)
    record["passed"] = bool(summary["pass"])
    record["summary"] = summary
    record["mismatch_count"] = len(mismatches)
    if not record["passed"]:
        record["error"] = f"Python parity mismatches: {len(mismatches)}"
    return record, mismatches


def cmd_import_pine_exports(args: argparse.Namespace) -> int:
    raw_source_dirs = args.source_dir if isinstance(args.source_dir, list) else [args.source_dir]
    source_dirs = [Path(value) for value in raw_source_dirs]
    for source_dir in source_dirs:
        if not source_dir.exists():
            print(f"source directory not found: {source_dir}")
            return 1
        if not source_dir.is_dir():
            print(f"source path is not a directory: {source_dir}")
            return 1

    target_dir = fixture_dir_from_arg(args.fixture_dir)
    if not target_dir.exists():
        print(f"target fixture directory not found: {target_dir}")
        return 1
    if not target_dir.is_dir():
        print(f"target fixture path is not a directory: {target_dir}")
        return 1

    manifest_path = manifest_path_from_arg(args.manifest)
    if not manifest_path:
        print("import-pine-exports requires a parity manifest")
        return 1

    try:
        payload = load_manifest_payload(manifest_path)
        fixture_dirs = fixture_search_dirs_from_arg(str(target_dir))
        rows = export_checklist_rows(payload, fixture_dirs, manifest_path)
        rows = filter_export_checklist_rows(rows, args.case)
    except ValueError as exc:
        print(f"invalid parity manifest: {exc}")
        return 1

    imports: list[dict[str, object]] = []
    errors: list[str] = []
    for row in rows:
        spec = ParityFixture(str(row["name"]), str(row["filename"]), settings_from_mapping(row["settings"]))
        candidate_source_paths = [source_dir / spec.filename for source_dir in source_dirs]
        source_path = next((path for path in candidate_source_paths if path.is_file()), None)
        destination_path = target_dir / spec.filename
        record: dict[str, object] = {
            "name": spec.name,
            "filename": spec.filename,
            "source": str(source_path or candidate_source_paths[0]),
            "candidate_sources": [str(path) for path in candidate_source_paths],
            "destination": str(destination_path),
            "copied": False,
            "dry_run": args.dry_run,
            "status": "pending",
            "validation": None,
            "error": None,
        }
        if source_path is None:
            record["status"] = "missing_source"
            record["error"] = "source export not found in source directories: " + ", ".join(
                str(path) for path in candidate_source_paths
            )
            errors.append(str(record["error"]))
            imports.append(record)
            continue

        validation, _mismatches = validate_pine_export_file(source_path, spec, args.tolerance)
        record["validation"] = validation
        if not validation["passed"]:
            record["status"] = "invalid_source"
            record["error"] = validation.get("error") or "source export did not pass validation"
            errors.append(f"{spec.name}: {record['error']}")
            imports.append(record)
            continue

        if destination_path.exists():
            if not destination_path.is_file():
                record["status"] = "invalid_destination"
                record["error"] = f"destination is not a file: {destination_path}"
                errors.append(str(record["error"]))
                imports.append(record)
                continue
            if file_sha256(source_path) == file_sha256(destination_path):
                record["status"] = "already_present"
                imports.append(record)
                continue
            if not args.overwrite:
                record["status"] = "destination_exists"
                record["error"] = f"destination exists and differs; rerun with --overwrite: {destination_path}"
                errors.append(str(record["error"]))
                imports.append(record)
                continue

        if args.dry_run:
            record["status"] = "would_copy"
        else:
            shutil.copy2(source_path, destination_path)
            record["copied"] = True
            record["status"] = "copied"
        imports.append(record)

    report = {
        "valid": not errors,
        "source_dir": str(source_dirs[0]),
        "source_dirs": [str(source_dir) for source_dir in source_dirs],
        "target_dir": str(target_dir),
        "manifest": str(manifest_path),
        "imports": imports,
        "summary": {
            "total": len(imports),
            "copied": sum(1 for row in imports if row["status"] == "copied"),
            "would_copy": sum(1 for row in imports if row["status"] == "would_copy"),
            "already_present": sum(1 for row in imports if row["status"] == "already_present"),
            "failed": len(errors),
        },
        "errors": errors,
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("import Pine exports: PASS" if report["valid"] else "import Pine exports: FAIL")
        print("sources:")
        for source_dir in source_dirs:
            print(f"  - {source_dir}")
        print(f"target: {target_dir}")
        for item in imports:
            print(f"  - {item['name']}: {item['status']}")
            if item.get("error"):
                print(f"    {item['error']}")
    return 0 if report["valid"] else 1


def cmd_export_pack(args: argparse.Namespace) -> int:
    if args.allow_stale and args.clean_stale:
        print("export-pack cannot combine --allow-stale and --clean-stale")
        return 1

    fixture_dirs = fixture_search_dirs_from_arg(args.fixture_dir)
    existing_fixture_dirs, fixture_error = fixture_directory_status(fixture_dirs)
    if fixture_error:
        print(fixture_error)
        return 1

    manifest_path = manifest_path_from_arg(args.manifest)
    if not manifest_path:
        print("export pack requires a parity manifest")
        return 1
    try:
        payload = load_manifest_payload(manifest_path)
        rows = export_checklist_rows(payload, existing_fixture_dirs, manifest_path)
        rows = filter_export_checklist_rows(rows, args.case)
        pine_records, pine_errors = validate_source_records(existing_fixture_dirs, load_pine_source_specs(payload))
    except ValueError as exc:
        print(f"invalid parity manifest: {exc}")
        return 1
    if pine_errors:
        print("export pack requires valid manifest-pinned Pine sources:")
        for error in pine_errors:
            print(f"  - {error}")
        return 1
    pine_source_locks = source_lock_entries(pine_records)
    pine_source_lock_fingerprint = source_lock_fingerprint(pine_source_locks)

    if not args.include_present:
        rows = [row for row in rows if not row["present"]]

    duplicate_snippets = duplicate_export_pack_snippet_names(rows)
    if duplicate_snippets:
        print("export pack helper snippet filenames are ambiguous:")
        for snippet, owners in sorted(duplicate_snippets.items()):
            print(f"  - {snippet}: {', '.join(owners)}")
        return 1

    output_dir = Path(args.output)
    output_error = output_directory_error(output_dir)
    if output_error:
        print(f"invalid export pack output directory: {output_error}")
        return 1
    output_dir.mkdir(parents=True, exist_ok=True)
    expected_snippet_names = {safe_export_pack_name(str(row["name"])) + ".pine" for row in rows}
    stale_snippets = sorted(
        path
        for path in output_dir.glob("*.pine")
        if path.name not in expected_snippet_names
    )
    if stale_snippets and args.clean_stale:
        for path in stale_snippets:
            source = path.read_text(encoding="utf-8", errors="replace")
            if not source.startswith(EXPORT_PACK_HEADER):
                print(f"refusing to remove non export-pack Pine file: {path}")
                return 1
            path.unlink()
    elif stale_snippets and not args.allow_stale:
        print("export pack output contains stale helper snippets:")
        for path in stale_snippets:
            print(f"  - {path}")
        print("rerun with --clean-stale to remove generated stale snippets or --allow-stale to keep them")
        return 1

    exports = []
    for row in rows:
        record = dict(row)
        record["pine_source_lock_fingerprint"] = pine_source_lock_fingerprint
        record["pine_source_locks"] = pine_source_locks
        snippet_name = safe_export_pack_name(str(row["name"])) + ".pine"
        snippet_path = output_dir / snippet_name
        snippet_path.write_text(export_pack_snippet(record), encoding="utf-8")
        record["helper_snippet"] = str(snippet_path)
        record["helper_snippet_sha256"] = file_sha256(snippet_path)
        exports.append(record)

    payload_out = {
        "fixture_directories": [str(path) for path in existing_fixture_dirs],
        "manifest": str(manifest_path),
        "output_dir": str(output_dir),
        "include_present": args.include_present,
        "pine_source_locks": pine_source_locks,
        "pine_source_lock_fingerprint": pine_source_lock_fingerprint,
        "exports": exports,
        "summary": {
            "total": len(exports),
            "missing": sum(1 for row in exports if not row["present"]),
            "present": sum(1 for row in exports if row["present"]),
        },
    }
    acceptance_manifest_path = output_dir / "acceptance_manifest.csv"
    payload_out["acceptance_manifest"] = str(acceptance_manifest_path)
    (output_dir / "export_pack.json").write_text(
        json.dumps(payload_out, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with acceptance_manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPORT_PACK_ACCEPTANCE_FIELDNAMES)
        writer.writeheader()
        for row in exports:
            writer.writerow(
                {
                    "name": row["name"],
                    "target_csv": row["filename"],
                    "helper_snippet": Path(str(row["helper_snippet"])).name,
                    "helper_snippet_sha256": row["helper_snippet_sha256"],
                    "status": "present" if row["present"] else "missing",
                    "settings_fingerprint": row["settings_fingerprint"],
                    "settings_fingerprint_column": row["settings_fingerprint_column"],
                    "pine_source_lock_fingerprint": row["pine_source_lock_fingerprint"],
                    "required_full_export_column_count": len(row["full_instrumented_export_columns"]),
                    "required_full_export_columns": "|".join(
                        str(column) for column in row["full_instrumented_export_columns"]
                    ),
                    "cli_flags": " ".join(str(item) for item in row["cli_flags"]),
                    "settings_json": json.dumps(row["settings"], sort_keys=True),
                    "proves": " | ".join(str(item) for item in row["proves"]),
                }
            )

    (output_dir / "README.md").write_text(
        export_pack_readme_text(
            str(manifest_path),
            acceptance_manifest_path.name,
            pine_source_lock_fingerprint,
            exports,
        ),
        encoding="utf-8",
    )

    if args.json:
        print(json.dumps(payload_out, indent=2, sort_keys=True))
    else:
        print(f"export pack written: {output_dir}")
        print(f"helper snippets: {len(exports)}")
        for row in exports:
            print(f"  - {row['name']} -> {row['helper_snippet']}")
    return 0


def cmd_verify_export_pack(args: argparse.Namespace) -> int:
    pack_dir = Path(args.input)
    if pack_dir.exists() and not pack_dir.is_dir():
        print(f"export pack input is not a directory: {pack_dir}")
        return 1
    index_path = pack_dir / "export_pack.json"
    errors: list[str] = []
    if not index_path.exists():
        print(f"export pack index not found: {index_path}")
        return 1
    if not index_path.is_file():
        print(f"invalid export pack index: path is not a file: {index_path}")
        return 1

    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"invalid export pack index: {exc}")
        return 1

    exports = payload.get("exports", [])
    if not isinstance(exports, list):
        print("invalid export pack index: exports must be a list")
        return 1

    acceptance_manifest = payload.get("acceptance_manifest")
    if not isinstance(acceptance_manifest, str) or not acceptance_manifest:
        acceptance_path = pack_dir / "acceptance_manifest.csv"
        errors.append("missing acceptance_manifest path in export_pack.json")
        acceptance_path_error = None
    else:
        acceptance_path_error = windows_artifact_path_error(acceptance_manifest)
        if acceptance_path_error:
            errors.append(f"acceptance manifest path {acceptance_path_error}: {acceptance_manifest}")
        acceptance_path = Path(acceptance_manifest)
        if not acceptance_path.is_absolute():
            acceptance_path = pack_dir / acceptance_path

    acceptance_path_within_pack = is_path_within(acceptance_path, pack_dir)
    acceptance_rows: dict[str, dict[str, str]] = {}
    seen_acceptance_targets: set[str] = set()
    seen_acceptance_helpers: set[str] = set()
    if not acceptance_path_error:
        if not acceptance_path.exists():
            errors.append(f"acceptance manifest not found: {acceptance_path}")
        elif not acceptance_path_within_pack:
            errors.append(f"acceptance manifest path escapes export pack: {acceptance_path}")
        elif not acceptance_path.is_file():
            errors.append(f"acceptance manifest path is not a file: {acceptance_path}")
        else:
            with acceptance_path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames != EXPORT_PACK_ACCEPTANCE_FIELDNAMES:
                    errors.append(
                        "acceptance manifest header mismatch expected "
                        f"{','.join(EXPORT_PACK_ACCEPTANCE_FIELDNAMES)} got "
                        f"{','.join(reader.fieldnames or [])}"
                    )
                for row in reader:
                    name = row.get("name")
                    if not name:
                        errors.append("acceptance manifest row missing name")
                        continue
                    if name in acceptance_rows:
                        errors.append(f"duplicate acceptance manifest row: {name}")
                    target_csv = row.get("target_csv", "")
                    if not target_csv:
                        errors.append(f"{name}: acceptance target_csv missing")
                    elif target_csv_error := manifest_relative_path_error(target_csv):
                        errors.append(f"{name}: acceptance target_csv {target_csv_error}")
                    elif target_csv in seen_acceptance_targets:
                        errors.append(f"{name}: duplicate acceptance target_csv: {target_csv}")
                    seen_acceptance_targets.add(target_csv)
                    helper_snippet = row.get("helper_snippet", "")
                    if not helper_snippet:
                        errors.append(f"{name}: acceptance helper_snippet missing")
                    elif helper_error := manifest_relative_path_error(helper_snippet):
                        errors.append(f"{name}: acceptance helper_snippet {helper_error}")
                    elif Path(helper_snippet).name != helper_snippet or PureWindowsPath(helper_snippet).name != helper_snippet:
                        errors.append(f"{name}: acceptance helper_snippet must be a filename")
                    elif helper_snippet in seen_acceptance_helpers:
                        errors.append(f"{name}: duplicate acceptance helper_snippet: {helper_snippet}")
                    seen_acceptance_helpers.add(helper_snippet)
                    acceptance_rows[name] = row

    source_fingerprint = payload.get("pine_source_lock_fingerprint")
    source_fingerprint_valid = isinstance(source_fingerprint, str) and is_sha256_hex(source_fingerprint)
    if not source_fingerprint_valid:
        errors.append("invalid or missing pine_source_lock_fingerprint")

    pine_source_locks = payload.get("pine_source_locks")
    source_locks_valid = True
    if not isinstance(pine_source_locks, list) or not pine_source_locks:
        errors.append("invalid or missing pine_source_locks")
        pine_source_locks = []
        source_locks_valid = False
    else:
        seen_source_lock_names: set[str] = set()
        seen_source_lock_paths: set[str] = set()
        for index, lock in enumerate(pine_source_locks, start=1):
            if not isinstance(lock, dict):
                source_locks_valid = False
                break
            for key in ["name", "path", "sha256"]:
                value = lock.get(key)
                if not isinstance(value, str) or not value:
                    source_locks_valid = False
                    break
            name_value = lock.get("name")
            path_value = lock.get("path")
            if isinstance(name_value, str) and name_value:
                if name_value in seen_source_lock_names:
                    errors.append(f"pine_source_locks {index}: duplicate name: {name_value}")
                    source_locks_valid = False
                seen_source_lock_names.add(name_value)
            if isinstance(path_value, str) and path_value:
                if path_error := manifest_relative_path_error(path_value):
                    errors.append(f"pine_source_locks {index}: path {path_error}")
                    source_locks_valid = False
                if path_value in seen_source_lock_paths:
                    errors.append(f"pine_source_locks {index}: duplicate path: {path_value}")
                    source_locks_valid = False
                seen_source_lock_paths.add(path_value)
            sha256_value = lock.get("sha256")
            if not isinstance(sha256_value, str) or not is_sha256_hex(sha256_value):
                source_locks_valid = False
            if not source_locks_valid:
                break
        if not source_locks_valid:
            errors.append("invalid pine_source_locks")

    if source_locks_valid and source_fingerprint_valid:
        computed_source_fingerprint = source_lock_fingerprint(pine_source_locks)
        if computed_source_fingerprint != source_fingerprint:
            errors.append(
                "pine_source_lock_fingerprint mismatch "
                f"expected {computed_source_fingerprint} got {source_fingerprint}"
            )

    summary = payload.get("summary")
    if not isinstance(summary, dict):
        errors.append("invalid or missing export pack summary")
    else:
        expected_summary = {
            "total": len(exports),
            "missing": sum(1 for export in exports if isinstance(export, dict) and not export.get("present")),
            "present": sum(1 for export in exports if isinstance(export, dict) and export.get("present")),
        }
        for key, expected_value in expected_summary.items():
            if summary.get(key) != expected_value:
                errors.append(f"summary {key} mismatch expected {expected_value} got {summary.get(key)}")

    seen_export_names: set[str] = set()
    seen_export_filenames: set[str] = set()
    seen_helper_snippets: set[str] = set()
    verified_helpers = 0
    for export in exports:
        if not isinstance(export, dict):
            errors.append("export row must be an object")
            continue
        name = str(export.get("name", ""))
        if not name:
            errors.append("export row missing name")
            continue
        if name in seen_export_names:
            errors.append(f"duplicate export row: {name}")
        seen_export_names.add(name)
        filename = str(export.get("filename", ""))
        if not filename:
            errors.append(f"{name}: missing target_csv filename")
        elif filename_error := manifest_relative_path_error(filename):
            errors.append(f"{name}: target_csv {filename_error}")
        elif filename in seen_export_filenames:
            errors.append(f"{name}: duplicate export target_csv: {filename}")
        seen_export_filenames.add(filename)
        helper = export.get("helper_snippet")
        if not isinstance(helper, str) or not helper:
            errors.append(f"{name}: missing helper_snippet")
            continue
        helper_path_error = windows_artifact_path_error(helper)
        if helper_path_error:
            errors.append(f"{name}: helper snippet path {helper_path_error}: {helper}")
            continue
        helper_path = Path(helper)
        if not helper_path.is_absolute():
            helper_path = pack_dir / helper_path
        if not is_path_within(helper_path, pack_dir):
            errors.append(f"{name}: helper snippet path escapes export pack: {helper_path}")
            continue
        helper_key = str(helper_path)
        if helper_key in seen_helper_snippets:
            errors.append(f"{name}: duplicate helper snippet path: {helper_path}")
        seen_helper_snippets.add(helper_key)
        if not helper_path.exists():
            errors.append(f"{name}: helper snippet not found: {helper_path}")
            continue
        if not helper_path.is_file():
            errors.append(f"{name}: helper snippet path is not a file: {helper_path}")
            continue
        helper_sha = file_sha256(helper_path)
        expected_helper_sha = export.get("helper_snippet_sha256")
        if helper_sha != expected_helper_sha:
            errors.append(f"{name}: helper snippet sha256 mismatch expected {expected_helper_sha} got {helper_sha}")
        source_text = helper_path.read_text(encoding="utf-8", errors="replace")
        if not source_text.startswith(EXPORT_PACK_HEADER):
            errors.append(f"{name}: helper snippet missing export-pack header")
        try:
            expected_helper_text = export_pack_snippet(export)
        except (KeyError, ValueError) as exc:
            errors.append(f"{name}: cannot regenerate helper snippet: {exc}")
        else:
            if source_text != expected_helper_text:
                errors.append(f"{name}: helper snippet content does not match export metadata")
        export_source_fingerprint = export.get("pine_source_lock_fingerprint")
        if export_source_fingerprint != source_fingerprint:
            errors.append(f"{name}: pine source lock fingerprint does not match pack index")
        if source_locks_valid and export.get("pine_source_locks") != pine_source_locks:
            errors.append(f"{name}: pine_source_locks mismatch")
        if isinstance(source_fingerprint, str) and source_fingerprint not in source_text:
            errors.append(f"{name}: helper snippet missing pine source lock fingerprint")
        settings_fingerprint_column = export.get("settings_fingerprint_column")
        settings_fingerprint_value = export.get("settings_fingerprint")
        raw_settings = export.get("settings")
        parsed_settings: Settings | None = None
        if not isinstance(raw_settings, dict):
            errors.append(f"{name}: settings must be an object")
        else:
            try:
                parsed_settings = settings_from_mapping(raw_settings)
            except ValueError as exc:
                errors.append(f"{name}: invalid settings: {exc}")
        if parsed_settings is not None and settings_fingerprint_value != settings_fingerprint(parsed_settings):
            errors.append(
                f"{name}: settings_fingerprint does not match settings "
                f"expected {settings_fingerprint(parsed_settings)} got {settings_fingerprint_value}"
            )
        cli_flags = export.get("cli_flags", [])
        if not isinstance(cli_flags, list):
            errors.append(f"{name}: cli_flags must be a list")
        elif isinstance(raw_settings, dict):
            actual_cli_flag_groups = cli_flag_group_counts(cli_flags)
            expected_cli_flags: list[str] = []
            for key, value in raw_settings.items():
                expected_cli_flags.extend(setting_to_cli_flag(key, value))
            expected_cli_flag_groups = cli_flag_group_counts(expected_cli_flags)
            if actual_cli_flag_groups is None:
                errors.append(f"{name}: invalid cli_flags")
            elif actual_cli_flag_groups != expected_cli_flag_groups:
                errors.append(f"{name}: cli_flags do not match settings")
        tradingview_settings = export.get("tradingview_settings", [])
        if not isinstance(tradingview_settings, list):
            errors.append(f"{name}: tradingview_settings must be a list")
        elif isinstance(raw_settings, dict):
            expected_tradingview_settings: list[dict[str, object]] = []
            for key, value in raw_settings.items():
                expected_tradingview_settings.extend(setting_to_pine_inputs(key, value))
            actual_tradingview_counts = object_list_counts(tradingview_settings)
            expected_tradingview_counts = object_list_counts(expected_tradingview_settings)
            if actual_tradingview_counts is None:
                errors.append(f"{name}: invalid tradingview_settings")
            elif actual_tradingview_counts != expected_tradingview_counts:
                errors.append(f"{name}: tradingview_settings do not match settings")
        if not isinstance(settings_fingerprint_value, int) or not (0 <= settings_fingerprint_value <= 0xFFFFFFFF):
            errors.append(f"{name}: invalid or missing settings_fingerprint")
        if settings_fingerprint_column != SETTINGS_FINGERPRINT_COLUMN:
            errors.append(f"{name}: invalid or missing settings_fingerprint_column")
        if settings_fingerprint_column and str(settings_fingerprint_column) not in source_text:
            errors.append(f"{name}: helper snippet missing settings fingerprint column")
        if settings_fingerprint_value is not None and str(settings_fingerprint_value) not in source_text:
            errors.append(f"{name}: helper snippet missing settings fingerprint value")
        pine_export_series = export.get("pine_export_series", [])
        if not isinstance(pine_export_series, list):
            errors.append(f"{name}: pine_export_series must be a list")
        elif parsed_settings is not None:
            expected_pine_export_series = pine_export_series_for_settings(parsed_settings, include_full=True)
            actual_series_counts = object_list_counts(pine_export_series)
            expected_series_counts = object_list_counts(expected_pine_export_series)
            if actual_series_counts is None:
                errors.append(f"{name}: invalid pine_export_series")
            elif actual_series_counts != expected_series_counts:
                errors.append(f"{name}: pine_export_series do not match settings")

        acceptance_row = acceptance_rows.get(name)
        if acceptance_row is None:
            errors.append(f"{name}: missing acceptance manifest row")
        else:
            expected_status = "present" if export.get("present") else "missing"
            expected_cli_flags = " ".join(str(item) for item in cli_flags) if isinstance(cli_flags, list) else ""
            expected_settings_json = json.dumps(export.get("settings", {}), sort_keys=True)
            expected_proves = " | ".join(str(item) for item in export.get("proves", []))
            if acceptance_row.get("target_csv") != str(export.get("filename")):
                errors.append(f"{name}: acceptance target_csv mismatch")
            if acceptance_row.get("helper_snippet") != helper_path.name:
                errors.append(f"{name}: acceptance helper_snippet mismatch")
            if acceptance_row.get("helper_snippet_sha256") != helper_sha:
                errors.append(f"{name}: acceptance helper_snippet_sha256 mismatch")
            if acceptance_row.get("status") != expected_status:
                errors.append(f"{name}: acceptance status mismatch")
            if acceptance_row.get("pine_source_lock_fingerprint") != source_fingerprint:
                errors.append(f"{name}: acceptance pine_source_lock_fingerprint mismatch")
            if acceptance_row.get("settings_fingerprint") != str(settings_fingerprint_value):
                errors.append(f"{name}: acceptance settings_fingerprint mismatch")
            if acceptance_row.get("cli_flags") != expected_cli_flags:
                errors.append(f"{name}: acceptance cli_flags mismatch")
            if acceptance_row.get("settings_json") != expected_settings_json:
                errors.append(f"{name}: acceptance settings_json mismatch")
            if acceptance_row.get("proves") != expected_proves:
                errors.append(f"{name}: acceptance proves mismatch")
            minimum_columns = export.get("minimum_export_columns", [])
            if not isinstance(minimum_columns, list):
                errors.append(f"{name}: minimum_export_columns must be a list")
            elif parsed_settings is not None:
                expected_minimum_columns = pine_export_columns_for_settings(parsed_settings, include_full=False)
                if [str(column) for column in minimum_columns] != expected_minimum_columns:
                    errors.append(f"{name}: minimum_export_columns do not match settings")
            full_columns = export.get("full_instrumented_export_columns", [])
            if not isinstance(full_columns, list):
                errors.append(f"{name}: full_instrumented_export_columns must be a list")
            else:
                normalized_full_columns = [str(column) for column in full_columns]
                if parsed_settings is not None:
                    expected_full_columns_for_settings = (
                        pine_export_columns_for_settings(parsed_settings, include_full=True)
                        + [SETTINGS_FINGERPRINT_COLUMN]
                    )
                    if normalized_full_columns != expected_full_columns_for_settings:
                        errors.append(f"{name}: full_instrumented_export_columns do not match settings")
                expected_columns = "|".join(str(column) for column in full_columns)
                if SETTINGS_FINGERPRINT_COLUMN not in normalized_full_columns:
                    errors.append(f"{name}: full_instrumented_export_columns missing settings fingerprint column")
                if acceptance_row.get("required_full_export_column_count") != str(len(full_columns)):
                    errors.append(f"{name}: acceptance required_full_export_column_count mismatch")
                if acceptance_row.get("required_full_export_columns") != expected_columns:
                    errors.append(f"{name}: acceptance required_full_export_columns mismatch")
            if settings_fingerprint_column and str(settings_fingerprint_column) not in acceptance_row.get(
                "required_full_export_columns", ""
            ):
                errors.append(f"{name}: acceptance required columns missing settings fingerprint column")
        verified_helpers += 1

    for name in sorted(set(acceptance_rows) - seen_export_names):
        errors.append(f"{name}: acceptance manifest row has no matching export")

    readme_path = pack_dir / "README.md"
    if not readme_path.exists():
        errors.append(f"export pack README not found: {readme_path}")
    elif not readme_path.is_file():
        errors.append(f"export pack README path is not a file: {readme_path}")
    else:
        try:
            expected_readme_text = export_pack_readme_text(
                payload.get("manifest"),
                acceptance_path.name,
                source_fingerprint,
                exports,
            )
        except (KeyError, TypeError) as exc:
            errors.append(f"cannot regenerate export pack README: {exc}")
        else:
            readme_text = readme_path.read_text(encoding="utf-8", errors="replace")
            if readme_text != expected_readme_text:
                errors.append("export pack README content does not match export metadata")

    report = {
        "valid": not errors,
        "input": str(pack_dir),
        "export_count": len(exports),
        "helper_snippets_verified": verified_helpers,
        "acceptance_manifest": str(acceptance_path),
        "errors": errors,
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"export pack verification: {'PASS' if report['valid'] else 'FAIL'}")
        print(f"exports={report['export_count']} helpers_verified={report['helper_snippets_verified']}")
        for error in errors:
            print(f"  - {error}")
    return 0 if report["valid"] else 1


def cmd_verify_external_runner_pack(args: argparse.Namespace) -> int:
    report = external_runner_pack_verification(Path(args.input))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["valid"] else 1

    if report["valid"]:
        print(f"external runner pack verification: PASS")
        print(f"input: {report['input']}")
        print(f"records={report['record_count']}")
        return 0

    print("external runner pack verification: FAIL")
    print(f"input: {report['input']}")
    errors = report["errors"]
    assert isinstance(errors, list)
    for error in errors:
        print(f"  - {error}")
    return 1


def run_cli_json_command(func: object, args: argparse.Namespace) -> tuple[int, dict[str, object] | None, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rc = func(args)  # type: ignore[misc]
    output = buffer.getvalue()
    try:
        payload = json.loads(output) if output.strip() else None
    except json.JSONDecodeError:
        payload = None
    return int(rc), payload if isinstance(payload, dict) else None, output


def readiness_artifacts_readme(report: dict[str, object]) -> str:
    export_verify = report.get("export_pack", {})
    external_verify = report.get("external_runner_pack", {})
    fixture_directories = report.get("fixture_directories", [])
    export_count = "unknown"
    external_count = "unknown"
    if isinstance(export_verify, dict):
        verify = export_verify.get("verify")
        if isinstance(verify, dict):
            export_count = str(verify.get("export_count", "unknown"))
    if isinstance(external_verify, dict):
        verify = external_verify.get("verify")
        if isinstance(verify, dict):
            external_count = str(verify.get("record_count", "unknown"))
    verify_export_command = shell_command_string(
        [
            "PYTHONPATH=ports/python",
            "python3",
            "-m",
            "lorentzian_classification",
            "verify-export-pack",
            str(report["export_pack"]["output_dir"]),
        ]
    )
    verify_external_command = shell_command_string(
        [
            "PYTHONPATH=ports/python",
            "python3",
            "-m",
            "lorentzian_classification",
            "verify-external-runner-pack",
            str(report["external_runner_pack"]["output_dir"]),
        ]
    )
    target_fixture_dir = (
        str(fixture_directories[0])
        if isinstance(fixture_directories, list) and fixture_directories
        else "/path/to/external"
    )
    pine_export_source_dir = str(report.get("pine_export_source_dir", DEFAULT_PINE_EXPORT_SOURCE_DIR))
    import_pine_exports_command = shell_command_string(
        [
            "PYTHONPATH=ports/python",
            "python3",
            "-m",
            "lorentzian_classification",
            "import-pine-exports",
            pine_export_source_dir,
            "--fixture-dir",
            target_fixture_dir,
            "--manifest",
            str(report["manifest"]),
        ]
    )
    readiness_command = shell_command_string(
        [
            "PYTHONPATH=ports/python",
            "python3",
            "-m",
            "lorentzian_classification",
            "readiness",
            "--fixture-dir",
            target_fixture_dir,
            "--manifest",
            str(report["manifest"]),
        ]
    )
    return "\n".join(
        [
            "# Lorentzian Readiness Artifacts",
            "",
            f"Manifest: `{report['manifest']}`",
            f"Valid: `{report['valid']}`",
            "",
            "## Artifact Packs",
            "",
            f"- Pine export pack: `{report['export_pack']['output_dir']}`",
            f"- Pine export cases: `{export_count}`",
            f"- external runner pack: `{report['external_runner_pack']['output_dir']}`",
            f"- external runner cases: `{external_count}`",
            "",
            "## Verification",
            "",
            "Run these before using the artifacts:",
            "",
            "```bash",
            verify_export_command,
            verify_external_command,
            "```",
            "",
            "The Pine export pack contains helper snippets for TradingView exports that are still required.",
            "The external runner pack contains `.set` presets and `[StartUp]` configs for refreshing stale or missing external parity reports.",
            "",
            "## Import Downloaded Pine Exports",
            "",
            "After exporting the CSV files from TradingView, validate and copy them into the fixture directory:",
            "",
            "```bash",
            import_pine_exports_command,
            readiness_command,
            "```",
            "",
        ]
    )


def readiness_artifacts_verification(input_dir: Path) -> dict[str, object]:
    errors: list[str] = []
    if not input_dir.exists():
        return {"input": str(input_dir), "valid": False, "errors": [f"input directory not found: {input_dir}"]}
    if not input_dir.is_dir():
        return {"input": str(input_dir), "valid": False, "errors": [f"readiness artifacts input is not a directory: {input_dir}"]}

    manifest_path = input_dir / "readiness_artifacts.json"
    readme_path = input_dir / "README.md"
    if not manifest_path.exists():
        return {"input": str(input_dir), "valid": False, "errors": [f"readiness artifact manifest not found: {manifest_path}"]}
    if not manifest_path.is_file():
        return {"input": str(input_dir), "valid": False, "errors": [f"readiness artifact manifest is not a file: {manifest_path}"]}

    try:
        report = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"input": str(input_dir), "valid": False, "errors": [f"invalid readiness artifact manifest: {exc}"]}
    if not isinstance(report, dict):
        return {"input": str(input_dir), "valid": False, "errors": ["invalid readiness artifact manifest: expected object"]}

    if report.get("output_dir") != str(input_dir):
        errors.append(f"output_dir mismatch: expected {input_dir}, got {report.get('output_dir')}")
    if report.get("readiness_manifest") != str(manifest_path):
        errors.append("readiness_manifest path does not match input directory")
    if report.get("readme") != str(readme_path):
        errors.append("readme path does not match input directory")

    export_pack = report.get("export_pack")
    external_pack = report.get("external_runner_pack")
    if not isinstance(export_pack, dict) or not isinstance(export_pack.get("output_dir"), str):
        errors.append("missing export_pack output_dir")
        export_verify_report: dict[str, object] | None = None
    else:
        export_dir = Path(str(export_pack["output_dir"]))
        if not is_path_within(export_dir, input_dir):
            errors.append(f"export_pack output_dir escapes readiness artifacts: {export_dir}")
        export_rc, export_verify_report, export_stdout = run_cli_json_command(
            cmd_verify_export_pack,
            argparse.Namespace(input=str(export_dir), json=True),
        )
        if export_rc != 0:
            errors.append(f"export_pack verification failed: {export_stdout.strip()}")
        if export_verify_report != export_pack.get("verify"):
            errors.append("export_pack verify payload is stale or mismatched")

    if not isinstance(external_pack, dict) or not isinstance(external_pack.get("output_dir"), str):
        errors.append("missing external_runner_pack output_dir")
        external_verify_report: dict[str, object] | None = None
    else:
        external_dir = Path(str(external_pack["output_dir"]))
        if not is_path_within(external_dir, input_dir):
            errors.append(f"external_runner_pack output_dir escapes readiness artifacts: {external_dir}")
        external_rc, external_verify_report, external_stdout = run_cli_json_command(
            cmd_verify_external_runner_pack,
            argparse.Namespace(input=str(external_dir), json=True),
        )
        if external_rc != 0:
            errors.append(f"external_runner_pack verification failed: {external_stdout.strip()}")
        if external_verify_report != external_pack.get("verify"):
            errors.append("external_runner_pack verify payload is stale or mismatched")

    if not readme_path.exists():
        errors.append(f"readiness artifact README not found: {readme_path}")
    elif not readme_path.is_file():
        errors.append(f"readiness artifact README is not a file: {readme_path}")
    else:
        expected_readme = readiness_artifacts_readme(report)
        actual_readme = readme_path.read_text(encoding="utf-8", errors="replace")
        if actual_readme != expected_readme:
            errors.append("readiness artifact README content does not match manifest")

    return {
        "input": str(input_dir),
        "valid": not errors,
        "readiness_manifest": str(manifest_path),
        "readme": str(readme_path),
        "export_pack": export_verify_report,
        "external_runner_pack": external_verify_report,
        "errors": errors,
    }


def cmd_prepare_readiness_artifacts(args: argparse.Namespace) -> int:
    fixture_dirs = fixture_search_dirs_from_arg(args.fixture_dir)
    existing_fixture_dirs, fixture_error = fixture_directory_status(fixture_dirs)
    if fixture_error:
        print(fixture_error)
        return 1
    manifest_path = manifest_path_from_arg(args.manifest)
    if not manifest_path:
        print("prepare-readiness-artifacts requires a parity manifest")
        return 1
    try:
        load_manifest_payload(manifest_path)
    except ValueError as exc:
        print(f"invalid parity manifest: {exc}")
        return 1

    output_dir = Path(args.output)
    output_error = output_directory_error(output_dir)
    if output_error:
        print(f"invalid readiness artifact output directory: {output_error}")
        return 1
    export_output = output_dir / "pine-export-pack"
    external_output = output_dir / "external-runner-pack"
    fixture_dir_arg = preferred_fixture_dir_arg(existing_fixture_dirs)

    export_rc, export_payload, export_stdout = run_cli_json_command(
        cmd_export_pack,
        argparse.Namespace(
            fixture_dir=fixture_dir_arg,
            manifest=str(manifest_path),
            case=None,
            include_present=False,
            clean_stale=args.clean_stale,
            allow_stale=args.allow_stale,
            output=str(export_output),
            json=True,
        ),
    )
    verify_export_rc, verify_export_payload, verify_export_stdout = run_cli_json_command(
        cmd_verify_export_pack,
        argparse.Namespace(input=str(export_output), json=True),
    )
    external_rc, external_payload, external_stdout = run_cli_json_command(
        cmd_external_runner_pack,
        argparse.Namespace(
            fixture_dir=fixture_dir_arg,
            manifest=str(manifest_path),
            case=None,
            only_failing=True,
            symbol=args.symbol,
            period=args.period,
            tolerance=args.tolerance,
            output=str(external_output),
            json=True,
        ),
    )
    verify_external_rc, verify_external_payload, verify_external_stdout = run_cli_json_command(
        cmd_verify_external_runner_pack,
        argparse.Namespace(input=str(external_output), json=True),
    )

    report = {
        "valid": all(rc == 0 for rc in [export_rc, verify_export_rc, external_rc, verify_external_rc]),
        "manifest": str(manifest_path),
        "fixture_directories": [str(path) for path in existing_fixture_dirs],
        "pine_export_source_dir": args.pine_export_source_dir,
        "output_dir": str(output_dir),
        "readiness_manifest": str(output_dir / "readiness_artifacts.json"),
        "readme": str(output_dir / "README.md"),
        "export_pack": {
            "output_dir": str(export_output),
            "generate_returncode": export_rc,
            "verify_returncode": verify_export_rc,
            "generate": export_payload,
            "verify": verify_export_payload,
            "stdout": None if export_payload is not None else export_stdout,
            "verify_stdout": None if verify_export_payload is not None else verify_export_stdout,
        },
        "external_runner_pack": {
            "output_dir": str(external_output),
            "generate_returncode": external_rc,
            "verify_returncode": verify_external_rc,
            "generate": external_payload,
            "verify": verify_external_payload,
            "stdout": None if external_payload is not None else external_stdout,
            "verify_stdout": None if verify_external_payload is not None else verify_external_stdout,
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "readiness_artifacts.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (output_dir / "README.md").write_text(readiness_artifacts_readme(report))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["valid"] else 1

    print("readiness artifacts: PASS" if report["valid"] else "readiness artifacts: FAIL")
    print(f"output directory: {output_dir}")
    print(f"Pine export pack: {export_output} (generate={export_rc}, verify={verify_export_rc})")
    print(f"external runner pack: {external_output} (generate={external_rc}, verify={verify_external_rc})")
    return 0 if report["valid"] else 1


def cmd_verify_readiness_artifacts(args: argparse.Namespace) -> int:
    report = readiness_artifacts_verification(Path(args.input))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["valid"] else 1

    print("readiness artifacts verification: PASS" if report["valid"] else "readiness artifacts verification: FAIL")
    print(f"input: {report['input']}")
    errors = report.get("errors", [])
    if isinstance(errors, list):
        for error in errors:
            print(f"  - {error}")
    return 0 if report["valid"] else 1


def classify_csv_candidate(path: Path) -> str:
    if not path.exists():
        return "missing_csv"
    if not path.is_file():
        return "not_a_file"
    fields = csv_header_fields(path)
    if not fields:
        return "empty_csv"

    lowered = [field.lower() for field in fields]
    field_set = set(lowered)
    has_ohlc = {"time", "open", "high", "low", "close"}.issubset(field_set)
    has_pine_outputs = any(
        field in fields
        for field in [
            "Kernel Regression Estimate",
            "Prediction",
            "Direction",
            "Buy",
            "Sell",
            "StopBuy",
            "StopSell",
        ]
    )
    if has_ohlc and has_pine_outputs:
        return "tradingview_pine_export_candidate"
    if any(field.startswith("tv_") for field in lowered) and any(field.startswith("external_") for field in lowered):
        return "external_parity_comparison"
    if any(field.endswith("_diff") for field in lowered) or {"pred_match", "dir_match"}.intersection(field_set):
        return "external_parity_comparison"
    if {"status", "symbol", "timeframe", "bars"}.issubset(field_set):
        return "indicator_smoke_report"
    if has_ohlc and {"kernel", "direction", "prediction"}.issubset(field_set):
        return "external_runtime_generated_indicator_export"
    if len(fields) >= 5 and "." in fields[0] and ":" in fields[1]:
        return "tick_import_data"
    if has_ohlc:
        return "ohlc_price_csv"
    return "unknown_csv"


def cmd_audit_fixtures(args: argparse.Namespace) -> int:
    fixture_dirs = fixture_search_dirs_from_arg(args.fixture_dir)
    existing_fixture_dirs, fixture_error = fixture_directory_status(fixture_dirs)
    if fixture_error:
        print(fixture_error)
        return 1

    manifest_path = manifest_path_from_arg(args.manifest)
    if manifest_path:
        try:
            payload = load_manifest_payload(manifest_path)
            tracked_specs = load_parity_manifest(manifest_path)
            required_specs, labels_without_fixture = load_required_fixture_cases(payload)
            pine_source_specs = load_pine_source_specs(payload)
            external_source_specs = load_external_source_specs(payload)
            external_parity_report_specs = load_external_parity_report_specs(payload)
        except ValueError as exc:
            print(f"invalid parity manifest: {exc}")
            return 1
    else:
        tracked_specs = builtin_parity_fixtures()
        required_specs = []
        labels_without_fixture = []
        pine_source_specs = []
        external_source_specs = []
        external_parity_report_specs = []
        payload = {}

    tracked_filenames = {spec.filename for spec in tracked_specs}
    required_filenames = {spec.filename for spec in required_specs}
    external_report_filenames = {spec.filename for spec in external_parity_report_specs}
    ignored_rows = ignored_csv_candidate_rows(payload, existing_fixture_dirs) if manifest_path else []
    ignored_filenames = {str(row["filename"]) for row in ignored_rows}
    manifest_filenames = tracked_filenames | required_filenames | ignored_filenames | external_report_filenames
    pine_source_rows = pine_source_records(existing_fixture_dirs, pine_source_specs)
    external_source_rows = source_records(existing_fixture_dirs, external_source_specs)
    external_source_sanity_rows = external_source_sanity_records(external_source_rows)
    external_compiled_artifact_rows = external_compiled_artifact_records(external_source_rows)
    external_parity_script_artifact_rows = external_parity_script_artifact_records(
        existing_fixture_dirs, external_parity_report_specs
    )
    external_parity_report_rows = external_parity_report_records(
        existing_fixture_dirs,
        external_parity_report_specs,
        external_compiled_artifact_rows,
        1e-9,
        external_parity_script_artifact_rows,
    )

    tracked_rows = []
    for spec in tracked_specs:
        path = fixture_path_for(existing_fixture_dirs, spec.filename)
        tracked_rows.append(
            {
                "name": spec.name,
                "filename": spec.filename,
                "present": path is not None,
                "path": str(path) if path else None,
            }
        )

    required_rows = []
    for spec in required_specs:
        path = fixture_path_for(existing_fixture_dirs, spec.filename)
        required_rows.append(
            {
                "name": spec.name,
                "filename": spec.filename,
                "present": path is not None,
                "path": str(path) if path else None,
            }
        )
    for label in labels_without_fixture:
        required_rows.append({"name": label, "filename": None, "present": False, "path": None})

    untracked_rows = untracked_csv_candidate_rows(existing_fixture_dirs, manifest_filenames)
    unexpected_pine_rows = [
        row for row in untracked_rows if row["classification"] == "tradingview_pine_export_candidate"
    ]

    pine_sources: list[Path] = []
    for source_root in pine_source_roots(existing_fixture_dirs):
        pine_sources.extend(sorted(source_root.rglob("*.pine")))

    audit_payload = {
        "fixture_directories": [str(path) for path in existing_fixture_dirs],
        "manifest": str(manifest_path) if manifest_path else None,
        "manifest_pine_sources": pine_source_rows,
        "manifest_external_sources": external_source_rows,
        "external_source_sanity": external_source_sanity_rows,
        "external_compiled_artifacts": external_compiled_artifact_rows,
        "external_parity_script_artifacts": external_parity_script_artifact_rows,
        "external_parity_reports": external_parity_report_rows,
        "tracked_fixtures": tracked_rows,
        "required_uncovered_fixtures": required_rows,
        "ignored_csv_candidates": ignored_rows,
        "untracked_csv_candidates": untracked_rows,
        "unexpected_pine_export_candidates": unexpected_pine_rows,
        "pine_sources": [str(path) for path in pine_sources],
        "summary": {
            "tracked_present": sum(1 for row in tracked_rows if row["present"]),
            "tracked_missing": sum(1 for row in tracked_rows if not row["present"]),
            "required_present": sum(1 for row in required_rows if row["present"]),
            "required_missing": sum(1 for row in required_rows if not row["present"]),
            "ignored_csv_candidates": len(ignored_rows),
            "untracked_csv_candidates": len(untracked_rows),
            "unexpected_pine_export_candidates": len(unexpected_pine_rows),
            "untracked_pine_export_candidates": sum(
                1 for row in untracked_rows if row["classification"] == "tradingview_pine_export_candidate"
            ),
            "pine_sources": len(pine_sources),
            "external_sources_present": sum(1 for row in external_source_rows if row["present"]),
            "external_sources_invalid": sum(1 for row in external_source_rows if not row["valid"]),
            "external_source_sanity_failed": sum(1 for row in external_source_sanity_rows if not row["passed"]),
            "external_source_sanity_total": len(external_source_sanity_rows),
            "external_compiled_artifacts_failed": sum(1 for row in external_compiled_artifact_rows if not row["passed"]),
            "external_compiled_artifacts_total": len(external_compiled_artifact_rows),
            "external_parity_script_artifacts_failed": sum(
                1 for row in external_parity_script_artifact_rows if not row["passed"]
            ),
            "external_parity_script_artifacts_total": len(external_parity_script_artifact_rows),
            "external_parity_reports_total": len(external_parity_report_rows),
            "external_parity_reports_required": sum(1 for row in external_parity_report_rows if row["required"]),
            "external_parity_reports_failed": sum(1 for row in external_parity_report_rows if not row["passed"]),
            "external_parity_reports_required_failed": sum(
                1 for row in external_parity_report_rows if row["required"] and not row["passed"]
            ),
            "external_parity_reports_stale": sum(1 for row in external_parity_report_rows if row["stale"]),
        },
    }

    if args.json:
        print(json.dumps(audit_payload, indent=2, sort_keys=True))
        return 0

    print("fixture directories:")
    for fixture_dir in audit_payload["fixture_directories"]:
        print(f"  - {fixture_dir}")
    if manifest_path:
        print(f"manifest: {manifest_path}")

    print("manifest pine sources:")
    if not pine_source_rows:
        print("  none")
    for row in pine_source_rows:
        if not row["present"]:
            print(f"  MISSING {row['name']} ({row['role']}) {row['path']}")
            continue
        debug_status = "debug_markers=yes" if row["debug_markers"] else "debug_markers=no"
        if row.get("expected_sha256"):
            hash_status = "sha256_match=yes" if row["sha256_matches"] else "sha256_match=no"
        else:
            hash_status = "sha256_unpinned"
        code_hash_status = ""
        if row.get("expected_code_sha256"):
            code_hash_status = (
                " code_sha256_match=yes" if row["code_sha256_matches"] else " code_sha256_match=no"
            )
        print(
            f"  PRESENT {row['name']} ({row['role']}) {row['path']} "
            f"sha256={row['sha256']} {hash_status}{code_hash_status} {debug_status}"
        )

    print("manifest external sources:")
    if not external_source_rows:
        print("  none")
    for row in external_source_rows:
        if not row["present"]:
            print(f"  MISSING {row['name']} ({row['role']}) {row['path']}")
            continue
        debug_status = "debug_markers=yes" if row["debug_markers"] else "debug_markers=no"
        if row.get("expected_sha256"):
            hash_status = "sha256_match=yes" if row["sha256_matches"] else "sha256_match=no"
        else:
            hash_status = "sha256_unpinned"
        code_hash_status = ""
        if row.get("expected_code_sha256"):
            code_hash_status = (
                " code_sha256_match=yes" if row["code_sha256_matches"] else " code_sha256_match=no"
            )
        print(
            f"  PRESENT {row['name']} ({row['role']}) {row['path']} "
            f"sha256={row['sha256']} {hash_status}{code_hash_status} {debug_status}"
        )
    failed_sanity = [row for row in external_source_sanity_rows if not row["passed"]]
    if failed_sanity:
        print("external source sanity failures:")
        for row in failed_sanity:
            print(f"  - {row['source']}:{row['check']}: {row['detail']}")
    failed_compiled = [row for row in external_compiled_artifact_rows if not row["passed"]]
    if external_compiled_artifact_rows:
        print("external compiled artifacts:")
        for row in external_compiled_artifact_rows:
            status = "PASS" if row["passed"] else "FAIL"
            print(f"  {status} {row['source']} -> {row['artifact_path']} ({row['detail']})")
    if failed_compiled:
        print("external compiled artifact freshness failures:")
        for row in failed_compiled:
            print(f"  - {row['source']}: {row['detail']}")
    failed_parity_scripts = [row for row in external_parity_script_artifact_rows if not row["passed"]]
    if external_parity_script_artifact_rows:
        print("external parity script artifacts:")
        for row in external_parity_script_artifact_rows:
            status = "PASS" if row["passed"] else "FAIL"
            print(f"  {status} {row['script_path']} -> {row['artifact_path']} ({row['detail']})")
    if failed_parity_scripts:
        print("external parity script artifact freshness failures:")
        for row in failed_parity_scripts:
            print(f"  - {row['script_path']}: {row['detail']}")

    print("external parity reports:")
    if not external_parity_report_rows:
        print("  none")
    for row in external_parity_report_rows:
        status = "PASS" if row["passed"] else "FAIL"
        required = "required" if row["required"] else "optional"
        suffix = f" -> {row['path']}" if row["path"] else ""
        print(f"  {status} {row['name']} ({required}) {row['filename']}{suffix}")
        if row.get("error"):
            print(f"    error: {row['error']}")
        print(
            f"    inputs: InpInputFile={row['input_filename']} "
            f"InpOutputFile={row['filename']} "
            f"InpIncludeFullHist={row['include_full_history']}"
        )
        print(
            f"    rows={row['rows']} pred_mismatches={row['prediction_mismatches']} "
            f"dir_mismatches={row['direction_mismatches']} "
            f"buy_mismatches={row['buy_mismatches']} sell_mismatches={row['sell_mismatches']}"
        )

    print("tracked fixtures:")
    for row in tracked_rows:
        status = "PRESENT" if row["present"] else "MISSING"
        suffix = f" -> {row['path']}" if row["path"] else ""
        print(f"  {status} {row['name']} ({row['filename']}){suffix}")

    print("required uncovered fixtures:")
    if not required_rows:
        print("  none")
    for row in required_rows:
        if row["filename"] is None:
            print(f"  PLANNED {row['name']}")
            continue
        status = "PRESENT" if row["present"] else "MISSING"
        suffix = f" -> {row['path']}" if row["path"] else ""
        print(f"  {status} {row['name']} ({row['filename']}){suffix}")

    print("ignored csv candidates:")
    if not ignored_rows:
        print("  none")
    for row in ignored_rows:
        status = "PRESENT" if row["present"] else "MISSING"
        suffix = f" -> {row['path']}" if row["path"] else ""
        print(f"  {status} {row['filename']} [{row['classification']}]: {row['reason']}{suffix}")

    print("untracked csv candidates:")
    if not untracked_rows:
        print("  none")
    for row in untracked_rows:
        print(f"  - {row['path']} [{row['classification']}]")

    print("pine sources:")
    if not pine_sources:
        print("  none")
    for path in pine_sources:
        print(f"  - {path}")

    return 0


def cmd_external_report_checklist(args: argparse.Namespace) -> int:
    fixture_dirs = fixture_search_dirs_from_arg(args.fixture_dir)
    existing_fixture_dirs, fixture_error = fixture_directory_status(fixture_dirs)
    if fixture_error:
        print(fixture_error)
        return 1

    manifest_path = manifest_path_from_arg(args.manifest)
    if not manifest_path:
        print("external report checklist requires a parity manifest")
        return 1

    try:
        payload = load_manifest_payload(manifest_path)
        rows, compiled_rows, script_artifact_rows = external_report_checklist(
            payload, existing_fixture_dirs, args.tolerance
        )
        rows = filter_external_parity_report_records(rows, args.case)
    except ValueError as exc:
        print(f"invalid parity manifest: {exc}")
        return 1

    checklist = {
        "fixture_directories": [str(path) for path in existing_fixture_dirs],
        "manifest": str(manifest_path),
        "external_compiled_artifacts": compiled_rows,
        "external_parity_script_artifacts": script_artifact_rows,
        "reports": rows,
        "summary": {
            "total": len(rows),
            "required": sum(1 for row in rows if row["required"]),
            "present": sum(1 for row in rows if row["present"]),
            "passed": sum(1 for row in rows if row["passed"]),
            "failed": sum(1 for row in rows if not row["passed"]),
            "stale": sum(1 for row in rows if row["stale"]),
            "script_artifacts_failed": sum(1 for row in script_artifact_rows if not row["passed"]),
            "script_artifacts_total": len(script_artifact_rows),
        },
    }
    if args.json:
        print(json.dumps(checklist, indent=2, sort_keys=True))
        return 0

    print("external parity report checklist:")
    print(f"manifest: {manifest_path}")
    for fixture_dir in existing_fixture_dirs:
        print(f"fixture directory: {fixture_dir}")
    if compiled_rows:
        print("compiled artifacts:")
        for row in compiled_rows:
            status = "PASS" if row["passed"] else "FAIL"
            print(f"  {status} {row['source']}: {row['detail']}")
    else:
        print("compiled artifacts: none tracked")
    if script_artifact_rows:
        print("parity script artifacts:")
        for row in script_artifact_rows:
            status = "PASS" if row["passed"] else "FAIL"
            print(f"  {status} {row['script_path']}: {row['detail']}")
    else:
        print("parity script artifacts: none tracked")
    for row in rows:
        if row["passed"]:
            status = "PASS"
        elif not row["present"]:
            status = "MISSING"
        elif row["stale"]:
            status = "STALE"
        else:
            status = "FAIL"
        print(f"{status} {row['name']}")
        print(f"  report: {row['filename']}")
        print(f"  input: {row['input_filename']}")
        print(f"  include full history: {row['include_full_history']}")
        if row["path"]:
            print(f"  path: {row['path']}")
        if row["error"]:
            print(f"  error: {row['error']}")
        action = row["regeneration_action"]
        assert isinstance(action, dict)
        compile_command = action["compile_command"]
        script_inputs = action["script_inputs"]
        assert isinstance(compile_command, list)
        assert isinstance(script_inputs, dict)
        print(f"  compile: {' '.join(str(item) for item in compile_command)}")
        print(
            "  run inputs: "
            f"InpInputFile={script_inputs['InpInputFile']} "
            f"InpOutputFile={script_inputs['InpOutputFile']} "
            f"InpIncludeFullHist={script_inputs['InpIncludeFullHist']}"
        )
        print(f"  manual run: {action['manual_run']}")
    return 0


def cmd_external_runner_pack(args: argparse.Namespace) -> int:
    fixture_dirs = fixture_search_dirs_from_arg(args.fixture_dir)
    existing_fixture_dirs, fixture_error = fixture_directory_status(fixture_dirs)
    if fixture_error:
        print(fixture_error)
        return 1

    manifest_path = manifest_path_from_arg(args.manifest)
    if not manifest_path:
        print("external runner pack requires a parity manifest")
        return 1

    output_dir = Path(args.output)
    try:
        payload = load_manifest_payload(manifest_path)
        records = external_runner_pack_records(
            payload,
            existing_fixture_dirs,
            output_dir,
            args.case,
            args.tolerance,
            args.only_failing,
            args.symbol,
            args.period,
        )
        write_external_runner_pack(records, output_dir)
    except ValueError as exc:
        print(f"invalid parity manifest: {exc}")
        return 1

    manifest = {
        "manifest": str(manifest_path),
        "fixture_directories": [str(path) for path in existing_fixture_dirs],
        "output_dir": str(output_dir),
        "only_failing": args.only_failing,
        "record_count": len(records),
        "records": [
            {key: value for key, value in record.items() if key not in {"preset_text", "startup_config_text"}}
            for record in records
        ],
    }
    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0

    print("external runner pack:")
    print(f"manifest: {manifest_path}")
    print(f"output directory: {output_dir}")
    if not records:
        print("reports: none")
    for record in records:
        print(f"{record['name']}")
        print(f"  preset: {record['preset_path']}")
        print(f"  startup config: {record['startup_config_path']}")
        print(f"  report: {record['report_filename']}")
        print(f"  input: {record['input_filename']}")
        if record.get("error"):
            print(f"  current error: {record['error']}")
        print(f"  command: {' '.join(str(item) for item in record['terminal_command'])}")
    return 0


def cmd_export_checklist(args: argparse.Namespace) -> int:
    fixture_dirs = fixture_search_dirs_from_arg(args.fixture_dir)
    existing_fixture_dirs, fixture_error = fixture_directory_status(fixture_dirs)
    if fixture_error:
        print(fixture_error)
        return 1

    manifest_path = manifest_path_from_arg(args.manifest)
    if not manifest_path:
        print("export checklist requires a parity manifest")
        return 1
    try:
        payload = load_manifest_payload(manifest_path)
        rows = export_checklist_rows(payload, existing_fixture_dirs, manifest_path)
        rows = filter_export_checklist_rows(rows, args.case)
    except ValueError as exc:
        print(f"invalid parity manifest: {exc}")
        return 1

    checklist = {
        "fixture_directories": [str(path) for path in existing_fixture_dirs],
        "manifest": str(manifest_path),
        "export_workflow": export_pack_workflow_commands(manifest_path, existing_fixture_dirs),
        "exports": rows,
        "summary": {
            "total": len(rows),
            "present": sum(1 for row in rows if row["present"]),
            "missing": sum(1 for row in rows if not row["present"]),
        },
    }
    if args.json:
        print(json.dumps(checklist, indent=2, sort_keys=True))
        return 0

    print("required Pine export checklist:")
    print(f"manifest: {manifest_path}")
    for fixture_dir in existing_fixture_dirs:
        print(f"fixture directory: {fixture_dir}")
    workflow = checklist["export_workflow"]
    assert isinstance(workflow, dict)
    print(f"export pack command: {' '.join(str(item) for item in workflow['export_pack_command'])}")
    print(f"verify export pack command: {' '.join(str(item) for item in workflow['verify_export_pack_command'])}")
    for row in rows:
        status = "PRESENT" if row["present"] else "MISSING"
        print(f"{status} {row['name']}")
        print(f"  filename: {row['filename']}")
        if row["path"]:
            print(f"  path: {row['path']}")
        if row.get("python_smoke_fixture"):
            smoke_status = "present" if row.get("python_smoke_fixture_present") else "missing"
            print(f"  Python settings smoke fixture: {row['python_smoke_fixture']} ({smoke_status})")
        proves = row["proves"]
        if proves:
            assert isinstance(proves, list)
            print(f"  proves: {', '.join(str(item) for item in proves)}")
        settings = row["settings"]
        if settings:
            assert isinstance(settings, dict)
            print("  Pine settings:")
            for key, value in settings.items():
                print(f"    {key}: {value}")
        tradingview_settings = row["tradingview_settings"]
        if tradingview_settings:
            assert isinstance(tradingview_settings, list)
            print("  TradingView inputs:")
            for item in tradingview_settings:
                assert isinstance(item, dict)
                group = f" [{item['group']}]" if item.get("group") else ""
                variable = f" ({item['variable']})" if item.get("variable") else ""
                inline = f" inline={item['inline']}" if item.get("inline") else ""
                print(f"    {item['title']}{group}{variable}{inline}: {item['value']}")
        cli_flags = row["cli_flags"]
        if cli_flags:
            assert isinstance(cli_flags, list)
            print(f"  equivalent CLI flags: {' '.join(str(item) for item in cli_flags)}")
        helper_command = row["pine_export_helper_command"]
        full_helper_command = row["pine_export_helper_command_full"]
        assert isinstance(helper_command, list)
        assert isinstance(full_helper_command, list)
        print(f"  Pine export helper command: {' '.join(str(item) for item in helper_command)}")
        print(f"  full helper command: {' '.join(str(item) for item in full_helper_command)}")
        minimum_columns = row["minimum_export_columns"]
        full_columns = row["full_instrumented_export_columns"]
        assert isinstance(minimum_columns, list)
        assert isinstance(full_columns, list)
        print(f"  minimum export columns: {', '.join(str(column) for column in minimum_columns)}")
        print(f"  full instrumented export columns: {', '.join(str(column) for column in full_columns)}")
        if row.get("settings_fingerprint") is not None:
            print(f"  settings fingerprint: {row['settings_fingerprint']} ({row['settings_fingerprint_column']})")
        print("  Pine export series:")
        pine_series = row["pine_export_series"]
        assert isinstance(pine_series, list)
        for item in pine_series:
            assert isinstance(item, dict)
            mode = item["export_mode"]
            print(f"    {item['column']} [{mode}]: {item['pine_expression']}")
            if item.get("note"):
                print(f"      note: {item['note']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lorentzian-classification")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="calculate Lorentzian Classification outputs for a CSV")
    run.add_argument("input")
    run.add_argument("-o", "--output", required=True)
    add_settings_args(run)
    run.set_defaults(func=cmd_run)

    parity = sub.add_parser("parity", help="compare Python outputs with TradingView/Pine export columns")
    parity.add_argument("input")
    parity.add_argument("-o", "--output")
    parity.add_argument("--tolerance", type=float, default=1e-9)
    add_settings_args(parity)
    parity.set_defaults(func=cmd_parity)

    validate = sub.add_parser("validate-fixtures", help="run the known external_runtime/Pine export parity suite")
    validate.add_argument(
        "--fixture-dir",
        default=None,
        help="directory containing TradingView/Pine export CSVs; defaults to local external_runtime Files directory",
    )
    validate.add_argument(
        "--manifest",
        help="JSON manifest mapping Pine export filenames to the settings used when exported",
    )
    validate.add_argument(
        "--require-full-coverage",
        action="store_true",
        help="fail if the manifest still lists required uncovered Pine export cases",
    )
    validate.add_argument("--tolerance", type=float, default=1e-9)
    validate.add_argument("--output-mismatches", help="optional directory for mismatch CSVs")
    validate.add_argument(
        "--pine-export-source-dir",
        default=DEFAULT_PINE_EXPORT_SOURCE_DIR,
        help="directory to use in missing Pine export import workflow commands",
    )
    validate.add_argument("--json", action="store_true", help="emit validation results as machine-readable JSON")
    validate.set_defaults(func=cmd_validate_fixtures)

    audit = sub.add_parser("audit-fixtures", help="inventory fixture CSVs and Pine sources used for parity")
    audit.add_argument(
        "--fixture-dir",
        default=None,
        help="external workspace root or directory containing TradingView/Pine export CSVs",
    )
    audit.add_argument(
        "--manifest",
        help="JSON manifest mapping Pine export filenames to the settings used when exported",
    )
    audit.add_argument("--json", action="store_true", help="emit the audit as machine-readable JSON")
    audit.set_defaults(func=cmd_audit_fixtures)

    pine_contract = sub.add_parser(
        "pine-input-contract",
        help="verify that Python CLI settings still match the pinned lcv6 Pine inputs",
    )
    pine_contract.add_argument(
        "--fixture-dir",
        default=None,
        help="external workspace root containing manifest-pinned Pine sources",
    )
    pine_contract.add_argument(
        "--manifest",
        help="JSON manifest with the canonical Pine source pin",
    )
    pine_contract.add_argument("--json", action="store_true", help="emit the contract report as machine-readable JSON")
    pine_contract.set_defaults(func=cmd_pine_input_contract)

    pine_output_contract = sub.add_parser(
        "pine-output-contract",
        help="verify that Python CLI outputs still cover pinned lcv6 Pine plots, alerts, labels, colors, and table fields",
    )
    pine_output_contract.add_argument(
        "--fixture-dir",
        default=None,
        help="external workspace root containing manifest-pinned Pine sources",
    )
    pine_output_contract.add_argument(
        "--manifest",
        help="JSON manifest with the canonical Pine source pin",
    )
    pine_output_contract.add_argument("--json", action="store_true", help="emit the contract report as machine-readable JSON")
    pine_output_contract.set_defaults(func=cmd_pine_output_contract)

    external_reports = sub.add_parser(
        "external-report-checklist",
        help="print required external parity reports and exact script inputs needed to refresh them",
    )
    external_reports.add_argument(
        "--fixture-dir",
        default=None,
        help="external workspace root or directory containing external parity report CSVs",
    )
    external_reports.add_argument(
        "--manifest",
        help="JSON manifest mapping external parity reports to their source Pine export CSVs",
    )
    external_reports.add_argument(
        "--case",
        action="append",
        help="limit output to a report name, report filename, or input CSV filename; may be repeated",
    )
    external_reports.add_argument("--tolerance", type=float, default=1e-9)
    external_reports.add_argument("--json", action="store_true", help="emit the checklist as machine-readable JSON")
    external_reports.set_defaults(func=cmd_external_report_checklist)

    external_runner = sub.add_parser(
        "external-runner-pack",
        help="write external_runtime preset and startup config files for required external parity report runs",
    )
    external_runner.add_argument(
        "--fixture-dir",
        default=None,
        help="external workspace root or directory containing external parity report CSVs",
    )
    external_runner.add_argument(
        "--manifest",
        help="JSON manifest mapping external parity reports to their source Pine export CSVs",
    )
    external_runner.add_argument(
        "--case",
        action="append",
        help="limit output to a report name, report filename, or input CSV filename; may be repeated",
    )
    external_runner.add_argument(
        "--only-failing",
        action="store_true",
        help="write runner files only for reports that currently fail, are stale, or are missing",
    )
    external_runner.add_argument("--symbol", default="EURUSD", help="chart symbol used by the external_runtime startup config")
    external_runner.add_argument("--period", default="M1", help="chart period used by the external_runtime startup config")
    external_runner.add_argument("--tolerance", type=float, default=1e-9)
    external_runner.add_argument("--output", required=True, help="external root or staging directory where runner files are written")
    external_runner.add_argument("--json", action="store_true", help="emit the generated runner pack as machine-readable JSON")
    external_runner.set_defaults(func=cmd_external_runner_pack)

    verify_external_runner = sub.add_parser(
        "verify-external-runner-pack",
        help="verify external_runtime preset and startup config files generated by external-runner-pack",
    )
    verify_external_runner.add_argument("input", help="directory containing external_runner_pack.json")
    verify_external_runner.add_argument("--json", action="store_true", help="emit verification report as machine-readable JSON")
    verify_external_runner.set_defaults(func=cmd_verify_external_runner_pack)

    prepare = sub.add_parser(
        "prepare-readiness-artifacts",
        help="generate and verify Pine export-pack and external runner-pack artifacts for remaining parity work",
    )
    prepare.add_argument(
        "--fixture-dir",
        default=None,
        help="external workspace root or directory containing parity fixture/report CSVs",
    )
    prepare.add_argument(
        "--manifest",
        help="JSON manifest mapping Pine exports and external reports to parity evidence",
    )
    prepare.add_argument(
        "--output",
        default=DEFAULT_READINESS_ARTIFACTS_OUTPUT,
        help="directory where readiness handoff artifacts are written",
    )
    prepare.add_argument(
        "--clean-stale",
        action="store_true",
        help="remove stale generated Pine helper snippets from the export-pack output",
    )
    prepare.add_argument(
        "--allow-stale",
        action="store_true",
        help="permit unrelated or stale .pine files in the export-pack output directory",
    )
    prepare.add_argument("--symbol", default="EURUSD", help="chart symbol used by generated external_runtime startup configs")
    prepare.add_argument("--period", default="M1", help="chart period used by generated external_runtime startup configs")
    prepare.add_argument("--tolerance", type=float, default=1e-9)
    prepare.add_argument(
        "--pine-export-source-dir",
        default=DEFAULT_PINE_EXPORT_SOURCE_DIR,
        help="directory to use in the generated import-pine-exports README command",
    )
    prepare.add_argument("--json", action="store_true", help="emit the preparation report as machine-readable JSON")
    prepare.set_defaults(func=cmd_prepare_readiness_artifacts)

    verify_readiness_artifacts = sub.add_parser(
        "verify-readiness-artifacts",
        help="verify a complete prepare-readiness-artifacts output directory",
    )
    verify_readiness_artifacts.add_argument("input", help="directory containing readiness_artifacts.json")
    verify_readiness_artifacts.add_argument("--json", action="store_true", help="emit verification report as machine-readable JSON")
    verify_readiness_artifacts.set_defaults(func=cmd_verify_readiness_artifacts)

    checklist = sub.add_parser("export-checklist", help="print required Pine exports still needed for full parity")
    checklist.add_argument(
        "--fixture-dir",
        default=None,
        help="external workspace root or directory containing TradingView/Pine export CSVs",
    )
    checklist.add_argument(
        "--manifest",
        help="JSON manifest mapping Pine export filenames to the settings used when exported",
    )
    checklist.add_argument(
        "--case",
        action="append",
        help="limit output to a required export case name or filename; may be repeated",
    )
    checklist.add_argument("--json", action="store_true", help="emit the export checklist as machine-readable JSON")
    checklist.set_defaults(func=cmd_export_checklist)

    import_exports = sub.add_parser(
        "import-pine-exports",
        help="validate downloaded TradingView CSV exports and copy passing files into the fixture directory",
    )
    import_exports.add_argument(
        "source_dir",
        nargs="+",
        help="one or more directories containing downloaded TradingView CSV exports",
    )
    import_exports.add_argument(
        "--fixture-dir",
        default=None,
        help="external workspace root or target directory where validated Pine export CSVs are copied",
    )
    import_exports.add_argument(
        "--manifest",
        help="JSON manifest mapping required Pine export filenames to parity settings",
    )
    import_exports.add_argument(
        "--case",
        action="append",
        help="limit import to a required export case name or filename; may be repeated",
    )
    import_exports.add_argument("--overwrite", action="store_true", help="replace an existing differing target CSV")
    import_exports.add_argument("--dry-run", action="store_true", help="validate and report copies without writing files")
    import_exports.add_argument("--tolerance", type=float, default=1e-9)
    import_exports.add_argument("--json", action="store_true", help="emit the import report as machine-readable JSON")
    import_exports.set_defaults(func=cmd_import_pine_exports)

    export_pack = sub.add_parser("export-pack", help="write full Pine helper snippets for required export cases")
    export_pack.add_argument(
        "--fixture-dir",
        default=None,
        help="external workspace root or directory containing TradingView/Pine export CSVs",
    )
    export_pack.add_argument(
        "--manifest",
        help="JSON manifest mapping Pine export filenames to the settings used when exported",
    )
    export_pack.add_argument(
        "--case",
        action="append",
        help="limit output to a required export case name or filename; may be repeated",
    )
    export_pack.add_argument(
        "--include-present",
        action="store_true",
        help="also write snippets for required cases whose CSV export is already present",
    )
    export_pack.add_argument(
        "--clean-stale",
        action="store_true",
        help="remove stale .pine files generated by a previous export-pack run",
    )
    export_pack.add_argument(
        "--allow-stale",
        action="store_true",
        help="permit unrelated or stale .pine files in the output directory",
    )
    export_pack.add_argument("--output", required=True, help="directory where helper snippets and index files are written")
    export_pack.add_argument("--json", action="store_true", help="emit the export pack index as machine-readable JSON")
    export_pack.set_defaults(func=cmd_export_pack)

    verify_pack = sub.add_parser("verify-export-pack", help="verify export-pack helper snippets and acceptance manifest")
    verify_pack.add_argument("input", help="directory containing export_pack.json")
    verify_pack.add_argument("--json", action="store_true", help="emit verification report as machine-readable JSON")
    verify_pack.set_defaults(func=cmd_verify_export_pack)

    helper = sub.add_parser("pine-export-helper", help="print Pine plot lines for parity CSV exports")
    helper.add_argument(
        "--manifest",
        help="JSON manifest with required uncovered fixture cases for settings-aware helper output",
    )
    helper.add_argument(
        "--manifest-case",
        help="required uncovered fixture case name or filename whose settings should drive export labels",
    )
    helper.add_argument(
        "--full",
        action="store_true",
        help="include optional alert, display, and trade-stat export fields in addition to the minimum parity fields",
    )
    helper.add_argument("--json", action="store_true", help="emit helper rows and plot lines as machine-readable JSON")
    add_settings_args(helper)
    helper.set_defaults(func=cmd_pine_export_helper)

    readiness = sub.add_parser("readiness", help="report whether the Python port has release-ready Pine parity evidence")
    readiness.add_argument(
        "--fixture-dir",
        default=None,
        help="external workspace root or directory containing TradingView/Pine export CSVs",
    )
    readiness.add_argument(
        "--manifest",
        help="JSON manifest mapping Pine export filenames to the settings used when exported",
    )
    readiness.add_argument("--tolerance", type=float, default=1e-9)
    readiness.add_argument(
        "--pine-export-source-dir",
        default=DEFAULT_PINE_EXPORT_SOURCE_DIR,
        help="directory to use in missing Pine export import workflow commands",
    )
    readiness.add_argument("--json", action="store_true", help="emit readiness report as machine-readable JSON")
    readiness.set_defaults(func=cmd_readiness)

    blockers = sub.add_parser("readiness-blockers", help="summarize only the blockers preventing release readiness")
    blockers.add_argument(
        "--fixture-dir",
        default=None,
        help="external workspace root or directory containing TradingView/Pine export CSVs",
    )
    blockers.add_argument(
        "--manifest",
        help="JSON manifest mapping Pine export filenames to the settings used when exported",
    )
    blockers.add_argument("--tolerance", type=float, default=1e-9)
    blockers.add_argument(
        "--pine-export-source-dir",
        default=DEFAULT_PINE_EXPORT_SOURCE_DIR,
        help="directory to use in missing Pine export import workflow commands",
    )
    blockers.add_argument("--json", action="store_true", help="emit blocker summary as machine-readable JSON")
    blockers.set_defaults(func=cmd_readiness_blockers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
