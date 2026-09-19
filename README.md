# Camera and control pages for OrcaSlicer's Device tab

Orca's **Device** tab is a webview, and for anything that is not a Bambu printer the
only thing it can show is the one URL in the printer preset — there is no webcam
setting, and no way to point it at two places. Most printers have two: an MJPEG
camera on one port and a web interface on another.

This gives each printer a local page holding both, and points Orca's Device tab at it.

* **Camera** tab — the MJPEG stream, reconnecting on its own when the printer sleeps
  or drops off the network
* **Control** tab — the printer's own web UI (Mainsail, Fluidd, OctoPrint, DuetWebControl,
  UltiMaker, whatever it serves) in a frame
* A switcher along the top for every printer you have configured, each with a live/offline
  dot, plus an **All** view that puts every camera in one grid

The stream is dropped when you leave the Camera tab, so a printer that only serves one
or two clients is not tied up while you are using the control panel.

## Install

Needs Python 3 and nothing else. Close OrcaSlicer first — it rewrites its user presets on
exit, and would put back what it had in memory when it started.

```bash
python3 install.py add        # asks for a name, an address, and which presets to bind
python3 install.py            # re-run any time: rewrites the pages, re-binds the presets
```

`add` scans the address for the usual camera and web-UI ports, suggests URLs from what
answers, then lists your Orca printer presets so you can pick the ones this printer is.
Everything it asks can be given as a flag instead:

```bash
python3 install.py add --label "Voron 2.4" --host 192.168.1.50 \
        --camera ":8080/?action=stream" --panel ":80" --preset "Voron 2.4"
```

Pages are written into every Orca install found — native, Flatpak, Snap, macOS, Windows —
and each matching preset's `print_host_webui` is pointed at its page. Presets are matched
on the start of the name, so one entry covers a whole family (`Voron 2.4` catches
`Voron 2.4 0.4 nozzle` and its siblings). Physical printers are bound too, where you have
them. Anything changed is copied to `device-tab-backup-<timestamp>/` inside the Orca
config directory first.

## Day to day

```bash
python3 install.py list             # printers, bound presets, and what answers right now
python3 install.py set voron --panel "http://192.168.1.50/"
python3 install.py remove old-ender
python3 install.py uninstall        # clear the presets, delete the pages
```

`list` prints the path of the config file, which is plain JSON and fine to edit by hand:

```json
{
  "printers": [
    {
      "id": "voron",
      "label": "Voron 2.4",
      "note": "in the shed",
      "host": "192.168.1.50",
      "camera": ":8080/?action=stream",
      "panel": ":80",
      "presets": ["Voron 2.4"]
    }
  ]
}
```

`camera` and `panel` take a full URL, or a `:port/path` shorthand filled in from `host`.
Leave either one out and that tab is not built — a camera with no web UI gets a single
Camera view with no tab bar, and a printer whose only interface is a web page gets the
reverse. Run `python3 install.py` after editing to rebuild the pages.

## Notes

The pages are static HTML with no dependencies and no server: the camera is an `<img>`
pointed at the printer's MJPEG endpoint, the control panel an `<iframe>`. Nothing is
proxied, so both have to be reachable from the machine running Orca, and a web UI that
sends `X-Frame-Options: DENY` will refuse to appear in the frame — the tab says so when
nothing loads. Printers that expose no HTTP interface at all (FlashForge's 8899 control
port, for instance) need something in front of them that does; point `panel` at that.

Tested against OrcaSlicer 2.4.2 on Linux. The other platforms use the same config-directory
discovery as the rest of my Orca tooling but have not been run there.

## Licence

MIT. Not affiliated with the OrcaSlicer project.
