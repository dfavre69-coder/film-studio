from __future__ import annotations

import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from filmstudio import comfy_local
from filmstudio.demo import film_exemple
from filmstudio.production import Production
from tests.faux_comfy import WORKFLOW_VIDEO

RACINE = Path(__file__).resolve().parents[1]

# Faux « main.py » de ComfyUI : démarre le faux serveur sur le port demandé
FAUX_MAIN = '''
import sys, time
sys.path.insert(0, {racine!r})
from pathlib import Path
import tests.faux_comfy as fc
from http.server import ThreadingHTTPServer
port = int(sys.argv[sys.argv.index("--port") + 1])
print("Starting server", flush=True)
if "--planter" in sys.argv:
    print("RuntimeError: CUDA driver too old", flush=True); sys.exit(1)
time.sleep(1.0)
faux = fc.FauxComfy(Path(__file__).parent / "donnees")
faux.serveur.shutdown(); faux.serveur.server_close()
srv = ThreadingHTTPServer(("127.0.0.1", port), faux.serveur.RequestHandlerClass)
srv.serve_forever()
'''


def _port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fausse_installation(dossier: Path) -> None:
    (dossier / "ComfyUI").mkdir(parents=True)
    (dossier / "ComfyUI" / "main.py").write_text(FAUX_MAIN.format(racine=str(RACINE)), encoding="utf-8")
    bin_ = dossier / "venv_comfy" / ("Scripts" if sys.platform == "win32" else "bin")
    bin_.mkdir(parents=True)
    os.symlink(sys.executable, bin_ / ("python.exe" if sys.platform == "win32" else "python"))


def test_trouver_installation(tmp_path):
    assert comfy_local.trouver_installation(tmp_path) is None
    _fausse_installation(tmp_path / "a")
    inst = comfy_local.trouver_installation(tmp_path / "a")
    assert inst.racine == tmp_path / "a" / "ComfyUI" and "venv_comfy" in str(inst.python)
    # ComfyUI portable pour Windows
    p = tmp_path / "portable"
    (p / "ComfyUI").mkdir(parents=True)
    (p / "ComfyUI" / "main.py").write_text("")
    (p / "python_embeded").mkdir()
    (p / "python_embeded" / "python.exe").write_text("")
    assert comfy_local.trouver_installation(p).python.name == "python.exe"


def test_commandes_installation(tmp_path):
    etapes = comfy_local.commandes_installation(tmp_path, "python3")
    assert etapes[0][1] == ["python3", "-m", "venv", str(tmp_path / "venv_comfy")]
    derniere = etapes[-1][1]
    # comfy-cli reçoit le sous-dossier ComfyUI : jamais le dossier qui contient venv_comfy
    assert derniere[1:] == [f"--workspace={tmp_path / 'ComfyUI'}", "install", "--nvidia"]
    assert "comfy" in Path(derniere[0]).name


def test_demarrage_automatique_puis_arret(faux, tmp_path):
    _fausse_installation(tmp_path / "comfyui")
    port = _port_libre()
    url = f"http://127.0.0.1:{port}"
    from filmstudio import comfy
    comfy.enregistrer_workflow(comfy.Workflow("ltx", "video", WORKFLOW_VIDEO, comfy.deviner_carte(WORKFLOW_VIDEO)))
    r = faux.reglages.copie(comfy_url=url, comfy_dossier=str(tmp_path / "comfyui"), comfy_workflow_video="ltx",
                            moteur_images="manuel")
    film = film_exemple()
    film.enregistrer(tmp_path / "film")
    img = film.sous_dossier("images") / "plan_01.png"
    from PIL import Image
    Image.new("RGB", (1280, 720), (10, 20, 30)).save(img)
    film.plans[0].image = film.relatif(img)
    etats = []
    assert not comfy_local.repond(url)
    b = Production(film, r, etat=etats.append).videos([0])
    assert not b.problemes, b.texte()
    assert film.plans[0].prise and any("Démarrage de ComfyUI" in e for e in etats)
    assert comfy_local.GESTIONNAIRE.actif
    # Déjà démarré : rien à refaire
    assert comfy_local.GESTIONNAIRE.assurer(r) is False
    comfy_local.GESTIONNAIRE.arreter()
    assert not comfy_local.repond(url)


def test_demarrage_qui_echoue(tmp_path):
    _fausse_installation(tmp_path / "c")
    r = comfy_local.Reglages(comfy_url=f"http://127.0.0.1:{_port_libre()}", comfy_dossier=str(tmp_path / "c"),
                             comfy_options="--planter")
    with pytest.raises(comfy_local.ErreurComfyLocal, match="CUDA driver too old"):
        comfy_local.GESTIONNAIRE.assurer(r)


def test_pas_d_installation(tmp_path):
    r = comfy_local.Reglages(comfy_url=f"http://127.0.0.1:{_port_libre()}", comfy_dossier=str(tmp_path))
    with pytest.raises(comfy_local.ErreurComfyLocal, match="Installer ComfyUI"):
        comfy_local.GESTIONNAIRE.assurer(r)
    with pytest.raises(comfy_local.ErreurComfyLocal, match="Démarrer ComfyUI automatiquement"):
        comfy_local.GESTIONNAIRE.assurer(r.copie(comfy_demarrage_auto=False))


class _Serveur:
    """Sert un fichier avec prise en charge de Range ; peut couper la première réponse en plein milieu."""

    def __init__(self, contenu: bytes, couper: bool):
        self.contenu, self.couper, self.requetes = contenu, couper, []
        srv = self

        class G(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_HEAD(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(srv.contenu)))
                self.end_headers()

            def do_GET(self):
                srv.requetes.append(self.headers.get("Range"))
                debut = int(self.headers["Range"].split("=")[1].split("-")[0]) if self.headers.get("Range") else 0
                corps = srv.contenu[debut:]
                self.send_response(206 if debut else 200)
                self.send_header("Content-Length", str(len(corps)))
                self.end_headers()
                if srv.couper:
                    srv.couper = False
                    self.wfile.write(corps[: len(corps) // 3])
                    self.wfile.flush()
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                self.wfile.write(corps)

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), G)
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}/f.bin"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()


def test_telechargement_avec_reprise(tmp_path, monkeypatch):
    monkeypatch.setattr(comfy_local.time, "sleep", lambda s: None)
    contenu = os.urandom(3 * 2**20 + 123)
    srv = _Serveur(contenu, couper=True)
    _fausse_installation(tmp_path / "c")
    modeles = [comfy_local.Modele("modele.safetensors", srv.url, "diffusion_models")]
    etats = []
    fichiers = comfy_local.telecharger_modeles(tmp_path / "c", modeles, etats.append)
    assert fichiers[0].read_bytes() == contenu
    assert fichiers[0] == tmp_path / "c" / "ComfyUI" / "models" / "diffusion_models" / "modele.safetensors"
    assert srv.requetes[0] is None and srv.requetes[1].startswith("bytes=")   # reprise, pas de redémarrage
    assert not list(fichiers[0].parent.glob("*.part"))
    # Déjà présent : rien n'est retéléchargé
    n = len(srv.requetes)
    comfy_local.telecharger_modeles(tmp_path / "c", modeles)
    assert len(srv.requetes) == n
    srv.http.shutdown()


def test_liste_des_modeles_ltx():
    noms = {m.sous_dossier for m in comfy_local.MODELES_LTX}
    assert noms == {"diffusion_models", "text_encoders", "vae", "latent_upscale_models"}
    assert all(m.url.startswith("https://huggingface.co/") and "/resolve/main/" in m.url for m in comfy_local.MODELES_LTX)


def test_installation_reprend_apres_l_echec_de_la_1_1_1(tmp_path, monkeypatch):
    """Cas réel : venv_comfy déjà créé dans D:\\ai\\ComfyUI par la 1.1.1. On réutilise l'environnement
    et ComfyUI va dans le sous-dossier ComfyUI ; les commandes reçoivent « oui » aux questions."""
    venv_bin = tmp_path / "venv_comfy" / ("Scripts" if sys.platform == "win32" else "bin")
    venv_bin.mkdir(parents=True)
    (venv_bin / ("python.exe" if sys.platform == "win32" else "python")).write_text("")
    lancees = []

    def faux_run(cmd, **kw):
        lancees.append((cmd, kw.get("input", "")))
        if any(a.startswith("--workspace=") for a in cmd):  # comfy-cli installe ComfyUI dans le dossier donné
            ws = Path(next(a for a in cmd if a.startswith("--workspace=")).split("=", 1)[1])
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "main.py").write_text("")

        class R:
            returncode = 0
        return R()
    monkeypatch.setattr(comfy_local.subprocess, "run", faux_run)
    inst = comfy_local.installer(tmp_path)
    assert not any(c[0][1:3] == ["-m", "venv"] for c in lancees)       # environnement réutilisé
    assert inst.racine == tmp_path / "ComfyUI" and inst.python.parent == venv_bin
    assert all(entree.startswith("y\n") for _, entree in lancees)


def test_installation_refuse_un_dossier_occupe(tmp_path):
    (tmp_path / "ComfyUI").mkdir()
    (tmp_path / "ComfyUI" / "mes_photos.txt").write_text("")
    with pytest.raises(comfy_local.ErreurComfyLocal, match="autre chose que ComfyUI"):
        comfy_local.installer(tmp_path)


class _ServeurProtege:
    """Imite Hugging Face : 401 sans jeton, puis redirection vers un autre serveur (le réseau de diffusion)."""

    def __init__(self, contenu: bytes):
        self.recus_cdn = []
        srv = self

        class Cdn(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                srv.recus_cdn.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.send_header("Content-Length", str(len(contenu)))
                self.end_headers()
                self.wfile.write(contenu)
            do_HEAD = do_GET

        self.cdn = ThreadingHTTPServer(("127.0.0.1", 0), Cdn)
        url_cdn = f"http://127.0.0.1:{self.cdn.server_address[1]}/fichier"

        class Hub(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer hf_bon":
                    self.send_response(401)
                    self.end_headers()
                    return
                self.send_response(302)
                self.send_header("Location", url_cdn)
                self.end_headers()
            do_HEAD = do_GET

        self.hub = ThreadingHTTPServer(("127.0.0.1", 0), Hub)
        self.url = f"http://127.0.0.1:{self.hub.server_address[1]}/Lightricks/LTX-2.5/resolve/main/m.safetensors"
        for s in (self.cdn, self.hub):
            threading.Thread(target=s.serve_forever, daemon=True).start()


def test_modele_protege_hugging_face(tmp_path, monkeypatch):
    srv = _ServeurProtege(b"poids" * 1000)
    # Le jeton n'est ajouté qu'aux adresses de Hugging Face : on fait passer le faux serveur pour lui
    monkeypatch.setattr(comfy_local, "_requete", lambda url, entetes, jeton="", methode=None: __import__(
        "urllib.request").request.Request(url, headers={**entetes, **({"Authorization": f"Bearer {jeton}"} if jeton else {})},
                                          method=methode))
    dest = tmp_path / "m.safetensors"
    with pytest.raises(comfy_local.ErreurAcces) as e:
        comfy_local.telecharger(srv.url, dest)
    assert "jeton" in str(e.value) and e.value.page.endswith("/Lightricks/LTX-2.5") and not e.value.avec_jeton
    with pytest.raises(comfy_local.ErreurAcces, match="conditions"):
        comfy_local.telecharger(srv.url, dest, jeton="hf_mauvais")
    comfy_local.telecharger(srv.url, dest, jeton="hf_bon")
    assert dest.read_bytes() == b"poids" * 1000
    assert srv.recus_cdn == [None]          # le jeton n'est jamais envoyé au réseau de diffusion


def test_jeton_hf(tmp_path, monkeypatch):
    for v in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    assert comfy_local.jeton_hf() == ""
    (tmp_path / "token").write_text("hf_du_cli\n")        # « huggingface-cli login »
    assert comfy_local.jeton_hf() == "hf_du_cli"
    monkeypatch.setenv("HF_TOKEN", "hf_env")
    assert comfy_local.jeton_hf() == "hf_env"
    assert comfy_local.jeton_hf(comfy_local.Reglages(hf_jeton=" hf_reglage ")) == "hf_reglage"
    req = comfy_local._requete("https://huggingface.co/a/b/resolve/main/x", {}, "hf_x")
    assert req.get_header("Authorization") == "Bearer hf_x"
    assert comfy_local._requete("https://example.com/x", {}, "hf_x").get_header("Authorization") is None


def test_ligne_de_commande_utilise_le_jeton_saisi(tmp_path, monkeypatch):
    """Bug de la 1.2.1 : le jeton collé dans la console était enregistré mais pas utilisé au nouvel essai."""
    from filmstudio import cli
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    for v in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.setattr(comfy_local, "telecharger_template", lambda nom=None: {})
    essais = []

    def faux_telecharger(dossier, liste, etat=None, arret=None, jeton=None):
        essais.append(jeton)
        if jeton != "hf_bon":
            raise comfy_local.ErreurAcces("https://huggingface.co/Lightricks/LTX-2.5/resolve/main/x", 401, bool(jeton))
        return []
    monkeypatch.setattr(comfy_local, "telecharger_modeles", faux_telecharger)
    monkeypatch.setattr("webbrowser.open", lambda url: None)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda invite="": "hf_bon")
    assert cli.main(["telecharger-modeles", "--dossier", str(tmp_path / "c")]) == 0
    assert essais == ["", "hf_bon"]
    assert comfy_local.Reglages.charger().hf_jeton == "hf_bon"


def test_comfyui_arrete_avant_les_voix(faux, tmp_path):
    """ComfyUI démarré par Film Studio (LTX en mémoire) est arrêté avant Qwen3-TTS, pas seulement « vidé »."""
    _fausse_installation(tmp_path / "comfyui")
    port = _port_libre()
    url = f"http://127.0.0.1:{port}"
    r = faux.reglages.copie(comfy_url=url, comfy_dossier=str(tmp_path / "comfyui"))
    assert comfy_local.GESTIONNAIRE.assurer(r) is True and comfy_local.repond(url)
    film = film_exemple()
    film.enregistrer(tmp_path / "film")
    etats = []
    b = Production(film, r, etat=etats.append).voix()
    assert not b.problemes, b.texte()
    assert not comfy_local.GESTIONNAIRE.actif and not comfy_local.repond(url)
    assert any("Arrêt de ComfyUI" in e for e in etats)
