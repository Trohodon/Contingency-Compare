# core/pwb_exporter.py

import os
import tempfile
import win32com.client


VIOLATION_CTG_EXPORT_FIELDS = [
    "CTGLabel",
    "LimViolID",
    "LimViolLimit",
    "LimViolValue",
    "LimViolPct",
    "LimViolCat",
]


def _write_empty_violation_ctg_csv(csv_path: str) -> None:
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        f.write("ViolationCTG\n")
        f.write(",".join(VIOLATION_CTG_EXPORT_FIELDS) + "\n")


def _field_list_for_script(fields) -> str:
    return "[" + ",".join(str(f).strip() for f in fields if str(f).strip()) + "]"


def _safe_threshold(threshold) -> float:
    try:
        value = float(threshold)
        return value if value > 0 else 0.0
    except Exception:
        return 0.0


def _write_threshold_filter_aux(threshold: float) -> tuple[str, str]:
    filter_name = f"DCC_ViolationCTG_Pct_GE_{str(threshold).replace('.', '_')}"
    aux_text = f'''DATA (FILTER, [ObjectType,FilterName,FilterLogic,FilterPre,Enabled], AUXDEF, YES)
{{
"ViolationCTG" "{filter_name}" "AND" "NO " "YES"
 <SUBDATA Condition>
 LimViolPct >= {threshold:.10g}
 </SUBDATA>
}}
'''
    fd, aux_path = tempfile.mkstemp(prefix="dcc_violation_ctg_filter_", suffix=".aux")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(aux_text)
    return aux_path, filter_name


def _try_define_threshold_filter(simauto, threshold: float, log_func):
    threshold = _safe_threshold(threshold)
    if threshold <= 0:
        return ""

    aux_path = None
    try:
        aux_path, filter_name = _write_threshold_filter_aux(threshold)
        log_func(
            f"Defining PowerWorld export filter for ViolationCTG: "
            f"LimViolPct >= {threshold:.2f}"
        )
        result = simauto.ProcessAuxFile(aux_path)
        err = result[0] if isinstance(result, tuple) and result else result
        if err:
            log_func(f"WARNING: PowerWorld export filter was not accepted: {err}")
            log_func("Continuing with required columns only; percent threshold will be applied after export.")
            return ""
        return filter_name
    except Exception as e:
        log_func(f"WARNING: Could not define PowerWorld export filter: {e}")
        log_func("Continuing with required columns only; percent threshold will be applied after export.")
        return ""
    finally:
        if aux_path:
            try:
                os.remove(aux_path)
            except Exception:
                pass


def export_violation_ctg(pwb_path: str, log_func, threshold: float = 0.0) -> str:
    """
    Core logic that talks to PowerWorld SimAuto and exports
    the ViolationCTG table to CSV.

    Returns:
        Path to the CSV file that was written.

    Raises:
        RuntimeError on PowerWorld/SimAuto errors.
    """

    base, _ = os.path.splitext(pwb_path)
    csv_out = base + "_ViolationCTG.csv"

    log_func("Connecting to PowerWorld via SimAuto...")
    simauto = win32com.client.Dispatch("pwrworld.SimulatorAuto")
    log_func("Connected.")

    try:
        # 1) Open the case (must already have contingency results stored)
        log_func(f"Opening case: {pwb_path}")
        (err,) = simauto.OpenCase(pwb_path)
        if err:
            raise RuntimeError(f"OpenCase error: {err}")
        log_func("Case opened successfully; using existing contingency results.")

        # 2) Enter Contingency mode so ViolationCTG is active
        log_func("Entering Contingency mode...")
        (err,) = simauto.RunScriptCommand("EnterMode(Contingency);")
        if err:
            raise RuntimeError(f"EnterMode(Contingency) error: {err}")

        # 3) Save ViolationCTG table to CSV
        log_func(f"Saving ViolationCTG data to CSV:\n  {csv_out}")
        log_func(
            "Exporting only required ViolationCTG fields: "
            + ", ".join(VIOLATION_CTG_EXPORT_FIELDS)
        )
        threshold = _safe_threshold(threshold)
        filter_name = _try_define_threshold_filter(simauto, threshold, log_func)
        if filter_name:
            log_func(
                f"PowerWorld export filter active: {filter_name} "
                f"(LimViolPct >= {threshold:.2f})."
            )
        elif threshold:
            log_func(f"Percent loading threshold requested: {threshold:.2f}%")
        clean_csv = csv_out.replace("\\", "/")  # avoid backslash issues in script
        cmd = (
            f'SaveData("{clean_csv}", CSV, ViolationCTG, '
            f"{_field_list_for_script(VIOLATION_CTG_EXPORT_FIELDS)}, [], \"{filter_name}\");"
        )
        (err,) = simauto.RunScriptCommand(cmd)
        if err:
            raise RuntimeError(f"SaveData(ViolationCTG) error: {err}")
        if not os.path.isfile(csv_out):
            log_func(
                "WARNING: PowerWorld did not create the ViolationCTG CSV. "
                "Creating an empty export so processing can continue."
            )
            _write_empty_violation_ctg_csv(csv_out)
        log_func("CSV export complete for ViolationCTG.")

    finally:
        # Clean up SimAuto
        try:
            simauto.CloseCase()
        except Exception:
            pass
        del simauto

    return csv_out
