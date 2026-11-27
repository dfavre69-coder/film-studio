"""Claude et ChatGPT avec l'abonnement de l'utilisateur (même principe que Whiteboard Studio 2.1).

Aucune clé API : Film Studio lance les programmes installés sur le PC, eux-mêmes
connectés au compte de l'utilisateur (« /login » dans Claude Code, « codex login »).

- Claude Code (`claude -p`) écrit le découpage du film : il reçoit le prompt et renvoie le JSON.
- Codex (`codex exec`, outil de génération d'images « $imagegen ») dessine les fiches des
  personnages et des lieux, puis la première image de chaque plan, avec ChatGPT Images.
  Les fiches de référence sont jointes à la demande (option --image) : c'est ce qui garde
  les personnages identiques d'un plan à l'autre.

Les variables d'environnement de clés API (ANTHROPIC_API_KEY, OPENAI_API_KEY…) sont retirées
pour ces programmes : sinon ils factureraient l'API au lieu d'utiliser l'abonnement.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image

from .config import Reglages

CLES_API_CLAUDE = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")
CLES_API_CODEX = ("OPENAI_API_KEY", "CODEX_API_KEY")

DELAI_CLAUDE = 15 * 60
DELAI_IMAGE = 10 * 60
OPTIONS_CODEX_DEFAUT = "--sandbox workspace-write --skip-git-repo-check"
EXTENSIONS_IMAGES = (".png", ".jpg", ".jpeg", ".webp")

AIDE_CONNEXION_CLAUDE = ("Claude Code n'est pas connecté à votre abonnement. Ouvrez un terminal, tapez « claude », "
                         "choisissez la connexion avec votre compte Claude (abonnement), puis tapez « /exit ». "
                         "install.bat peut aussi le faire pour vous.")
AIDE_CONNEXION_CODEX = ("Codex n'est pas connecté à votre compte ChatGPT. Ouvrez un terminal et tapez "
                        "« codex login » (une page de connexion s'ouvre dans le navigateur). "
                        "install.bat peut aussi le faire pour vous.")


class ErreurForfait(RuntimeError):
    """Erreur d'un outil. `bloquante` : inutile de continuer (outil absent, non connecté, limite…)."""

    def __init__(self, message: str, bloquante: bool = False):
        super().__init__(message)
        self.bloquante = bloquante


class Arrete(Exception):
    pass


# ---------------------------------------------------------------------------
# Trouver et lancer les programmes
# ---------------------------------------------------------------------------
def _emplacements_connus(nom: str) -> list[Path]:
    maison = Path.home()
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA") or maison / "AppData" / "Roaming")
        local = Path(os.environ.get("LOCALAPPDATA") or maison / "AppData" / "Local")
        return [maison / ".local" / "bin" / f"{nom}.exe", appdata / "npm" / f"{nom}.cmd",
                local / "Programs" / nom / f"{nom}.exe"]
    return [maison / ".local" / "bin" / nom, maison / ".npm-global" / "bin" / nom, Path("/usr/local/bin") / nom]


EXTENSIONS_WINDOWS = (".exe", ".cmd", ".bat")


def _version_windows(chemin: Path) -> Path:
    """npm installe « codex » (script pour Linux), « codex.cmd » et « codex.ps1 » côte à côte :
    sous Windows, seul le .exe ou le .cmd peut être lancé directement."""
    if chemin.suffix.lower() in EXTENSIONS_WINDOWS:
        return chemin
    for ext in EXTENSIONS_WINDOWS:
        voisin = chemin.with_name(chemin.name + ext)
        if voisin.is_file():
            return voisin
    return chemin


def trouver(nom: str, commande: str = "", windows: bool | None = None) -> str | None:
    windows = sys.platform == "win32" if windows is None else windows
    commande = (commande or "").strip().strip('"')
    trouve = None
    if commande:
        if Path(commande).is_file():
            trouve = str(Path(commande))
        else:
            trouve = shutil.which(commande)
    else:
        if windows:  # d'abord les vrais exécutables, jamais le script sans extension
            trouve = next((t for t in (shutil.which(nom + ext) for ext in EXTENSIONS_WINDOWS) if t), None)
        trouve = trouve or shutil.which(nom) or next((str(p) for p in _emplacements_connus(nom) if p.is_file()), None)
    if trouve and windows:
        trouve = str(_version_windows(Path(trouve)))
    return trouve


def lanceur(chemin: str) -> list[str]:
    """Commande pour lancer le programme. Un lanceur npm (.cmd) est contourné : on appelle node
    directement avec le script JavaScript, ce qui évite les pièges de cmd.exe avec les arguments."""
    p = Path(chemin)
    if p.suffix.lower() != ".cmd":
        return [chemin]
    try:
        texte = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [chemin]
    m = re.search(r'%~?dp0%?\\?([^"%\r\n]+?\.js)', texte, flags=re.I)
    if not m:
        return [chemin]
    script = p.parent / m.group(1).replace("\\", "/")
    node = p.parent / "node.exe"
    node = str(node) if node.is_file() else shutil.which("node")
    if not script.is_file() or not node:
        return [chemin]
    return [node, str(script)]


def environnement(sans: tuple[str, ...]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in sans}
    if sys.platform == "win32":
        # Juste après l'installation, le PATH d'un raccourci ignore encore Node.js (nécessaire à codex.cmd)
        chemins = env.get("PATH", "").split(os.pathsep)
        for dossier in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs",
                        Path(os.environ.get("APPDATA") or Path.home()) / "npm", Path.home() / ".local" / "bin"):
            if dossier.is_dir() and str(dossier) not in chemins:
                chemins.append(str(dossier))
        env["PATH"] = os.pathsep.join(c for c in chemins if c)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


@dataclass
class Sortie:
    code: int
    texte: str
    erreurs: str


def executer(commande: list[str], entree: str | None = None, dossier: str | Path | None = None,
             env: dict[str, str] | None = None, delai: float = 600, arret: threading.Event | None = None,
             attente: Callable[[float], None] | None = None) -> Sortie:
    """Lance un programme, lui donne `entree` et attend sa fin (arrêt possible à tout moment)."""
    options = {}
    if sys.platform == "win32":
        options["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(commande, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=str(dossier) if dossier else None, env=env, text=True, encoding="utf-8",
                                errors="replace", **options)
    except OSError as e:
        raise ErreurForfait(f"Impossible de lancer « {Path(commande[0]).name} » : {e}", bloquante=True) from e
    debut = time.monotonic()
    a_envoyer = entree if entree is not None else ""
    while True:
        try:
            sortie, erreurs = proc.communicate(a_envoyer, timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            a_envoyer = None
            ecoule = time.monotonic() - debut
            if arret is not None and arret.is_set():
                proc.kill()
                proc.communicate()
                raise Arrete() from None
            if ecoule > delai:
                proc.kill()
                proc.communicate()
                raise ErreurForfait(f"Pas de réponse au bout de {int(delai // 60)} minutes : abandon.") from None
            if attente:
                attente(ecoule)
    return Sortie(proc.returncode, sortie or "", erreurs or "")


def _fin(texte: str, lignes: int = 12) -> str:
    morceaux = [l for l in texte.strip().splitlines() if l.strip()]
    return "\n".join(morceaux[-lignes:])


def _probleme_connexion(texte: str) -> bool:
    t = texte.lower()
    return any(m in t for m in ("/login", "not logged in", "not authenticated", "please login", "please log in",
                                "log in again", "invalid api key", "authentication", "unauthorized", "401",
                                "codex login", "sign in"))


def _probleme_limite(texte: str) -> bool:
    t = texte.lower()
    return any(m in t for m in ("usage limit", "rate limit", "limit reached", "quota", "429", "credit balance",
                                "out of credits", "try again later"))


# ---------------------------------------------------------------------------
# Claude Code : écriture du découpage
# ---------------------------------------------------------------------------
def commande_claude(reglages: Reglages) -> list[str]:
    exe = trouver("claude", reglages.claude_commande)
    if not exe:
        raise ErreurForfait("Claude Code est introuvable. Relancez install.bat (il propose de l'installer), "
                            "ou indiquez le chemin de claude.exe dans les Réglages.", bloquante=True)
    cmd = [*lanceur(exe), "-p", "--output-format", "json"]
    if reglages.claude_modele.strip():
        cmd += ["--model", reglages.claude_modele.strip()]
    return cmd


def lire_reponse_claude(sortie: Sortie) -> str:
    resultat = None
    brut = sortie.texte.strip()
    try:
        donnees = json.loads(brut) if brut else None
    except ValueError:
        donnees = None
    if isinstance(donnees, list):
        donnees = next((d for d in reversed(donnees) if isinstance(d, dict) and d.get("type") == "result"), None)
    if isinstance(donnees, dict):
        resultat = donnees
    tout = f"{sortie.texte}\n{sortie.erreurs}"
    if resultat is not None:
        texte = str(resultat.get("result") or "")
        if not resultat.get("is_error") and resultat.get("subtype", "success") == "success" and texte.strip():
            return texte
        explication = texte or resultat.get("subtype") or "erreur inconnue"
        tout = f"{explication}\n{sortie.erreurs}"
    elif sortie.code == 0 and brut:
        return brut
    if _probleme_connexion(tout):
        raise ErreurForfait(AIDE_CONNEXION_CLAUDE, bloquante=True)
    if _probleme_limite(tout):
        raise ErreurForfait("Limite d'utilisation de votre abonnement Claude atteinte pour le moment. "
                            "Réessayez plus tard.\n\n" + _fin(tout, 4), bloquante=True)
    raise ErreurForfait("Claude Code a renvoyé une erreur :\n\n" + (_fin(tout) or f"code {sortie.code}"))


def generer_claude(prompt: str, reglages: Reglages, attente: Callable[[float], None] | None = None,
                   arret: threading.Event | None = None, delai: float = DELAI_CLAUDE) -> str:
    cmd = commande_claude(reglages)
    with tempfile.TemporaryDirectory(prefix="filmstudio_claude_") as dossier:
        sortie = executer(cmd, prompt, dossier, environnement(CLES_API_CLAUDE), delai, arret, attente)
    return lire_reponse_claude(sortie)


# ---------------------------------------------------------------------------
# Codex : images avec ChatGPT Images
# ---------------------------------------------------------------------------
def commande_codex(reglages: Reglages, dossier: str | Path, references: list[Path] | None = None) -> list[str]:
    exe = trouver("codex", reglages.codex_commande)
    if not exe:
        raise ErreurForfait("Codex (ChatGPT) est introuvable. Relancez install.bat (il propose de l'installer), "
                            "ou indiquez le chemin de codex dans les Réglages.", bloquante=True)
    options = shlex.split(reglages.codex_options or OPTIONS_CODEX_DEFAUT, posix=True)
    cmd = [*lanceur(exe), "exec"]
    if references:
        # Les images jointes viennent avant « -C » : l'option suivante clôt la liste des fichiers
        cmd += ["--image", ",".join(str(Path(r).resolve()) for r in references)]
    return cmd + ["-C", str(dossier), *options, "-"]


def _images_recentes(dossier: Path, depuis: float, exclure: set[Path] = frozenset()) -> list[Path]:
    if not dossier.is_dir():
        return []
    trouvees = []
    for p in dossier.rglob("*"):
        try:
            if (p.suffix.lower() in EXTENSIONS_IMAGES and p.is_file() and p.stat().st_mtime >= depuis - 1
                    and p.resolve() not in exclure):
                trouvees.append(p)
        except OSError:
            continue
    return sorted(trouvees, key=lambda p: p.stat().st_mtime, reverse=True)


def _chemins_cites(texte: str) -> list[Path]:
    motif = r"([A-Za-z]:[\\/][^\s\"'<>|*?]+?|/[^\s\"'<>|*?]+?)\.(png|jpe?g|webp)\b"
    return [Path(f"{m.group(1)}.{m.group(2)}") for m in re.finditer(motif, texte, flags=re.I)]


def dossier_codex() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def retrouver_image(cle: str, dossier: Path, texte: str, depuis: float,
                    exclure: set[Path] = frozenset()) -> Path | None:
    """L'image que Codex vient de produire : au nom demandé, sinon la plus récente du dossier de
    travail, sinon un chemin cité dans sa réponse, sinon la plus récente de son propre dossier."""
    for ext in EXTENSIONS_IMAGES:
        if (dossier / f"{cle}{ext}").is_file():
            return dossier / f"{cle}{ext}"
    candidats = _images_recentes(dossier, depuis, exclure)
    candidats += [p for p in _chemins_cites(texte)
                  if p.is_file() and p.stat().st_mtime >= depuis - 1 and p.resolve() not in exclure]
    candidats += _images_recentes(dossier_codex(), depuis, exclure)
    for p in candidats:
        try:
            with Image.open(p) as img:
                img.verify()
            return p
        except Exception:  # noqa: BLE001
            continue
    return None


def generer_image_codex(prompt: str, cle: str, dossier: str | Path, reglages: Reglages,
                        references: list[Path] | None = None,
                        attente: Callable[[float], None] | None = None,
                        arret: threading.Event | None = None) -> Path:
    """Fait dessiner une image par Codex ; renvoie le fichier « <dossier>/<cle>.png »."""
    dossier = Path(dossier)
    dossier.mkdir(parents=True, exist_ok=True)
    travail = dossier / "_codex" / cle
    if travail.exists():
        shutil.rmtree(travail, ignore_errors=True)
    travail.mkdir(parents=True, exist_ok=True)
    references = [Path(r) for r in (references or []) if r and Path(r).is_file()]
    exclure = {r.resolve() for r in references}
    debut = time.time()
    try:
        sortie = executer(commande_codex(reglages, travail, references), prompt, travail,
                          environnement(CLES_API_CODEX), DELAI_IMAGE, arret, attente)
        tout = f"{sortie.texte}\n{sortie.erreurs}"
        image = retrouver_image(cle, travail, tout, debut, exclure)
        if image is None:
            if _probleme_connexion(tout):
                raise ErreurForfait(AIDE_CONNEXION_CODEX, bloquante=True)
            if _probleme_limite(tout):
                raise ErreurForfait("Limite d'utilisation de votre abonnement ChatGPT atteinte pour le moment "
                                    "(la génération d'images la consomme vite). Réessayez plus tard, ou importez "
                                    "vos propres images.\n\n" + _fin(tout, 4), bloquante=True)
            if "unexpected argument" in tout.lower() or "unrecognized" in tout.lower():
                raise ErreurForfait("Codex refuse les options de lancement (version différente ?). Vérifiez "
                                    "« Options de Codex » dans les Réglages.\n\n" + _fin(tout, 6), bloquante=True)
            raise ErreurForfait("Codex n'a produit aucune image." + (f"\n\nSa réponse :\n{_fin(tout)}"
                                                                      if tout.strip() else f" (code {sortie.code})"))
        final = dossier / f"{cle}.png"
        with Image.open(image) as img:
            img.load()
            (img.convert("RGBA") if "A" in img.getbands() or img.mode == "P" else img.convert("RGB")).save(final)
        return final
    finally:
        shutil.rmtree(travail, ignore_errors=True)
        try:
            (dossier / "_codex").rmdir()
        except OSError:
            pass


@dataclass
class Demande:
    cle: str                     # nom du fichier attendu (perso_lumi, plan_03)
    libelle: str                 # pour l'affichage
    prompt: str
    dossier: Path
    references: list[Path] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Vérifications (réglages, install.bat)
# ---------------------------------------------------------------------------
def verifier_claude(reglages: Reglages, essai: bool = True) -> str:
    exe = trouver("claude", reglages.claude_commande)
    if not exe:
        raise ErreurForfait("Claude Code n'est pas installé.", bloquante=True)
    version = executer([*lanceur(exe), "--version"], env=environnement(CLES_API_CLAUDE), delai=60)
    texte = (version.texte.strip() or version.erreurs.strip()).splitlines()
    nom = texte[0] if texte else "version inconnue"
    if not essai:
        return f"Claude Code trouvé ({nom})."
    r = reglages.copie(claude_modele=reglages.claude_modele or "haiku")
    reponse = generer_claude("Réponds uniquement par le mot OK.", r, delai=180)
    return f"Claude Code {nom} : connecté, réponse reçue ({reponse.strip()[:20]})."


def verifier_codex(reglages: Reglages) -> str:
    exe = trouver("codex", reglages.codex_commande)
    if not exe:
        raise ErreurForfait("Codex n'est pas installé.", bloquante=True)
    env = environnement(CLES_API_CODEX)
    version = executer([*lanceur(exe), "--version"], env=env, delai=60)
    nom = (version.texte.strip() or version.erreurs.strip() or "version inconnue").splitlines()[0]
    statut = executer([*lanceur(exe), "login", "status"], env=env, delai=60)
    tout = f"{statut.texte}\n{statut.erreurs}".strip()
    bas = tout.lower()
    if statut.code != 0 or "not logged in" in bas:
        raise ErreurForfait(AIDE_CONNEXION_CODEX, bloquante=True)
    if "api key" in bas and "chatgpt" not in bas:
        raise ErreurForfait("Codex est connecté avec une clé API (facturée à l'usage), pas avec votre compte "
                            "ChatGPT. Tapez « codex logout » puis « codex login » dans un terminal.",
                            bloquante=True)
    return f"{nom} : {_fin(tout, 1) or 'connecté'}."
