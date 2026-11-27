"""Sous-titres incrustés dans l'image, mot à mot (style karaoké), pour les Shorts regardés sans le son.

Le minutage de chaque mot est proportionnel à sa longueur (avec une respiration après la ponctuation) à
l'intérieur de la réplique : avec une voix de synthèse au débit régulier, c'est très proche de la réalité
(même méthode que Whiteboard Studio).

Fabrication en deux temps, avec ffmpeg :
1. une image transparente par mot prononcé (le groupe de mots en blanc, le mot en cours en jaune),
   assemblées en une piste vidéo transparente (une seule entrée, quel que soit le nombre de mots) ;
2. cette piste est superposée au film, dans la « zone sûre » des Shorts (au-dessus des boutons de YouTube).
"""
from __future__ import annotations

import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .video import ffmpeg, ligne_concat

POLICE_FOURNIE = Path(__file__).parent / "assets" / "polices" / "FiraSans-Black.ttf"
BLANC = (255, 255, 255, 255)
JAUNE = (255, 214, 0, 255)
CONTOUR = (0, 0, 0, 255)
FIN_PHRASE = ".!?;:…"
MOTS_PAR_GROUPE = 3


@dataclass
class Mot:
    texte: str
    debut: float
    fin: float


def _poids(mot: str) -> tuple[float, float]:
    lettres = len(re.sub(r"[^\w]", "", mot))
    pause = 3.0 if mot.endswith(tuple(FIN_PHRASE)) else 1.5 if mot.endswith(",") else 0.0
    return max(1.0, lettres + 1.5), pause


def minuter(texte: str, debut: float, fin: float) -> list[Mot]:
    """Répartit les mots d'une réplique entre `debut` et `fin` selon leur longueur."""
    mots = texte.split()
    if not mots or fin <= debut:
        return []
    poids = [_poids(m) for m in mots]
    total = sum(a + b for a, b in poids[:-1]) + poids[-1][0]
    t, res = debut, []
    for m, (a, b) in zip(mots, poids):
        d = (fin - debut) * a / total
        res.append(Mot(m, t, t + d))
        t += d + (fin - debut) * b / total
    return res


def grouper(mots: list[Mot], maxi: int = MOTS_PAR_GROUPE) -> list[list[Mot]]:
    """Petits groupes de mots, coupés aux fins de phrase."""
    groupes, courant = [], []
    for m in mots:
        courant.append(m)
        if len(courant) >= maxi or m.texte.endswith(tuple(FIN_PHRASE + ",")):
            groupes.append(courant)
            courant = []
    if courant:
        groupes.append(courant)
    return groupes


def police(taille: int) -> ImageFont.FreeTypeFont:
    candidats = [str(POLICE_FOURNIE)]
    if sys.platform == "win32":
        candidats += [r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\segoeuib.ttf"]
    candidats += ["DejaVuSans-Bold.ttf"]
    for c in candidats:
        try:
            return ImageFont.truetype(c, taille)
        except OSError:
            continue
    return ImageFont.load_default(taille)


class Dessinateur:
    def __init__(self, largeur: int, hauteur: int):
        self.largeur, self.hauteur = largeur, hauteur
        verticale = hauteur > largeur
        self.taille = int(largeur * (0.085 if verticale else 0.045))
        self.police = police(self.taille)
        self.contour = max(3, self.taille // 9)
        self.marge = int(largeur * 0.08)
        # Zone sûre : en Short, au-dessus des boutons et du titre de YouTube (bas ≈ 35 % de l'écran)
        self.centre_y = int(hauteur * (0.62 if verticale else 0.85))

    def _lignes(self, mots: list[str]) -> list[list[int]]:
        """Indices des mots par ligne, sans dépasser la largeur utile."""
        d = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        utile = self.largeur - 2 * self.marge
        lignes, courante = [], []
        for i, m in enumerate(mots):
            essai = " ".join(mots[j] for j in courante + [i]).upper()
            if courante and d.textlength(essai, font=self.police) > utile:
                lignes.append(courante)
                courante = []
            courante.append(i)
        if courante:
            lignes.append(courante)
        return lignes

    def image(self, mots: list[str], actif: int | None) -> Image.Image:
        img = Image.new("RGBA", (self.largeur, self.hauteur), (0, 0, 0, 0))
        if not mots:
            return img
        d = ImageDraw.Draw(img)
        espace = d.textlength(" ", font=self.police)
        lignes = self._lignes(mots)
        hauteur_ligne = int(self.taille * 1.18)
        y = self.centre_y - hauteur_ligne * len(lignes) // 2
        for ligne in lignes:
            textes = [mots[i].upper() for i in ligne]
            largeurs = [d.textlength(t, font=self.police) for t in textes]
            x = (self.largeur - (sum(largeurs) + espace * (len(ligne) - 1))) / 2
            for i, t, w in zip(ligne, textes, largeurs):
                d.text((x, y), t, font=self.police, fill=JAUNE if i == actif else BLANC,
                       stroke_width=self.contour, stroke_fill=CONTOUR)
                x += w + espace
            y += hauteur_ligne
        return img


def evenements(entrees: list[tuple[float, float, str]]) -> list[tuple[float, float, list[str], int]]:
    """(début, fin, mots du groupe, indice du mot en cours) pour chaque mot prononcé."""
    res = []
    for debut, fin, texte in entrees:
        for groupe in grouper(minuter(texte, debut, fin)):
            textes = [m.texte for m in groupe]
            for k, m in enumerate(groupe):
                # le groupe reste affiché jusqu'au mot suivant (pas de clignotement entre deux mots)
                suite = groupe[k + 1].debut if k + 1 < len(groupe) else m.fin
                res.append((m.debut, max(suite, m.fin), textes, k))
    return res


def incruster(video: str | Path, entrees: list[tuple[float, float, str]], destination: str | Path,
              largeur: int, hauteur: int, fps: int, duree: float) -> Path:
    """Superpose les sous-titres mot à mot à `video` (qui garde son son)."""
    destination = Path(destination)
    evts = sorted(evenements(entrees), key=lambda e: e[0])
    if not evts:
        return Path(video)
    dessin = Dessinateur(largeur, hauteur)
    with tempfile.TemporaryDirectory(prefix="filmstudio_st_") as tmp:
        t = Path(tmp)
        vide = t / "vide.png"
        dessin.image([], None).save(vide)
        lignes, instant, n = [], 0.0, 0

        def ajouter(fichier: Path, d: float):
            if d > 0.001:
                lignes.append(ligne_concat(fichier) + f"duration {d:.3f}\n")
        for debut, fin, mots, actif in evts:
            debut = max(debut, instant)
            if debut >= duree:
                break
            ajouter(vide, debut - instant)
            n += 1
            f = t / f"m{n:04d}.png"
            dessin.image(mots, actif).save(f)
            fin = min(fin, duree)
            ajouter(f, fin - debut)
            instant = max(instant, fin)
        ajouter(vide, max(0.05, duree - instant))
        lignes.append(ligne_concat(vide))   # le concat a besoin d'une dernière image
        liste = t / "liste.txt"
        liste.write_text("".join(lignes), encoding="utf-8")
        piste = t / "sous_titres.mov"
        ffmpeg("-f", "concat", "-safe", "0", "-i", str(liste), "-vf", f"fps={fps},format=rgba",
               "-c:v", "png", "-t", f"{duree:.3f}", str(piste))
        ffmpeg("-i", str(video), "-i", str(piste), "-filter_complex",
               "[0:v][1:v]overlay=0:0:format=auto:eof_action=pass,format=yuv420p[v]",
               "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
               "-c:a", "copy", "-movflags", "+faststart", str(destination))
    return destination
