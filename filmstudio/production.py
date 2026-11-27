"""Fabrication du film, étape par étape : références → premières images → voix → vidéos → montage.

Chaque étape peut être lancée seule (depuis l'interface ou la ligne de commande) et ne refait que ce
qui manque, sauf demande contraire. Le film est enregistré après chaque élément produit : on peut
arrêter à tout moment et reprendre plus tard.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import comfy, comfy_local, forfait, tts, video, voix
from .config import Reglages
from .projet import Film, Plan, Prise, identifiant

ORIENTATION = {"16:9": "paysage 16:9 (1536 × 1024 pixels)", "9:16": "portrait 9:16 (1024 × 1536 pixels)",
               "1:1": "carré (1024 × 1024 pixels)"}


class ErreurProduction(RuntimeError):
    pass


class Arrete(Exception):
    pass


@dataclass
class Bilan:
    faits: list[str] = field(default_factory=list)
    problemes: list[str] = field(default_factory=list)

    def ajouter(self, autre: "Bilan") -> "Bilan":
        self.faits += autre.faits
        self.problemes += autre.problemes
        return self

    def texte(self) -> str:
        lignes = list(self.faits)
        if self.problemes:
            lignes += ["", "À revoir :"] + [f"• {p}" for p in self.problemes]
        return "\n".join(lignes) or "Rien à faire : tout est déjà prêt."


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
def _personnages_du_plan(film: Film, plan: Plan):
    return [p for p in (film.personnage(i) for i in plan.personnages) if p is not None]


def prompt_reference_personnage(film: Film, perso) -> str:
    return (
        "$imagegen Dessine la fiche de référence d'un personnage de film d'animation.\n"
        f"Personnage : {perso.nom} — {perso.apparence or perso.description}\n"
        f"Style : {film.style}\n"
        "Cadrage : le personnage en entier, vu de trois-quarts face, pose neutre, expression calme, "
        "sur un fond uni gris clair, éclairage doux et uniforme. Un seul personnage. "
        "Aucun texte, aucune légende, aucun cadre.\n"
        "Format : carré (1024 × 1024 pixels).\n"
        f"Enregistre l'image en PNG dans le dossier courant sous le nom « perso_{perso.id}.png ». "
        "Ne fais rien d'autre."
    )


def prompt_reference_lieu(film: Film, lieu) -> str:
    return (
        "$imagegen Dessine le décor de référence d'un film d'animation, sans aucun personnage.\n"
        f"Lieu : {lieu.nom} — {lieu.apparence or lieu.description}\n"
        f"Style : {film.style}\n"
        "Cadrage : plan d'ensemble qui montre bien le lieu, sa lumière et ses couleurs. Aucun texte.\n"
        f"Format : {ORIENTATION.get(film.format, ORIENTATION['16:9'])}.\n"
        f"Enregistre l'image en PNG dans le dossier courant sous le nom « lieu_{lieu.id}.png ». "
        "Ne fais rien d'autre."
    )


def prompt_image_plan(film: Film, i: int) -> tuple[str, list[Path]]:
    """Prompt de la première image du plan i, et images de référence à joindre."""
    plan = film.plans[i]
    references: list[Path] = []
    lignes = ["$imagegen Dessine la première image d'un plan de film d'animation (une image fixe, "
              "qui servira de point de départ à la vidéo).",
              f"Format : {ORIENTATION.get(film.format, ORIENTATION['16:9'])}.",
              f"Style : {film.style}"]
    lieu = film.lieu(plan.lieu) if plan.lieu else None
    persos = _personnages_du_plan(film, plan)
    joints = []
    for p in persos:
        f = film.chemin(p.image)
        if f and f.exists():
            references.append(f)
            joints.append(f"{len(references)} = {p.nom}")
    if lieu:
        f = film.chemin(lieu.image)
        if f and f.exists():
            references.append(f)
            joints.append(f"{len(references)} = le lieu « {lieu.nom} »")
    if joints:
        lignes.append("Images jointes (dans l'ordre) : " + " ; ".join(joints) + ". Respecte EXACTEMENT "
                      "l'apparence des personnages et le décor de ces images (formes, couleurs, vêtements, "
                      "proportions), mais compose une image nouvelle.")
    if lieu:
        lignes.append(f"Lieu : {lieu.nom} — {lieu.apparence or lieu.description}")
    for p in persos:
        lignes.append(f"Personnage : {p.nom} — {p.apparence or p.description}")
    lignes.append(f"Composition : {plan.image_prompt or plan.action}")
    if plan.camera:
        lignes.append(f"Cadrage : {plan.camera}")
    lignes.append("Aucun texte, aucune bulle, aucun sous-titre, aucun logo.")
    lignes.append(f"Enregistre l'image en PNG dans le dossier courant sous le nom « plan_{i + 1:02d}.png ». "
                  "Ne fais rien d'autre.")
    return "\n".join(lignes), references


def prompt_image_comfy(film: Film, i: int | None = None, perso=None, lieu=None) -> str:
    """Prompt (anglais) pour un workflow d'images local, sans images de référence."""
    if perso is not None:
        return (f"character reference sheet, full body, three-quarter view, neutral pose, plain light grey "
                f"background, {perso.apparence or perso.description}. {film.style}")
    if lieu is not None:
        return f"establishing shot, no people, {lieu.apparence or lieu.description}. {film.style}"
    plan = film.plans[i]
    morceaux = [plan.image_prompt or plan.action]
    for p in _personnages_du_plan(film, plan):
        morceaux.append(f"{p.nom}: {p.apparence}")
    lieu = film.lieu(plan.lieu) if plan.lieu else None
    if lieu:
        morceaux.append(f"setting: {lieu.apparence}")
    morceaux.append(film.style)
    return ". ".join(m for m in morceaux if m)


def prompt_video(film: Film, plan: Plan) -> str:
    morceaux = [plan.mouvement or plan.action]
    persos = _personnages_du_plan(film, plan)
    if persos:
        morceaux.append("Characters: " + "; ".join(f"{p.nom}, {p.apparence}" for p in persos if p.apparence))
    lieu = film.lieu(plan.lieu) if plan.lieu else None
    if lieu and lieu.apparence:
        morceaux.append(f"Setting: {lieu.apparence}")
    if plan.camera:
        morceaux.append(f"Camera: {plan.camera}")
    morceaux.append(film.style)
    if film.mode_voix == "modele" and plan.repliques:
        dits = " ".join(f'{r.personnage} says in {film.langue}: "{r.texte}"' for r in plan.repliques)
        morceaux.append(dits)
    return ". ".join(m.strip().rstrip(".") for m in morceaux if m and m.strip()) + "."


def duree_rendu(film: Film, plan: Plan, r: Reglages) -> float:
    d = plan.duree
    if film.mode_voix == "tts" and plan.duree_voix:
        d = max(d, plan.duree_voix)
    return round(min(max(d, 2.0), max(2.0, r.duree_max_plan)), 2)


# ---------------------------------------------------------------------------
# La production
# ---------------------------------------------------------------------------
class Production:
    def __init__(self, film: Film, reglages: Reglages, etat: Callable[[str], None] | None = None,
                 arret: threading.Event | None = None, sauver: Callable[[], None] | None = None):
        self.film = film
        self.r = reglages
        self._etat = etat
        self.arret = arret or threading.Event()
        self._sauver = sauver or (lambda: film.enregistrer() if film.dossier else None)
        self._client: comfy.ClientComfy | None = None

    # -- utilitaires ---------------------------------------------------------------
    def etat(self, texte: str) -> None:
        if self._etat:
            self._etat(texte)

    def verifier_arret(self) -> None:
        if self.arret.is_set():
            raise Arrete()

    def sauver(self) -> None:
        self._sauver()

    @property
    def client(self) -> comfy.ClientComfy:
        if self._client is None:
            self._client = comfy.ClientComfy(self.r.comfy_url)
        return self._client

    def comfy_pret(self) -> comfy.ClientComfy:
        """ComfyUI prêt à rendre : démarré par Film Studio s'il ne répond pas (si c'est permis)."""
        try:
            comfy_local.GESTIONNAIRE.assurer(self.r, self.etat, self.arret)
        except comfy_local.Arrete:
            raise Arrete() from None
        except comfy_local.ErreurComfyLocal as e:
            raise comfy.ErreurComfy(str(e)) from None
        return self.client

    def _attente_comfy(self, quoi: str):
        def attente(secondes, position):
            file = "" if position in (0, None) else f" (en file d'attente : {position})"
            self.etat(f"{quoi} : ComfyUI calcule… {int(secondes // 60)} min {int(secondes % 60):02d} s{file}")
        return attente

    # -- images (fiches et premières images) ------------------------------------------
    def _dessiner(self, cle: str, dossier: Path, libelle: str, prompt_chatgpt: str, prompt_local: str,
                  references: list[Path], format_image: str) -> Path:
        moteur = self.r.moteur_images
        if moteur == "chatgpt":
            def attente(s):
                self.etat(f"{libelle} : ChatGPT dessine… {int(s)} s")
            try:
                return forfait.generer_image_codex(prompt_chatgpt, cle, dossier, self.r, references, attente, self.arret)
            except forfait.Arrete:
                raise Arrete() from None
        if moteur == "comfyui":
            wf = comfy.charger_workflow(self.r.comfy_workflow_image)
            self.comfy_pret()
            largeur, hauteur = {"carre": (1024, 1024)}.get(format_image, (self.film.largeur, self.film.hauteur))
            try:
                fichier, _ = self.client.rendre(wf, dossier / f"{cle}.png", prompt_local, self.film.negatif,
                                                largeur=largeur, hauteur=hauteur,
                                                delai=self.r.comfy_delai_minutes * 60, arret=self.arret,
                                                attente=self._attente_comfy(libelle))
            except comfy.Arrete:
                raise Arrete() from None
            if fichier.suffix.lower() != ".png":
                final = dossier / f"{cle}.png"
                video.ajuster_image(fichier, final, *video.infos(fichier)["taille"] or (largeur, hauteur))
                fichier.unlink(missing_ok=True)
                fichier = final
            return fichier
        raise ErreurProduction("Moteur d'images « Mes propres images » : importez les images vous-même "
                               "(bouton « Importer… »).")

    def references(self, forcer: bool = False, personnages: list[str] | None = None,
                   lieux: list[str] | None = None) -> Bilan:
        """Fiches de référence des personnages et des lieux (celles qui manquent, ou toutes si forcer)."""
        b = Bilan()
        dossier = self.film.sous_dossier("references")
        taches = []
        for p in self.film.personnages:
            if personnages is not None and p.id not in personnages:
                continue
            if forcer or not (p.image and self.film.chemin(p.image).exists()):
                taches.append(("perso", p))
        for l in self.film.lieux:
            if lieux is not None and l.id not in lieux:
                continue
            if forcer or not (l.image and self.film.chemin(l.image).exists()):
                taches.append(("lieu", l))
        for n, (genre, el) in enumerate(taches, 1):
            self.verifier_arret()
            libelle = f"Référence {n}/{len(taches)} ({el.nom})"
            try:
                if genre == "perso":
                    f = self._dessiner(f"perso_{el.id}", dossier, libelle, prompt_reference_personnage(self.film, el),
                                       prompt_image_comfy(self.film, perso=el), [], "carre")
                else:
                    f = self._dessiner(f"lieu_{el.id}", dossier, libelle, prompt_reference_lieu(self.film, el),
                                       prompt_image_comfy(self.film, lieu=el), [], "film")
            except forfait.ErreurForfait as e:
                b.problemes.append(f"{el.nom} : {e}")
                if e.bloquante:
                    break
                continue
            except (comfy.ErreurComfy, ErreurProduction, video.ErreurVideo) as e:
                b.problemes.append(f"{el.nom} : {e}")
                break
            el.image = self.film.relatif(f)
            self.sauver()
            b.faits.append(f"Référence dessinée : {el.nom}")
        return b

    def images(self, indices: list[int] | None = None, forcer: bool = False) -> Bilan:
        """Premières images des plans (sauf les plans enchaînés, qui partent du plan précédent)."""
        b = Bilan()
        indices = list(range(len(self.film.plans))) if indices is None else indices
        dossier = self.film.sous_dossier("images")
        a_faire = [i for i in indices if not self.film.plans[i].enchainer
                   and (forcer or not (self.film.plans[i].image and self.film.chemin(self.film.plans[i].image).exists()))]
        for n, i in enumerate(a_faire, 1):
            self.verifier_arret()
            plan = self.film.plans[i]
            prompt, refs = prompt_image_plan(self.film, i)
            libelle = f"Image {n}/{len(a_faire)} (plan {i + 1})"
            try:
                f = self._dessiner(f"plan_{i + 1:02d}", dossier, libelle, prompt, prompt_image_comfy(self.film, i),
                                   refs, "film")
            except forfait.ErreurForfait as e:
                b.problemes.append(f"Plan {i + 1} : {e}")
                if e.bloquante:
                    break
                continue
            except (comfy.ErreurComfy, ErreurProduction, video.ErreurVideo) as e:
                b.problemes.append(f"Plan {i + 1} : {e}")
                break
            plan.image = self.film.relatif(f)
            self.sauver()
            b.faits.append(f"Première image du plan {i + 1}")
        return b

    # -- voix ----------------------------------------------------------------------
    def voix(self, indices: list[int] | None = None, forcer: bool = False) -> Bilan:
        b = Bilan()
        if self.film.mode_voix != "tts":
            return b
        indices = list(range(len(self.film.plans))) if indices is None else indices
        if not any(self.film.plans[i].repliques for i in indices):
            return b
        if self.r.comfy_liberer_vram:
            self.client.liberer()
        try:
            problemes = voix.generer_plans(self.film, indices, self.r, self.etat, self.arret, forcer)
        except tts.Annule:
            raise Arrete() from None
        except tts.ErreurTTS as e:
            b.problemes.append(str(e))
            return b
        self.sauver()
        b.problemes += problemes
        b.faits.append("Voix prêtes" + (" (avec des manques)" if problemes else ""))
        return b

    # -- vidéos -----------------------------------------------------------------------
    def image_de_depart(self, i: int) -> Path:
        plan = self.film.plans[i]
        dossier = self.film.sous_dossier("images")
        if plan.enchainer and i > 0:
            precedent = self.film.plans[i - 1].prise
            if precedent and self.film.chemin(precedent.fichier).exists():
                self.etat(f"Plan {i + 1} : reprise de la dernière image du plan {i}…")
                return video.derniere_image(self.film.chemin(precedent.fichier), dossier / f"plan_{i + 1:02d}_suite.png")
            if not (plan.image and self.film.chemin(plan.image).exists()):
                raise ErreurProduction(f"Plan {i + 1} : il s'enchaîne au plan {i}, qui n'a pas encore de vidéo. "
                                       f"Rendez d'abord le plan {i}.")
        if not (plan.image and self.film.chemin(plan.image).exists()):
            raise ErreurProduction(f"Plan {i + 1} : pas de première image. Dessinez-la ou importez-la.")
        return self.film.chemin(plan.image)

    def videos(self, indices: list[int] | None = None, nouvelle_prise: bool = False) -> Bilan:
        """Rend les plans avec ComfyUI. Sans `nouvelle_prise`, seuls les plans sans vidéo sont rendus."""
        b = Bilan()
        indices = list(range(len(self.film.plans))) if indices is None else sorted(indices)
        a_faire = [i for i in indices if nouvelle_prise or not (self.film.plans[i].prise and
                                                                 self.film.chemin(self.film.plans[i].prise.fichier).exists())]
        if not a_faire:
            return b
        wf = comfy.charger_workflow(self.r.comfy_workflow_video)
        if wf.genre != "video":
            raise ErreurProduction(f"Le workflow « {wf.nom} » produit des images, pas des vidéos.")
        tts.liberer()  # rend la mémoire vidéo à ComfyUI
        try:
            self.comfy_pret()
        except comfy.ErreurComfy as e:
            b.problemes.append(str(e))
            return b
        dossier = self.film.sous_dossier("clips")
        for n, i in enumerate(a_faire, 1):
            self.verifier_arret()
            plan = self.film.plans[i]
            try:
                depart = self.image_de_depart(i)
            except (ErreurProduction, video.ErreurVideo) as e:
                b.problemes.append(str(e))
                continue
            envoi = video.ajuster_image(depart, dossier / f"_depart_{i + 1:02d}.png", self.film.largeur, self.film.hauteur)
            d = duree_rendu(self.film, plan, self.r)
            numero = len(plan.prises) + 1
            destination = dossier / f"plan_{i + 1:02d}_prise_{numero}.mp4"
            libelle = f"Vidéo {n}/{len(a_faire)} (plan {i + 1}, {d:g} s)"
            self.etat(f"{libelle} : envoi à ComfyUI…")
            debut = time.time()
            try:
                fichier, graine = self.client.rendre(
                    wf, destination, prompt_video(self.film, plan), self.film.negatif, envoi, d, self.film.fps,
                    self.film.largeur, self.film.hauteur, delai=self.r.comfy_delai_minutes * 60, arret=self.arret,
                    attente=self._attente_comfy(libelle))
            except comfy.Arrete:
                raise Arrete() from None
            except comfy.ErreurComfy as e:
                b.problemes.append(f"Plan {i + 1} : {e}")
                if "ne répond pas" in str(e) or "carte" in str(e):
                    break
                continue
            finally:
                envoi.unlink(missing_ok=True)
            try:
                d_reelle = video.duree(fichier)
            except video.ErreurVideo:
                d_reelle = d
            plan.prises.append(Prise(self.film.relatif(fichier), graine, round(d_reelle, 2),
                                     time.strftime("%Y-%m-%d %H:%M")))
            plan.prise_choisie = len(plan.prises) - 1
            self.sauver()
            b.faits.append(f"Plan {i + 1} rendu en {int(time.time() - debut)} s (prise {numero})")
        return b

    # -- montage ---------------------------------------------------------------------
    def exporter(self, destination: str | Path | None = None) -> tuple[Path, Bilan]:
        b = Bilan()
        plans = [(i, p) for i, p in enumerate(self.film.plans) if p.prise and self.film.chemin(p.prise.fichier).exists()]
        manquants = [i + 1 for i, p in enumerate(self.film.plans) if (i, p) not in plans]
        if not plans:
            raise ErreurProduction("Aucun plan n'a encore de vidéo : rien à monter.")
        if manquants:
            b.problemes.append("Plans sans vidéo, laissés de côté : " + ", ".join(map(str, manquants)))
        export = self.film.sous_dossier("export")
        montage = export / "_montage"
        montage.mkdir(exist_ok=True)
        clips, srt, t = [], [], 0.0
        for n, (i, plan) in enumerate(plans, 1):
            self.verifier_arret()
            self.etat(f"Montage : plan {n} sur {len(plans)}…")
            voix_plan = None
            if self.film.mode_voix == "tts" and plan.voix and self.film.chemin(plan.voix).exists():
                voix_plan = self.film.chemin(plan.voix)
            sortie = montage / f"plan_{i + 1:02d}.mp4"
            d = video.habiller_clip(self.film.chemin(plan.prise.fichier), sortie, self.film.largeur,
                                    self.film.hauteur, self.film.fps, voix_plan, self.film.son_modele,
                                    self.film.volume_modele)
            clips.append(sortie)
            if voix_plan:
                srt += [(t + a, t + z, texte) for a, z, texte in plan.sous_titres]
            elif self.film.mode_voix == "modele":
                srt += [(t + 0.3, t + d - 0.2, f"{r.texte}") for r in plan.repliques[:1]]
            t += d
        nom = identifiant(self.film.titre, longueur=60) or "film"
        destination = Path(destination) if destination else export / f"{nom}.mp4"
        self.etat("Montage : assemblage, musique et niveau sonore…")
        musique = self.film.chemin(self.film.musique) if self.film.musique else None
        if musique and not musique.exists():
            b.problemes.append(f"Musique introuvable : {musique}")
            musique = None
        video.assembler(clips, destination, musique, self.film.volume_musique)
        for c in clips:
            c.unlink(missing_ok=True)
        try:
            montage.rmdir()
        except OSError:
            pass
        if srt:
            video.ecrire_srt(srt, destination.with_suffix(".srt"))
        b.faits.append(f"Film exporté : {destination.name} ({int(t // 60)} min {int(t % 60):02d} s)")
        return destination, b

    # -- tout ------------------------------------------------------------------------
    def tout(self, exporter: bool = True) -> tuple[Path | None, Bilan]:
        b = Bilan()
        if self.r.moteur_images != "manuel":
            b.ajouter(self.references())
            if b.problemes:
                return None, b
            b.ajouter(self.images())
            if b.problemes:
                return None, b
        b.ajouter(self.voix())
        b.ajouter(self.videos())
        if exporter and any(p.prise for p in self.film.plans):
            chemin, b2 = self.exporter()
            b.ajouter(b2)
            return chemin, b
        return None, b
