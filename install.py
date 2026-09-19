#!/usr/bin/env python3
"""Gives OrcaSlicer a working Device tab for printers it has no live view of.

    python3 install.py                  write the pages, point matching presets at them
    python3 install.py add              add a printer, then do the above
    python3 install.py list             printers, what they are bound to, what answers
    python3 install.py set ID --panel URL
    python3 install.py remove ID
    python3 install.py uninstall        unbind the presets and delete the pages

Printers live in one config file (`printers.json`, path under `list`). Each one
gets a page with a Camera tab and a Control tab, written into every Orca install
found, and every printer preset whose name matches is pointed at it through
`print_host_webui`.

Close OrcaSlicer first: it rewrites its user presets on exit, and would put back
what it had in memory when it started.
"""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import pages

PAGES_SUBDIR = "device-pages"
CAMERA_PORTS = (8080, 8081, 81, 8000)
PANEL_PORTS = (80, 8080, 5000, 4408, 8081)
SCAN_PORTS = (80, 81, 443, 3000, 4408, 5000, 7125, 8000, 8080, 8081, 8899)


# ---------------------------------------------------------------- config

def config_path():
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "orca-device-tab" / "printers.json"


def load(path):
    if not path.exists():
        return {"printers": []}
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        sys.exit("ERROR: %s is not valid JSON: %s" % (path, e))
    cfg.setdefault("printers", [])
    for p in cfg["printers"]:
        normalise(p)
    return cfg


def save(cfg, args):
    """Writes the config back, unless this is a dry run."""
    if args.dry_run:
        print("(dry run: %s left alone)" % args.config)
        return
    path = args.config
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def slug(text):
    out = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return out or "printer"


def url_for(value, host, default_port=None, default_path="/"):
    """Expands what people actually type: a bare IP, `:8080/?action=stream`,
    `printer.local/webcam`, or a full URL, which is left alone."""
    value = (value or "").strip()
    if not value:
        return ""
    if "://" in value:
        return value
    if value.startswith(":") or value.startswith("/"):
        if not host:
            return ""
        value = host + value
    parts = value.split("/", 1)
    hostport, path = parts[0], ("/" + parts[1] if len(parts) > 1 else default_path)
    if ":" not in hostport and default_port and default_port != 80:
        hostport = "%s:%d" % (hostport, default_port)
    if hostport.endswith(":80"):
        hostport = hostport[:-3]
    return "http://" + hostport + path


def normalise(p):
    p["id"] = pages.check_id(p.get("id") or slug(p.get("label", "")))
    p.setdefault("label", p["id"])
    host = p.get("host", "")
    p["camera"] = url_for(p.get("camera"), host, 8080, "/?action=stream")
    p["panel"] = url_for(p.get("panel"), host, 80, "/")
    presets = p.get("presets") or []
    p["presets"] = [presets] if isinstance(presets, str) else list(presets)
    return p


def find(cfg, printer_id):
    for p in cfg["printers"]:
        if p["id"] == printer_id:
            return p
    sys.exit("ERROR: no printer %r. `install.py list` shows them." % printer_id)


# ---------------------------------------------------------------- orca installs

def orca_datadirs(override=None):
    if override:
        return [Path(d).expanduser() for d in override]
    home = Path.home()
    if sys.platform == "darwin":
        found = [home / "Library" / "Application Support" / "OrcaSlicer"]
    elif os.name == "nt":
        found = [Path(os.environ.get("APPDATA", "")) / "OrcaSlicer"]
    else:
        found = [
            home / ".config" / "OrcaSlicer",
            home / ".var/app/com.orcaslicer.OrcaSlicer/config/OrcaSlicer",
            home / ".var/app/io.github.softfever.OrcaSlicer/config/OrcaSlicer",
            home / ".var/app/io.github.orcaslicer.OrcaSlicer/config/OrcaSlicer",
            home / "snap/orcaslicer/current/.config/OrcaSlicer",
        ]
    return [d for d in found if (d / "user").is_dir()]


def preset_files(datadir):
    """Every user printer preset, and the physical printers that name one."""
    for path in sorted(datadir.glob("user/*/machine/*.json")):
        yield path
    for path in sorted(datadir.glob("user/*/physical_printer/*.json")):
        yield path


def read_preset(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def describe(path, data):
    if path.parent.name == "physical_printer":
        return "%s (physical printer)" % (data.get("name") or path.stem)
    return data.get("name") or path.stem


def preset_names(path, data):
    """The printer preset names a file speaks for: its own, or for a physical
    printer, the presets it is attached to."""
    if path.parent.name == "physical_printer":
        names = data.get("preset_names") or data.get("preset_name") or []
        return [names] if isinstance(names, str) else list(names)
    return [data.get("name") or path.stem]


def orca_running():
    for name in ("orca-slicer", "OrcaSlicer", "orcaslicer"):
        try:
            if subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0:
                return True
        except OSError:
            return False
    return False


# ---------------------------------------------------------------- applying

class Backup:
    """Copies a preset aside the first time this run touches it."""

    def __init__(self, datadir, dry):
        self.root = datadir / ("device-tab-backup-%s" % time.strftime("%Y%m%d-%H%M%S"))
        self.datadir = datadir
        self.dry = dry
        self.made = False

    def keep(self, path):
        if self.dry:
            return
        dest = self.root / path.relative_to(self.datadir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        self.made = True


def apply(cfg, datadirs, dry=False):
    printers = cfg["printers"]
    keep = {"%s.html" % p["id"] for p in printers} | {"all.html"}
    total = 0
    for datadir in datadirs:
        pages_dir = datadir / PAGES_SUBDIR
        ours = pages_dir.as_uri() + "/"
        say = print
        say(str(datadir))
        if not dry:
            pages.write(printers, pages_dir)
            for stale in pages_dir.glob("*.html"):
                if stale.name not in keep:
                    stale.unlink()
        say("  pages: %s" % pages_dir)

        backup = Backup(datadir, dry)
        changed = 0
        for path in preset_files(datadir):
            data = read_preset(path)
            if data is None:
                continue
            names = preset_names(path, data)
            match = None
            for p in printers:
                if any(name.lower().startswith(pre.lower())
                       for name in names for pre in p["presets"] if pre):
                    match = p
                    break
            current = data.get("print_host_webui", "")
            if match:
                url = (pages_dir / ("%s.html" % match["id"])).as_uri()
                if current == url:
                    continue
                data["print_host_webui"] = url
                action = "%s -> %s" % (describe(path, data), match["id"])
            elif current.startswith(ours):
                del data["print_host_webui"]
                action = "%s -> cleared" % describe(path, data)
            else:
                continue
            changed += 1
            say("  %s" % action)
            if not dry:
                backup.keep(path)
                path.write_text(json.dumps(data, indent=4, ensure_ascii=False) + "\n",
                                encoding="utf-8")
        if backup.made:
            say("  backup: %s" % backup.root)
        if not changed:
            say("  presets: already up to date")
        total += changed
    if not datadirs:
        print("No OrcaSlicer install found. Pass --datadir if it is somewhere unusual.")
    return total


def uninstall(cfg, datadirs, dry=False):
    for datadir in datadirs:
        pages_dir = datadir / PAGES_SUBDIR
        ours = pages_dir.as_uri() + "/"
        print("%s" % datadir)
        backup = Backup(datadir, dry)
        for path in preset_files(datadir):
            data = read_preset(path)
            if data is None or not data.get("print_host_webui", "").startswith(ours):
                continue
            del data["print_host_webui"]
            print("  cleared: %s" % describe(path, data))
            if not dry:
                backup.keep(path)
                path.write_text(json.dumps(data, indent=4, ensure_ascii=False) + "\n",
                                encoding="utf-8")
        if pages_dir.is_dir() and not dry:
            shutil.rmtree(pages_dir)
            print("  removed: %s" % pages_dir)


# ---------------------------------------------------------------- probing

def reachable(url, timeout=1.5):
    """True/False for something listening, None if there is nothing to try."""
    if not url:
        return None
    bits = urlsplit(url)
    if not bits.hostname:
        return None
    port = bits.port or (443 if bits.scheme == "https" else 80)
    return open_port(bits.hostname, port, timeout)


def open_port(host, port, timeout=1.5):
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def scan(host):
    return [p for p in SCAN_PORTS if open_port(host, p, 0.6)]


# ---------------------------------------------------------------- commands

def bindings(datadirs):
    """Printer id -> the presets currently pointed at its page."""
    bound = {}
    for datadir in datadirs:
        prefix = (datadir / PAGES_SUBDIR).as_uri() + "/"
        for path in preset_files(datadir):
            data = read_preset(path)
            url = (data or {}).get("print_host_webui", "")
            if url.startswith(prefix):
                bound.setdefault(url[len(prefix):].replace(".html", ""), []).append(
                    describe(path, data))
    return bound


def known_presets(datadirs):
    """Every user printer preset name Orca knows about, sorted."""
    names = set()
    for datadir in datadirs:
        for path in preset_files(datadir):
            if path.parent.name == "physical_printer":
                continue
            data = read_preset(path)
            if data is not None:
                names.update(preset_names(path, data))
    return sorted(names)


def cmd_list(cfg, datadirs, args):
    print("config: %s" % args.config)
    for datadir in datadirs:
        print("pages:  %s" % (datadir / PAGES_SUBDIR))
    if not cfg["printers"]:
        print("\nNo printers yet. Add one with `install.py add`.")
        return
    bound = bindings(datadirs)
    for p in cfg["printers"]:
        print("\n%s  %s%s" % (p["id"], p["label"], "  (%s)" % p["note"] if p.get("note") else ""))
        for kind in ("camera", "panel"):
            if p.get(kind):
                up = reachable(p[kind])
                print("  %-7s %-52s %s" % (kind, p[kind],
                                           "answers" if up else "no answer"))
        names = bound.get(p["id"], [])
        if names:
            shown = ", ".join(names[:3]) + (" +%d more" % (len(names) - 3) if len(names) > 3 else "")
            print("  presets %d bound: %s" % (len(names), shown))
        elif p["presets"]:
            print("  presets matching %s: none bound yet, run `install.py`"
                  % ", ".join(repr(x) for x in p["presets"]))
        else:
            print("  presets none set, run `install.py set %s --preset \"Name prefix\"`" % p["id"])


def ask(prompt, default=""):
    try:
        answer = input("%s%s: " % (prompt, " [%s]" % default if default else "")).strip()
    except EOFError:
        answer = ""
    return answer or default


def pick_presets(datadirs, preset_args):
    """Turns --preset flags, or a numbered pick from the presets Orca has, into
    the list of name prefixes a printer matches on."""
    if preset_args:
        return list(preset_args)
    names = known_presets(datadirs)
    if not names or not sys.stdin.isatty():
        return []
    print("\nOrca printer presets:")
    for i, name in enumerate(names, 1):
        print("  %2d  %s" % (i, name))
    answer = ask("\nBind which? numbers, or a name prefix like 'Voron 2.4'")
    if not answer:
        return []
    if re.fullmatch(r"[\d ,-]+", answer):
        ordered = names
        chosen = []
        for part in re.split(r"[ ,]+", answer.strip()):
            if "-" in part:
                lo, hi = part.split("-", 1)
                span = range(int(lo), int(hi) + 1)
            else:
                span = [int(part)]
            for i in span:
                if 1 <= i <= len(ordered) and ordered[i - 1] not in chosen:
                    chosen.append(ordered[i - 1])
        return chosen
    return [answer]


def cmd_add(cfg, datadirs, args):
    interactive = sys.stdin.isatty() and not args.label
    label = args.label or (ask("Name (as it should read in the tab bar)") if interactive else "")
    if not label:
        sys.exit("ERROR: --label is required")
    host = args.host or (ask("Host or IP") if interactive else "")
    if host and interactive:
        found = scan(host)
        print("  %s: %s" % (host, ", ".join("port %d" % p for p in found) if found
                            else "nothing answered on the usual ports"))
        camera_default = next((url_for(":%d/?action=stream" % p, host)
                               for p in CAMERA_PORTS if p in found), "")
        panel_default = next((url_for(":%d/" % p, host)
                              for p in PANEL_PORTS if p in found and p not in CAMERA_PORTS), "")
    else:
        camera_default = url_for(":8080/?action=stream", host) if host else ""
        panel_default = url_for(":80/", host) if host else ""

    camera = args.camera if args.camera is not None else (
        ask("Camera stream URL (blank for none)", camera_default) if interactive else camera_default)
    panel = args.panel if args.panel is not None else (
        ask("Control panel URL (blank for none)", panel_default) if interactive else panel_default)

    printer = normalise({
        "id": args.id or slug(label),
        "label": label,
        "note": args.note or "",
        "host": host,
        "camera": camera,
        "panel": panel,
        "panel_label": args.panel_label or "",
        "presets": pick_presets(datadirs, args.preset),
    })
    if any(p["id"] == printer["id"] for p in cfg["printers"]):
        sys.exit("ERROR: a printer with id %r is already configured" % printer["id"])
    cfg["printers"].append(printer)
    print("\nadded %s" % printer["id"])
    save(cfg, args)
    return printer


def cmd_set(cfg, datadirs, args):
    p = find(cfg, args.id)
    if args.host is not None:
        p["host"] = args.host
    if args.label:
        p["label"] = args.label
    if args.note is not None:
        p["note"] = args.note
    if args.camera is not None:
        p["camera"] = args.camera
    if args.panel is not None:
        p["panel"] = args.panel
    if args.panel_label is not None:
        p["panel_label"] = args.panel_label
    if args.preset:
        p["presets"] = list(args.preset)
    normalise(p)
    print("updated %s" % p["id"])
    save(cfg, args)


def cmd_remove(cfg, datadirs, args):
    p = find(cfg, args.id)
    cfg["printers"].remove(p)
    print("removed %s" % p["id"])
    save(cfg, args)


# ---------------------------------------------------------------- entry point

def build_parser():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="With no command: write the pages and bind the presets.")
    ap.add_argument("--config", type=Path, default=config_path(),
                    help="printers.json to use (default: %(default)s)")
    ap.add_argument("--datadir", action="append",
                    help="OrcaSlicer config directory; repeatable, default is every one found")
    ap.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
    ap.add_argument("--force", action="store_true", help="carry on even if OrcaSlicer is running")
    sub = ap.add_subparsers(dest="command")

    def printer_flags(parser, for_add):
        parser.add_argument("--label", help="name shown in the tab bar")
        parser.add_argument("--host", help="IP or hostname the URLs default from")
        parser.add_argument("--note", help="small print in the page footer")
        parser.add_argument("--camera", help="MJPEG stream URL, or ':8080/?action=stream'")
        parser.add_argument("--panel", help="control panel URL, or ':80/'")
        parser.add_argument("--panel-label", dest="panel_label", help="name for the second tab")
        parser.add_argument("--preset", action="append",
                            help="printer preset name, or the start of one; repeatable")
        if for_add:
            parser.add_argument("--id", help="short id, also the page filename")

    add = sub.add_parser("add", help="add a printer")
    printer_flags(add, True)
    setp = sub.add_parser("set", help="change a printer")
    setp.add_argument("id")
    printer_flags(setp, False)
    rm = sub.add_parser("remove", help="forget a printer")
    rm.add_argument("id")
    sub.add_parser("list", help="show printers, bindings and what answers")
    sub.add_parser("gui", help="the same jobs, in a window")
    sub.add_parser("uninstall", help="unbind presets and delete the pages")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = load(args.config)
    datadirs = orca_datadirs(args.datadir)

    if args.command == "list":
        return cmd_list(cfg, datadirs, args)
    if args.command == "gui":
        import gui
        return gui.run(args)

    writing = args.command in (None, "add", "set", "remove", "uninstall")
    if writing and not args.dry_run and not args.force and orca_running():
        sys.exit("ERROR: OrcaSlicer is running. Quit it first - it rewrites its presets on exit.")

    if args.command == "uninstall":
        return uninstall(cfg, datadirs, args.dry_run)
    if args.command == "add":
        cmd_add(cfg, datadirs, args)
    elif args.command == "set":
        cmd_set(cfg, datadirs, args)
    elif args.command == "remove":
        cmd_remove(cfg, datadirs, args)
    elif not cfg["printers"]:
        print("No printers configured yet (%s).\nAdd one with: python3 install.py add"
              % args.config)
        return

    changed = apply(cfg, datadirs, args.dry_run)
    if changed:
        print("\nOpen Orca and pick the printer: its Device tab is the page now.")


if __name__ == "__main__":
    main()
