"""Faux programmes (Claude Code, Codex, Python de Qwen3-TTS) et faux ComfyUI pour les tests."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from filmstudio.config import Reglages  # noqa: E402

PY = sys.executable

FAUX_CLAUDE = r'''#!{py}
import json, os, sys
if "--version" in sys.argv:
    print("9.9.9 (Claude Code)"); sys.exit(0)
prompt = sys.stdin.read()
open(os.environ["FAUX_JOURNAL"], "a", encoding="utf-8").write(json.dumps({{"outil": "claude", "args": sys.argv[1:],
    "cle_api": "ANTHROPIC_API_KEY" in os.environ, "prompt": prompt[:200]}}) + "\n")
if os.environ.get("FAUX_CLAUDE_DECONNECTE"):
    print(json.dumps({{"type": "result", "subtype": "success", "is_error": True,
                      "result": "Invalid API key · Please run /login"}})); sys.exit(1)
reponse = open(os.environ["FAUX_REPONSE"], encoding="utf-8").read() if "OK" not in prompt[-40:] else "OK"
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": reponse}}))
'''

FAUX_CODEX = r'''#!{py}
import json, os, re, sys
from PIL import Image
a = sys.argv[1:]
if "--version" in a:
    print("codex-cli 0.99.0"); sys.exit(0)
if a[:2] == ["login", "status"]:
    print("Logged in using ChatGPT"); sys.exit(0)
images = []
if "--image" in a:
    images = a[a.index("--image") + 1].split(",")
dossier = a[a.index("-C") + 1]
prompt = sys.stdin.read()
open(os.environ["FAUX_JOURNAL"], "a", encoding="utf-8").write(json.dumps({{"outil": "codex", "args": a,
    "images": images, "cle_api": "OPENAI_API_KEY" in os.environ, "prompt": prompt}}) + "\n")
if os.environ.get("FAUX_CODEX_LIMITE"):
    print("ERROR: You've hit your usage limit."); sys.exit(1)
compteur = os.environ.get("FAUX_CODEX_ECHECS")
restants = int(open(compteur).read() or 0) if compteur and os.path.exists(compteur) else 0
if restants > 0:
    open(compteur, "w").write(str(restants - 1))
    print("ERROR: You've hit your usage limit."); sys.exit(1)
nom = re.search(r"« ([\w-]+\.png) »", prompt).group(1)
taille = (1536, 1024) if "paysage" in prompt else (1024, 1536) if "portrait" in prompt else (1024, 1024)
Image.new("RGB", taille, (200, 170, 60)).save(os.path.join(dossier, nom))
print("Image enregistrée : " + nom)
'''

# Faux interpréteur Python de l'environnement Qwen3-TTS : il imite tts_worker.py (diagnostic et serveur)
FAUX_PYTHON_TTS = r'''#!{py}
import json, math, os, struct, sys, wave
a = sys.argv[1:]
if a and a[0] == "-c":
    print(json.dumps({{"torch": "2.5.1", "cuda": True, "gpu": "Faux GPU", "qwen_tts": "0.1.1"}})); sys.exit(0)
def ecrire(chemin, texte):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    duree = 0.3 + 0.04 * len(texte)
    with wave.open(chemin, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        n = int(24000 * duree)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 330 * i / 24000))) for i in range(n)))
    return duree
def traiter(t, ident=None):
    open(os.environ["FAUX_JOURNAL"], "a", encoding="utf-8").write(json.dumps({{"outil": "tts",
        "voix": t.get("voix_reference"), "textes": [p["texte"] for p in t["phrases"]]}}) + "\n")
    for k, p in enumerate(t["phrases"]):
        d = ecrire(p["sortie"], p["texte"])
        print(json.dumps({{"etat": "fait", "id": ident, "index": k, "cle": p.get("cle"), "fichier": p["sortie"],
                          "duree": d, "faites": k + 1, "total": len(t["phrases"])}}), flush=True)
    print(json.dumps({{"etat": "termine", "id": ident}}), flush=True)
if "--serveur" in a:
    for ligne in sys.stdin:
        t = json.loads(ligne)
        if t.get("commande") == "quitter":
            break
        traiter(t, t.get("id"))
else:
    traiter(json.load(open(a[-1], encoding="utf-8")))
'''


def _script(dossier: Path, nom: str, contenu: str) -> Path:
    p = dossier / nom
    p.write_text(contenu.format(py=PY), encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


@pytest.fixture
def faux(tmp_path, monkeypatch):
    """Faux outils + configuration isolée. Renvoie un objet avec reglages, journal, comfy…"""
    from tests.faux_comfy import FauxComfy

    bin_ = tmp_path / "bin"
    bin_.mkdir()
    claude = _script(bin_, "claude", FAUX_CLAUDE)
    codex = _script(bin_, "codex", FAUX_CODEX)
    python_tts = _script(bin_, "python_tts", FAUX_PYTHON_TTS)
    journal = tmp_path / "journal.jsonl"
    journal.touch()
    monkeypatch.setenv("FAUX_JOURNAL", str(journal))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ne-doit-pas-servir")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ne-doit-pas-servir")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex_home"))
    comfy = FauxComfy(tmp_path / "comfy")
    ref = tmp_path / "narrateur.wav"
    ref.write_bytes(b"RIFF")  # le faux TTS ne le lit pas
    r = Reglages(comfy_url=comfy.url, claude_commande=str(claude), codex_commande=str(codex),
                 tts_python=str(python_tts), tts_voix_reference=str(ref), tts_garder_minutes=0)

    class Faux:
        pass
    f = Faux()
    f.reglages, f.journal, f.comfy, f.tmp = r, journal, comfy, tmp_path

    def lire_journal():
        return [json.loads(l) for l in journal.read_text(encoding="utf-8").splitlines() if l.strip()]
    f.lire_journal = lire_journal
    yield f
    comfy.arreter()
    from filmstudio import tts
    tts.liberer()
