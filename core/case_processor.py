import os
import pandas as pd

from .pwb_exporter import export_violation_ctg
from .column_blacklist import (
    apply_blacklist,
    apply_row_filter,
    apply_limviolid_max_filter,
    apply_contingency_name_exclusion,
    apply_voltage_resulting_issue_exclusion,
)

REQUIRED_FILTERED_COLUMNS = [
    "CTGLabel",
    "LimViolID",
    "LimViolLimit",
    "LimViolValue",
    "LimViolPct",
    "LimViolCat",
]


def _make_filtered_path(original_csv: str) -> str:
    base, ext = os.path.splitext(original_csv)
    if not ext:
        ext = ".csv"
    return f"{base}_Filtered{ext}"


def _ensure_required_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in REQUIRED_FILTERED_COLUMNS:
        if col not in out.columns:
            out[col] = None
    return out


def _save_filtered_csv(csv_path: str, filtered_data: pd.DataFrame, log_func=None) -> str:
    filtered_csv = _make_filtered_path(csv_path)
    filtered_data = _ensure_required_columns(filtered_data)
    filtered_data.to_csv(filtered_csv, index=False)

    if log_func:
        log_func(f"Filtered CSV saved to:\n  {filtered_csv}")
        if filtered_data.empty:
            log_func("Filtered CSV has no violation rows.")
        else:
            log_func("\nPreview of first few filtered data rows:")
            preview = filtered_data.head(10).to_string(index=False)
            log_func(preview)

    return filtered_csv


def _to_float_series(series: pd.Series) -> pd.Series:
    if series is None:
        return pd.Series(dtype="float64")
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float)
    cleaned = series.astype(str).str.replace("%", "", regex=False).str.strip()
    return pd.to_numeric(cleaned, errors="coerce")


def apply_percent_threshold(df: pd.DataFrame, threshold: float, log_func=None):
    if df is None or df.empty:
        return df, 0
    if not threshold or float(threshold) <= 0:
        return df, 0
    if "LimViolPct" not in df.columns:
        if log_func:
            log_func("WARNING: Percent threshold skipped because 'LimViolPct' was not found.")
        return df, 0

    before = len(df)
    pct = _to_float_series(df["LimViolPct"])
    filtered_df = df[pct.fillna(float("-inf")) >= float(threshold)].copy()
    removed = before - len(filtered_df)
    return filtered_df, removed


def post_process_csv(
    csv_path: str,
    dedup_enabled: bool,
    keep_categories,
    log_func=None,
    threshold: float = 0.0,
    skip_voltage_46_33kv: bool = False,
) -> str:
    """
    Apply:
      1) Row filter (LimViolCat) using keep_categories
      2) If dedup_enabled:
            v2 behavior: KEEP ALL rows but sort so max LimViolPct is first per LimViolID
            (used later for Excel grouping/collapsing)
         Else:
            leave row order as-is
      3) Column blacklist

    Returns:
        path to filtered CSV (or None on failure)
    """
    if log_func:
        log_func("\nReading CSV to detect headers...")

    try:
        if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
            if log_func:
                log_func("ViolationCTG export was empty; creating empty filtered CSV.")
            empty = pd.DataFrame(columns=REQUIRED_FILTERED_COLUMNS)
            return _save_filtered_csv(csv_path, empty, log_func=log_func)

        # Skip the first row because it only has "ViolationCTG" in one column.
        try:
            raw = pd.read_csv(csv_path, header=None, skiprows=1)
        except pd.errors.EmptyDataError:
            if log_func:
                log_func("ViolationCTG export has no header/data rows; creating empty filtered CSV.")
            empty = pd.DataFrame(columns=REQUIRED_FILTERED_COLUMNS)
            return _save_filtered_csv(csv_path, empty, log_func=log_func)

        if raw.shape[0] < 1:
            if log_func:
                log_func("No header/data rows found after title row; creating empty filtered CSV.")
            empty = pd.DataFrame(columns=REQUIRED_FILTERED_COLUMNS)
            return _save_filtered_csv(csv_path, empty, log_func=log_func)

        header_row = list(raw.iloc[0])

        if log_func:
            log_func(f"Detected {len(header_row)} headers from row 2.")

        if raw.shape[0] <= 1:
            if log_func:
                log_func("No data rows found after header row; creating empty filtered CSV.")
            empty = pd.DataFrame(columns=header_row)
            return _save_filtered_csv(csv_path, empty, log_func=log_func)

        # Data rows are index >= 1
        data = raw.iloc[1:].copy()
        data.columns = header_row

        if log_func:
            log_func("\nRemoving configured excluded contingencies...")

        data, removed_excluded_contingencies = apply_contingency_name_exclusion(
            data,
            log_func=log_func,
        )

        if log_func:
            log_func(f"Rows removed by contingency name exclusion: {removed_excluded_contingencies}")

        # 1) Row filter with chosen categories
        if log_func:
            cats_txt = ", ".join(sorted(keep_categories)) if keep_categories else "NONE"
            log_func(f"\nApplying row filter for LimViolCat categories: {cats_txt}")

        filtered_data, removed_rows = apply_row_filter(
            data, keep_values=keep_categories, log_func=log_func
        )

        if log_func:
            log_func(f"Rows removed by row filter: {removed_rows}")

        if log_func and skip_voltage_46_33kv:
            log_func('\nSkipping voltage Resulting Issues starting with "1" or "2" (46 kV / 33 kV)...')

        filtered_data, removed_voltage_level_rows = apply_voltage_resulting_issue_exclusion(
            filtered_data,
            enabled=skip_voltage_46_33kv,
            log_func=log_func,
        )

        if log_func and skip_voltage_46_33kv:
            log_func(f"Rows removed by voltage level exclusion: {removed_voltage_level_rows}")

        # 1b) Percent loading threshold before sorting/deduping.
        if log_func:
            log_func(f"\nApplying percent loading threshold: {float(threshold):.2f}%")

        filtered_data, removed_pct_rows = apply_percent_threshold(
            filtered_data,
            threshold=threshold,
            log_func=log_func,
        )

        if log_func:
            log_func(f"Rows removed by percent threshold: {removed_pct_rows}")

        # 2) v2 LimViolID behavior: keep all, sort max first per LimViolID (for Excel dropdown grouping)
        if dedup_enabled:
            if log_func:
                log_func(
                    "\nExpandable issue view enabled:"
                    "\n  - Keeping ALL contingencies per Resulting Issue (LimViolID)"
                    "\n  - Sorting so the highest LimViolPct is first per LimViolID"
                    "\n  - Excel workbook will collapse the non-max rows into a dropdown/outline"
                )
            filtered_data, _ = apply_limviolid_max_filter(
                filtered_data, log_func=log_func, keep_all=True
            )
        else:
            if log_func:
                log_func("\nExpandable issue view disabled; leaving all rows unsorted by LimViolID.")

        # 3) Column blacklist
        if log_func:
            log_func("\nApplying column blacklist...")

        filtered_data, removed_cols = apply_blacklist(filtered_data)

        if log_func:
            if removed_cols:
                log_func("Columns removed by blacklist:")
                for c in removed_cols:
                    log_func(f"  - {c}")
            else:
                log_func("No columns matched blacklist; no columns removed.")

        # Save filtered CSV
        return _save_filtered_csv(csv_path, filtered_data, log_func=log_func)

    except Exception as e:
        if log_func:
            log_func(f"(Could not read CSV for header inspection: {e})")
        return None


def process_case(
    pwb_path: str,
    dedup_enabled: bool,
    keep_categories,
    delete_original: bool,
    log_func=None,
    threshold: float = 0.0,
    skip_voltage_46_33kv: bool = False,
) -> str:
    """
    Full pipeline for a single .pwb:
      - Export ViolationCTG to CSV via SimAuto
      - Run post_process_csv on it
      - Optionally delete the original (unfiltered) CSV

    Returns:
      path to filtered CSV (or None on error)
    """
    if log_func:
        log_func("\nConnecting to PowerWorld and exporting ViolationCTG...")

    csv_out = export_violation_ctg(pwb_path, log_func, threshold=threshold)

    if log_func:
        log_func(f"Exported CSV path: {csv_out}")

    filtered_csv = post_process_csv(
        csv_out,
        dedup_enabled,
        keep_categories,
        log_func,
        threshold=threshold,
        skip_voltage_46_33kv=skip_voltage_46_33kv,
    )

    if delete_original and filtered_csv and os.path.exists(csv_out):
        try:
            os.remove(csv_out)
            if log_func:
                log_func(f"Deleted original (unfiltered) CSV: {csv_out}")
        except Exception as e:
            if log_func:
                log_func(f"WARNING: Failed to delete original CSV: {e}")

    return filtered_csv
