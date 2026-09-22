"""Neighbor Studies tab for N-1 P1 and P2-7 case processing."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core.neighbor_studies import RESULTS_DIR, discover_n1_cases, run_neighbor_studies, write_discovery_failure_logs


class NeighborStudiesTab(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.folder = tk.StringVar(value="No main folder selected")
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._running = False

        controls = ttk.LabelFrame(self, text="N-1 neighbor studies", padding=12)
        controls.pack(fill="x", padx=10, pady=10)
        ttk.Label(controls, text="Main folder containing study folders:").grid(row=0, column=0, sticky="w")
        ttk.Label(controls, textvariable=self.folder, wraplength=700).grid(row=1, column=0, sticky="w", pady=(4, 8))
        self.browse_button = ttk.Button(controls, text="Browse main folder…", command=self._browse)
        self.browse_button.grid(row=1, column=1, padx=8)
        self.run_button = ttk.Button(controls, text="Run N-1 neighbor studies", command=self._run)
        self.run_button.grid(row=2, column=0, sticky="w")
        ttk.Label(
            controls,
            text=("For each study, uses a completed N-1/*_ACCA_N-1.PWB when available; "
                  "otherwise runs *_CA_NOT_RUN_N-1.PWB. Creates company CON files "
                  "and one workbook per company with P1 and P2-7 sheets for each study."),
            wraplength=800,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
        controls.columnconfigure(0, weight=1)

        preview = ttk.LabelFrame(self, text="Cases found", padding=8)
        preview.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.tree = ttk.Treeview(preview, columns=("study", "source", "working"), show="headings", height=8)
        for column, label, width in (("study", "Study", 220), ("source", "Source PWB", 450), ("working", "Working PWB", 450)):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True)

        log_frame = ttk.LabelFrame(self, text="Neighbor Studies Log", padding=8)
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log_text = tk.Text(log_frame, height=10, state="disabled", wrap="word")
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)
        self.after(100, self._drain_events)

    def _log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", str(message) + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _browse(self) -> None:
        selected = filedialog.askdirectory(title="Select Neighbor Studies main folder")
        if not selected:
            return
        self.folder.set(selected)
        self.tree.delete(*self.tree.get_children())
        cases, warnings = discover_n1_cases(Path(selected))
        for case in cases:
            self.tree.insert("", "end", values=(case.study, str(case.source) if case.source else "Saved case only", str(case.working)))
        self._log(f"Found {len(cases)} N-1 study cases in {selected}")
        for warning in warnings:
            self._log(f"Skipped: {warning}")

    def _run(self) -> None:
        if self._running:
            return
        root = Path(self.folder.get())
        if not root.is_dir():
            messagebox.showwarning("No folder", "Select a valid main folder first.")
            return
        cases, discovery_warnings = discover_n1_cases(root)
        if not cases:
            write_discovery_failure_logs(root, discovery_warnings, self._log)
            messagebox.showwarning("No cases", "No N-1 source or ACCA cases were found. See log.txt in each affected study folder.")
            return
        if any(case.working.exists() for case in cases) or (root / RESULTS_DIR).exists():
            if not messagebox.askyesno(
                "Existing results",
                "Some generated ACCA cases or result files already exist. "
                "Running again will replace files with the same names. Continue?",
            ):
                return
        self._running = True
        self.browse_button.configure(state="disabled")
        self.run_button.configure(state="disabled")
        self._log(f"Starting {len(cases)} N-1 studies...")
        assets_dir = Path(__file__).resolve().parent.parent / "assets"
        threading.Thread(target=self._worker, args=(root, assets_dir), daemon=True).start()

    def _worker(self, root: Path, assets_dir: Path) -> None:
        # A worker thread needs its own COM initialization for PowerWorld SimAuto.
        try:
            import pythoncom
            pythoncom.CoInitialize()
            try:
                result = run_neighbor_studies(
                    root, assets_dir, lambda message: self._events.put(("log", message))
                )
                self._events.put(("done", result))
            finally:
                pythoncom.CoUninitialize()
        except Exception as exc:
            self._events.put(("error", str(exc)))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "log":
                    self._log(str(payload))
                elif kind == "done":
                    workbooks, warnings = payload
                    self._finish()
                    for warning in warnings:
                        self._log(f"WARNING: {warning}")
                    show_result = messagebox.showwarning if warnings else messagebox.showinfo
                    show_result(
                        "Neighbor Studies complete with warnings" if warnings else "Neighbor Studies complete",
                        f"Created {len(workbooks)} company workbooks.\n"
                        f"Warnings: {len(warnings)}\n\n"
                        + ("Failure details: log.txt in each affected study folder.\n\n" if warnings else "")
                        + f"Results: {Path(self.folder.get()) / RESULTS_DIR}",
                    )
                elif kind == "error":
                    self._finish()
                    self._log(f"ERROR: {payload}")
                    messagebox.showerror("Neighbor Studies failed", str(payload))
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _finish(self) -> None:
        self._running = False
        self.browse_button.configure(state="normal")
        self.run_button.configure(state="normal")
