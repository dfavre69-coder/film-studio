"""Ligne de commande de Film Studio (sans argument : ouvre l'interface).

    python -m filmstudio                          interface graphique
    python -m filmstudio exemple DOSSIER          crée le film d'exemple (la luciole)
    python -m filmstudio produire DOSSIER         produit tout ce qui manque, puis monte le film
    python -m filmstudio etape NOM DOSSIER        une seule étape : references, images, voix, videos, export
    python -m filmstudio verifier-ia --outil claude|codex
    python -m filmstudio verifier-comfy
"""
from __future__ import annotations

import argparse
import sys

from . import __version__


def _ecrire(texte: str) -> None:
    try:
        print(texte, flush=True)
    except UnicodeEncodeError:
        print(texte.encode("ascii", "replace").decode(), flush=True)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        from .gui import lancer
        lancer()
        return 0
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(prog="filmstudio", description=f"Film Studio {__version__}")
    sous = ap.add_subparsers(dest="commande", required=True)
    s = sous.add_parser("exemple", help="crée le film d'exemple")
    s.add_argument("dossier")
    s = sous.add_parser("produire", help="produit tout ce qui manque puis monte le film")
    s.add_argument("dossier")
    s.add_argument("--sans-export", action="store_true")
    s = sous.add_parser("etape", help="lance une seule étape")
    s.add_argument("nom", choices=["references", "images", "voix", "videos", "export"])
    s.add_argument("dossier")
    s.add_argument("--plans", help="numéros des plans, ex. 1,3,4")
    s.add_argument("--refaire", action="store_true", help="refait aussi ce qui existe déjà (nouvelle prise)")
    s = sous.add_parser("verifier-ia", help="vérifie Claude Code ou Codex")
    s.add_argument("--outil", choices=["claude", "codex"], required=True)
    s.add_argument("--sans-essai", action="store_true")
    sous.add_parser("verifier-comfy", help="vérifie la connexion à ComfyUI et le workflow vidéo")
    s = sous.add_parser("installer-comfy", help="installe ComfyUI avec comfy-cli")
    s.add_argument("--dossier", help="dossier d'installation (par défaut D:\\ai\\ComfyUI ou ~/ComfyUI)")
    s.add_argument("--verifier", action="store_true", help="dit seulement s'il est déjà installé (code 0)")
    s = sous.add_parser("telecharger-modeles", help="télécharge les modèles LTX-2.5 dans ComfyUI")
    s.add_argument("--dossier")
    s.add_argument("--verifier", action="store_true", help="dit seulement s'ils sont déjà tous là (code 0)")
    s = sous.add_parser("installer-workflow", help="installe le workflow officiel LTX-2.5 (et ses modèles)")
    s.add_argument("--dossier")
    s.add_argument("--sans-modeles", action="store_true")
    s.add_argument("--verifier", action="store_true", help="dit seulement s'il est déjà installé (code 0)")
    s = sous.add_parser("demarrer-comfy", help="démarre ComfyUI et attend qu'il soit prêt (Ctrl+C pour l'arrêter)")
    s.add_argument("--dossier")
    s.add_argument("--ouvrir", action="store_true", help="ouvre ComfyUI dans le navigateur")
    sous.add_parser("ouvrir", help="ouvre l'interface").add_argument("dossier", nargs="?")
    a = ap.parse_args(argv)

    from .config import Reglages
    r = Reglages.charger()

    if a.commande == "ouvrir":
        from .gui import lancer
        lancer(a.dossier)
        return 0
    if a.commande == "verifier-ia":
        from . import forfait
        try:
            if a.outil == "claude":
                _ecrire("  " + forfait.verifier_claude(r, essai=not a.sans_essai))
            else:
                _ecrire("  " + forfait.verifier_codex(r))
        except forfait.ErreurForfait as e:
            _ecrire(f"  {e}")
            return 1
        return 0
    if a.commande == "verifier-comfy":
        from . import comfy
        try:
            _ecrire("  " + comfy.ClientComfy(r.comfy_url).description())
            wf = comfy.charger_workflow(r.comfy_workflow_video)
            manque = wf.manquants()
            _ecrire(f"  Workflow vidéo : {wf.nom}" + (f" — à compléter : {', '.join(manque)}" if manque else " — prêt"))
        except comfy.ErreurComfy as e:
            _ecrire(f"  {e}")
            return 1
        return 0
    if a.commande in ("installer-comfy", "telecharger-modeles", "demarrer-comfy", "installer-workflow"):
        from . import comfy_local
        dossier = getattr(a, "dossier", None) or r.comfy_dossier or str(comfy_local.dossier_par_defaut())
        if getattr(a, "verifier", False):
            if a.commande == "installer-workflow":
                from . import comfy
                try:
                    return 0 if not comfy.charger_workflow(r.comfy_workflow_video).manquants() else 1
                except comfy.ErreurComfy:
                    return 1
            if a.commande == "installer-comfy":
                return 0 if comfy_local.trouver_installation(dossier) else 1
            racine = comfy_local.dossier_modeles(dossier)
            return 0 if all((racine / m.sous_dossier / m.nom).is_file() for m in comfy_local.MODELES_LTX) else 1
        for _ in range(3):
            try:
                return _commande_comfy(a, r, dossier)
            except comfy_local.ErreurAcces as e:
                _ecrire(f"\n  {e}\n")
                if not sys.stdin or not sys.stdin.isatty():
                    return 1
                import webbrowser
                webbrowser.open(e.page)
                try:
                    jeton = input("  Collez ici votre jeton Hugging Face (hf_…), puis Entrée (Entrée seule = annuler) : ")
                except EOFError:
                    return 1
                jeton = jeton.strip()
                if not jeton:
                    return 1
                r.hf_jeton = jeton
                r.enregistrer()
                _ecrire("  Jeton enregistré, nouvel essai…")
            except comfy_local.ErreurComfyLocal as e:
                _ecrire(f"  {e}")
                return 1
        _ecrire("  Toujours refusé : vérifiez que les conditions du modèle sont acceptées sur sa page.")
        return 1
    if a.commande == "exemple":
        from .demo import creer_exemple
        film = creer_exemple(a.dossier)
        _ecrire(f"Film d'exemple créé : {film.dossier / 'film.json'}")
        return 0

    from .production import Arrete, Bilan, ErreurProduction, Production
    from .projet import Film
    from . import comfy
    film = Film.ouvrir(a.dossier)
    prod = Production(film, r, etat=lambda t: _ecrire(f"  {t}"))
    try:
        if a.commande == "produire":
            chemin, b = prod.tout(exporter=not a.sans_export)
        else:
            plans = None
            if a.plans:
                plans = [int(x) - 1 for x in a.plans.replace(" ", "").split(",") if x]
            chemin, b = None, Bilan()
            if a.nom == "references":
                b = prod.references(forcer=a.refaire)
            elif a.nom == "images":
                b = prod.images(plans, forcer=a.refaire)
            elif a.nom == "voix":
                b = prod.voix(plans, forcer=a.refaire)
            elif a.nom == "videos":
                b = prod.videos(plans, nouvelle_prise=a.refaire)
            else:
                chemin, b = prod.exporter()
    except (ErreurProduction, comfy.ErreurComfy) as e:
        _ecrire(f"Erreur : {e}")
        return 1
    except Arrete:
        _ecrire("Arrêté.")
        return 1
    finally:
        from . import comfy_local
        comfy_local.GESTIONNAIRE.arreter()
    _ecrire(b.texte())
    if chemin:
        _ecrire(f"→ {chemin}")
    return 1 if b.problemes else 0


def _commande_comfy(a, r, dossier) -> int:
    from . import comfy_local
    if a.commande == "installer-comfy":
        existante = comfy_local.trouver_installation(dossier)
        if existante:
            _ecrire(f"  ComfyUI est déjà installé dans {existante.racine}.")
        else:
            _ecrire(f"Installation de ComfyUI dans {dossier}")
            comfy_local.installer(dossier, etat=lambda t: _ecrire(f"\n>>> {t}"))
            _ecrire(f"  ComfyUI est installé dans {dossier}.")
        r.comfy_dossier = str(dossier)
        r.enregistrer()
    elif a.commande == "installer-workflow":
        r.comfy_dossier = str(dossier)
        wf, remarques = comfy_local.installer_workflow_ltx(r, lambda t: _ecrire(f"  {t}"),
                                                            modeles=not a.sans_modeles)
        for rq in remarques:
            _ecrire(f"  Attention : {rq}")
        _ecrire(f"  Prêt : les vidéos utiliseront « {wf.nom} ».")
    elif a.commande == "telecharger-modeles":
        _ecrire(f"Modèles LTX-2.5 → {comfy_local.dossier_modeles(dossier)}")
        try:
            liste = comfy_local.modeles_du_template(comfy_local.telecharger_template()) or None
        except comfy_local.ErreurComfyLocal:
            liste = None
        fichiers = comfy_local.telecharger_modeles(dossier, liste, etat=lambda t: _ecrire(f"  {t}"),
                                                   jeton=comfy_local.jeton_hf(r))
        _ecrire(f"  {len(fichiers)} fichier(s) en place.")
    else:
        r.comfy_dossier, r.comfy_demarrage_auto = str(dossier), True
        demarre = comfy_local.GESTIONNAIRE.assurer(r, etat=lambda t: _ecrire(f"  {t}"), visible=True)
        if a.ouvrir:
            import webbrowser
            webbrowser.open(r.comfy_url)
        if not demarre:
            _ecrire(f"  ComfyUI répond déjà à {r.comfy_url}.")
            return 0
        _ecrire(f"  {r.comfy_url} — Ctrl+C pour l'arrêter.")
        try:
            comfy_local.GESTIONNAIRE.proc.wait()
        except KeyboardInterrupt:
            pass
        comfy_local.GESTIONNAIRE.arreter()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
