"""Le film : personnages, lieux, plans, prises. Enregistré dans « film.json » au sein du dossier du film.

Arborescence d'un film :

    MonFilm/
      film.json
      references/   fiches des personnages et des lieux (images de référence)
      images/       première image de chaque plan
      voix/         répliques (Qwen3-TTS) et bande-son de chaque plan
      clips/        les prises vidéo rendues par ComfyUI
      export/       le film assemblé (.mp4) et ses sous-titres (.srt)

Tous les chemins enregistrés sont relatifs au dossier du film : on peut le déplacer ou le copier.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

VERSION_FICHIER = 1
NARRATEUR = "Narrateur"

FORMATS = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "1:1": (960, 960),
}
SONS_MODELE = {"ambiance": "Ambiance (sous les voix)", "complet": "Son complet du modèle", "aucun": "Aucun"}
MODES_VOIX = {"tts": "Qwen3-TTS (voix françaises)", "modele": "Le modèle vidéo fait parler", "aucune": "Pas de voix"}
SOUS_DOSSIERS = ("references", "images", "voix", "clips", "export")


def identifiant(nom: str, deja: set[str] | None = None, longueur: int = 30) -> str:
    """« La Luciole » → « la_luciole » (unique parmi `deja`)."""
    t = unicodedata.normalize("NFKD", nom or "").encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "_", t).strip("_").lower() or "element"
    t = t[:longueur].strip("_")
    if deja is None or t not in deja:
        return t
    n = 2
    while f"{t}_{n}" in deja:
        n += 1
    return f"{t}_{n}"


def _filtrer(cls, d: dict) -> dict:
    noms = {f.name for f in fields(cls)}
    return {k: v for k, v in (d or {}).items() if k in noms}


@dataclass
class Personnage:
    id: str
    nom: str
    description: str = ""        # pour vous, en français
    apparence: str = ""          # description visuelle détaillée (en anglais, pour les modèles d'image et de vidéo)
    voix: str = ""               # description de la voix (indication)
    image: str = ""              # fiche de référence (relative au dossier du film)
    voix_reference: str = ""     # extrait audio à cloner (vide = voix du narrateur)
    voix_texte: str = ""         # ce qui est dit dans l'extrait
    voix_locuteur: str = ""      # voix prédéfinie (modèles CustomVoice)
    voix_consigne: str = ""      # consigne de style (modèles CustomVoice)

    @classmethod
    def depuis(cls, d: dict) -> "Personnage":
        return cls(**_filtrer(cls, d))


@dataclass
class Lieu:
    id: str
    nom: str
    description: str = ""
    apparence: str = ""
    image: str = ""

    @classmethod
    def depuis(cls, d: dict) -> "Lieu":
        return cls(**_filtrer(cls, d))


@dataclass
class Replique:
    personnage: str              # nom du personnage, ou « Narrateur »
    texte: str

    @classmethod
    def depuis(cls, d: dict) -> "Replique":
        return cls(**_filtrer(cls, d))


@dataclass
class Prise:
    fichier: str                 # relatif au dossier du film
    graine: int = 0
    duree: float = 0.0
    date: str = ""

    @classmethod
    def depuis(cls, d: dict) -> "Prise":
        return cls(**_filtrer(cls, d))


@dataclass
class Plan:
    titre: str = ""
    action: str = ""             # ce qui se passe, en français (pour vous)
    image_prompt: str = ""       # la première image du plan (anglais)
    mouvement: str = ""          # le mouvement et l'action pendant le plan (anglais)
    camera: str = ""             # cadrage et mouvement de caméra
    duree: float = 6.0           # secondes souhaitées (allongée si les répliques sont plus longues)
    lieu: str = ""               # identifiant du lieu
    personnages: list[str] = field(default_factory=list)   # identifiants
    repliques: list[Replique] = field(default_factory=list)
    enchainer: bool = False      # part de la dernière image du plan précédent
    image: str = ""              # première image (relative)
    voix: str = ""               # bande-son des répliques (relative)
    duree_voix: float = 0.0
    empreinte_voix: str = ""
    sous_titres: list[list] = field(default_factory=list)  # [début, fin, texte] relatifs au plan
    prises: list[Prise] = field(default_factory=list)
    prise_choisie: int = -1

    @classmethod
    def depuis(cls, d: dict) -> "Plan":
        d = dict(d or {})
        d["repliques"] = [Replique.depuis(r) for r in d.get("repliques") or []]
        d["prises"] = [Prise.depuis(p) for p in d.get("prises") or []]
        return cls(**_filtrer(cls, d))

    @property
    def prise(self) -> Prise | None:
        if 0 <= self.prise_choisie < len(self.prises):
            return self.prises[self.prise_choisie]
        return None


@dataclass
class Film:
    titre: str = "Mon film"
    pitch: str = ""
    style: str = ("3D animated feature film look, soft cinematic lighting, expressive characters, "
                  "rich colors, shallow depth of field")
    format: str = "16:9"
    largeur: int = 1280
    hauteur: int = 720
    fps: int = 24
    langue: str = "français"
    mode_voix: str = "tts"        # tts | modele | aucune
    son_modele: str = "ambiance"  # ambiance | complet | aucun
    volume_modele: float = 0.35
    musique: str = ""             # fichier audio (chemin absolu ou relatif)
    volume_musique: float = 0.15
    negatif: str = ("blurry, low quality, distorted face, deformed hands, extra limbs, text, subtitles, "
                    "watermark, logo, flicker")
    short: bool = False           # Short YouTube : vertical, moins de 60 s, accroche et fin en boucle
    sous_titres_incrustes: bool = False   # sous-titres mot à mot dans l'image (indispensable en Short)
    publication: dict = field(default_factory=dict)   # titre, description, hashtags (écrits par l'IA)
    personnages: list[Personnage] = field(default_factory=list)
    lieux: list[Lieu] = field(default_factory=list)
    plans: list[Plan] = field(default_factory=list)
    dossier: Path | None = field(default=None, repr=False, compare=False)

    # -- chemins --------------------------------------------------------------
    def chemin(self, relatif: str) -> Path | None:
        if not relatif:
            return None
        p = Path(relatif)
        if p.is_absolute() or self.dossier is None:
            return p
        return self.dossier / p

    def relatif(self, chemin: str | Path) -> str:
        p = Path(chemin)
        if self.dossier is not None:
            try:
                return p.resolve().relative_to(self.dossier.resolve()).as_posix()
            except ValueError:
                pass
        return str(p)

    def sous_dossier(self, nom: str) -> Path:
        if self.dossier is None:
            raise ValueError("Enregistrez d'abord le film dans un dossier.")
        d = self.dossier / nom
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -- recherches -------------------------------------------------------------
    def personnage(self, cle: str) -> Personnage | None:
        cle_n = identifiant(cle)
        for p in self.personnages:
            if p.id == cle or p.nom == cle or p.id == cle_n or identifiant(p.nom) == cle_n:
                return p
        return None

    def lieu(self, cle: str) -> Lieu | None:
        cle_n = identifiant(cle)
        for l in self.lieux:
            if l.id == cle or l.nom == cle or l.id == cle_n or identifiant(l.nom) == cle_n:
                return l
        return None

    def regler_short(self, actif: bool = True) -> None:
        self.short = actif
        if actif:
            self.regler_format("9:16")
            self.sous_titres_incrustes = True

    def regler_format(self, fmt: str) -> None:
        self.format = fmt
        self.largeur, self.hauteur = FORMATS.get(fmt, FORMATS["16:9"])

    def duree_totale(self) -> float:
        total = 0.0
        for p in self.plans:
            total += p.prise.duree if p.prise and p.prise.duree else max(p.duree, p.duree_voix)
        return total

    # -- fichier ----------------------------------------------------------------
    def vers_dict(self) -> dict:
        d = asdict(self)
        d.pop("dossier", None)
        d["version"] = VERSION_FICHIER
        return d

    @classmethod
    def depuis_dict(cls, d: dict, dossier: Path | None = None) -> "Film":
        d = dict(d or {})
        d["personnages"] = [Personnage.depuis(p) for p in d.get("personnages") or []]
        d["lieux"] = [Lieu.depuis(l) for l in d.get("lieux") or []]
        d["plans"] = [Plan.depuis(p) for p in d.get("plans") or []]
        film = cls(**_filtrer(cls, {k: v for k, v in d.items() if k != "dossier"}))
        film.dossier = Path(dossier) if dossier else None
        return film

    def enregistrer(self, dossier: str | Path | None = None) -> Path:
        if dossier is not None:
            self.dossier = Path(dossier)
        if self.dossier is None:
            raise ValueError("Aucun dossier choisi pour le film.")
        self.dossier.mkdir(parents=True, exist_ok=True)
        for nom in SOUS_DOSSIERS:
            (self.dossier / nom).mkdir(exist_ok=True)
        fichier = self.dossier / "film.json"
        temp = fichier.with_suffix(".json.tmp")
        temp.write_text(json.dumps(self.vers_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(fichier)
        return fichier

    @classmethod
    def ouvrir(cls, chemin: str | Path) -> "Film":
        chemin = Path(chemin)
        fichier = chemin / "film.json" if chemin.is_dir() else chemin
        d = json.loads(fichier.read_text(encoding="utf-8"))
        return cls.depuis_dict(d, fichier.parent)


def lire_repliques(texte: str) -> list[Replique]:
    """« Lumi : J'ai peur. » (une réplique par ligne) → répliques. Sans « Nom : », c'est le narrateur."""
    repliques = []
    for ligne in (texte or "").splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        m = re.match(r"^([^:：]{1,40}?)\s*[:：]\s*(.+)$", ligne)
        if m and not re.search(r"https?$", m.group(1)):
            repliques.append(Replique(m.group(1).strip(), m.group(2).strip()))
        else:
            repliques.append(Replique(NARRATEUR, ligne))
    return repliques


def ecrire_repliques(repliques: list[Replique]) -> str:
    return "\n".join(f"{r.personnage} : {r.texte}" for r in repliques)
