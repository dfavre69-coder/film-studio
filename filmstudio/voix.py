"""Voix des plans avec Qwen3-TTS : chaque personnage peut avoir sa propre voix (clonée ou prédéfinie).

Sans voix propre, un personnage parle avec la voix du narrateur (Réglages → Voix). Les répliques
déjà générées ne sont pas refaites tant que leur texte et leur voix ne changent pas.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Callable

from . import tts
from .config import Reglages
from .projet import Film, Plan
from .video import Segment, assembler_voix


def reglages_pour(film: Film, nom: str, r: Reglages) -> Reglages:
    """Réglages de voix du personnage `nom` (ceux du narrateur s'il n'a pas de voix propre)."""
    perso = film.personnage(nom) if nom else None
    if perso is None:
        return r
    changements = {}
    ref = film.chemin(perso.voix_reference) if perso.voix_reference else None
    if ref and ref.exists():
        changements.update(tts_voix_reference=str(ref), tts_texte_reference=perso.voix_texte)
    if perso.voix_locuteur:
        changements["tts_locuteur"] = perso.voix_locuteur
    if perso.voix_consigne:
        changements["tts_consigne"] = perso.voix_consigne
    return r.copie(**changements) if changements else r


def empreinte_plan(film: Film, plan: Plan, r: Reglages) -> str:
    morceaux = [[x.personnage, x.texte, tts.empreinte(x.texte, reglages_pour(film, x.personnage, r))]
                for x in plan.repliques]
    return hashlib.sha1(json.dumps(morceaux, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def plan_a_jour(film: Film, plan: Plan, r: Reglages) -> bool:
    if not plan.repliques:
        return True
    fichier = film.chemin(plan.voix)
    return bool(fichier and fichier.exists() and plan.empreinte_voix == empreinte_plan(film, plan, r))


def generer_plans(film: Film, indices: list[int], r: Reglages, etat: Callable[[str], None] | None = None,
                  arret: threading.Event | None = None, forcer: bool = False) -> list[str]:
    """Génère la bande-son des plans demandés. Renvoie la liste des problèmes (vide si tout va bien)."""
    dossier = film.sous_dossier("voix")
    a_faire = [i for i in indices if film.plans[i].repliques and (forcer or not plan_a_jour(film, film.plans[i], r))]
    for i in indices:  # plans sans répliques : plus de bande-son
        p = film.plans[i]
        if not p.repliques:
            p.voix, p.duree_voix, p.sous_titres, p.empreinte_voix = "", 0.0, [], ""
    if not a_faire:
        return []
    # Toutes les répliques, regroupées par voix (un seul passage du modèle par voix)
    groupes: dict[str, tuple[Reglages, list[tts.Phrase]]] = {}
    fichiers: dict[tuple[int, int], Path] = {}
    for i in a_faire:
        for k, rep in enumerate(film.plans[i].repliques):
            rv = reglages_pour(film, rep.personnage, r)
            emp = tts.empreinte(rep.texte, rv)
            sortie = dossier / f"plan_{i + 1:02d}_r{k + 1}_{emp}.wav"
            fichiers[(i, k)] = sortie
            if sortie.exists() and not forcer:
                continue
            cle_voix = json.dumps([rv.tts_voix_reference, rv.tts_texte_reference, rv.tts_locuteur, rv.tts_consigne])
            groupes.setdefault(cle_voix, (rv, []))[1].append(tts.Phrase(rep.texte, str(sortie), [i, k]))
    total = sum(len(g[1]) for g in groupes.values())
    faites = 0
    for rv, phrases in groupes.values():
        def evenement(msg):
            nonlocal faites
            if not etat:
                return
            if msg.get("etat") == "fait":
                faites += 1
                etat(f"Voix : réplique {faites} sur {total}")
            elif msg.get("message"):
                etat(f"Voix : {msg['message']}")
        if etat:
            etat(f"Voix : {total} réplique(s) à générer…")
        tts.generer(phrases, rv, evenement, arret)
    problemes = []
    for i in a_faire:
        plan = film.plans[i]
        segments = [Segment(fichiers[(i, k)], rep.texte) for k, rep in enumerate(plan.repliques)]
        manquants = [s for s in segments if not s.fichier.exists()]
        if manquants:
            problemes.append(f"Plan {i + 1} : {len(manquants)} réplique(s) non générée(s).")
            continue
        sortie = dossier / f"plan_{i + 1:02d}.wav"
        plan.duree_voix, plan.sous_titres = assembler_voix(segments, sortie)
        plan.voix = film.relatif(sortie)
        plan.empreinte_voix = empreinte_plan(film, plan, r)
    return problemes
