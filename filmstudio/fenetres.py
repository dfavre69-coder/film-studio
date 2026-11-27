"""Fenêtres secondaires : réglages, carte d'un workflow ComfyUI, collage d'une réponse d'IA."""
from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import comfy
from .config import LANGUES_TTS, MODELES_TTS, Reglages

ROLES_MULTIPLES = ("graine", "images", "secondes", "fps", "largeur", "hauteur")
AUCUN = "— aucun —"


def zone_texte(parent, hauteur=4, largeur=60) -> tk.Text:
    cadre = ttk.Frame(parent)
    t = tk.Text(cadre, height=hauteur, width=largeur, wrap="word", undo=True, font=("Segoe UI", 10))
    barre = ttk.Scrollbar(cadre, orient="vertical", command=t.yview)
    t.configure(yscrollcommand=barre.set)
    t.pack(side="left", fill="both", expand=True)
    barre.pack(side="right", fill="y")
    t.cadre = cadre
    return t


def lire(t: tk.Text) -> str:
    return t.get("1.0", "end").strip()


def ecrire(t: tk.Text, texte: str) -> None:
    t.delete("1.0", "end")
    t.insert("1.0", texte or "")
    t.edit_reset()


def en_fond(fenetre, travail, fini) -> None:
    """Lance `travail()` dans un fil séparé, puis `fini(resultat, erreur)` dans l'interface."""
    resultat = {}

    def fil():
        try:
            resultat["ok"] = travail()
        except Exception as e:  # noqa: BLE001
            resultat["err"] = e

    th = threading.Thread(target=fil, daemon=True)
    th.start()

    def guetter():
        if th.is_alive():
            fenetre.after(150, guetter)
        else:
            fini(resultat.get("ok"), resultat.get("err"))
    fenetre.after(150, guetter)


# ---------------------------------------------------------------------------
# Carte d'un workflow
# ---------------------------------------------------------------------------
class FenetreCarte(tk.Toplevel):
    """Montre où Film Studio écrit chaque réglage dans le workflow, et permet de corriger."""

    def __init__(self, parent, wf: comfy.Workflow, au_retour):
        super().__init__(parent)
        self.title(f"Workflow « {wf.nom} »")
        self.transient(parent)
        self.wf = wf
        self.au_retour = au_retour
        self.champs = comfy.champs(wf.graphe)
        self.libelles = [AUCUN] + [c[2] for c in self.champs]
        cadre = ttk.Frame(self, padding=12)
        cadre.pack(fill="both", expand=True)
        ttk.Label(cadre, text=("Film Studio remplit ces champs à chaque plan. Le repérage est automatique ; "
                               "corrigez-le si besoin.\nLes réglages marqués * s'appliquent aussi aux autres "
                               "champs du même nom (par exemple toutes les largeurs)."),
                  wraplength=620, justify="left").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Label(cadre, text="Nom :").grid(row=1, column=0, sticky="w")
        self.v_nom = tk.StringVar(value=wf.nom)
        ttk.Entry(cadre, textvariable=self.v_nom, width=40).grid(row=1, column=1, sticky="w")
        ttk.Label(cadre, text="Type : " + ("image → vidéo" if wf.genre == "video" else "texte → image")).grid(
            row=1, column=2, sticky="w", padx=8)
        self.choix: dict[str, tk.StringVar] = {}
        ligne = 2
        roles = [r for r in comfy.ROLES if not (wf.genre == "image" and r in ("image", "images", "secondes", "fps"))]
        for role in roles:
            cibles = wf.carte.get(role) or []
            v = tk.StringVar(value=self._libelle(cibles[0]) if cibles else AUCUN)
            self.choix[role] = v
            etoile = " *" if role in ROLES_MULTIPLES else ""
            ttk.Label(cadre, text=comfy.ROLES[role] + etoile).grid(row=ligne, column=0, sticky="w", pady=2)
            ttk.Combobox(cadre, textvariable=v, values=self.libelles, width=62, state="readonly").grid(
                row=ligne, column=1, columnspan=2, sticky="we", pady=2)
            ligne += 1
        if wf.genre == "video":
            ttk.Label(cadre, text="Nombre d'images = pas × n + 1, pas :").grid(row=ligne, column=0, sticky="w", pady=6)
            self.v_pas = tk.IntVar(value=wf.pas_images)
            ttk.Spinbox(cadre, from_=1, to=16, textvariable=self.v_pas, width=5).grid(row=ligne, column=1, sticky="w")
            ttk.Label(cadre, text="(LTX : 8 · Wan : 4 · autre : 1)").grid(row=ligne, column=2, sticky="w")
            ligne += 1
        boutons = ttk.Frame(cadre)
        boutons.grid(row=ligne, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(boutons, text="Enregistrer", command=self._ok).pack(side="right")
        ttk.Button(boutons, text="Annuler", command=self.destroy).pack(side="right", padx=6)
        self.grab_set()

    def _libelle(self, cible) -> str:
        for nid, nom, lib, _ in self.champs:
            if nid == cible[0] and nom == cible[1]:
                return lib
        return AUCUN

    def _ok(self):
        carte = {}
        for role, v in self.choix.items():
            if v.get() == AUCUN:
                continue
            idx = self.libelles.index(v.get()) - 1
            nid, nom, _, _ = self.champs[idx]
            cibles = [[nid, nom]]
            anciennes = self.wf.carte.get(role) or []
            if anciennes and list(anciennes[0]) == cibles[0]:
                carte[role] = [list(c) for c in anciennes]  # choix inchangé : on garde tout le repérage
                continue
            if role in ROLES_MULTIPLES:
                cibles += [[n, c] for n, c, _, val in self.champs if c == nom and n != nid
                           and isinstance(val, (int, float)) and not isinstance(val, bool)]
            carte[role] = cibles
        self.wf.carte = carte
        nom = self.v_nom.get().strip() or self.wf.nom
        nom = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in nom).strip()
        self.wf.nom = nom
        if hasattr(self, "v_pas"):
            self.wf.pas_images = max(1, int(self.v_pas.get() or 1))
        manque = self.wf.manquants()
        if manque and not messagebox.askyesno("Workflow incomplet", "Il manque : " + ", ".join(
                comfy.ROLES[m] for m in manque) + ".\nEnregistrer quand même ?", parent=self):
            return
        comfy.enregistrer_workflow(self.wf)
        self.destroy()
        self.au_retour(self.wf)


def importer_workflow(parent, genre: str, au_retour) -> None:
    chemin = filedialog.askopenfilename(
        parent=parent, title="Workflow ComfyUI exporté au format API",
        filetypes=[("Workflow ComfyUI (API)", "*.json"), ("Tous les fichiers", "*.*")])
    if not chemin:
        return
    try:
        graphe = comfy.lire_graphe(chemin)
    except comfy.ErreurComfy as e:
        messagebox.showerror("Import impossible", str(e), parent=parent)
        return
    nom = Path(chemin).stem.replace(" ", "_")
    pas = 4 if "wan" in nom.lower() or any("Wan" in n.get("class_type", "") for n in graphe.values()) else 8
    wf = comfy.Workflow(nom, genre, graphe, comfy.deviner_carte(graphe, genre), pas)
    FenetreCarte(parent, wf, au_retour)


# ---------------------------------------------------------------------------
# Réglages
# ---------------------------------------------------------------------------
class FenetreReglages(tk.Toplevel):
    def __init__(self, parent, reglages: Reglages, au_retour, onglet: int = 0):
        super().__init__(parent)
        self.title("Réglages")
        self.transient(parent)
        self.r = reglages.copie()
        self.au_retour = au_retour
        self.vars: dict[str, tk.Variable] = {}
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)
        self._onglet_comfy(nb)
        self._onglet_forfait(nb)
        self._onglet_ollama(nb)
        self._onglet_voix(nb)
        nb.select(onglet)
        bas = ttk.Frame(self, padding=(10, 0, 10, 10))
        bas.pack(fill="x")
        ttk.Button(bas, text="Enregistrer", command=self._ok).pack(side="right")
        ttk.Button(bas, text="Annuler", command=self.destroy).pack(side="right", padx=6)
        self.grab_set()

    # -- aides de construction ------------------------------------------------------
    def _var(self, cle, genre=tk.StringVar):
        v = genre(value=getattr(self.r, cle))
        self.vars[cle] = v
        return v

    def _ligne(self, cadre, ligne, texte, cle, largeur=50, genre=tk.StringVar, parcourir=None, valeurs=None):
        ttk.Label(cadre, text=texte).grid(row=ligne, column=0, sticky="w", pady=3)
        v = self._var(cle, genre)
        if valeurs is not None:
            w = ttk.Combobox(cadre, textvariable=v, values=valeurs, width=largeur)
        elif genre is tk.BooleanVar:
            w = ttk.Checkbutton(cadre, variable=v)
        else:
            w = ttk.Entry(cadre, textvariable=v, width=largeur)
        w.grid(row=ligne, column=1, sticky="we", pady=3)
        if parcourir:
            ttk.Button(cadre, text="…", width=3, command=lambda: self._parcourir(v, parcourir)).grid(
                row=ligne, column=2, padx=4)
        return w

    def _parcourir(self, var, types):
        chemin = filedialog.askopenfilename(parent=self, filetypes=types)
        if chemin:
            var.set(chemin)

    def _resultat(self, label: ttk.Label, travail):
        label.configure(text="Vérification en cours…", foreground="")

        def fini(ok, err):
            if not label.winfo_exists():
                return
            if err:
                label.configure(text=str(err), foreground="#b00020")
            else:
                label.configure(text=str(ok), foreground="#1b5e20")
        en_fond(self, travail, fini)

    def _lire(self) -> Reglages:
        r = self.r.copie()
        for cle, v in self.vars.items():
            try:
                val = v.get()
            except tk.TclError:
                continue
            ancien = getattr(r, cle)
            if isinstance(ancien, bool):
                val = bool(val)
            elif isinstance(ancien, int):
                val = int(float(val or 0))
            elif isinstance(ancien, float):
                val = float(str(val or 0).replace(",", "."))
            setattr(r, cle, val)
        return r

    # -- onglets ----------------------------------------------------------------------
    def _onglet_comfy(self, nb):
        c = ttk.Frame(nb, padding=12)
        nb.add(c, text="ComfyUI (vidéos)")
        c.columnconfigure(1, weight=1)
        ttk.Label(c, text=("ComfyUI rend les vidéos sur votre carte graphique. Préparez et testez votre workflow "
                           "dans ComfyUI (par exemple « LTX-2.5 Image to Video » de la bibliothèque de modèles), "
                           "puis exportez-le avec Workflow → Exporter (API) et importez-le ici."),
                  wraplength=640, justify="left").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        self._ligne(c, 1, "Adresse de ComfyUI", "comfy_url")
        lab = ttk.Label(c, text="", wraplength=640)
        ttk.Button(c, text="Tester", command=lambda: self._resultat(
            lab, lambda: comfy.ClientComfy(self.vars["comfy_url"].get()).description())).grid(row=1, column=2, padx=4)
        lab.grid(row=2, column=0, columnspan=4, sticky="w")

        self.cb_video = self._ligne(c, 3, "Workflow vidéo", "comfy_workflow_video", valeurs=comfy.lister_workflows("video"))
        self.cb_video.configure(state="readonly")
        b = ttk.Frame(c)
        b.grid(row=4, column=1, sticky="w")
        ttk.Button(b, text="Importer…", command=lambda: importer_workflow(self, "video", self._wf_importe)).pack(side="left")
        ttk.Button(b, text="Champs remplis…", command=lambda: self._modifier_carte("comfy_workflow_video")).pack(
            side="left", padx=4)
        ttk.Button(b, text="Retirer", command=lambda: self._retirer("comfy_workflow_video", "video")).pack(side="left")

        self.cb_image = self._ligne(c, 5, "Workflow d'images (facultatif)", "comfy_workflow_image",
                                    valeurs=[""] + comfy.lister_workflows("image"))
        self.cb_image.configure(state="readonly")
        b = ttk.Frame(c)
        b.grid(row=6, column=1, sticky="w")
        ttk.Button(b, text="Importer…", command=lambda: importer_workflow(self, "image", self._wf_importe)).pack(side="left")
        ttk.Button(b, text="Champs remplis…", command=lambda: self._modifier_carte("comfy_workflow_image")).pack(
            side="left", padx=4)
        ttk.Button(b, text="Retirer", command=lambda: self._retirer("comfy_workflow_image", "image")).pack(side="left")
        ttk.Label(c, text="(pour dessiner les images en local, sans ChatGPT — la ressemblance des personnages "
                          "d'un plan à l'autre est alors moins bonne)", foreground="#555").grid(
            row=7, column=1, columnspan=3, sticky="w")
        self._ligne(c, 8, "Durée maximale d'un plan (s)", "duree_max_plan", 8, tk.DoubleVar)
        self._ligne(c, 9, "Abandon d'un rendu après (min)", "comfy_delai_minutes", 8, tk.IntVar)
        self._ligne(c, 10, "Vider la mémoire de ComfyUI avant les voix", "comfy_liberer_vram", genre=tk.BooleanVar)
        self._cadre_comfy_local(c, 11)

    def _cadre_comfy_local(self, parent, ligne):
        from . import comfy_local
        c = ttk.LabelFrame(parent, text="ComfyUI sur ce PC (démarré et arrêté par Film Studio)", padding=8)
        c.grid(row=ligne, column=0, columnspan=4, sticky="we", pady=(10, 0))
        c.columnconfigure(1, weight=1)
        ttk.Label(c, text="Dossier de ComfyUI").grid(row=0, column=0, sticky="w")
        v = self._var("comfy_dossier")
        if not v.get():
            v.set(str(comfy_local.dossier_par_defaut()))
        ttk.Entry(c, textvariable=v, width=50).grid(row=0, column=1, sticky="we", pady=2)

        def choisir():
            d = filedialog.askdirectory(parent=self, title="Dossier de ComfyUI")
            if d:
                v.set(d)
                maj()
        ttk.Button(c, text="…", width=3, command=choisir).grid(row=0, column=2, padx=4)
        ttk.Label(c, text="Démarrer ComfyUI automatiquement").grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(c, variable=self._var("comfy_demarrage_auto", tk.BooleanVar)).grid(row=1, column=1, sticky="w")
        ttk.Label(c, text="Options de lancement").grid(row=2, column=0, sticky="w")
        ttk.Entry(c, textvariable=self._var("comfy_options"), width=30).grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(c, text="ex. --reserve-vram 4", foreground="#555").grid(row=2, column=1, sticky="e")
        ttk.Label(c, text="Jeton Hugging Face").grid(row=5, column=0, sticky="w")
        ttk.Entry(c, textvariable=self._var("hf_jeton"), width=30, show="•").grid(row=5, column=1, sticky="w", pady=2)
        ttk.Label(c, text="pour les modèles protégés (LTX-2.5)", foreground="#555").grid(row=5, column=1, sticky="e")
        etat = ttk.Label(c, text="", wraplength=620)
        etat.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

        def maj():
            inst = comfy_local.trouver_installation(v.get())
            if inst is None:
                etat.configure(text="ComfyUI n'est pas installé dans ce dossier.", foreground="#b00020")
                return
            modeles = comfy_local.dossier_modeles(v.get())
            presents = sum((modeles / m.sous_dossier / m.nom).is_file() for m in comfy_local.MODELES_LTX)
            etat.configure(text=f"ComfyUI trouvé ({inst.racine}). Modèles LTX-2.5 : {presents} fichier(s) sur "
                                f"{len(comfy_local.MODELES_LTX)}.", foreground="#1b5e20")

        def console(args):
            self._lire().copie(comfy_dossier=v.get()).enregistrer()
            comfy_local.ouvrir_console(args + ["--dossier", v.get()])
            etat.configure(text="Suivez l'avancement dans la fenêtre noire. Rouvrez ensuite les Réglages.",
                           foreground="")

        def demarrer():
            r = self._lire()
            self._resultat(etat, lambda: ("ComfyUI démarré." if comfy_local.GESTIONNAIRE.assurer(r)
                                          else "ComfyUI répond déjà."))
        b = ttk.Frame(c)
        b.grid(row=3, column=1, columnspan=2, sticky="w", pady=4)
        ttk.Button(b, text="Installer ComfyUI…", command=lambda: console(["installer-comfy"])).pack(side="left")
        ttk.Button(b, text="Modèles et workflow LTX-2.5…",
                   command=lambda: console(["installer-workflow"])).pack(side="left", padx=4)
        ttk.Button(b, text="Démarrer maintenant", command=demarrer).pack(side="left")
        maj()

    def _wf_importe(self, wf: comfy.Workflow):
        cle = "comfy_workflow_video" if wf.genre == "video" else "comfy_workflow_image"
        cb = self.cb_video if wf.genre == "video" else self.cb_image
        cb.configure(values=([""] if wf.genre == "image" else []) + comfy.lister_workflows(wf.genre))
        self.vars[cle].set(wf.nom)

    def _modifier_carte(self, cle):
        nom = self.vars[cle].get()
        if not nom:
            messagebox.showinfo("Workflow", "Importez d'abord un workflow.", parent=self)
            return
        try:
            wf = comfy.charger_workflow(nom)
        except comfy.ErreurComfy as e:
            messagebox.showerror("Workflow", str(e), parent=self)
            return
        ancien = wf.nom

        def retour(w):
            if w.nom != ancien:
                comfy.supprimer_workflow(ancien)
            self._wf_importe(w)
        FenetreCarte(self, wf, retour)

    def _retirer(self, cle, genre):
        nom = self.vars[cle].get()
        if nom and messagebox.askyesno("Retirer", f"Retirer le workflow « {nom} » de Film Studio ?", parent=self):
            comfy.supprimer_workflow(nom)
            self.vars[cle].set("")
            (self.cb_video if genre == "video" else self.cb_image).configure(
                values=([""] if genre == "image" else []) + comfy.lister_workflows(genre))

    def _onglet_forfait(self, nb):
        from . import forfait
        c = ttk.Frame(nb, padding=12)
        nb.add(c, text="Claude et ChatGPT")
        c.columnconfigure(1, weight=1)
        ttk.Label(c, text=("Sans clé API : Film Studio lance Claude Code et Codex, connectés à vos abonnements "
                           "(install.bat les installe et les connecte). Claude écrit le découpage ; ChatGPT "
                           "dessine les fiches et les premières images."), wraplength=640, justify="left").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self._ligne(c, 1, "Claude Code (vide = automatique)", "claude_commande", parcourir=[("Programme", "*.exe *.cmd"),
                                                                                           ("Tous", "*.*")])
        self._ligne(c, 2, "Modèle Claude (vide = par défaut)", "claude_modele", 20, valeurs=["", "sonnet", "opus", "haiku"])
        lab_c = ttk.Label(c, text="", wraplength=640)
        ttk.Button(c, text="Vérifier Claude", command=lambda: self._resultat(
            lab_c, lambda: forfait.verifier_claude(self._lire()))).grid(row=3, column=1, sticky="w", pady=4)
        lab_c.grid(row=4, column=0, columnspan=3, sticky="w")
        self._ligne(c, 5, "Codex (vide = automatique)", "codex_commande", parcourir=[("Programme", "*.exe *.cmd"),
                                                                                    ("Tous", "*.*")])
        self._ligne(c, 6, "Options de « codex exec » (avancé)", "codex_options")
        lab_x = ttk.Label(c, text="", wraplength=640)
        ttk.Button(c, text="Vérifier Codex", command=lambda: self._resultat(
            lab_x, lambda: forfait.verifier_codex(self._lire()))).grid(row=7, column=1, sticky="w", pady=4)
        lab_x.grid(row=8, column=0, columnspan=3, sticky="w")

    def _onglet_ollama(self, nb):
        from . import ollama
        c = ttk.Frame(nb, padding=12)
        nb.add(c, text="Ollama")
        c.columnconfigure(1, weight=1)
        ttk.Label(c, text="Pour écrire le découpage avec un modèle local (gratuit, hors ligne).",
                  wraplength=640).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self._ligne(c, 1, "Adresse d'Ollama", "ollama_url")
        cb = self._ligne(c, 2, "Modèle", "ollama_modele", 30, valeurs=[])
        lab = ttk.Label(c, text="")
        lab.grid(row=3, column=1, sticky="w")

        def actualiser():
            def fini(ok, err):
                if err:
                    lab.configure(text=str(err), foreground="#b00020")
                else:
                    cb.configure(values=ok)
                    lab.configure(text=f"{len(ok)} modèle(s) installé(s).", foreground="#1b5e20")
            en_fond(self, lambda: ollama.lister_modeles(self.vars["ollama_url"].get()), fini)
        ttk.Button(c, text="Actualiser", command=actualiser).grid(row=2, column=2, padx=4)
        self._ligne(c, 4, "Contexte (jetons)", "ollama_contexte", 10, tk.IntVar)
        self._ligne(c, 5, "Température", "ollama_temperature", 10, tk.DoubleVar)
        self._ligne(c, 6, "Sans « réflexion » (plus rapide)", "ollama_sans_reflexion", genre=tk.BooleanVar)
        self.after(200, actualiser)

    def _onglet_voix(self, nb):
        from . import tts
        c = ttk.Frame(nb, padding=12)
        nb.add(c, text="Voix (Qwen3-TTS)")
        c.columnconfigure(1, weight=1)
        ttk.Label(c, text=("Qwen3-TTS lit les répliques. Voici la voix du narrateur ; chaque personnage peut avoir "
                           "la sienne (onglet Personnages)."), wraplength=640).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self._ligne(c, 1, "python.exe de l'environnement Qwen3-TTS", "tts_python",
                    parcourir=[("Python", "python.exe"), ("Tous", "*.*")])
        self._ligne(c, 2, "Modèle", "tts_modele", valeurs=MODELES_TTS)
        self._ligne(c, 3, "Langue", "tts_langue", 20, valeurs=LANGUES_TTS)
        self._ligne(c, 4, "Voix à cloner (extrait audio)", "tts_voix_reference",
                    parcourir=[("Audio", "*.wav *.mp3 *.flac *.m4a"), ("Tous", "*.*")])
        self._ligne(c, 5, "Ce qui est dit dans l'extrait", "tts_texte_reference")
        self._ligne(c, 6, "Voix prédéfinie (CustomVoice)", "tts_locuteur", 20)
        self._ligne(c, 7, "Consigne de style (CustomVoice)", "tts_consigne")
        self._ligne(c, 8, "Phrases par lot", "tts_lot", 6, tk.IntVar)
        self._ligne(c, 9, "Garder le modèle chargé (min)", "tts_garder_minutes", 6, tk.IntVar)
        lab = ttk.Label(c, text="", wraplength=640)
        ttk.Button(c, text="Diagnostiquer", command=lambda: self._resultat(
            lab, lambda: tts.diagnostiquer(self._lire()))).grid(row=10, column=1, sticky="w", pady=4)
        lab.grid(row=11, column=0, columnspan=3, sticky="w")

    def _ok(self):
        try:
            r = self._lire()
        except ValueError as e:
            messagebox.showerror("Réglages", f"Valeur incorrecte : {e}", parent=self)
            return
        r.enregistrer()
        self.destroy()
        self.au_retour(r)


# ---------------------------------------------------------------------------
# Coller une réponse
# ---------------------------------------------------------------------------
class FenetreCollage(tk.Toplevel):
    def __init__(self, parent, au_retour):
        super().__init__(parent)
        self.title("Coller la réponse de l'IA")
        self.transient(parent)
        self.au_retour = au_retour
        c = ttk.Frame(self, padding=12)
        c.pack(fill="both", expand=True)
        ttk.Label(c, text="Collez ici toute la réponse de l'IA (le JSON du découpage) :").pack(anchor="w")
        self.t = zone_texte(c, 20, 90)
        self.t.cadre.pack(fill="both", expand=True, pady=6)
        try:
            presse_papier = self.clipboard_get()
            if "{" in presse_papier:
                ecrire(self.t, presse_papier)
        except tk.TclError:
            pass
        b = ttk.Frame(c)
        b.pack(fill="x")
        ttk.Button(b, text="Créer le film", command=self._ok).pack(side="right")
        ttk.Button(b, text="Annuler", command=self.destroy).pack(side="right", padx=6)
        self.grab_set()

    def _ok(self):
        texte = lire(self.t)
        if texte:
            self.destroy()
            self.au_retour(texte)
