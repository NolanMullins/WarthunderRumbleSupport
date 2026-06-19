"""
verify_app.py — tiny local web app to hand-verify / correct ground truth, one frame at a time.

Zero extra dependencies (Python stdlib http.server). Serves a single "card" per frame: the HUD
image, the current ground-truth value per weapon, and the CURRENT detector's read (from the
cached re-detection) so disagreements are obvious. You confirm, correct, or mark a row absent;
feedback is saved to tests/feedback/<clip>.json which the test side can read + apply.

Run:
  python tools/verify_app.py            # serves on http://localhost:8765
  python tools/verify_app.py --port N

Then open the URL. Pick a clip, step through frames (or jump to disagreements), and mark each.
"""
import os
import sys
import json
import argparse
import http.server
import urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "tests"))
from lib import recordings as R       # noqa: E402
from lib import groundtruth as G      # noqa: E402
from lib import detect as D           # noqa: E402

FEEDBACK_DIR = os.path.normpath(os.path.join(_HERE, "..", "tests", "feedback"))

_CLIPS = {c.key: c for c in R.discover()}
_DET_CACHE = {}   # clip_key -> redetect result (lazy)


def _safe(key):
    return key.replace("/", "__").replace("\\", "__")


def _feedback_path(key):
    return os.path.join(FEEDBACK_DIR, _safe(key) + ".json")


def _load_feedback(key):
    p = _feedback_path(key)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {"clip": key, "frames": {}}


def _save_feedback(key, data):
    os.makedirs(FEEDBACK_DIR, exist_ok=True)
    with open(_feedback_path(key), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _det_reads(clip):
    if clip.key not in _DET_CACHE:
        try:
            _DET_CACHE[clip.key] = D.redetect(clip).get("reads", [])
        except Exception:
            _DET_CACHE[clip.key] = []
    return _DET_CACHE[clip.key]


def _clip_summary():
    out = []
    for key, c in sorted(_CLIPS.items()):
        if not G.has_gt(key):
            continue
        n = len(c.png_paths())
        gt = G.load(key, n)
        fb = _load_feedback(key)
        out.append({"key": key, "frames": n, "weapons": c.weapons,
                    "unverified": gt.unverified, "marked": len(fb.get("frames", {}))})
    return out


def _frame_payload(key, n):
    c = _CLIPS[key]
    paths = c.png_paths()
    n = max(0, min(n, len(paths) - 1))
    gt = G.load(key, len(paths))
    reads = _det_reads(c)
    det = reads[n] if n < len(reads) else {}
    fb = _load_feedback(key).get("frames", {}).get(str(n), {})
    rows = []
    for wp in c.weapons:
        rows.append({
            "weapon": wp,
            "gt_value": gt.value_at(wp, n),       # None in a transition zone
            "gt_present": gt.is_present(wp, n),
            "det_value": det.get(wp),             # None = detector missed the row
            "feedback": fb.get(wp),               # prior mark for this cell
        })
    # disagreement = detector read differs from GT value, or detector missed a present row
    disagree = []
    for r in rows:
        if r["gt_present"] and r["det_value"] is None:
            disagree.append(r["weapon"])
        elif r["gt_value"] is not None and r["det_value"] is not None \
                and r["det_value"] != r["gt_value"]:
            disagree.append(r["weapon"])
    return {"clip": key, "n": n, "total": len(paths), "rows": rows,
            "disagree": disagree}


def _next_disagreement(key, start):
    c = _CLIPS[key]
    total = len(c.png_paths())
    gt = G.load(key, total)
    reads = _det_reads(c)
    for n in range(start + 1, total):
        det = reads[n] if n < len(reads) else {}
        for wp in c.weapons:
            present = gt.is_present(wp, n)
            v = gt.value_at(wp, n)
            d = det.get(wp)
            if (present and d is None) or (v is not None and d is not None and d != v):
                return n
    return -1


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>GT Verify</title>
<style>
 body{font-family:Segoe UI,system-ui,sans-serif;background:#0f1216;color:#e6edf3;margin:0;padding:16px}
 h2{margin:4px 0 12px}
 .wrap{display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap}
 .card{background:#171c22;border:1px solid #262d36;border-radius:10px;padding:14px;min-width:360px}
 img{image-rendering:pixelated;background:#000;border:1px solid #333;width:400px;height:auto}
 select,button,input{font:inherit;background:#1e252d;color:#e6edf3;border:1px solid #333;border-radius:6px;padding:5px 8px}
 button{cursor:pointer}
 button.primary{background:#ff7a18;border-color:#ff7a18;color:#111;font-weight:600}
 table{border-collapse:collapse;width:100%}
 td,th{padding:6px 8px;border-bottom:1px solid #262d36;text-align:left;font-size:14px}
 .bad{color:#e5484d;font-weight:600}
 .ok{color:#33d17a}
 .muted{color:#8b97a4}
 .pill{display:inline-block;padding:1px 7px;border-radius:10px;font-size:12px}
 .p-correct{background:#1c3a28;color:#33d17a}
 .p-value{background:#3a2f1c;color:#ffb455}
 .p-absent{background:#3a1c1c;color:#e5484d}
 .nav{display:flex;gap:8px;align-items:center;margin:10px 0;flex-wrap:wrap}
 .vinput{width:64px}
</style></head><body>
<h2>Ground-truth verifier</h2>
<div class="nav">
 <select id="clip"></select>
 <button onclick="go(-1)">&larr; Prev</button>
 <input id="jump" class="vinput" type="number" min="0" value="0"><button onclick="jump()">Go</button>
 <button onclick="go(1)">Next &rarr;</button>
 <button onclick="nextDis()">Next disagreement &raquo;</button>
 <span id="pos" class="muted"></span>
</div>
<div class="wrap">
 <div class="card"><img id="img" src=""></div>
 <div class="card" style="flex:1">
  <div id="dis" class="muted" style="margin-bottom:8px"></div>
  <table id="tbl"><thead><tr><th>Weapon</th><th>GT</th><th>Detector</th><th>Mark</th><th>Saved</th></tr></thead><tbody></tbody></table>
  <div class="nav"><button class="primary" onclick="allCorrect()">All correct &amp; next &rarr;</button></div>
 </div>
</div>
<script>
let clip=null, n=0, total=0, rows=[];
async function loadClips(){
 const cs=await (await fetch('/api/clips')).json();
 const sel=document.getElementById('clip'); sel.innerHTML='';
 cs.forEach(c=>{const o=document.createElement('option');o.value=c.key;
   o.textContent=`${c.key.split('/').pop()}  [${c.frames}f, ${c.marked} marked${c.unverified?', UNVERIFIED':''}]`;
   sel.appendChild(o);});
 sel.onchange=()=>{clip=sel.value;n=0;render();};
 clip=cs[0].key; render();
}
async function render(){
 const d=await (await fetch(`/api/frame?clip=${encodeURIComponent(clip)}&n=${n}`)).json();
 n=d.n; total=d.total; rows=d.rows;
 document.getElementById('img').src=`/img?clip=${encodeURIComponent(clip)}&n=${n}`;
 document.getElementById('pos').textContent=`frame ${n} / ${total-1}`;
 document.getElementById('jump').value=n;
 document.getElementById('dis').innerHTML = d.disagree.length
   ? `<span class="bad">Disagreement:</span> ${d.disagree.join(', ')}` : `<span class="ok">detector matches GT</span>`;
 const tb=document.querySelector('#tbl tbody'); tb.innerHTML='';
 rows.forEach(r=>{
  const tr=document.createElement('tr');
  const gt = r.gt_present ? (r.gt_value===null?'<span class="muted">(transition)</span>':r.gt_value) : '<span class="muted">absent</span>';
  const det = r.det_value===null?'<span class="bad">MISSED</span>':r.det_value;
  const mismatch = (r.gt_present&&r.det_value===null)||(r.gt_value!==null&&r.det_value!==null&&r.det_value!==r.gt_value);
  let saved='';
  if(r.feedback){const s=r.feedback.status; saved=`<span class="pill p-${s}">${s}${s==='value'?(': '+r.feedback.value):''}</span>`;}
  tr.innerHTML=`<td><b>${r.weapon}</b></td><td>${gt}</td><td class="${mismatch?'bad':''}">${det}</td>
   <td><button onclick="mark('${r.weapon}','correct')">✓ correct</button>
       <input class="vinput" id="v_${r.weapon}" type="number" placeholder="val">
       <button onclick="markVal('${r.weapon}')">set</button>
       <button onclick="mark('${r.weapon}','absent')">absent</button></td>
   <td id="s_${r.weapon}">${saved}</td>`;
  tb.appendChild(tr);
 });
}
async function mark(wp,status,value){
 await fetch('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({clip,frame:n,weapon:wp,status,value})});
 const el=document.getElementById('s_'+wp);
 el.innerHTML=`<span class="pill p-${status}">${status}${status==='value'?(': '+value):''}</span>`;
}
function markVal(wp){const v=document.getElementById('v_'+wp).value; if(v==='')return; mark(wp,'value',parseInt(v));}
async function allCorrect(){for(const r of rows){await mark(r.weapon,'correct');} go(1);}
function go(d){n=Math.max(0,Math.min(total-1,n+d));render();}
function jump(){n=parseInt(document.getElementById('jump').value)||0;render();}
async function nextDis(){
 const r=await (await fetch(`/api/next_disagreement?clip=${encodeURIComponent(clip)}&n=${n}`)).json();
 if(r.n>=0){n=r.n;render();}else{document.getElementById('dis').innerHTML='<span class="ok">no more disagreements</span>';}
}
loadClips();
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, "application/json", json.dumps(obj).encode("utf-8"))

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/":
            return self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
        if u.path == "/api/clips":
            return self._json(_clip_summary())
        if u.path == "/api/frame":
            key = q.get("clip", [""])[0]; n = int(q.get("n", ["0"])[0])
            if key not in _CLIPS:
                return self._json({"error": "unknown clip"}, 404)
            return self._json(_frame_payload(key, n))
        if u.path == "/api/next_disagreement":
            key = q.get("clip", [""])[0]; n = int(q.get("n", ["0"])[0])
            return self._json({"n": _next_disagreement(key, n)})
        if u.path == "/img":
            key = q.get("clip", [""])[0]; n = int(q.get("n", ["0"])[0])
            c = _CLIPS.get(key)
            if not c:
                return self._json({"error": "unknown clip"}, 404)
            paths = c.png_paths()
            n = max(0, min(n, len(paths) - 1))
            with open(paths[n], "rb") as f:
                return self._send(200, "image/png", f.read())
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/feedback":
            ln = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(ln) or b"{}")
            key = body.get("clip")
            if key not in _CLIPS:
                return self._json({"error": "unknown clip"}, 404)
            fb = _load_feedback(key)
            fr = fb.setdefault("frames", {})
            cell = fr.setdefault(str(body["frame"]), {})
            entry = {"status": body["status"]}
            if body["status"] == "value":
                entry["value"] = body.get("value")
            cell[body["weapon"]] = entry
            _save_feedback(key, fb)
            return self._json({"ok": True})
        return self._json({"error": "not found"}, 404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    clips = [k for k in _CLIPS if G.has_gt(k)]
    if not clips:
        print("No recordings with ground truth under recordings/. Nothing to verify.")
        return
    print(f"Verifier on http://localhost:{args.port}  ({len(clips)} clips)")
    print("Feedback -> tests/feedback/<clip>.json   (Ctrl+C to stop)")
    http.server.HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
