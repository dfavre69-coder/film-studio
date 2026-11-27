"""Écriture du découpage par une IA : prompt, lecture de la réponse, création du film."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Callable

from .config import Reglages
from .projet import NARRATEUR, Film, Lieu, Personnage, Plan, Replique, identifiant

GABARIT = Path(__file__).parent / "assets" / "prompt_film.md"


class ReponseIllisible(ValueError):
    pass


def construire_prompt(film: Film, pitch: str, nb_plans: int = 6, duree_plan: float = 6,
                      duree_max: float = 12) -> str:
    texte = GABARIT.read_text(encoding="utf-8")
    nb_plans = max(1, int(nb_plans))
    remplacements = {
        "{{NB_PLANS}}": str(nb_plans),
        "{{DUREE_MAX}}": f"{duree_max:g}",
        "{{DUREE_PLAN}}": f"{duree_plan:g}",
        "{{DUREE_TOTALE}}": f"{nb_plans * duree_plan:g}",
        "{{LANGUE}}": film.langue or "français",
        "{{PITCH}}": (pitch or film.pitch or "").strip() or "(aucune idée fournie : invente une histoire courte et touchante)",
        "{{STYLE}}": film.style.strip() or "au choix",
        "{{FORMAT}}": {"16:9": "paysage 16:9", "9:16": "vertical 9:16 (format Short)", "1:1": "carré"}.get(
            film.format, film.format),
    }
    for cle, val in remplacements.items():
        texte = texte.replace(cle, val)
    return texte


def extraire_json(reponse: str):
    """Trouve et lit le JSON d'une réponse d'IA, même entouré de texte ou un peu abîmé."""
    texte = (reponse or "").strip().lstrip("﻿")
    bloc = re.search(r"```(?:json)?\s*(.*?)```", texte, re.S | re.I)
    if bloc:
        texte = bloc.group(1).strip()
    debuts = [i for i in (texte.find("{"), texte.find("[")) if i >= 0]
    if not debuts:
        raise ReponseIllisible("Aucun JSON trouvé dans la réponse.")
    debut = min(debuts)
    fin = max(texte.rfind("}"), texte.rfind("]"))
    texte = texte[debut:fin + 1]
    essais = [texte]
    repare = re.sub(r"^\s*//.*$", "", texte, flags=re.M)
    repare = re.sub(r",(\s*[}\]])", r"\1", repare)
    repare = repare.replace("“", '"').replace("”", '"')
    essais.append(repare)
    erreur = None
    for e in essais:
        for strict in (True, False):
            try:
                return json.loads(e, strict=strict)
            except json.JSONDecodeError as err:
                erreur = err
    raise ReponseIllisible(f"JSON invalide : {erreur}")


def _txt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ", ".join(_txt(x) for x in v)
    return str(v).strip()


def _nombre(v, defaut: float, mini: float, maxi: float) -> float:
    try:
        x = float(str(v).replace(",", ".").rstrip("s ").strip())
    except (TypeError, ValueError):
        return defaut
    return min(maxi, max(mini, x))


def film_depuis_ia(reponse: str, base: Film, duree_plan: float = 6, duree_max: float = 12
                   ) -> tuple[Film, list[str]]:
    """Construit un film (personnages, lieux, plans) à partir de la réponse de l'IA.

    Les réglages de `base` (style, format, voix, dossier…) sont conservés. Renvoie aussi les
    remarques sur ce qui a dû être corrigé."""
    donnees = extraire_json(reponse)
    if isinstance(donnees, list):
        donnees = {"plans": donnees}
    if not isinstance(donnees, dict):
        raise ReponseIllisible("La réponse n'est pas un objet JSON.")
    plans_bruts = donnees.get("plans") or donnees.get("scenes") or donnees.get("shots") or []
    if not plans_bruts:
        raise ReponseIllisible("La réponse ne contient aucun plan (« plans »).")
    remarques: list[str] = []
    film = Film.depuis_dict(base.vers_dict(), base.dossier)
    film.personnages, film.lieux, film.plans = [], [], []
    film.titre = _txt(donnees.get("titre")) or base.titre

    ids: set[str] = set()
    for p in donnees.get("personnages") or []:
        if not isinstance(p, dict) or not _txt(p.get("nom")):
            continue
        ident = identifiant(_txt(p["nom"]), ids)
        ids.add(ident)
        film.personnages.append(Personnage(id=ident, nom=_txt(p["nom"]), description=_txt(p.get("description")),
                                           apparence=_txt(p.get("apparence") or p.get("appearance")),
                                           voix=_txt(p.get("voix"))))
    ids = set()
    for l in donnees.get("lieux") or []:
        if not isinstance(l, dict) or not _txt(l.get("nom")):
            continue
        ident = identifiant(_txt(l["nom"]), ids)
        ids.add(ident)
        film.lieux.append(Lieu(id=ident, nom=_txt(l["nom"]), description=_txt(l.get("description")),
                               apparence=_txt(l.get("apparence") or l.get("appearance"))))

    for n, p in enumerate(plans_bruts, 1):
        if not isinstance(p, dict):
            remarques.append(f"Plan {n} ignoré (format inattendu).")
            continue
        plan = Plan(titre=_txt(p.get("titre")) or f"Plan {n}", action=_txt(p.get("action")),
                    image_prompt=_txt(p.get("image") or p.get("image_prompt")),
                    mouvement=_txt(p.get("mouvement") or p.get("motion")), camera=_txt(p.get("camera")),
                    duree=_nombre(p.get("duree"), duree_plan, 2, duree_max),
                    enchainer=bool(p.get("enchainer")) and n > 1)
        nom_lieu = _txt(p.get("lieu"))
        if nom_lieu:
            lieu = film.lieu(nom_lieu)
            if lieu is None:
                ident = identifiant(nom_lieu, {l.id for l in film.lieux})
                lieu = Lieu(id=ident, nom=nom_lieu)
                film.lieux.append(lieu)
                remarques.append(f"Plan {n} : lieu « {nom_lieu} » absent de la liste, ajouté (à décrire).")
            plan.lieu = lieu.id
        noms = p.get("personnages") or []
        if isinstance(noms, str):
            noms = [x.strip() for x in noms.split(",") if x.strip()]
        for nom in noms:
            perso = film.personnage(_txt(nom))
            if perso is None:
                ident = identifiant(_txt(nom), {x.id for x in film.personnages})
                perso = Personnage(id=ident, nom=_txt(nom))
                film.personnages.append(perso)
                remarques.append(f"Plan {n} : personnage « {_txt(nom)} » absent de la liste, ajouté (à décrire).")
            if perso.id not in plan.personnages:
                plan.personnages.append(perso.id)
        for r in p.get("repliques") or p.get("dialogues") or []:
            if isinstance(r, str):
                r = {"personnage": NARRATEUR, "texte": r}
            if not isinstance(r, dict) or not _txt(r.get("texte")):
                continue
            qui = _txt(r.get("personnage")) or NARRATEUR
            perso = film.personnage(qui)
            if perso is not None:
                qui = perso.nom
            elif qui.lower() not in ("narrateur", "narrator", "voix off"):
                remarques.append(f"Plan {n} : « {qui} » parle mais n'est pas un personnage : lu par le narrateur.")
                qui = NARRATEUR
            else:
                qui = NARRATEUR
            plan.repliques.append(Replique(qui, _txt(r["texte"])))
        film.plans.append(plan)
    for perso in film.personnages:
        if not perso.apparence:
            remarques.append(f"Personnage « {perso.nom} » sans apparence : décrivez-le pour qu'il reste reconnaissable.")
    return film, remarques


# ---------------------------------------------------------------------------
# Envoi du prompt à une IA
# ---------------------------------------------------------------------------
def generer(prompt: str, reglages: Reglages, etat: Callable[[str], None] | None = None,
            arret: threading.Event | None = None) -> str:
    """Envoie le prompt au moteur choisi dans les réglages (Claude ou Ollama) et renvoie la réponse."""
    if reglages.moteur_scenario == "claude":
        from . import forfait

        def attente(s):
            if etat:
                etat(f"Claude écrit le découpage… {int(s)} s")
        return forfait.generer_claude(prompt, reglages, attente, arret)
    if reglages.moteur_scenario == "ollama":
        from . import ollama

        def progression(texte, stats):
            if etat:
                etat(f"Ollama écrit le découpage… {stats.caracteres} caractères"
                     + (" (réflexion)" if stats.reflexion else ""))
        return ollama.generer(prompt, reglages, progression, arret)
    raise ValueError("Mode copier-coller : copiez le prompt dans votre IA, puis collez sa réponse.")
