"""ComfyUI installé et piloté par Film Studio : installation (comfy-cli), démarrage, arrêt, modèles.

- Installation : un environnement Python dédié (« <dossier>\\venv_comfy ») reçoit comfy-cli, l'outil
  officiel, qui installe ComfyUI dans « <dossier>\\ComfyUI » avec PyTorch pour carte NVIDIA.
  (comfy-cli installe dans le dossier qu'on lui donne, qui doit être vide ou absent : on lui donne
  donc le sous-dossier « ComfyUI », jamais le dossier qui contient l'environnement Python.)
- Démarrage : quand un rendu en a besoin et que ComfyUI ne répond pas, Film Studio le lance
  lui-même (sans fenêtre), attend qu'il soit prêt, et l'arrête en quittant.
- Modèles : téléchargement des fichiers LTX-2.5 aux bons endroits, avec reprise si la connexion coupe.

Un ComfyUI déjà installé (portable, manuel, comfy-cli) peut aussi être démarré : indiquez son dossier.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import Reglages, dossier_config

_SANS_FENETRE = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
DELAI_DEMARRAGE = 240  # secondes : le premier démarrage charge beaucoup de choses


class ErreurComfyLocal(RuntimeError):
    pass


class Arrete(Exception):
    pass


def dossier_par_defaut() -> Path:
    if sys.platform == "win32" and Path("D:/").exists():
        return Path("D:/ai/ComfyUI")
    return Path.home() / "ComfyUI"


def _python_venv(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


# ---------------------------------------------------------------------------
# Trouver une installation
# ---------------------------------------------------------------------------
@dataclass
class Installation:
    racine: Path        # dossier qui contient main.py
    python: Path        # interpréteur qui a PyTorch et les dépendances de ComfyUI


def trouver_installation(dossier: str | Path) -> Installation | None:
    """Reconnaît les installations courantes : celle de Film Studio (comfy-cli), le ComfyUI portable
    pour Windows, ou une installation manuelle avec son environnement .venv / venv."""
    if not dossier:
        return None
    d = Path(dossier)
    racines = [d / "ComfyUI", d, d / "ComfyUI" / "ComfyUI"]
    pythons = [_python_venv(d / "venv_comfy"), d / "python_embeded" / "python.exe"]
    for r in racines:
        if not (r / "main.py").is_file():
            continue
        candidats = pythons + [_python_venv(r / ".venv"), _python_venv(r / "venv"), _python_venv(d / ".venv"),
                               _python_venv(d / "venv")]
        for p in candidats:
            if p.is_file():
                return Installation(r, p)
    return None


# ---------------------------------------------------------------------------
# Installation avec comfy-cli
# ---------------------------------------------------------------------------
def commandes_installation(dossier: Path, python_base: str) -> list[tuple[str, list[str]]]:
    """Les étapes de l'installation, à lancer dans l'ordre : (description, commande)."""
    venv = dossier / "venv_comfy"
    py = str(_python_venv(venv))
    comfy = str(venv / ("Scripts/comfy.exe" if sys.platform == "win32" else "bin/comfy"))
    etapes = []
    if not Path(py).is_file():
        etapes.append(("Création de l'environnement Python de ComfyUI", [python_base, "-m", "venv", str(venv)]))
    etapes += [
        ("Mise à jour de pip", [py, "-m", "pip", "install", "--upgrade", "pip", "-q", "--disable-pip-version-check"]),
        ("Installation de comfy-cli (l'outil officiel)", [py, "-m", "pip", "install", "--upgrade", "comfy-cli", "-q",
                                                         "--disable-pip-version-check"]),
        ("Installation de ComfyUI et de PyTorch pour carte NVIDIA (10 à 20 minutes)",
         [comfy, f"--workspace={dossier / 'ComfyUI'}", "install", "--nvidia"]),
    ]
    return etapes


def installer(dossier: str | Path, python_base: str | None = None,
              etat: Callable[[str], None] | None = None) -> Installation:
    """Installe ComfyUI dans `dossier` (les messages des outils s'affichent dans la console)."""
    dossier = Path(dossier)
    dossier.mkdir(parents=True, exist_ok=True)
    libre = shutil.disk_usage(dossier).free / 2**30
    if libre < 15:
        raise ErreurComfyLocal(f"Seulement {libre:.0f} Go libres sur ce disque : ComfyUI et PyTorch demandent "
                               "environ 10 Go, sans compter les modèles.")
    env = dict(os.environ, VIRTUAL_ENV=str(dossier / "venv_comfy"), DO_NOT_TRACK="1", COMFY_NO_TELEMETRY="1",
               PYTHONIOENCODING="utf-8")
    env["PATH"] = str(_python_venv(dossier / "venv_comfy").parent) + os.pathsep + env.get("PATH", "")
    cible = dossier / "ComfyUI"
    if cible.exists() and any(cible.iterdir()) and not (cible / "main.py").is_file():
        if (cible / ".git").exists() or not any(p.name != ".git" for p in cible.iterdir()):
            pass  # installation interrompue : comfy-cli saura reprendre ou le signalera
        else:
            raise ErreurComfyLocal(f"Le dossier {cible} existe déjà et contient autre chose que ComfyUI. "
                                   "Videz-le ou choisissez un autre dossier d'installation.")
    for description, cmd in commandes_installation(dossier, python_base or sys.executable):
        if etat:
            etat(description + "…")
        # Les questions éventuelles de comfy-cli reçoivent « oui »
        res = subprocess.run(cmd, input="y\n" * 10, text=True, env=env, cwd=str(dossier))
        if res.returncode != 0:
            raise ErreurComfyLocal(f"Échec de l'étape « {description} » (code {res.returncode}). "
                                   "Les messages ci-dessus en donnent la raison.")
    inst = trouver_installation(dossier)
    if inst is None:
        raise ErreurComfyLocal(f"L'installation s'est terminée, mais ComfyUI est introuvable dans {dossier}.")
    return inst


# ---------------------------------------------------------------------------
# Démarrage et arrêt
# ---------------------------------------------------------------------------
def _port(url: str) -> tuple[str, int]:
    u = urllib.parse.urlparse(url if "://" in url else f"http://{url}")
    return (u.hostname or "127.0.0.1"), (u.port or 8188)


def repond(url: str, delai: float = 3) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/system_stats", timeout=delai) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def fichier_journal() -> Path:
    return dossier_config() / "comfyui.log"


class Gestionnaire:
    """Le ComfyUI démarré par Film Studio (un seul à la fois)."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self._verrou = threading.Lock()

    @property
    def actif(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def assurer(self, r: Reglages, etat: Callable[[str], None] | None = None,
                arret: threading.Event | None = None, visible: bool = False) -> bool:
        """Vérifie que ComfyUI répond ; sinon le démarre si c'est permis. Renvoie True s'il a fallu le démarrer.

        `visible` : ComfyUI écrit dans la console courante et s'arrête avec elle (demarrer_comfyui.bat)."""
        with self._verrou:
            if repond(r.comfy_url):
                return False
            if not r.comfy_demarrage_auto:
                raise ErreurComfyLocal(f"ComfyUI ne répond pas à {r.comfy_url}. Lancez-le, ou cochez « Démarrer "
                                       "ComfyUI automatiquement » dans Réglages → ComfyUI.")
            dossier = r.comfy_dossier or dossier_par_defaut()
            inst = trouver_installation(dossier)
            if inst is None:
                raise ErreurComfyLocal(f"ComfyUI ne répond pas et Film Studio ne trouve pas d'installation à "
                                       f"démarrer dans {dossier}. Installez-le (Réglages → ComfyUI → Installer ComfyUI) ou indiquez "
                                       "son dossier.")
            hote, port = _port(r.comfy_url)
            if hote not in ("127.0.0.1", "localhost", "0.0.0.0"):
                raise ErreurComfyLocal(f"ComfyUI ne répond pas à {r.comfy_url} (ordinateur distant : "
                                       "Film Studio ne peut pas le démarrer).")
            if self.actif:
                self.proc.kill()
            cmd = [str(inst.python), "main.py", "--listen", "127.0.0.1", "--port", str(port)]
            cmd += [x for x in (r.comfy_options or "").split() if x]
            journal = fichier_journal()
            journal.parent.mkdir(parents=True, exist_ok=True)
            if etat:
                etat("Démarrage de ComfyUI…")
            env = dict(os.environ, PYTHONIOENCODING="utf-8", DO_NOT_TRACK="1")
            if visible:
                self.proc = subprocess.Popen(cmd, cwd=str(inst.racine), stdin=subprocess.DEVNULL, env=env)
            else:
                with open(journal, "w", encoding="utf-8", errors="replace") as sortie:
                    self.proc = subprocess.Popen(cmd, cwd=str(inst.racine), stdout=sortie, stderr=subprocess.STDOUT,
                                                 stdin=subprocess.DEVNULL, env=env, **_SANS_FENETRE)
            debut = time.monotonic()
            while True:
                if repond(r.comfy_url, 2):
                    if etat:
                        etat(f"ComfyUI prêt (démarré en {int(time.monotonic() - debut)} s).")
                    return True
                if self.proc.poll() is not None:
                    fin = "" if visible else _fin_journal(journal)
                    self.proc = None
                    raise ErreurComfyLocal("ComfyUI s'est arrêté au démarrage" + (f" :\n{fin}" if fin else
                                                                                   " (voir les messages ci-dessus)."))
                if arret is not None and arret.is_set():
                    self.arreter()
                    raise Arrete()
                ecoule = time.monotonic() - debut
                if ecoule > DELAI_DEMARRAGE:
                    self.arreter()
                    raise ErreurComfyLocal(f"ComfyUI n'a pas démarré en {DELAI_DEMARRAGE // 60} minutes.\n"
                                           + _fin_journal(journal))
                if etat and not visible:
                    etat(f"Démarrage de ComfyUI… {int(ecoule)} s")
                time.sleep(1)

    def arreter(self) -> None:
        """Arrête le ComfyUI démarré par Film Studio (jamais celui que vous avez lancé vous-même)."""
        proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


def _fin_journal(chemin: Path, lignes: int = 12) -> str:
    try:
        texte = chemin.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join([l for l in texte.splitlines() if l.strip()][-lignes:])


GESTIONNAIRE = Gestionnaire()


# ---------------------------------------------------------------------------
# Modèles LTX-2.5 (ceux du modèle « LTX-2.5 Image to Video » de ComfyUI)
# ---------------------------------------------------------------------------
@dataclass
class Modele:
    nom: str
    url: str
    sous_dossier: str          # dans ComfyUI/models
    facultatif: bool = False


_HF = "https://huggingface.co/Lightricks/LTX-2.5/resolve/main/"
MODELES_LTX = [
    Modele("ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
           _HF + "diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors", "diffusion_models"),
    Modele("gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
           _HF + "text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors", "text_encoders"),
    Modele("ltx-2.5-video-vae-bf16.safetensors", _HF + "vae/ltx-2.5-video-vae-bf16.safetensors", "vae"),
    Modele("ltx-2.5-audio-vae-bf16.safetensors", _HF + "vae/ltx-2.5-audio-vae-bf16.safetensors", "vae"),
    Modele("ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
           _HF + "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
           "latent_upscale_models"),
    # Petit modèle de l'« amélioration du prompt » : chargé par le workflow officiel même désactivée
    Modele("gemma4_e2b_it_int8_convrot.safetensors",
           "https://huggingface.co/Comfy-Org/gemma-4/resolve/main/text_encoders/gemma4_e2b_it_int8_convrot.safetensors",
           "text_encoders"),
]


def dossier_modeles(r_ou_dossier) -> Path:
    if isinstance(r_ou_dossier, Reglages):
        dossier = r_ou_dossier.comfy_dossier or dossier_par_defaut()
    else:
        dossier = r_ou_dossier
    inst = trouver_installation(dossier)
    racine = inst.racine if inst else Path(dossier) / "ComfyUI"
    return racine / "models"


class ErreurAcces(ErreurComfyLocal):
    """Modèle protégé : compte Hugging Face, licence acceptée et jeton nécessaires."""

    def __init__(self, url: str, code: int, avec_jeton: bool):
        self.page = page_modele(url)
        self.avec_jeton = avec_jeton
        if avec_jeton:
            texte = (f"Hugging Face refuse l'accès ({code}) malgré votre jeton. Ouvrez {self.page}, connectez-vous et "
                     "acceptez les conditions d'utilisation du modèle (bouton en haut de la page), puis relancez. "
                     "Vérifiez aussi que le jeton est valide (https://huggingface.co/settings/tokens).")
        else:
            texte = ("Ce modèle est protégé sur Hugging Face : il faut un compte (gratuit) et un jeton d'accès.\n"
                     "  1. Connectez-vous (ou créez un compte) sur https://huggingface.co\n"
                     f"  2. Ouvrez {self.page} et acceptez les conditions d'utilisation du modèle\n"
                     "  3. Créez un jeton de type « Read » sur https://huggingface.co/settings/tokens\n"
                     "  4. Donnez-le à Film Studio (Réglages → ComfyUI → Jeton Hugging Face)")
        super().__init__(texte)


def page_modele(url: str) -> str:
    """https://huggingface.co/Lightricks/LTX-2.5/resolve/main/x → https://huggingface.co/Lightricks/LTX-2.5"""
    u = urllib.parse.urlparse(url)
    morceaux = [m for m in u.path.split("/") if m]
    return f"{u.scheme}://{u.netloc}/" + "/".join(morceaux[:2]) if len(morceaux) >= 2 else url


def jeton_hf(r: Reglages | None = None) -> str:
    """Le jeton Hugging Face : réglage de Film Studio, sinon variable HF_TOKEN, sinon celui enregistré par
    « huggingface-cli login » (même jeton que ComfyUI et les autres outils)."""
    if r is not None and r.hf_jeton.strip():
        return r.hf_jeton.strip()
    for var in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        if os.environ.get(var, "").strip():
            return os.environ[var].strip()
    base = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface")
    try:
        return (base / "token").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class _RedirectionSansJeton(urllib.request.HTTPRedirectHandler):
    """Hugging Face redirige vers son réseau de diffusion : le jeton ne doit pas y être envoyé."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        nouvelle = super().redirect_request(req, fp, code, msg, headers, newurl)
        if nouvelle is not None and urllib.parse.urlparse(newurl).netloc != urllib.parse.urlparse(req.full_url).netloc:
            for nom in ("Authorization", "authorization"):
                nouvelle.headers.pop(nom, None)
                nouvelle.unredirected_hdrs.pop(nom, None)
        return nouvelle


_OUVREUR = urllib.request.build_opener(_RedirectionSansJeton())


def _requete(url: str, entetes: dict, jeton: str = "", methode: str | None = None) -> urllib.request.Request:
    entetes = {"User-Agent": "FilmStudio", **entetes}
    if jeton and urllib.parse.urlparse(url).netloc.endswith("huggingface.co"):
        entetes["Authorization"] = f"Bearer {jeton}"
    return urllib.request.Request(url, headers=entetes, method=methode)


def _taille_distante(url: str, jeton: str = "") -> int | None:
    try:
        with _OUVREUR.open(_requete(url, {}, jeton, "HEAD"), timeout=30) as r:
            taille = r.headers.get("Content-Length")
            return int(taille) if taille else None
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ErreurAcces(url, e.code, bool(jeton)) from e
        return None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def telecharger(url: str, destination: Path, progression: Callable[[int, int | None], None] | None = None,
                arret: threading.Event | None = None, morceau: int = 4 * 2**20, jeton: str = "") -> Path:
    """Télécharge `url` ; reprend là où il s'était arrêté (fichier .part)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partiel = destination.with_name(destination.name + ".part")
    for essai in range(5):
        deja = partiel.stat().st_size if partiel.exists() else 0
        entetes = {}
        if deja:
            entetes["Range"] = f"bytes={deja}-"
        try:
            with _OUVREUR.open(_requete(url, entetes, jeton), timeout=60) as r:
                if deja and r.status != 206:  # le serveur ne reprend pas : on recommence
                    deja = 0
                    partiel.unlink(missing_ok=True)
                total = r.headers.get("Content-Length")
                total = int(total) + deja if total else None
                with open(partiel, "ab") as f:
                    recu = deja
                    while True:
                        if arret is not None and arret.is_set():
                            raise Arrete()
                        bloc = r.read(morceau)
                        if not bloc:
                            break
                        f.write(bloc)
                        recu += len(bloc)
                        if progression:
                            progression(recu, total)
            if total is not None and partiel.stat().st_size < total:
                raise OSError("téléchargement incomplet")
            partiel.replace(destination)
            return destination
        except urllib.error.HTTPError as e:
            if e.code == 416 and partiel.exists():  # déjà complet
                partiel.replace(destination)
                return destination
            if e.code in (401, 403):
                raise ErreurAcces(url, e.code, bool(jeton)) from e
            if e.code == 404:
                raise ErreurComfyLocal(f"Fichier introuvable (404) : {url}") from e
            erreur = e
        except (urllib.error.URLError, OSError) as e:
            erreur = e
        time.sleep(min(30, 3 * (essai + 1)))
    raise ErreurComfyLocal(f"Téléchargement impossible après plusieurs essais : {destination.name} ({erreur})")


def telecharger_modeles(r_ou_dossier, modeles: list[Modele] | None = None, etat: Callable[[str], None] | None = None,
                        arret: threading.Event | None = None, jeton: str | None = None) -> list[Path]:
    """Télécharge les modèles qui manquent. Renvoie les fichiers présents à la fin."""
    modeles = MODELES_LTX if modeles is None else modeles
    if jeton is None:
        jeton = jeton_hf(r_ou_dossier if isinstance(r_ou_dossier, Reglages) else None)
    racine = dossier_modeles(r_ou_dossier)
    a_faire = [m for m in modeles if not (racine / m.sous_dossier / m.nom).is_file()]
    if a_faire:
        tailles = [_taille_distante(m.url, jeton) for m in a_faire]
        besoin = sum(t or 0 for t in tailles) / 2**30
        racine.mkdir(parents=True, exist_ok=True)
        libre = shutil.disk_usage(racine).free / 2**30
        if besoin and libre < besoin + 2:
            raise ErreurComfyLocal(f"Il faut environ {besoin:.0f} Go pour les modèles, mais seulement "
                                   f"{libre:.0f} Go sont libres sur ce disque.")
        if etat:
            etat(f"{len(a_faire)} fichier(s) à télécharger" + (f", environ {besoin:.0f} Go" if besoin else ""))
    for n, m in enumerate(a_faire, 1):
        dernier = [0.0]

        def progression(recu, total, m=m, n=n):
            if etat and time.monotonic() - dernier[0] > 1:
                dernier[0] = time.monotonic()
                pct = f" — {100 * recu / total:.0f} %" if total else ""
                etat(f"Modèle {n}/{len(a_faire)} : {m.nom} : {recu / 2**30:.1f} Go{pct}")
        telecharger(m.url, racine / m.sous_dossier / m.nom, progression, arret, jeton=jeton)
        if etat:
            etat(f"Modèle {n}/{len(a_faire)} prêt : {m.nom}")
    return [racine / m.sous_dossier / m.nom for m in modeles if (racine / m.sous_dossier / m.nom).is_file()]


# ---------------------------------------------------------------------------
# Le workflow officiel « LTX-2.5 : Image to Video », installé sans passer par l'interface
# ---------------------------------------------------------------------------
TEMPLATE_LTX = "video_ltx2_5_i2v"
URL_TEMPLATE = "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/{nom}.json"
NOM_WORKFLOW_LTX = "LTX-2.5_image_vers_video"

# Lancé avec le Python de ComfyUI (où comfy-cli est installé) : convertit le format « interface »
# en format API, exactement comme le bouton « Exporter (API) » de ComfyUI.
_CODE_CONVERSION = r"""
import json, sys
from comfy_cli.workflow_to_api import convert_ui_to_api
ui = json.load(open(sys.argv[1], encoding="utf-8"))
oi = json.load(open(sys.argv[2], encoding="utf-8"))
json.dump(convert_ui_to_api(ui, oi), open(sys.argv[3], "w", encoding="utf-8"))
"""


def telecharger_template(nom: str = TEMPLATE_LTX) -> dict:
    import json
    try:
        with urllib.request.urlopen(urllib.request.Request(URL_TEMPLATE.format(nom=nom),
                                                           headers={"User-Agent": "FilmStudio"}), timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ErreurComfyLocal(f"Impossible de récupérer le modèle de workflow « {nom} » : {e}") from e


def modeles_du_template(ui: dict) -> list[Modele]:
    """Les fichiers de modèles que le workflow officiel déclare (nom, adresse, dossier)."""
    noeuds = list(ui.get("nodes") or [])
    for sg in (ui.get("definitions") or {}).get("subgraphs") or []:
        noeuds += sg.get("nodes") or []
    vus, modeles = set(), []
    for n in noeuds:
        for m in (n.get("properties") or {}).get("models") or []:
            nom, url, dossier = m.get("name"), m.get("url"), m.get("directory")
            if nom and url and dossier and nom not in vus:
                vus.add(nom)
                modeles.append(Modele(nom, url, dossier))
    return modeles


def convertir_en_api(ui: dict, object_info: dict, python: str | Path | None = None) -> dict:
    """Format « interface » → format API, avec le convertisseur officiel de comfy-cli."""
    import json
    import tempfile
    try:  # comfy-cli importable ici (tests, ou installation commune)
        from comfy_cli.workflow_to_api import convert_ui_to_api
        return convert_ui_to_api(ui, object_info)
    except ImportError:
        pass
    if not python:
        raise ErreurComfyLocal("comfy-cli est introuvable : impossible de convertir le workflow.")
    with tempfile.TemporaryDirectory(prefix="filmstudio_wf_") as tmp:
        t = Path(tmp)
        (t / "ui.json").write_text(json.dumps(ui), encoding="utf-8")
        (t / "oi.json").write_text(json.dumps(object_info), encoding="utf-8")
        res = subprocess.run([str(python), "-c", _CODE_CONVERSION, str(t / "ui.json"), str(t / "oi.json"),
                              str(t / "api.json")], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", **_SANS_FENETRE)
        if res.returncode != 0:
            fin = "\n".join(res.stderr.strip().splitlines()[-6:])
            if "No module named 'comfy_cli'" in res.stderr:
                fin = ("comfy-cli n'est pas installé dans le Python de ComfyUI. Installez ComfyUI depuis Film Studio, "
                       "ou tapez « python -m pip install comfy-cli » avec ce Python.")
            raise ErreurComfyLocal("La conversion du workflow a échoué : " + fin)
        return json.loads((t / "api.json").read_text(encoding="utf-8"))


def modeles_absents(api: dict, object_info: dict) -> list[str]:
    """Fichiers (modèles…) choisis dans le workflow mais absents de ce ComfyUI."""
    absents = []
    for nid, n in api.items():
        schema = object_info.get(n.get("class_type"), {}).get("input", {})
        entrees = {**(schema.get("required") or {}), **(schema.get("optional") or {})}
        for nom, val in (n.get("inputs") or {}).items():
            spec = entrees.get(nom)
            if not isinstance(val, str) or not spec:
                continue
            choix = spec[0] if isinstance(spec, list) and spec else None
            if isinstance(choix, list) and choix and val not in choix and val.endswith((".safetensors", ".ckpt", ".pt",
                                                                                     ".gguf", ".bin", ".pth")):
                absents.append(val)
    return sorted(set(absents))


def installer_workflow_ltx(r: Reglages, etat: Callable[[str], None] | None = None,
                           arret: threading.Event | None = None, modeles: bool = True):
    """Télécharge le workflow officiel LTX-2.5 et ses modèles, le convertit et l'enregistre comme
    workflow vidéo de Film Studio. Renvoie (workflow, remarques)."""
    from . import comfy
    dire = etat or (lambda t: None)
    dossier = r.comfy_dossier or str(dossier_par_defaut())
    inst = trouver_installation(dossier)
    dire("Récupération du workflow officiel « LTX-2.5 : Image to Video »…")
    ui = telecharger_template(TEMPLATE_LTX)
    liste = modeles_du_template(ui) or MODELES_LTX
    if modeles:
        if inst is None and not repond(r.comfy_url):
            raise ErreurComfyLocal(f"ComfyUI n'est pas installé dans {dossier}.")
        telecharger_modeles(dossier, liste, dire, arret, jeton=jeton_hf(r))
    demarre = GESTIONNAIRE.assurer(r.copie(comfy_dossier=dossier, comfy_demarrage_auto=True), dire, arret)
    try:
        dire("Lecture des nœuds disponibles dans ComfyUI…")
        client = comfy.ClientComfy(r.comfy_url)
        object_info = client._requete("/object_info", delai=120)
        dire("Conversion au format API (convertisseur officiel de comfy-cli)…")
        api = convertir_en_api(ui, object_info, inst.python if inst else None)
    finally:
        if demarre:
            GESTIONNAIRE.arreter()
    wf = comfy.Workflow(NOM_WORKFLOW_LTX, "video", api, comfy.deviner_carte(api, "video"), 8,
                        comfy.multiple_conseille(api))
    remarques = []
    if wf.manquants():
        raise ErreurComfyLocal("Le workflow converti ne contient pas tous les réglages attendus ("
                               + ", ".join(comfy.ROLES[m] for m in wf.manquants()) + "). Importez-le à la main.")
    absents = modeles_absents(api, object_info)
    if absents:
        remarques.append("Fichiers de modèles absents de ComfyUI : " + ", ".join(absents))
    comfy.enregistrer_workflow(wf)
    r.comfy_workflow_video = wf.nom
    r.comfy_dossier = dossier
    r.enregistrer()
    dire(f"Workflow « {wf.nom} » installé et choisi pour les vidéos.")
    return wf, remarques


def ouvrir_console(arguments: list[str]) -> None:
    """Lance « python -m filmstudio <arguments> » dans une nouvelle console visible (Windows)."""
    cmd = [sys.executable.replace("pythonw.exe", "python.exe"), "-m", "filmstudio", *arguments]
    if sys.platform == "win32":
        subprocess.Popen(["cmd", "/c", *cmd, "&", "pause"], creationflags=0x00000010)  # CREATE_NEW_CONSOLE
    else:
        subprocess.Popen(cmd)
