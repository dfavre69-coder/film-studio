"""Pilotage de ComfyUI (rendu local des vidéos et, au choix, des images) par son API HTTP.

Film Studio ne fige aucun modèle : il utilise le workflow que vous avez préparé et testé dans
ComfyUI (par exemple le modèle « LTX-2.5 Image to Video » de la bibliothèque), exporté au format
API (menu Fichier → Exporter (API)). À l'import, Film Studio repère tout seul les réglages à
remplir à chaque plan : le texte du prompt, l'image de départ, la graine, le nombre d'images,
la cadence, la largeur et la hauteur. Vous pouvez corriger ce repérage (la « carte »).
"""
from __future__ import annotations

import copy
import json
import mimetypes
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import dossier_workflows

EXTENSIONS_VIDEO = (".mp4", ".webm", ".mov", ".mkv", ".gif", ".webp")
EXTENSIONS_IMAGE = (".png", ".jpg", ".jpeg", ".webp")

# Rôles qu'un workflow peut exposer. Chaque rôle vise un ou plusieurs champs [nœud, entrée].
ROLES = {
    "prompt": "Texte du prompt (positif)",
    "negatif": "Texte négatif",
    "image": "Image de départ",
    "graine": "Graine (seed)",
    "images": "Nombre d'images (durée)",
    "secondes": "Durée en secondes",
    "fps": "Images par seconde",
    "largeur": "Largeur",
    "hauteur": "Hauteur",
}
ROLES_VIDEO_OBLIGATOIRES = ("prompt", "image")
ROLES_IMAGE_OBLIGATOIRES = ("prompt",)

NOMS_TEXTE = ("text", "prompt", "positive_prompt", "text_g")
NOMS_GRAINE = ("seed", "noise_seed")
NOMS_IMAGES = ("length", "num_frames", "frames", "frames_number", "video_length", "frame_count", "frame_length")
NOMS_SECONDES = ("duration", "seconds", "duration_seconds")
NOMS_FPS = ("frame_rate", "fps", "framerate")

# Les modèles officiels exposent leurs réglages dans des nœuds « Primitive » titrés (Prompt, Width…)
TITRES_PRIMITIVES = [
    ("negatif", ("negative", "négatif", "negatif")),
    ("prompt", ("prompt", "positive")),
    ("largeur", ("width", "largeur")),
    ("hauteur", ("height", "hauteur")),
    ("secondes", ("duration", "durée", "duree", "seconds")),
    ("images", ("length", "frames", "frame count", "num frames")),
    ("fps", ("frame rate", "framerate", "fps")),
    ("graine", ("seed",)),
]


class ErreurComfy(RuntimeError):
    pass


class Arrete(Exception):
    pass


# ---------------------------------------------------------------------------
# Workflows et cartes
# ---------------------------------------------------------------------------
@dataclass
class Workflow:
    nom: str
    genre: str                       # « video » ou « image »
    graphe: dict                     # format API de ComfyUI
    carte: dict = field(default_factory=dict)   # rôle → [[nœud, entrée], …]
    pas_images: int = 8              # nombre d'images = pas × n + 1 (LTX : 8, Wan : 4)
    multiple: int = 1                # largeur et hauteur arrondies à ce multiple (LTX : 64)

    def vers_dict(self) -> dict:
        return {"nom": self.nom, "genre": self.genre, "carte": self.carte, "pas_images": self.pas_images,
                "multiple": self.multiple, "graphe": self.graphe}

    @classmethod
    def depuis_dict(cls, d: dict) -> "Workflow":
        return cls(nom=d["nom"], genre=d.get("genre", "video"), graphe=d["graphe"], carte=d.get("carte") or {},
                   pas_images=int(d.get("pas_images") or 8), multiple=int(d.get("multiple") or 1))

    def manquants(self) -> list[str]:
        requis = ROLES_VIDEO_OBLIGATOIRES if self.genre == "video" else ROLES_IMAGE_OBLIGATOIRES
        return [r for r in requis if not self.carte.get(r)]


def lire_graphe(chemin: str | Path) -> dict:
    """Lit un workflow exporté par ComfyUI. Refuse (avec explication) le format « interface »."""
    try:
        d = json.loads(Path(chemin).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ErreurComfy(f"Fichier illisible : {e}") from e
    if isinstance(d, dict) and "prompt" in d and isinstance(d["prompt"], dict) and "nodes" not in d:
        d = d["prompt"]  # copie d'une requête /prompt
    if isinstance(d, dict) and "nodes" in d and "links" in d:
        raise ErreurComfy("Ce fichier est un workflow au format « interface ». Dans ComfyUI, ouvrez-le puis "
                          "utilisez le menu Workflow → Exporter (API) et importez ce nouveau fichier.")
    if not isinstance(d, dict) or not d or not all(isinstance(v, dict) and "class_type" in v for v in d.values()):
        raise ErreurComfy("Ce fichier n'est pas un workflow ComfyUI au format API.")
    return d


def _titre(noeud: dict) -> str:
    return str((noeud.get("_meta") or {}).get("title") or "").lower()


def _est_lien(v) -> bool:
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)


def champs(graphe: dict) -> list[tuple[str, str, str, object]]:
    """Tous les champs modifiables : (nœud, entrée, description, valeur)."""
    res = []
    for nid, n in sorted(graphe.items(), key=lambda kv: (len(kv[0]), kv[0])):
        for nom, val in (n.get("inputs") or {}).items():
            if _est_lien(val) or isinstance(val, (dict, list)):
                continue
            titre = (n.get("_meta") or {}).get("title") or n.get("class_type", "")
            res.append((nid, nom, f"{nid} · {titre} · {nom}", val))
    return res


def deviner_carte(graphe: dict, genre: str = "video") -> dict:
    """Repère les champs à remplir à chaque plan (prompt, image, graine, durée, taille…)."""
    carte: dict[str, list[list[str]]] = {r: [] for r in ROLES}
    # Qui consomme quoi : pour distinguer prompt positif et négatif
    consommateurs: dict[str, set[str]] = {}
    for nid, n in graphe.items():
        for nom, val in (n.get("inputs") or {}).items():
            if _est_lien(val):
                consommateurs.setdefault(val[0], set()).add(nom.lower())

    def descendants_negatifs(nid: str, profondeur: int = 0) -> bool | None:
        noms = consommateurs.get(nid, set())
        if any("positive" in x for x in noms):
            return False  # positif l'emporte (un même texte peut servir aux deux)
        if any("negative" in x or "negatif" in x for x in noms):
            return True
        if profondeur > 4:
            return None
        for autre, n in graphe.items():
            if any(_est_lien(v) and v[0] == nid for v in (n.get("inputs") or {}).values()):
                r = descendants_negatifs(autre, profondeur + 1)
                if r is not None:
                    return r
        return None

    textes_pos, textes_neg = [], []
    primitives: dict[str, list] = {}
    for nid, n in sorted(graphe.items(), key=lambda kv: (len(kv[0]), kv[0])):
        entrees = n.get("inputs") or {}
        titre = _titre(n)
        classe = str(n.get("class_type", ""))
        if classe.startswith("Primitive") and "value" in entrees:
            role = next((r for r, mots in TITRES_PRIMITIVES if any(m in titre for m in mots)), None)
            if role and not (role == "prompt" and "enhance" in titre):
                primitives.setdefault(role, []).append([nid, "value"])
            continue
        for nom, val in entrees.items():
            if _est_lien(val):
                continue
            bas = nom.lower()
            if isinstance(val, str) and bas in NOMS_TEXTE and classe != "LoadImage":
                negatif = descendants_negatifs(nid)
                if negatif is None:
                    negatif = "neg" in titre or "négatif" in titre
                (textes_neg if negatif else textes_pos).append([nid, nom])
            elif isinstance(val, str) and nom == "negative_prompt":
                textes_neg.append([nid, nom])
            elif classe in ("LoadImage", "LoadImageOutput") and nom == "image":
                if any(m in titre for m in ("last", "end", "fin", "dernière")):
                    continue  # image de fin (FLF2V) : laissée telle quelle
                carte["image"].append([nid, nom])
            elif isinstance(val, (int, float)) and not isinstance(val, bool):
                if bas in NOMS_GRAINE:
                    carte["graine"].append([nid, nom])
                elif bas in NOMS_IMAGES and isinstance(val, int):
                    carte["images"].append([nid, nom])
                elif bas in NOMS_SECONDES:
                    carte["secondes"].append([nid, nom])
                elif bas in NOMS_FPS:
                    carte["fps"].append([nid, nom])
                elif bas == "width":
                    carte["largeur"].append([nid, nom])
                elif bas == "height":
                    carte["hauteur"].append([nid, nom])
    for role, cibles in primitives.items():  # prioritaires : ce sont les réglages voulus par le modèle
        carte[role] = cibles
    if "secondes" in primitives:
        carte["images"] = []  # la durée en secondes pilote déjà le nombre d'images
    if not textes_pos and textes_neg and "prompt" not in primitives:  # un seul texte mal identifié : le prompt
        textes_pos = [textes_neg.pop(0)]
    if textes_pos and "prompt" not in primitives:
        carte["prompt"] = [textes_pos[0]]
    if textes_neg and "negatif" not in primitives:
        carte["negatif"] = [textes_neg[0]]
    if genre == "video" and len(carte["image"]) > 1:
        carte["image"] = carte["image"][:1]
    if genre == "image":
        carte["image"] = []
    return {r: v for r, v in carte.items() if v}


def arrondir(taille: int, multiple: int) -> int:
    multiple = max(1, int(multiple))
    return max(multiple, int(round(taille / multiple)) * multiple)


def multiple_conseille(graphe: dict) -> int:
    """LTX travaille par blocs de 32 pixels, et à demi-résolution avant d'agrandir : multiple de 64."""
    return 64 if any("LTX" in str(n.get("class_type", "")) for n in graphe.values()) else 1


def nombre_images(duree: float, fps: float, pas: int = 8) -> int:
    """Nombre d'images pour `duree` secondes, de la forme pas × n + 1 (exigé par LTX, Wan…)."""
    pas = max(1, int(pas))
    n = max(1, round(duree * fps / pas))
    return pas * n + 1


def appliquer(graphe: dict, carte: dict, valeurs: dict) -> dict:
    """Copie du graphe où les champs de la carte reçoivent `valeurs` (rôle → valeur)."""
    g = copy.deepcopy(graphe)
    for role, valeur in valeurs.items():
        if valeur is None:
            continue
        for cible in carte.get(role) or []:
            nid, nom = cible[0], cible[1]
            noeud = g.get(nid)
            if noeud is None:
                raise ErreurComfy(f"Le nœud {nid} (rôle « {ROLES.get(role, role)} ») n'existe plus dans le workflow.")
            ancien = noeud.setdefault("inputs", {}).get(nom)
            if _est_lien(ancien):
                if not str(noeud.get("class_type", "")).startswith("Primitive"):
                    continue  # valeur fournie par un autre nœud : on n'y touche pas
                ancien = None  # réglage d'un modèle officiel relié à un sélecteur : on impose la valeur
            if ancien is None and str(noeud.get("class_type")) == "PrimitiveInt":
                valeur = int(round(valeur)) if isinstance(valeur, (int, float)) else valeur
            if isinstance(ancien, float) and isinstance(valeur, int):
                valeur = float(valeur)
            elif isinstance(ancien, int) and not isinstance(ancien, bool) and isinstance(valeur, float):
                valeur = int(round(valeur))
            noeud["inputs"][nom] = valeur
    return g


def chemin_workflow(nom: str) -> Path:
    return dossier_workflows() / f"{nom}.json"


def enregistrer_workflow(wf: Workflow) -> Path:
    chemin = chemin_workflow(wf.nom)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(wf.vers_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    return chemin


def charger_workflow(nom: str) -> Workflow:
    if not nom:
        raise ErreurComfy("Aucun workflow choisi : importez-en un dans Réglages → ComfyUI.")
    chemin = Path(nom) if Path(nom).suffix == ".json" and Path(nom).is_file() else chemin_workflow(nom)
    try:
        return Workflow.depuis_dict(json.loads(chemin.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError) as e:
        raise ErreurComfy(f"Workflow « {nom} » introuvable ou abîmé : {e}") from e


def lister_workflows(genre: str | None = None) -> list[str]:
    noms = []
    for p in sorted(dossier_workflows().glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if genre is None or d.get("genre") == genre:
            noms.append(p.stem)
    return noms


def supprimer_workflow(nom: str) -> None:
    try:
        chemin_workflow(nom).unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Client HTTP
# ---------------------------------------------------------------------------
class ClientComfy:
    def __init__(self, url: str = "http://127.0.0.1:8188"):
        self.url = url.rstrip("/")
        self.client_id = uuid.uuid4().hex

    def _requete(self, chemin: str, donnees: bytes | None = None, entetes: dict | None = None,
                 delai: float = 30, brut: bool = False):
        req = urllib.request.Request(self.url + chemin, data=donnees, headers=entetes or {},
                                     method="POST" if donnees is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=delai) as r:
                contenu = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise ErreurComfy(_expliquer_erreur(detail) or f"ComfyUI a refusé la requête ({e.code}).") from e
        except urllib.error.URLError as e:
            raise ErreurComfy(f"ComfyUI ne répond pas à {self.url} ({e.reason}). Est-il lancé ?") from e
        except (TimeoutError, OSError) as e:
            raise ErreurComfy(f"ComfyUI ne répond pas à {self.url} ({e}).") from e
        if brut:
            return contenu
        if not contenu:
            return {}
        try:
            return json.loads(contenu.decode("utf-8"))
        except ValueError:
            return {}

    def _post_json(self, chemin: str, d: dict, delai: float = 30):
        return self._requete(chemin, json.dumps(d).encode("utf-8"), {"Content-Type": "application/json"}, delai)

    def systeme(self) -> dict:
        return self._requete("/system_stats", delai=5)

    def description(self) -> str:
        d = self.systeme()
        version = (d.get("system") or {}).get("comfyui_version") or "?"
        cartes = []
        for dev in d.get("devices") or []:
            total = (dev.get("vram_total") or 0) / 2**30
            libre = (dev.get("vram_free") or 0) / 2**30
            cartes.append(f"{dev.get('name', 'GPU')} ({libre:.0f} Go libres sur {total:.0f})")
        return f"ComfyUI {version} — " + (", ".join(cartes) or "aucune carte graphique signalée")

    def envoyer_image(self, chemin: str | Path) -> str:
        """Dépose l'image dans le dossier « input » de ComfyUI ; renvoie le nom à mettre dans LoadImage."""
        chemin = Path(chemin)
        limite = uuid.uuid4().hex
        type_mime = mimetypes.guess_type(chemin.name)[0] or "application/octet-stream"
        nom = f"filmstudio_{uuid.uuid4().hex[:8]}_{chemin.name}"
        corps = b"".join([
            f"--{limite}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{nom}\"\r\n"
            f"Content-Type: {type_mime}\r\n\r\n".encode("utf-8"), chemin.read_bytes(), b"\r\n",
            f"--{limite}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n".encode(),
            f"--{limite}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\ninput\r\n".encode(),
            f"--{limite}--\r\n".encode(),
        ])
        r = self._requete("/upload/image", corps, {"Content-Type": f"multipart/form-data; boundary={limite}"}, 120)
        nom_final = r.get("name") or nom
        return f"{r['subfolder']}/{nom_final}" if r.get("subfolder") else nom_final

    def lancer(self, graphe: dict) -> str:
        r = self._post_json("/prompt", {"prompt": graphe, "client_id": self.client_id})
        if r.get("node_errors"):
            raise ErreurComfy(_expliquer_erreur(json.dumps(r)) or "Workflow refusé par ComfyUI.")
        if not r.get("prompt_id"):
            raise ErreurComfy("ComfyUI n'a pas accepté le travail.")
        return r["prompt_id"]

    def position(self, prompt_id: str) -> int | None:
        """0 = en cours, n = n travaux avant, None = plus dans la file."""
        try:
            q = self._requete("/queue", delai=10)
        except ErreurComfy:
            return None
        for item in q.get("queue_running") or []:
            if len(item) > 1 and item[1] == prompt_id:
                return 0
        attente = q.get("queue_pending") or []
        for i, item in enumerate(sorted(attente, key=lambda x: x[0] if x else 0)):
            if len(item) > 1 and item[1] == prompt_id:
                return i + 1
        return None

    def attendre(self, prompt_id: str, delai: float = 2400, arret: threading.Event | None = None,
                 attente: Callable[[float, int | None], None] | None = None) -> dict:
        debut = time.monotonic()
        while True:
            if arret is not None and arret.is_set():
                self.interrompre(prompt_id)
                raise Arrete()
            h = self._requete(f"/history/{prompt_id}", delai=15)
            entree = h.get(prompt_id) if isinstance(h, dict) else None
            if entree:
                statut = entree.get("status") or {}
                if statut.get("status_str") == "error":
                    raise ErreurComfy("Le rendu a échoué dans ComfyUI : " + _message_statut(statut))
                if statut.get("completed", True) or entree.get("outputs"):
                    return entree
            ecoule = time.monotonic() - debut
            if ecoule > delai:
                self.interrompre(prompt_id)
                raise ErreurComfy(f"Rendu trop long (plus de {int(delai // 60)} minutes) : abandon.")
            if attente:
                attente(ecoule, self.position(prompt_id))
            for _ in range(10):
                if arret is not None and arret.is_set():
                    break
                time.sleep(0.1)

    def fichiers_produits(self, entree: dict, genre: str = "video") -> list[dict]:
        extensions = EXTENSIONS_VIDEO if genre == "video" else EXTENSIONS_IMAGE
        trouves = []
        for sortie in (entree.get("outputs") or {}).values():
            for cle, liste in (sortie or {}).items():
                if not isinstance(liste, list):
                    continue
                for f in liste:
                    if isinstance(f, dict) and str(f.get("filename", "")).lower().endswith(extensions):
                        trouves.append(f)
        if genre == "video":  # une vraie vidéo plutôt qu'une image animée
            trouves.sort(key=lambda f: (not str(f["filename"]).lower().endswith((".mp4", ".webm", ".mov", ".mkv")),
                                        f.get("type") != "output"))
        else:
            trouves.sort(key=lambda f: f.get("type") != "output")
        return trouves

    def telecharger(self, fichier: dict, destination: str | Path) -> Path:
        params = urllib.parse.urlencode({"filename": fichier["filename"], "subfolder": fichier.get("subfolder", ""),
                                         "type": fichier.get("type", "output")})
        contenu = self._requete(f"/view?{params}", delai=300, brut=True)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(contenu)
        return destination

    def interrompre(self, prompt_id: str | None = None) -> None:
        try:
            if prompt_id:
                self._post_json("/queue", {"delete": [prompt_id]}, 10)
            self._post_json("/interrupt", {}, 10)
        except ErreurComfy:
            pass

    def liberer(self) -> None:
        """Décharge les modèles de ComfyUI (place pour Qwen3-TTS ou Ollama)."""
        try:
            self._post_json("/free", {"unload_models": True, "free_memory": True}, 15)
        except ErreurComfy:
            pass

    # -- rendu complet ------------------------------------------------------------
    def rendre(self, wf: Workflow, destination: str | Path, prompt: str, negatif: str = "",
               image: str | Path | None = None, duree: float = 5, fps: int = 24, largeur: int = 1280,
               hauteur: int = 720, graine: int | None = None, delai: float = 2400,
               arret: threading.Event | None = None,
               attente: Callable[[float, int | None], None] | None = None) -> tuple[Path, int]:
        """Rend un plan (ou une image) ; renvoie (fichier téléchargé, graine utilisée)."""
        if wf.manquants():
            raise ErreurComfy("Le workflow « %s » n'indique pas où mettre : %s. Corrigez sa carte dans "
                              "Réglages → ComfyUI." % (wf.nom, ", ".join(ROLES[r] for r in wf.manquants())))
        graine = random.randint(1, 2**31 - 1) if graine is None else int(graine)
        largeur, hauteur = arrondir(largeur, wf.multiple), arrondir(hauteur, wf.multiple)
        valeurs = {"prompt": prompt, "negatif": negatif or None, "graine": graine, "fps": fps,
                   "largeur": largeur, "hauteur": hauteur,
                   "images": nombre_images(duree, fps, wf.pas_images), "secondes": duree}
        if wf.genre == "video":
            if not image:
                raise ErreurComfy("Il manque l'image de départ du plan.")
            valeurs["image"] = self.envoyer_image(image)
        graphe = appliquer(wf.graphe, wf.carte, valeurs)
        pid = self.lancer(graphe)
        entree = self.attendre(pid, delai, arret, attente)
        fichiers = self.fichiers_produits(entree, wf.genre)
        if not fichiers:
            raise ErreurComfy("ComfyUI a terminé sans produire de " + ("vidéo" if wf.genre == "video" else "image")
                              + ". Le workflow contient-il un nœud d'enregistrement (Save Video / Save Image) ?")
        f = fichiers[0]
        destination = Path(destination)
        ext = Path(f["filename"]).suffix.lower()
        if destination.suffix.lower() != ext:
            destination = destination.with_suffix(ext)
        return self.telecharger(f, destination), graine


def _message_statut(statut: dict) -> str:
    for m in statut.get("messages") or []:
        if isinstance(m, list) and len(m) > 1 and m[0] == "execution_error":
            d = m[1] or {}
            return f"{d.get('node_type', '')} : {d.get('exception_message', '').strip()}".strip(" :")
    return "erreur inconnue"


def _expliquer_erreur(detail: str) -> str:
    try:
        d = json.loads(detail)
    except ValueError:
        return detail.strip()[:500]
    morceaux = []
    err = d.get("error")
    if isinstance(err, dict) and err.get("message"):
        morceaux.append(err["message"])
    elif isinstance(err, str):
        morceaux.append(err)
    for nid, e in (d.get("node_errors") or {}).items():
        for x in (e.get("errors") or [])[:3]:
            morceaux.append(f"nœud {nid} ({e.get('class_type', '')}) : {x.get('message', '')} {x.get('details', '')}"
                            .strip())
    texte = " — ".join(morceaux)
    if "value_not_in_list" in detail or "not in list" in texte.lower():
        texte += ("\nUn modèle ou un fichier choisi dans le workflow n'existe pas dans votre ComfyUI : "
                  "ouvrez le workflow dans ComfyUI, vérifiez qu'il fonctionne, puis réexportez-le.")
    return texte
