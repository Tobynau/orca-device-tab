#!/usr/bin/env python3
"""Builds the pages OrcaSlicer's Device tab loads.

Orca has no webcam setting for printers it does not speak to natively, and no
way at all to point the Device tab at two places. What it does have is
`print_host_webui` (PrintHost.cpp:81), a per-preset URL that the Device tab
loads into a webview and that takes `file:` as readily as `http:` - so each
printer gets a local page here instead.

Every page is the same shell: a switcher across all the printers, a Camera tab
holding the MJPEG stream and a Control tab holding the printer's own web UI in
a frame. Only the JSON block at the top differs between them, so a page still
works wherever it is copied to.
"""

import json
import re

SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body,#app{height:100%}
body{background:#1c1c1e;color:#e8e8ea;font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
     overflow:hidden}
#app{display:flex;flex-direction:column}
nav{display:flex;align-items:center;gap:6px;padding:8px 10px;background:#232326;
    border-bottom:1px solid #313135;flex:0 0 auto;overflow-x:auto}
nav a{display:flex;align-items:center;gap:7px;padding:6px 12px;border-radius:7px;
      color:#b6b6bb;text-decoration:none;white-space:nowrap;border:1px solid transparent;font-weight:500}
nav a:hover{background:#2c2c30;color:#e8e8ea}
nav a.on{background:#009688;color:#fff;border-color:#00b3a1}
nav .sp{flex:1}
button{background:#2c2c30;border:1px solid #3a3a3f;color:#b6b6bb;padding:6px 12px;
       border-radius:7px;cursor:pointer;font:inherit;font-weight:500}
button:hover{background:#35353a;color:#e8e8ea}
.tabs{display:flex;align-items:center;gap:4px;padding:6px 10px;background:#1f1f22;
      border-bottom:1px solid #313135;flex:0 0 auto}
.tabs button.tab{background:transparent;border-color:transparent;color:#8a8a90;padding:5px 14px}
.tabs button.tab:hover{color:#e8e8ea;background:#2a2a2e}
.tabs button.tab.on{background:#2c2c30;border-color:#3a3a3f;color:#e8e8ea}
.sp{flex:1}
.dot{width:7px;height:7px;border-radius:50%;background:#6b6b70;flex:0 0 auto}
.dot.live{background:#3ddc84;box-shadow:0 0 6px #3ddc8488}
.dot.off{background:#e5484d}
main{flex:1;min-height:0;position:relative}
.view{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:10px}
.view.hide{display:none}
.view img{max-width:100%;max-height:100%;object-fit:contain;border-radius:8px;background:#000;display:block}
.view img.hide{display:none}
.view iframe{width:100%;height:100%;border:0;border-radius:8px;background:#fff}
.view.panel{padding:0}
.msg{position:absolute;max-width:460px;text-align:center;color:#8a8a90;line-height:1.7;
     background:#1c1c1ecc;padding:14px 18px;border-radius:10px}
.msg.hide{display:none}
.msg b{display:block;color:#c9c9cf;font-size:15px;margin-bottom:4px}
.msg code{background:#2a2a2e;padding:2px 6px;border-radius:4px;color:#9fd8d0;font-size:12px}
.msg button{margin-top:10px}
footer{flex:0 0 auto;padding:6px 12px;background:#232326;border-top:1px solid #313135;
       color:#75757b;font-size:12px;display:flex;gap:10px;align-items:center;white-space:nowrap}
footer code{color:#9fd8d0;overflow:hidden;text-overflow:ellipsis}
footer .sp{flex:1}
.grid{display:grid;gap:10px;width:100%;height:100%}
.cell{position:relative;background:#000;border:1px solid #313135;border-radius:8px;overflow:hidden;
      display:flex;align-items:center;justify-content:center;min-height:0}
.cell img{width:100%;height:100%;object-fit:contain}
.cell .cap{position:absolute;left:0;bottom:0;right:0;padding:6px 9px;font-size:12px;font-weight:500;
           background:linear-gradient(180deg,transparent,rgba(0,0,0,.88) 55%);display:flex;align-items:center;
           gap:7px;z-index:2;text-shadow:0 1px 3px #000,0 0 8px #000}
.cell a{color:#e8e8ea;text-decoration:none}
.cell a:hover{text-decoration:underline}
.cell .ip{color:#8a8a90}
"""

APP = """
(function () {
  var CFG = window.DEVICE_TAB || {printers: [], active: ""};
  var PRINTERS = CFG.printers || [];
  var ACTIVE = CFG.active || "";
  var here = null;
  for (var i = 0; i < PRINTERS.length; i++) if (PRINTERS[i].id === ACTIVE) here = PRINTERS[i];

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function recall(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
  function store(key, value) { try { localStorage.setItem(key, value); } catch (e) {} }

  // An MJPEG stream is a never-ending response, so a dropped printer shows up
  // as an image error - reconnect, backing off, until it comes back.
  function stream(img, url, onstate) {
    var timer = null, tries = 0, stopped = true;
    function bust() { return url + (url.indexOf("?") > -1 ? "&" : "?") + "_t=" + Date.now(); }
    img.onload = function () { tries = 0; img.classList.remove("hide"); onstate("live", 0); };
    img.onerror = function () {
      if (stopped) return;
      tries++;
      img.classList.add("hide");
      onstate("off", tries);
      timer = setTimeout(function () { if (!stopped) img.src = bust(); },
                         Math.min(2000 + tries * 2000, 15000));
    };
    return {
      start: function () {
        if (!stopped) return;
        stopped = false;
        clearTimeout(timer);
        onstate("wait", 0);
        img.src = bust();
      },
      // Dropping src closes the connection: cameras that only serve one or two
      // clients stay free while the Control tab is up.
      stop: function () {
        stopped = true;
        clearTimeout(timer);
        img.classList.add("hide");
        img.removeAttribute("src");
      }
    };
  }

  function nav() {
    var bar = el("nav"), mine = null;
    PRINTERS.forEach(function (p) {
      var a = el("a", p.id === ACTIVE ? "on" : "");
      a.href = p.id + ".html";
      var dot = el("span", "dot");
      a.appendChild(dot);
      a.appendChild(document.createTextNode(p.label));
      if (p.id === ACTIVE) mine = dot;
      bar.appendChild(a);
    });
    if (PRINTERS.length > 1) {
      var all = el("a", ACTIVE === "all" ? "on" : "", "All");
      all.href = "all.html";
      bar.appendChild(all);
    }
    bar.appendChild(el("span", "sp"));
    var reload = el("button", "", "Reload");
    reload.onclick = function () { location.reload(); };
    bar.appendChild(reload);
    return {bar: bar, dot: mine};
  }

  function cameraView(p, dots, state) {
    var view = el("div", "view camera");
    var img = el("img", "hide");
    img.alt = "";
    var msg = el("div", "msg hide");
    var head = el("b", "", "No picture from " + p.label);
    msg.appendChild(head);
    msg.appendChild(document.createTextNode("Retrying "));
    msg.appendChild(el("code", "", p.camera));
    view.appendChild(img);
    view.appendChild(msg);
    var s = stream(img, p.camera, function (what, tries) {
      var cls = what === "live" ? "dot live" : (what === "off" ? "dot off" : "dot");
      dots.forEach(function (d) { if (d) d.className = cls; });
      if (what === "live") { msg.classList.add("hide"); state("live"); }
      else if (what === "off") { msg.classList.remove("hide"); state("offline, retry " + tries); }
      else { state("connecting"); }
    });
    return {node: view, show: s.start, hide: s.stop};
  }

  function panelView(p) {
    var view = el("div", "view panel");
    var frame = document.createElement("iframe");
    var msg = el("div", "msg hide");
    msg.appendChild(el("b", "", "Nothing loaded from the control panel"));
    msg.appendChild(document.createTextNode("Tried "));
    msg.appendChild(el("code", "", p.panel));
    msg.appendChild(el("br"));
    msg.appendChild(document.createTextNode(
      "The printer may be off, or its web UI may refuse to be framed."));
    var retry = el("button", "", "Try again");
    msg.appendChild(retry);
    view.appendChild(frame);
    view.appendChild(msg);

    var timer = null, loaded = false;
    function load() {
      loaded = false;
      msg.classList.add("hide");
      frame.src = p.panel;
      clearTimeout(timer);
      timer = setTimeout(function () { if (!loaded) msg.classList.remove("hide"); }, 8000);
    }
    frame.onload = function () { loaded = true; clearTimeout(timer); msg.classList.add("hide"); };
    retry.onclick = load;
    return {
      node: view,
      show: function () { if (!frame.src) load(); },
      hide: function () { clearTimeout(timer); }
    };
  }

  function printerPage(p, navDot) {
    var wrap = document.createDocumentFragment();
    var tabs = el("div", "tabs");
    var main = el("main");
    var foot = el("footer");
    var footDot = el("span", "dot");
    var footState = el("span", "", "");
    var footUrl = el("code", "", "");
    foot.appendChild(footDot);
    foot.appendChild(footState);
    foot.appendChild(el("span", "sp"));
    if (p.note) foot.appendChild(el("span", "", p.note));
    foot.appendChild(footUrl);

    var views = {};
    if (p.camera) {
      views.camera = cameraView(p, [navDot, footDot], function (text) { footState.textContent = text; });
    }
    if (p.panel) views.panel = panelView(p);

    var order = [];
    if (views.camera) order.push(["camera", "Camera", p.camera]);
    if (views.panel) order.push(["panel", p.panel_label || "Control", p.panel]);

    if (!order.length) {
      var none = el("div", "view");
      var msg = el("div", "msg");
      msg.appendChild(el("b", "", "Nothing configured for " + p.label));
      msg.appendChild(document.createTextNode("Give it a camera or a control panel with "));
      msg.appendChild(el("code", "", "install.py set " + p.id + " --camera URL --panel URL"));
      none.appendChild(msg);
      main.appendChild(none);
      wrap.appendChild(main);
      return wrap;
    }

    var buttons = {};
    function show(name) {
      order.forEach(function (t) {
        var on = t[0] === name;
        buttons[t[0]].className = on ? "tab on" : "tab";
        views[t[0]].node.classList.toggle("hide", !on);
        if (on) { footUrl.textContent = t[2]; views[t[0]].show(); }
        else views[t[0]].hide();
      });
      if (name !== "camera" && views.camera) { footDot.className = "dot"; footState.textContent = ""; }
      store("device-tab:" + p.id, name);
    }

    order.forEach(function (t) {
      var b = el("button", "tab", t[1]);
      b.onclick = function () { show(t[0]); };
      buttons[t[0]] = b;
      tabs.appendChild(b);
      main.appendChild(views[t[0]].node);
    });

    if (order.length > 1) wrap.appendChild(tabs);
    wrap.appendChild(main);
    wrap.appendChild(foot);

    var want = recall("device-tab:" + p.id);
    var start = views[want] ? want : order[0][0];
    setTimeout(function () { show(start); }, 0);
    return wrap;
  }

  function allPage() {
    var cams = PRINTERS.filter(function (p) { return !!p.camera; });
    var main = el("main");
    var view = el("div", "view");
    var grid = el("div", "grid");
    var cols = Math.ceil(Math.sqrt(Math.max(cams.length, 1)));
    var rows = Math.ceil(Math.max(cams.length, 1) / cols);
    grid.style.gridTemplateColumns = "repeat(" + cols + ",1fr)";
    grid.style.gridTemplateRows = "repeat(" + rows + ",1fr)";
    cams.forEach(function (p) {
      var cell = el("div", "cell");
      var img = el("img", "hide");
      img.alt = "";
      var cap = el("div", "cap");
      var dot = el("span", "dot");
      var link = el("a", "", p.label);
      link.href = p.id + ".html";
      cap.appendChild(dot);
      cap.appendChild(link);
      cap.appendChild(el("span", "sp"));
      cap.appendChild(el("span", "ip", p.host || ""));
      cell.appendChild(img);
      cell.appendChild(cap);
      grid.appendChild(cell);
      stream(img, p.camera, function (what) {
        dot.className = what === "live" ? "dot live" : (what === "off" ? "dot off" : "dot");
      }).start();
    });
    if (!cams.length) {
      var msg = el("div", "msg");
      msg.appendChild(el("b", "", "No cameras configured"));
      msg.appendChild(document.createTextNode("Add one with "));
      msg.appendChild(el("code", "", "install.py add"));
      view.appendChild(msg);
    }
    view.appendChild(grid);
    main.appendChild(view);
    return main;
  }

  var app = document.getElementById("app");
  var top = nav();
  app.appendChild(top.bar);
  app.appendChild(here ? printerPage(here, top.dot) : allPage());
})();
"""

SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title>
<style>%(css)s</style>
</head>
<body>
<div id="app"></div>
<script>window.DEVICE_TAB = %(data)s;</script>
<script>%(app)s</script>
</body>
</html>
"""

# only what the page itself reads, so a page carries no more than it needs
FIELDS = ("id", "label", "note", "host", "camera", "panel", "panel_label")


def check_id(printer_id):
    if not SLUG.match(printer_id or ""):
        raise ValueError("id %r must be lower-case letters, digits, - or _" % printer_id)
    return printer_id


def render(printers, active):
    slim = []
    for p in printers:
        check_id(p.get("id"))
        slim.append({k: p[k] for k in FIELDS if p.get(k)})
    title = "All printers"
    for p in printers:
        if p["id"] == active:
            title = p.get("label", active)
    return SHELL % {
        "title": title,
        "css": CSS,
        "app": APP,
        "data": json.dumps({"active": active, "printers": slim}, indent=1),
    }


def write(printers, out_dir):
    """Writes one page per printer plus the all-cameras grid. Returns the paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for p in printers:
        path = out_dir / ("%s.html" % check_id(p["id"]))
        path.write_text(render(printers, p["id"]), encoding="utf-8")
        written.append(path)
    path = out_dir / "all.html"
    path.write_text(render(printers, "all"), encoding="utf-8")
    written.append(path)
    return written
