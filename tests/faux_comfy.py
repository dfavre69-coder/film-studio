"""Un faux ComfyUI (API HTTP) pour les tests : il « rend » une vidéo avec ffmpeg à partir de l'image reçue."""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

from filmstudio.video import chemin_ffmpeg

# Workflow « image → vidéo » au format API, inspiré du modèle LTX de ComfyUI
WORKFLOW_VIDEO = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "ltx-2.5.safetensors"}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat", "clip": ["1", 1]},
          "_meta": {"title": "CLIP Text Encode (Prompt)"}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry", "clip": ["1", 1]},
          "_meta": {"title": "CLIP Text Encode (Prompt)"}},
    "4": {"class_type": "LoadImage", "inputs": {"image": "example.png"}},
    "5": {"class_type": "LTXVImgToVideo", "inputs": {"positive": ["2", 0], "negative": ["3", 0], "vae": ["1", 2],
                                                      "image": ["4", 0], "width": 768, "height": 512,
                                                      "length": 97, "batch_size": 1, "strength": 1.0}},
    "6": {"class_type": "LTXVConditioning", "inputs": {"positive": ["5", 0], "negative": ["5", 1],
                                                        "frame_rate": 25.0}},
    "7": {"class_type": "KSampler", "inputs": {"seed": 42, "steps": 20, "cfg": 3.0, "model": ["1", 0],
                                                "positive": ["6", 0], "negative": ["6", 1], "latent_image": ["5", 2],
                                                "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["1", 2]}},
    "9": {"class_type": "CreateVideo", "inputs": {"images": ["8", 0], "fps": 24.0}},
    "10": {"class_type": "SaveVideo", "inputs": {"video": ["9", 0], "filename_prefix": "video/ltx",
                                                  "format": "auto", "codec": "auto"}},
}

WORKFLOW_IMAGE = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "flux.safetensors"}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat", "clip": ["1", 1]}},
    "3": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
    "4": {"class_type": "KSampler", "inputs": {"seed": 1, "model": ["1", 0], "positive": ["2", 0],
                                                "negative": ["2", 0], "latent_image": ["3", 0]}},
    "5": {"class_type": "VAEDecode", "inputs": {"samples": ["4", 0], "vae": ["1", 2]}},
    "6": {"class_type": "SaveImage", "inputs": {"images": ["5", 0], "filename_prefix": "img"}},
}


class FauxComfy:
    def __init__(self, dossier: Path, duree_rendu: float = 0.3):
        self.dossier = Path(dossier)
        (self.dossier / "input").mkdir(parents=True, exist_ok=True)
        (self.dossier / "output").mkdir(parents=True, exist_ok=True)
        self.historique: dict[str, dict] = {}
        self.recus: list[dict] = []
        self.liberations = 0
        self.duree_rendu = duree_rendu
        self.object_info: dict = {}
        self.pannes = 0          # nombre de prochains rendus qui échouent (« out of memory »)
        faux = self

        class Gestion(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, d, code=200):
                corps = json.dumps(d).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(corps)))
                self.end_headers()
                self.wfile.write(corps)

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                if u.path == "/system_stats":
                    return self._json({"system": {"comfyui_version": "0.9.9"},
                                       "devices": [{"name": "Faux GPU", "vram_total": 20 * 2**30,
                                                    "vram_free": 18 * 2**30}]})
                if u.path == "/object_info":
                    return self._json(faux.object_info)
                if u.path == "/queue":
                    return self._json({"queue_running": [], "queue_pending": []})
                if u.path.startswith("/history/"):
                    pid = u.path.split("/")[-1]
                    return self._json({pid: faux.historique[pid]} if pid in faux.historique else {})
                if u.path == "/view":
                    q = urllib.parse.parse_qs(u.query)
                    f = faux.dossier / q["type"][0] / q.get("subfolder", [""])[0] / q["filename"][0]
                    contenu = f.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(contenu)))
                    self.end_headers()
                    self.wfile.write(contenu)
                    return
                self._json({"error": "inconnu"}, 404)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                corps = self.rfile.read(n)
                if self.path == "/upload/image":
                    m = re.search(rb'filename="([^"]+)"\r\nContent-Type: [^\r]+\r\n\r\n', corps)
                    nom = m.group(1).decode()
                    debut = m.end()
                    fin = corps.index(b"\r\n--", debut)
                    (faux.dossier / "input" / nom).write_bytes(corps[debut:fin])
                    return self._json({"name": nom, "subfolder": "", "type": "input"})
                if self.path == "/prompt":
                    d = json.loads(corps)
                    graphe = d["prompt"]
                    for nid, noeud in graphe.items():
                        if noeud["class_type"] == "LoadImage":
                            if not (faux.dossier / "input" / noeud["inputs"]["image"]).exists():
                                return self._json({"error": {"message": "Prompt outputs failed validation"},
                                                   "node_errors": {nid: {"class_type": "LoadImage", "errors": [
                                                       {"message": "Value not in list",
                                                        "details": noeud["inputs"]["image"]}]}}}, 400)
                    pid = uuid.uuid4().hex
                    faux.recus.append(graphe)
                    threading.Thread(target=faux._rendre, args=(pid, graphe), daemon=True).start()
                    return self._json({"prompt_id": pid, "number": 1, "node_errors": {}})
                if self.path == "/free":
                    faux.liberations += 1
                return self._json({})

        self.serveur = ThreadingHTTPServer(("127.0.0.1", 0), Gestion)
        self.url = f"http://127.0.0.1:{self.serveur.server_address[1]}"
        threading.Thread(target=self.serveur.serve_forever, daemon=True).start()

    def arreter(self):
        self.serveur.shutdown()

    def _rendre(self, pid: str, graphe: dict):
        time.sleep(self.duree_rendu)
        if self.pannes > 0:
            self.pannes -= 1
            self.historique[pid] = {"outputs": {}, "status": {"status_str": "error", "completed": False, "messages": [
                ["execution_error", {"node_type": "SamplerCustomAdvanced",
                                     "exception_message": "torch.OutOfMemoryError: CUDA out of memory."}]]}}
            return
        image = next((n["inputs"]["image"] for n in graphe.values() if n["class_type"] == "LoadImage"), None)
        sortie = self.dossier / "output"
        if image is None:  # workflow d'images
            largeur = next(n["inputs"]["width"] for n in graphe.values() if "width" in n["inputs"])
            hauteur = next(n["inputs"]["height"] for n in graphe.values() if "height" in n["inputs"])
            nom = f"img_{pid[:6]}.png"
            Image.new("RGB", (largeur, hauteur), (40, 90, 160)).save(sortie / nom)
            self.historique[pid] = {"outputs": {"6": {"images": [{"filename": nom, "subfolder": "",
                                                                     "type": "output"}]}},
                                    "status": {"status_str": "success", "completed": True}}
            return
        def titre(n):
            return (n.get("_meta") or {}).get("title", "")
        prim = {titre(n): n["inputs"].get("value") for n in graphe.values()
                if n["class_type"].startswith("Primitive")}
        if "Duration" in prim:  # modèle officiel LTX-2.5 : réglages dans des nœuds Primitive titrés
            fps, largeur, hauteur = prim["Frame Rate"], prim["Width"], prim["Height"]
            duree = prim["Duration"]
            assert all(isinstance(x, int) for x in (fps, largeur, hauteur, duree)), prim
        else:
            images = next(n["inputs"]["length"] for n in graphe.values() if "length" in n["inputs"])
            fps = next(n["inputs"]["fps"] for n in graphe.values() if n["class_type"] == "CreateVideo")
            largeur = next(n["inputs"]["width"] for n in graphe.values() if "width" in n["inputs"])
            hauteur = next(n["inputs"]["height"] for n in graphe.values() if "height" in n["inputs"])
            duree = images / fps
        nom = f"ltx_{pid[:6]}.mp4"
        (sortie / "video").mkdir(exist_ok=True)
        subprocess.run([chemin_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-loop", "1",
                        "-i", str(self.dossier / "input" / image), "-f", "lavfi",
                        "-i", f"sine=frequency=220:duration={duree}", "-vf",
                        f"scale={largeur}:{hauteur},fps={fps},format=yuv420p", "-t", f"{duree}",
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
                        str(sortie / "video" / nom)], check=True)
        self.historique[pid] = {"outputs": {"10": {"images": [{"filename": nom, "subfolder": "video",
                                                                  "type": "output"}], "animated": [True]}},
                                "status": {"status_str": "success", "completed": True}}
