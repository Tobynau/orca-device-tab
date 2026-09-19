#!/usr/bin/env python3
"""The same jobs install.py does, in a window.

    python3 install.py gui        (or: python3 gui.py)

A printer list with what each one points at and whether it answers, Add and
Edit dialogs that can scan a host for its camera and web UI and pick the Orca
presets to bind, and the buttons for writing the pages and taking them away
again.
"""

import contextlib
import io
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

import install

DASH = "—"


def background(widget, work, done):
    """Runs work() off the UI thread; calls done(result) back on it. Probing a
    printer that is switched off takes seconds, and the window should not go
    grey while it happens."""
    box = {}

    def run():
        try:
            box["value"] = work()
        except Exception as error:          # reported, never fatal
            box["error"] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    def poll():
        if thread.is_alive():
            widget.after(120, poll)
            return
        if "error" in box:
            messagebox.showerror("Error", str(box["error"]))
        else:
            done(box["value"])

    widget.after(120, poll)


class Dialog(tk.Toplevel):
    """Add / Edit. Returns the printer dict through self.result."""

    def __init__(self, parent, app, printer=None):
        tk.Toplevel.__init__(self, parent)
        self.app = app
        self.result = None
        self.original = printer
        self.title("Edit printer" if printer else "Add printer")
        self.transient(parent)
        self.resizable(False, False)

        p = printer or {}
        self.fields = {}
        form = ttk.Frame(self, padding=12)
        form.grid(sticky="nsew")
        ttk.Label(form, text="Leave a URL blank and that tab is not built.",
                  foreground="#777").grid(row=0, column=0, columnspan=3, sticky="w",
                                          pady=(0, 8))
        rows = [
            ("label", "Name", p.get("label", "")),
            ("host", "Host or IP", p.get("host", "")),
            ("camera", "Camera URL", p.get("camera", "")),
            ("panel", "Control URL", p.get("panel", "")),
            ("panel_label", "Control tab name", p.get("panel_label", "")),
            ("note", "Note", p.get("note", "")),
        ]
        for i, (key, label, value) in enumerate(rows):
            row = i + 1
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="e", padx=(0, 8), pady=3)
            var = tk.StringVar(value=value)
            ttk.Entry(form, textvariable=var, width=40).grid(row=row, column=1, sticky="we", pady=3)
            self.fields[key] = var
            if key == "host":
                self.scan_button = ttk.Button(form, text="Scan", width=7, command=self.scan)
                self.scan_button.grid(row=row, column=2, padx=(6, 0))

        ttk.Separator(form, orient="horizontal").grid(row=len(rows) + 1, column=0, columnspan=3,
                                                      sticky="we", pady=10)
        ttk.Label(form, text="Bind to presets - a name, or the start of one, one per line",
                  foreground="#777").grid(row=len(rows) + 2, column=0, columnspan=3, sticky="w")
        self.presets = tk.Text(form, height=3, width=40)
        self.presets.grid(row=len(rows) + 3, column=0, columnspan=3, sticky="we", pady=(4, 0))
        self.presets.insert("1.0", "\n".join(p.get("presets", [])))

        known = install.known_presets(app.datadirs)
        if known:
            box = ttk.Frame(form)
            box.grid(row=len(rows) + 4, column=0, columnspan=3, sticky="we", pady=(8, 0))
            self.known = tk.Listbox(box, selectmode="extended", height=6, exportselection=False)
            scroll = ttk.Scrollbar(box, orient="vertical", command=self.known.yview)
            self.known.configure(yscrollcommand=scroll.set)
            self.known.pack(side="left", fill="both", expand=True)
            scroll.pack(side="left", fill="y")
            for name in known:
                self.known.insert("end", name)
            ttk.Button(form, text="Add selected", command=self.add_selected).grid(
                row=len(rows) + 5, column=0, columnspan=3, sticky="w", pady=6)

        buttons = ttk.Frame(form)
        buttons.grid(row=len(rows) + 6, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(buttons, text="Save", command=self.save).pack(side="right")

        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()

    def add_selected(self):
        have = [line for line in self.presets.get("1.0", "end").splitlines() if line.strip()]
        for i in self.known.curselection():
            name = self.known.get(i)
            if name not in have:
                have.append(name)
        self.presets.delete("1.0", "end")
        self.presets.insert("1.0", "\n".join(have))

    def scan(self):
        host = self.fields["host"].get().strip()
        if not host:
            messagebox.showinfo("Scan", "Fill in a host or IP first.")
            return
        self.scan_button.configure(state="disabled", text="...")

        def done(open_ports):
            self.scan_button.configure(state="normal", text="Scan")
            if not open_ports:
                messagebox.showinfo("Scan", "Nothing answered on the usual ports.")
                return
            camera = next((p for p in install.CAMERA_PORTS if p in open_ports), None)
            panel = next((p for p in install.PANEL_PORTS
                          if p in open_ports and p != camera), None)
            if camera and not self.fields["camera"].get().strip():
                self.fields["camera"].set(install.url_for(":%d/?action=stream" % camera, host))
            if panel and not self.fields["panel"].get().strip():
                self.fields["panel"].set(install.url_for(":%d/" % panel, host))
            messagebox.showinfo("Scan", "%s answers on: %s"
                                % (host, ", ".join(str(p) for p in open_ports)))

        background(self, lambda: install.scan(host), done)

    def save(self):
        label = self.fields["label"].get().strip()
        if not label:
            messagebox.showwarning("Add printer", "Give the printer a name.")
            return
        printer = dict(self.original or {})
        printer.update({key: var.get().strip() for key, var in self.fields.items()})
        printer["presets"] = [line.strip() for line in
                              self.presets.get("1.0", "end").splitlines() if line.strip()]
        if not self.original:
            printer["id"] = install.slug(label)
        try:
            install.normalise(printer)
        except ValueError as error:
            messagebox.showerror("Add printer", str(error))
            return
        self.result = printer
        self.destroy()


class App(tk.Tk):
    COLUMNS = ("camera", "panel", "presets")

    def __init__(self, args):
        tk.Tk.__init__(self)
        self.args = args
        self.cfg = install.load(args.config)
        self.datadirs = install.orca_datadirs(args.datadir)
        self.title("OrcaSlicer device pages")
        self.geometry("1000x680")
        self.minsize(720, 460)

        top = ttk.Frame(self, padding=(10, 10, 10, 0))
        top.pack(fill="x")
        self.warning = ttk.Label(top, text="", foreground="#b00")
        self.warning.pack(side="right")
        ttk.Label(top, text="Printers").pack(side="left")

        middle = ttk.Frame(self, padding=10)
        self.tree = ttk.Treeview(middle, columns=self.COLUMNS, show="tree headings", height=9)
        self.tree.heading("#0", text="Printer")
        self.tree.heading("camera", text="Camera")
        self.tree.heading("panel", text="Control panel")
        self.tree.heading("presets", text="Presets")
        self.tree.column("#0", width=180)
        self.tree.column("camera", width=250)
        self.tree.column("panel", width=250)
        self.tree.column("presets", width=110, anchor="center")
        scroll = ttk.Scrollbar(middle, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.edit())

        bar = ttk.Frame(self, padding=(10, 0))
        for text, command in (("Add", self.add), ("Edit", self.edit), ("Remove", self.remove),
                              ("Recheck", self.recheck), ("Open page", self.open_page)):
            ttk.Button(bar, text=text, command=command).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="Remove from Orca", command=self.uninstall).pack(side="right")
        ttk.Button(bar, text="Apply", command=self.apply).pack(side="right", padx=6)

        self.status = ttk.Label(self, text="", anchor="w", padding=(10, 0, 10, 8))
        self.status.pack(side="bottom", fill="x")
        self.log = tk.Text(self, height=5, wrap="none", state="disabled",
                           background="#1c1c1e", foreground="#d8d8dc", insertbackground="#fff")
        self.log.pack(side="bottom", fill="x", padx=10, pady=10)
        bar.pack(side="bottom", fill="x", pady=(0, 4))
        middle.pack(fill="both", expand=True)
        self.refresh()
        self.recheck()

    # ------------------------------------------------------------ helpers

    def say(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def run_task(self, work):
        """Runs one of install.py's jobs, with its output going to the log."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            work()
        for line in out.getvalue().splitlines():
            self.say(line)
        self.refresh()

    def selected(self):
        picked = self.tree.selection()
        if not picked:
            messagebox.showinfo("Printers", "Pick a printer in the list first.")
            return None
        return install.find(self.cfg, picked[0])

    def refresh(self):
        bound = install.bindings(self.datadirs)
        self.tree.delete(*self.tree.get_children())
        for p in self.cfg["printers"]:
            names = bound.get(p["id"], [])
            self.tree.insert("", "end", iid=p["id"], text=p["label"],
                             values=(p.get("camera") or DASH,
                                     p.get("panel") or DASH,
                                     "%d bound" % len(names) if names else "not bound"))
        where = ", ".join(str(d) for d in self.datadirs) or "no OrcaSlicer install found"
        self.status.configure(text="%s   ·   %s" % (self.args.config, where))
        self.warning.configure(
            text="OrcaSlicer is open - close it before applying" if install.orca_running() else "")

    def recheck(self):
        """Fills the Camera and Control columns in with what answers."""
        printers = [dict(p) for p in self.cfg["printers"]]

        def work():
            return [(p["id"], install.reachable(p.get("camera")), install.reachable(p.get("panel")))
                    for p in printers]

        def done(results):
            for printer_id, camera, panel in results:
                if not self.tree.exists(printer_id):
                    continue
                p = install.find(self.cfg, printer_id)
                self.tree.set(printer_id, "camera", self.mark(p.get("camera"), camera))
                self.tree.set(printer_id, "panel", self.mark(p.get("panel"), panel))

        background(self, work, done)

    @staticmethod
    def mark(url, up):
        if not url:
            return DASH
        return "%s  %s" % (url, "✓" if up else "×")

    def guard(self):
        """Orca rewrites its presets on exit, so it has to be closed."""
        if self.args.force or not install.orca_running():
            return True
        return messagebox.askyesno(
            "OrcaSlicer is running",
            "OrcaSlicer rewrites its presets when it closes, which would undo this.\n\n"
            "Carry on anyway?")

    # ------------------------------------------------------------ buttons

    def add(self):
        dialog = Dialog(self, self)
        self.wait_window(dialog)
        if not dialog.result:
            return
        if any(p["id"] == dialog.result["id"] for p in self.cfg["printers"]):
            messagebox.showerror("Add printer", "A printer called %r is already in the list."
                                 % dialog.result["id"])
            return
        self.cfg["printers"].append(dialog.result)
        self.save()
        self.say("added %s" % dialog.result["id"])
        self.recheck()

    def edit(self):
        printer = self.selected()
        if not printer:
            return
        dialog = Dialog(self, self, printer)
        self.wait_window(dialog)
        if not dialog.result:
            return
        printer.clear()
        printer.update(dialog.result)
        self.save()
        self.say("updated %s" % printer["id"])
        self.recheck()

    def remove(self):
        printer = self.selected()
        if not printer:
            return
        if not messagebox.askyesno("Remove printer", "Forget %s?" % printer["label"]):
            return
        self.cfg["printers"].remove(printer)
        self.save()
        self.say("removed %s - press Apply to clear it from Orca" % printer["id"])

    def apply(self):
        if not self.guard():
            return
        self.run_task(lambda: install.apply(self.cfg, self.datadirs, self.args.dry_run))

    def uninstall(self):
        if not self.guard():
            return
        if not messagebox.askyesno("Remove from Orca",
                                   "Clear the Device tab URL from every preset and delete the "
                                   "pages?\n\nThe printer list itself is kept."):
            return
        self.run_task(lambda: install.uninstall(self.cfg, self.datadirs, self.args.dry_run))

    def open_page(self):
        printer = self.selected()
        if not printer or not self.datadirs:
            return
        page = self.datadirs[0] / install.PAGES_SUBDIR / ("%s.html" % printer["id"])
        if not page.exists():
            messagebox.showinfo("Open page", "Press Apply first - the page is not written yet.")
            return
        webbrowser.open(page.as_uri())

    def save(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            install.save(self.cfg, self.args)
        for line in out.getvalue().splitlines():
            self.say(line)
        self.refresh()


def run(args=None):
    if args is None:
        args = install.build_parser().parse_args([])
    App(args).mainloop()


if __name__ == "__main__":
    run()
