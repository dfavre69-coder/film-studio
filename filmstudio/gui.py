"""Interface de Film Studio : Histoire → Personnages et lieux → Plans → Production."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from . import __version__, comfy, ia, styles, video
from .config import MOTEURS_IMAGES, MOTEURS_SCENARIO, Reglages
from .demo import film_exemple
from .fenetres import FenetreCollage, FenetreReglages, ecrire, lire, zone_texte
from .production import Arrete, Bilan, ErreurProduction, Production
from .projet import (FORMATS, MODES_VOIX, SONS_MODELE, Film, Lieu, Personnage, Plan, ecrire_repliques,
                     identifiant, lire_repliques)

TYPES_IMAGES = [("Images", "*.png *.jpg *.jpeg *.webp"), ("Tous les fichiers", "*.*")]
TYPES_AUDIO = [("Audio", "*.wav *.mp3 *.flac *.m4a *.ogg"), ("Tous les fichiers", "*.*")]


def relance_au_demarrage(actif: bool) -> None:
    """Windows : tant qu'une production est en cours, Film Studio se relance à l'ouverture de session
    (après une extinction du PC, il reprendra la production tout seul)."""
    if sys.platform != "win32":
        return
    dossier = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    lanceur = dossier / "Film Studio (reprise).cmd"
    try:
        if not actif:
            lanceur.unlink(missing_ok=True)
            return
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        racine = Path(__file__).resolve().parents[1]
        lanceur.write_text(f'@start "" /d "{racine}" "{pythonw}" -m filmstudio\r\n', encoding="ascii",
                           errors="replace")
    except OSError:
        pass


def ouvrir_fichier(chemin: str | Path) -> None:
    chemin = str(chemin)
    try:
        if sys.platform == "win32":
            os.startfile(chemin)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", chemin])
        else:
            subprocess.Popen(["xdg-open", chemin])
    except OSError as e:
        messagebox.showerror("Ouverture impossible", str(e))


class Apercu(ttk.Label):
    """Vignette d'une image (ou d'une vidéo : sa première image)."""

    def __init__(self, parent, taille=(320, 200)):
        super().__init__(parent, text="(aucune image)", anchor="center", relief="groove")
        self.taille = taille
        self._photo = None

    def montrer(self, chemin: Path | None):
        self._photo = None
        if not chemin or not Path(chemin).exists():
            self.configure(image="", text="(aucune image)")
            return
        try:
            with Image.open(chemin) as img:
                img = img.convert("RGB")
                img.thumbnail(self.taille)
                self._photo = ImageTk.PhotoImage(img)
            self.configure(image=self._photo, text="")
        except Exception:  # noqa: BLE001
            self.configure(image="", text=Path(chemin).name)


class Application(tk.Tk):
    def __init__(self, dossier: str | None = None):
        super().__init__()
        self.title(f"Film Studio {__version__}")
        self.geometry("1180x780")
        self.minsize(980, 640)
        try:
            self.iconbitmap(str(Path(__file__).parent / "assets" / "icone.ico"))
        except tk.TclError:
            pass
        self.r = Reglages.charger()
        self.film = Film()
        self.arret = threading.Event()
        self.travail: threading.Thread | None = None
        self.messages: queue.Queue = queue.Queue()
        self.boutons_action: list[ttk.Button] = []
        self.sel_element: tuple[str, str] | None = None   # ("perso"|"lieu", id)
        self.sel_plan: int | None = None
        self._construire()
        self.protocol("WM_DELETE_WINDOW", self._quitter)
        a_ouvrir = dossier or self.r.dernier_film
        if a_ouvrir and (Path(a_ouvrir) / "film.json").exists():
            self._charger(Film.ouvrir(a_ouvrir))
        else:
            self._charger(self.film)
        if self.film.production_en_cours and self.film.dossier:
            self._journaliser("La production de ce film a été interrompue (fermeture, déconnexion…) : "
                              "elle reprend automatiquement là où elle s'était arrêtée.")
            self.v_etat.set("Reprise automatique de la production…")
            self.after(2500, self._reprendre_production)
        if getattr(self.r, "reprise_whiteboard", False):
            self.r.enregistrer()
            self._journaliser("Réglages de Whiteboard Studio repris : Qwen3-TTS, voix clonée, Ollama, Claude Code "
                              "et Codex. Il reste à importer votre workflow ComfyUI (Réglages → ComfyUI).")
        self.after(100, self._lire_messages)

    # =========================================================================
    # Construction
    # =========================================================================
    def _construire(self):
        barre = ttk.Frame(self, padding=(8, 6))
        barre.pack(fill="x")
        for texte, cmd in [("Nouveau", self._nouveau), ("Ouvrir…", self._ouvrir), ("Enregistrer", self._enregistrer),
                           ("Film d'exemple", self._exemple), ("Réglages…", lambda: self._reglages()),
                           ("Dossier du film", self._ouvrir_dossier)]:
            ttk.Button(barre, text=texte, command=cmd).pack(side="left", padx=2)
        self.l_titre = ttk.Label(barre, text="", font=("Segoe UI", 11, "bold"))
        self.l_titre.pack(side="right", padx=8)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8)
        self._onglet_histoire()
        self._onglet_elements()
        self._onglet_plans()
        self._onglet_production()
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._recuperer())

        bas = ttk.Frame(self, padding=(8, 4))
        bas.pack(fill="x")
        self.b_arreter = ttk.Button(bas, text="Arrêter", command=self._arreter, state="disabled")
        self.b_arreter.pack(side="right")
        self.v_etat = tk.StringVar(value="Prêt.")
        ttk.Label(bas, textvariable=self.v_etat).pack(side="left")
        self.progres = ttk.Progressbar(bas, mode="determinate", length=160, value=0)
        self.progres.pack(side="right", padx=8)

    def _action(self, parent, texte, cmd, **pack):
        b = ttk.Button(parent, text=texte, command=cmd)
        b.pack(**({"side": "left", "padx": 3} | pack))
        self.boutons_action.append(b)
        return b

    # -- 1. Histoire ----------------------------------------------------------------------
    def _onglet_histoire(self):
        c = ttk.Frame(self.nb, padding=12)
        self.nb.add(c, text="1. Histoire")
        c.columnconfigure(1, weight=1)
        c.rowconfigure(2, weight=1)
        ttk.Label(c, text="Titre").grid(row=0, column=0, sticky="w")
        self.v_titre = tk.StringVar()
        ttk.Entry(c, textvariable=self.v_titre, width=60).grid(row=0, column=1, sticky="we", pady=3)
        ttk.Label(c, text="Votre idée en quelques phrases (personnages, ce qui leur arrive, la fin) :").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 2))
        self.t_pitch = zone_texte(c, 7)
        self.t_pitch.cadre.grid(row=2, column=0, columnspan=2, sticky="nsew")
        ttk.Label(c, text="Style visuel").grid(row=3, column=0, sticky="nw", pady=(8, 0))
        cadre_style = ttk.Frame(c)
        cadre_style.grid(row=3, column=1, sticky="we", pady=(8, 0))
        cadre_style.columnconfigure(0, weight=1)
        self.v_style_predefini = tk.StringVar(value=styles.PERSONNALISE)
        cb = ttk.Combobox(cadre_style, textvariable=self.v_style_predefini, state="readonly", height=20,
                          values=[styles.PERSONNALISE] + styles.libelles(), width=48)
        cb.grid(row=0, column=0, sticky="w")
        cb.bind("<<ComboboxSelected>>", lambda e: self._choisir_style())
        ttk.Label(cadre_style, text="(la description en anglais ci-dessous est répétée dans tous les prompts ; "
                                    "vous pouvez la modifier)", foreground="#555").grid(row=0, column=1, sticky="w", padx=8)
        self.t_style = zone_texte(cadre_style, 2)
        self.t_style.cadre.grid(row=1, column=0, columnspan=2, sticky="we", pady=(4, 0))
        self.t_style.bind("<KeyRelease>", lambda e: self.v_style_predefini.set(styles.libelle_de(lire(self.t_style))))
        opt = ttk.Frame(c)
        opt.grid(row=4, column=0, columnspan=2, sticky="w", pady=8)
        self.v_short = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="Short YouTube", variable=self.v_short, command=self._basculer_short).pack(
            side="left", padx=(0, 16))
        ttk.Label(opt, text="Format").pack(side="left")
        self.v_format = tk.StringVar()
        ttk.Combobox(opt, textvariable=self.v_format, values=list(FORMATS), width=6, state="readonly").pack(
            side="left", padx=(4, 16))
        ttk.Label(opt, text="Plans").pack(side="left")
        self.v_nb_plans = tk.IntVar(value=self.r.nb_plans)
        ttk.Spinbox(opt, from_=1, to=40, textvariable=self.v_nb_plans, width=4).pack(side="left", padx=(4, 16))
        ttk.Label(opt, text="Secondes par plan").pack(side="left")
        self.v_duree_plan = tk.DoubleVar(value=self.r.duree_plan)
        ttk.Spinbox(opt, from_=3, to=20, increment=1, textvariable=self.v_duree_plan, width=4).pack(
            side="left", padx=(4, 16))
        ttk.Label(opt, text="Langue des voix").pack(side="left")
        self.v_langue = tk.StringVar()
        ttk.Entry(opt, textvariable=self.v_langue, width=12).pack(side="left", padx=4)

        ia_ = ttk.LabelFrame(c, text="Écrire le découpage (personnages, lieux, plans, répliques)", padding=8)
        ia_.grid(row=5, column=0, columnspan=2, sticky="we")
        self.v_moteur_scenario = tk.StringVar(value=self.r.moteur_scenario)
        for cle, nom in MOTEURS_SCENARIO.items():
            ttk.Radiobutton(ia_, text=nom, value=cle, variable=self.v_moteur_scenario,
                            command=self._changer_moteur_scenario).pack(side="left", padx=4)
        ttk.Separator(ia_, orient="vertical").pack(side="left", fill="y", padx=8)
        self._action(ia_, "Écrire le découpage", self._ecrire_decoupage)
        ttk.Button(ia_, text="Copier le prompt", command=self._copier_prompt).pack(side="left", padx=3)
        ttk.Button(ia_, text="Coller la réponse…", command=lambda: FenetreCollage(self, self._reponse_ia)).pack(
            side="left", padx=3)
        self.v_production_auto = tk.BooleanVar(value=self.r.production_auto)
        ttk.Checkbutton(c, text="Puis produire tout le film automatiquement (fiches, images, voix, vidéos, montage)",
                        variable=self.v_production_auto,
                        command=lambda: self._sauver_reglages(production_auto=bool(self.v_production_auto.get()))
                        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))

    # -- 2. Personnages et lieux ----------------------------------------------------------
    def _onglet_elements(self):
        c = ttk.Frame(self.nb, padding=12)
        self.nb.add(c, text="2. Personnages et lieux")
        c.columnconfigure(1, weight=1)
        c.rowconfigure(0, weight=1)
        gauche = ttk.Frame(c)
        gauche.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        self.arbre_el = ttk.Treeview(gauche, show="tree", height=18, selectmode="browse")
        self.arbre_el.column("#0", width=240)
        self.arbre_el.pack(fill="y", expand=True)
        self.arbre_el.bind("<<TreeviewSelect>>", lambda e: self._choisir_element())
        b = ttk.Frame(gauche)
        b.pack(fill="x", pady=4)
        ttk.Button(b, text="+ Personnage", command=lambda: self._ajouter_element("perso")).pack(side="left")
        ttk.Button(b, text="+ Lieu", command=lambda: self._ajouter_element("lieu")).pack(side="left", padx=3)
        ttk.Button(b, text="Supprimer", command=self._supprimer_element).pack(side="left")

        f = ttk.Frame(c)
        f.grid(row=0, column=1, sticky="nsew")
        f.columnconfigure(1, weight=1)
        self.form_el = f
        ttk.Label(f, text="Nom").grid(row=0, column=0, sticky="w")
        self.v_el_nom = tk.StringVar()
        ttk.Entry(f, textvariable=self.v_el_nom).grid(row=0, column=1, sticky="we", pady=2)
        ttk.Label(f, text="Description (pour vous)").grid(row=1, column=0, sticky="w")
        self.v_el_desc = tk.StringVar()
        ttk.Entry(f, textvariable=self.v_el_desc).grid(row=1, column=1, sticky="we", pady=2)
        ttk.Label(f, text="Apparence (anglais,\nrépétée dans les prompts)").grid(row=2, column=0, sticky="nw")
        self.t_el_app = zone_texte(f, 3)
        self.t_el_app.cadre.grid(row=2, column=1, sticky="we", pady=2)
        self.cadre_voix = ttk.LabelFrame(f, text="Voix du personnage (vide = voix du narrateur)", padding=6)
        self.cadre_voix.grid(row=3, column=0, columnspan=2, sticky="we", pady=6)
        self.cadre_voix.columnconfigure(1, weight=1)
        self.v_el_voix_ind = tk.StringVar()
        self.v_el_voix_ref = tk.StringVar()
        self.v_el_voix_txt = tk.StringVar()
        self.v_el_voix_loc = tk.StringVar()
        self.v_el_voix_cons = tk.StringVar()
        for n, (lib, v) in enumerate([("Indication", self.v_el_voix_ind), ("Extrait à cloner", self.v_el_voix_ref),
                                      ("Texte de l'extrait", self.v_el_voix_txt),
                                      ("Voix prédéfinie (CustomVoice)", self.v_el_voix_loc),
                                      ("Consigne (CustomVoice)", self.v_el_voix_cons)]):
            ttk.Label(self.cadre_voix, text=lib).grid(row=n, column=0, sticky="w")
            ttk.Entry(self.cadre_voix, textvariable=v).grid(row=n, column=1, sticky="we", pady=1)
        ttk.Button(self.cadre_voix, text="…", width=3, command=self._choisir_voix_perso).grid(row=1, column=2, padx=3)
        self.apercu_el = Apercu(f, (420, 300))
        self.apercu_el.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=6)
        f.rowconfigure(4, weight=1)
        b = ttk.Frame(f)
        b.grid(row=5, column=0, columnspan=2, sticky="w")
        self._action(b, "Dessiner cette fiche", lambda: self._dessiner_references(seul=True))
        ttk.Button(b, text="Importer une image…", command=self._importer_image_element).pack(side="left", padx=3)
        ttk.Button(b, text="Agrandir", command=lambda: self._ouvrir_image(self._element())).pack(side="left", padx=3)
        ttk.Separator(b, orient="vertical").pack(side="left", fill="y", padx=8)
        self._action(b, "Dessiner toutes les fiches manquantes", lambda: self._dessiner_references(seul=False))

    # -- 3. Plans ------------------------------------------------------------------------------
    def _onglet_plans(self):
        c = ttk.Frame(self.nb, padding=12)
        self.nb.add(c, text="3. Plans")
        c.columnconfigure(1, weight=1)
        c.rowconfigure(0, weight=1)
        gauche = ttk.Frame(c)
        gauche.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        colonnes = ("n", "titre", "duree", "image", "voix", "video")
        self.arbre_plans = ttk.Treeview(gauche, columns=colonnes, show="headings", height=20, selectmode="extended")
        for col, titre, larg in [("n", "N°", 34), ("titre", "Plan", 170), ("duree", "Durée", 50),
                                 ("image", "Image", 50), ("voix", "Voix", 45), ("video", "Vidéo", 50)]:
            self.arbre_plans.heading(col, text=titre)
            self.arbre_plans.column(col, width=larg, anchor="w" if col == "titre" else "center")
        self.arbre_plans.pack(fill="y", expand=True)
        self.arbre_plans.bind("<<TreeviewSelect>>", lambda e: self._choisir_plan())
        b = ttk.Frame(gauche)
        b.pack(fill="x", pady=4)
        for texte, cmd in [("+", self._ajouter_plan), ("Suppr.", self._supprimer_plan),
                           ("↑", lambda: self._deplacer_plan(-1)), ("↓", lambda: self._deplacer_plan(1))]:
            ttk.Button(b, text=texte, command=cmd, width=6 if len(texte) > 1 else 3).pack(side="left", padx=1)

        f = ttk.Frame(c)
        f.grid(row=0, column=1, sticky="nsew")
        f.columnconfigure(1, weight=1)
        f.columnconfigure(3, weight=1)
        self.v_pl_titre = tk.StringVar()
        self.v_pl_camera = tk.StringVar()
        self.v_pl_duree = tk.DoubleVar(value=6)
        self.v_pl_lieu = tk.StringVar()
        self.v_pl_enchainer = tk.BooleanVar()
        ttk.Label(f, text="Titre").grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.v_pl_titre).grid(row=0, column=1, sticky="we", pady=2)
        ttk.Label(f, text="Durée (s)").grid(row=0, column=2, sticky="e", padx=(10, 2))
        ttk.Spinbox(f, from_=2, to=30, increment=0.5, textvariable=self.v_pl_duree, width=6).grid(row=0, column=3, sticky="w")
        ttk.Label(f, text="Lieu").grid(row=1, column=0, sticky="w")
        self.cb_lieu = ttk.Combobox(f, textvariable=self.v_pl_lieu, state="readonly")
        self.cb_lieu.grid(row=1, column=1, sticky="we", pady=2)
        ttk.Checkbutton(f, text="Enchaîner (part de la dernière image du plan précédent)",
                        variable=self.v_pl_enchainer).grid(row=1, column=2, columnspan=2, sticky="w", padx=10)
        ttk.Label(f, text="Personnages").grid(row=2, column=0, sticky="nw")
        self.lb_persos = tk.Listbox(f, selectmode="multiple", height=3, exportselection=False)
        self.lb_persos.grid(row=2, column=1, sticky="we", pady=2)
        ttk.Label(f, text="Caméra").grid(row=2, column=2, sticky="ne", padx=(10, 2))
        ttk.Entry(f, textvariable=self.v_pl_camera).grid(row=2, column=3, sticky="new", pady=2)
        ttk.Label(f, text="Action (pour vous)").grid(row=3, column=0, sticky="nw")
        self.t_pl_action = zone_texte(f, 2)
        self.t_pl_action.cadre.grid(row=3, column=1, columnspan=3, sticky="we", pady=2)
        ttk.Label(f, text="Première image\n(anglais)").grid(row=4, column=0, sticky="nw")
        self.t_pl_image = zone_texte(f, 3)
        self.t_pl_image.cadre.grid(row=4, column=1, columnspan=3, sticky="we", pady=2)
        ttk.Label(f, text="Mouvement\n(anglais)").grid(row=5, column=0, sticky="nw")
        self.t_pl_mouv = zone_texte(f, 3)
        self.t_pl_mouv.cadre.grid(row=5, column=1, columnspan=3, sticky="we", pady=2)
        ttk.Label(f, text="Répliques\n« Nom : texte »").grid(row=6, column=0, sticky="nw")
        self.t_pl_rep = zone_texte(f, 3)
        self.t_pl_rep.cadre.grid(row=6, column=1, columnspan=3, sticky="we", pady=2)

        bas = ttk.Frame(f)
        bas.grid(row=7, column=0, columnspan=4, sticky="nsew", pady=6)
        f.rowconfigure(7, weight=1)
        self.apercu_plan = Apercu(bas, (360, 220))
        self.apercu_plan.pack(side="left", fill="both", expand=True)
        cmd = ttk.Frame(bas)
        cmd.pack(side="left", fill="y", padx=8)
        ttk.Label(cmd, text="Première image", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        l = ttk.Frame(cmd)
        l.pack(anchor="w", pady=2)
        self._action(l, "Dessiner", lambda: self._lancer_plans("images"), padx=(0, 3))
        ttk.Button(l, text="Importer…", command=self._importer_image_plan).pack(side="left")
        ttk.Label(cmd, text="Voix", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(6, 0))
        l = ttk.Frame(cmd)
        l.pack(anchor="w", pady=2)
        self._action(l, "Générer", lambda: self._lancer_plans("voix"), padx=(0, 3))
        ttk.Button(l, text="Écouter", command=self._ecouter_plan).pack(side="left")
        ttk.Label(cmd, text="Vidéo (ComfyUI)", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(6, 0))
        l = ttk.Frame(cmd)
        l.pack(anchor="w", pady=2)
        self._action(l, "Rendre (nouvelle prise)", lambda: self._lancer_plans("videos"), padx=(0, 3))
        ttk.Button(l, text="Lire", command=self._lire_prise).pack(side="left")
        l = ttk.Frame(cmd)
        l.pack(anchor="w", pady=2)
        ttk.Label(l, text="Prise retenue").pack(side="left")
        self.v_prise = tk.StringVar()
        self.cb_prise = ttk.Combobox(l, textvariable=self.v_prise, state="readonly", width=24)
        self.cb_prise.pack(side="left", padx=4)
        self.cb_prise.bind("<<ComboboxSelected>>", lambda e: self._choisir_prise())
        ttk.Label(cmd, text="Les actions portent sur les plans sélectionnés\n(Ctrl+clic pour en choisir plusieurs).",
                  foreground="#555").pack(anchor="w", pady=(8, 0))

    # -- 4. Production ------------------------------------------------------------------------
    def _onglet_production(self):
        c = ttk.Frame(self.nb, padding=12)
        self.nb.add(c, text="4. Production")
        c.columnconfigure(0, weight=1)
        c.rowconfigure(3, weight=1)
        opts = ttk.LabelFrame(c, text="Moteurs et son", padding=8)
        opts.grid(row=0, column=0, sticky="we")
        ttk.Label(opts, text="Images").grid(row=0, column=0, sticky="w")
        self.v_moteur_images = tk.StringVar(value=self.r.moteur_images)
        r_ = ttk.Frame(opts)
        r_.grid(row=0, column=1, columnspan=4, sticky="w")
        for cle, nom in MOTEURS_IMAGES.items():
            ttk.Radiobutton(r_, text=nom, value=cle, variable=self.v_moteur_images,
                            command=self._changer_moteur_images).pack(side="left", padx=4)
        ttk.Label(opts, text="Voix").grid(row=1, column=0, sticky="w", pady=3)
        self.v_mode_voix = tk.StringVar()
        ttk.Combobox(opts, textvariable=self.v_mode_voix, values=list(MODES_VOIX.values()), state="readonly",
                     width=30).grid(row=1, column=1, sticky="w")
        ttk.Label(opts, text="Son du modèle vidéo").grid(row=1, column=2, sticky="e", padx=(16, 4))
        self.v_son = tk.StringVar()
        ttk.Combobox(opts, textvariable=self.v_son, values=list(SONS_MODELE.values()), state="readonly",
                     width=24).grid(row=1, column=3, sticky="w")
        self.v_vol_modele = tk.DoubleVar()
        ttk.Spinbox(opts, from_=0, to=1, increment=0.05, textvariable=self.v_vol_modele, width=5).grid(
            row=1, column=4, sticky="w", padx=4)
        ttk.Label(opts, text="Musique de fond").grid(row=2, column=0, sticky="w")
        self.v_musique = tk.StringVar()
        ttk.Entry(opts, textvariable=self.v_musique, width=50).grid(row=2, column=1, columnspan=2, sticky="we")
        ttk.Button(opts, text="…", width=3, command=self._choisir_musique).grid(row=2, column=3, sticky="w", padx=4)
        self.v_vol_musique = tk.DoubleVar()
        ttk.Spinbox(opts, from_=0, to=1, increment=0.05, textvariable=self.v_vol_musique, width=5).grid(
            row=2, column=4, sticky="w", padx=4)

        etapes = ttk.LabelFrame(c, text="Fabrication (ne refait que ce qui manque)", padding=8)
        etapes.grid(row=1, column=0, sticky="we", pady=8)
        for texte, nom in [("1. Fiches", "references"), ("2. Premières images", "images"), ("3. Voix", "voix"),
                           ("4. Vidéos", "videos"), ("5. Monter le film", "export")]:
            self._action(etapes, texte, lambda n=nom: self._lancer_etape(n))
        ttk.Separator(etapes, orient="vertical").pack(side="left", fill="y", padx=10)
        self._action(etapes, "▶ Tout produire", lambda: self._lancer_etape("tout"))
        self.v_refaire = tk.BooleanVar(value=False)
        ttk.Checkbutton(etapes, text="Tout refaire (nouvelles images et prises)", variable=self.v_refaire).pack(
            side="left", padx=10)

        self.v_incrustes = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Sous-titres incrustés dans l'image, mot à mot (conseillé pour les Shorts)",
                        variable=self.v_incrustes).grid(row=3, column=0, columnspan=5, sticky="w", pady=(4, 0))
        fin = ttk.Frame(c)
        fin.grid(row=2, column=0, sticky="we")
        ttk.Button(fin, text="Regarder le film", command=self._regarder_film).pack(side="left")
        ttk.Button(fin, text="Fiche de publication", command=self._fiche_publication).pack(side="left", padx=4)
        ttk.Button(fin, text="Ouvrir le dossier d'export", command=lambda: self._ouvrir_dossier("export")).pack(
            side="left", padx=4)
        self.l_resume = ttk.Label(fin, text="")
        self.l_resume.pack(side="right")
        self.journal = zone_texte(c, 12)
        self.journal.cadre.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        self.journal.configure(state="disabled")

    # =========================================================================
    # Chargement et lecture des formulaires
    # =========================================================================
    def _charger(self, film: Film):
        self.film = film
        self.sel_element, self.sel_plan = None, None
        self.v_titre.set(film.titre)
        ecrire(self.t_pitch, film.pitch)
        ecrire(self.t_style, film.style)
        self.v_style_predefini.set(styles.libelle_de(film.style))
        self.v_format.set(film.format)
        self.v_langue.set(film.langue)
        self.v_mode_voix.set(MODES_VOIX.get(film.mode_voix, MODES_VOIX["tts"]))
        self.v_son.set(SONS_MODELE.get(film.son_modele, SONS_MODELE["ambiance"]))
        self.v_vol_modele.set(film.volume_modele)
        self.v_musique.set(film.musique)
        self.v_vol_musique.set(film.volume_musique)
        self.v_short.set(film.short)
        self.v_incrustes.set(film.sous_titres_incrustes)
        self._rafraichir()
        if film.dossier:
            self._sauver_reglages(dernier_film=str(film.dossier))

    def _rafraichir(self):
        """Redessine les listes (personnages, lieux, plans) à partir du film."""
        f = self.film
        self.l_titre.configure(text=f"{f.titre}  ·  {f.dossier if f.dossier else 'non enregistré'}")
        self.arbre_el.delete(*self.arbre_el.get_children())
        gp = self.arbre_el.insert("", "end", "g_perso", text="Personnages", open=True)
        for p in f.personnages:
            ok = "✔ " if p.image and f.chemin(p.image).exists() else "○ "
            self.arbre_el.insert(gp, "end", f"perso:{p.id}", text=ok + p.nom)
        gl = self.arbre_el.insert("", "end", "g_lieu", text="Lieux", open=True)
        for l in f.lieux:
            ok = "✔ " if l.image and f.chemin(l.image).exists() else "○ "
            self.arbre_el.insert(gl, "end", f"lieu:{l.id}", text=ok + l.nom)
        if self.sel_element:
            iid = f"{self.sel_element[0]}:{self.sel_element[1]}"
            if self.arbre_el.exists(iid):
                self.arbre_el.selection_set(iid)
        self._charger_element()

        selection = list(self.arbre_plans.selection())
        self.arbre_plans.delete(*self.arbre_plans.get_children())
        for i, p in enumerate(f.plans):
            image = "suite" if p.enchainer else ("✔" if p.image and f.chemin(p.image).exists() else "—")
            voix = "—" if not p.repliques else ("✔" if p.voix and f.chemin(p.voix).exists() else "○")
            vid = f"✔ {len(p.prises)}" if p.prise and f.chemin(p.prise.fichier).exists() else "—"
            duree = p.prise.duree if p.prise and p.prise.duree else max(p.duree, p.duree_voix)
            self.arbre_plans.insert("", "end", str(i), values=(i + 1, p.titre, f"{duree:.1f}", image, voix, vid))
        selection = [s for s in selection if self.arbre_plans.exists(s)]
        if self.sel_plan is not None and self.arbre_plans.exists(str(self.sel_plan)) and not selection:
            selection = [str(self.sel_plan)]
        if selection:
            self.arbre_plans.selection_set(selection)
        self.cb_lieu.configure(values=[""] + [l.nom for l in f.lieux])
        self.lb_persos.delete(0, "end")
        for p in f.personnages:
            self.lb_persos.insert("end", p.nom)
        self._charger_plan()
        n_prises = sum(1 for p in f.plans if p.prise)
        self.l_resume.configure(text=f"{len(f.plans)} plans · {n_prises} rendus · environ {f.duree_totale():.0f} s")

    def _element(self):
        if not self.sel_element:
            return None
        genre, ident = self.sel_element
        liste = self.film.personnages if genre == "perso" else self.film.lieux
        return next((x for x in liste if x.id == ident), None)

    def _charger_element(self):
        el = self._element()
        etat = "normal" if el else "disabled"
        for w in self.form_el.winfo_children():
            try:
                w.configure(state=etat)
            except tk.TclError:
                pass
        self.v_el_nom.set(el.nom if el else "")
        self.v_el_desc.set(el.description if el else "")
        ecrire(self.t_el_app, el.apparence if el else "")
        perso = isinstance(el, Personnage)
        for v, att in [(self.v_el_voix_ind, "voix"), (self.v_el_voix_ref, "voix_reference"),
                       (self.v_el_voix_txt, "voix_texte"), (self.v_el_voix_loc, "voix_locuteur"),
                       (self.v_el_voix_cons, "voix_consigne")]:
            v.set(getattr(el, att) if perso else "")
        if perso:
            self.cadre_voix.grid()
        else:
            self.cadre_voix.grid_remove()
        self.apercu_el.montrer(self.film.chemin(el.image) if el and el.image else None)

    def _lire_element(self):
        el = self._element()
        if el is None:
            return
        el.nom = self.v_el_nom.get().strip() or el.nom
        el.description = self.v_el_desc.get().strip()
        el.apparence = lire(self.t_el_app)
        if isinstance(el, Personnage):
            el.voix = self.v_el_voix_ind.get().strip()
            el.voix_reference = self.v_el_voix_ref.get().strip()
            el.voix_texte = self.v_el_voix_txt.get().strip()
            el.voix_locuteur = self.v_el_voix_loc.get().strip()
            el.voix_consigne = self.v_el_voix_cons.get().strip()

    def _plans_choisis(self) -> list[int]:
        return sorted(int(s) for s in self.arbre_plans.selection())

    def _charger_plan(self):
        p = self.film.plans[self.sel_plan] if self.sel_plan is not None and self.sel_plan < len(self.film.plans) else None
        self.v_pl_titre.set(p.titre if p else "")
        self.v_pl_camera.set(p.camera if p else "")
        self.v_pl_duree.set(p.duree if p else 6)
        lieu = self.film.lieu(p.lieu) if p and p.lieu else None
        self.v_pl_lieu.set(lieu.nom if lieu else "")
        self.v_pl_enchainer.set(bool(p and p.enchainer))
        self.lb_persos.selection_clear(0, "end")
        if p:
            for k, perso in enumerate(self.film.personnages):
                if perso.id in p.personnages:
                    self.lb_persos.selection_set(k)
        ecrire(self.t_pl_action, p.action if p else "")
        ecrire(self.t_pl_image, p.image_prompt if p else "")
        ecrire(self.t_pl_mouv, p.mouvement if p else "")
        ecrire(self.t_pl_rep, ecrire_repliques(p.repliques) if p else "")
        apercu = None
        if p:
            suite = self.film.dossier / "images" / f"plan_{self.sel_plan + 1:02d}_suite.png" if self.film.dossier else None
            if p.enchainer and suite and suite.exists():
                apercu = suite
            elif p.image:
                apercu = self.film.chemin(p.image)
        self.apercu_plan.montrer(apercu)
        prises = [f"Prise {k + 1} — {x.date or ''} ({x.duree:.1f} s)" for k, x in enumerate(p.prises)] if p else []
        self.cb_prise.configure(values=prises)
        self.v_prise.set(prises[p.prise_choisie] if p and p.prise else "")

    def _lire_plan(self):
        if self.sel_plan is None or self.sel_plan >= len(self.film.plans):
            return
        p = self.film.plans[self.sel_plan]
        p.titre = self.v_pl_titre.get().strip()
        p.camera = self.v_pl_camera.get().strip()
        try:
            p.duree = max(1.0, float(self.v_pl_duree.get()))
        except (tk.TclError, ValueError):
            pass
        lieu = self.film.lieu(self.v_pl_lieu.get()) if self.v_pl_lieu.get() else None
        p.lieu = lieu.id if lieu else ""
        p.enchainer = bool(self.v_pl_enchainer.get()) and self.sel_plan > 0
        p.personnages = [self.film.personnages[k].id for k in self.lb_persos.curselection()
                         if k < len(self.film.personnages)]
        p.action = lire(self.t_pl_action)
        p.image_prompt = lire(self.t_pl_image)
        p.mouvement = lire(self.t_pl_mouv)
        p.repliques = lire_repliques(lire(self.t_pl_rep))

    def _lire_film(self):
        f = self.film
        f.titre = self.v_titre.get().strip() or "Mon film"
        f.pitch = lire(self.t_pitch)
        f.style = lire(self.t_style)
        if self.v_format.get() != f.format:
            f.regler_format(self.v_format.get())
        f.langue = self.v_langue.get().strip() or "français"
        inv = {v: k for k, v in MODES_VOIX.items()}
        f.mode_voix = inv.get(self.v_mode_voix.get(), "tts")
        inv = {v: k for k, v in SONS_MODELE.items()}
        f.son_modele = inv.get(self.v_son.get(), "ambiance")
        try:
            f.volume_modele = float(self.v_vol_modele.get())
            f.volume_musique = float(self.v_vol_musique.get())
        except (tk.TclError, ValueError):
            pass
        f.musique = self.v_musique.get().strip()
        f.short = bool(self.v_short.get())
        f.sous_titres_incrustes = bool(self.v_incrustes.get())

    def _recuperer(self, sauver: bool = True):
        """Reporte les formulaires dans le film (et l'enregistre s'il a un dossier)."""
        self._lire_film()
        self._lire_element()
        self._lire_plan()
        if sauver and self.film.dossier:
            try:
                self.film.enregistrer()
            except OSError as e:
                self.v_etat.set(f"Enregistrement impossible : {e}")
        self.l_titre.configure(text=f"{self.film.titre}  ·  {self.film.dossier if self.film.dossier else 'non enregistré'}")

    # =========================================================================
    # Fichiers
    # =========================================================================
    def _assurer_dossier(self) -> bool:
        if self.film.dossier:
            return True
        parent = filedialog.askdirectory(parent=self, title="Où ranger le film ? (un sous-dossier sera créé)")
        if not parent:
            return False
        dossier = Path(parent) / (identifiant(self.film.titre) or "film")
        n = 2
        base = dossier
        while (dossier / "film.json").exists():
            dossier = Path(f"{base}_{n}")
            n += 1
        self.film.enregistrer(dossier)
        self._sauver_reglages(dernier_film=str(dossier))
        self._rafraichir()
        return True

    def _nouveau(self):
        if not self._libre():
            return
        self._recuperer()
        self._charger(Film())

    def _ouvrir(self):
        if not self._libre():
            return
        self._recuperer()
        chemin = filedialog.askopenfilename(parent=self, title="Ouvrir un film", filetypes=[("Film", "film.json")])
        if chemin:
            try:
                self._charger(Film.ouvrir(chemin))
            except (OSError, ValueError) as e:
                messagebox.showerror("Ouverture impossible", str(e))

    def _enregistrer(self):
        self._recuperer(sauver=False)
        if self._assurer_dossier():
            self.film.enregistrer()
            self.v_etat.set(f"Enregistré dans {self.film.dossier}")

    def _exemple(self):
        if not self._libre():
            return
        self._recuperer()
        film = film_exemple()
        self._charger(film)
        self.nb.select(2)
        self.v_etat.set("Film d'exemple chargé : enregistrez-le pour le produire.")

    def _ouvrir_dossier(self, sous=""):
        if self.film.dossier:
            d = self.film.dossier / sous if sous else self.film.dossier
            d.mkdir(parents=True, exist_ok=True)
            ouvrir_fichier(d)

    def _sauver_reglages(self, **changements):
        """Relit les réglages sur le disque (une commande lancée à côté a pu les changer), applique
        `changements` et enregistre."""
        r = Reglages.charger()
        for cle, val in changements.items():
            setattr(r, cle, val)
        r.enregistrer()
        self.r = r

    def _reglages(self, onglet=0):
        self.r = Reglages.charger()
        def retour(r):
            r.dernier_film = self.r.dernier_film
            self.r = r
            self.v_moteur_scenario.set(r.moteur_scenario)
            self.v_moteur_images.set(r.moteur_images)
        FenetreReglages(self, self.r, retour, onglet)

    def _changer_moteur_scenario(self):
        self._sauver_reglages(moteur_scenario=self.v_moteur_scenario.get())

    def _changer_moteur_images(self):
        self._sauver_reglages(moteur_images=self.v_moteur_images.get())

    # =========================================================================
    # Histoire
    # =========================================================================
    def _prompt(self) -> str:
        self._recuperer()
        self._sauver_reglages(nb_plans=int(self.v_nb_plans.get() or 6),
                              duree_plan=float(self.v_duree_plan.get() or 6))
        return ia.construire_prompt(self.film, self.film.pitch, self.r.nb_plans, self.r.duree_plan,
                                    self.r.duree_max_plan)

    def _choisir_style(self):
        desc = styles.description(self.v_style_predefini.get())
        if not desc:
            return
        deja = any(p.image or p.prises for p in self.film.plans) or any(x.image for x in self.film.personnages)
        ecrire(self.t_style, desc)
        self._recuperer()
        if deja:
            self.v_etat.set("Style changé : les nouvelles images et vidéos l'utiliseront. Pour tout refaire dans ce "
                            "style, cochez « Tout refaire » dans l'onglet Production.")
        else:
            self.v_etat.set(f"Style choisi : {self.v_style_predefini.get()}")

    def _basculer_short(self):
        """Short : vertical, une dizaine de plans de 4 s, sous-titres incrustés."""
        if self.v_short.get():
            self.v_format.set("9:16")
            self.v_nb_plans.set(10)
            self.v_duree_plan.set(4)
            self.v_incrustes.set(True)
            self.v_etat.set("Short : vertical, environ 40 s, accroche dès la 1re seconde, fin en boucle, "
                            "sous-titres incrustés.")
        else:
            self.v_format.set("16:9")
            self.v_nb_plans.set(max(int(self.v_nb_plans.get() or 6), 6))
            self.v_duree_plan.set(6)
            self.v_incrustes.set(False)

    def _copier_prompt(self):
        texte = self._prompt()
        self.clipboard_clear()
        self.clipboard_append(texte)
        self.v_etat.set("Prompt copié : collez-le dans Claude, ChatGPT…, puis « Coller la réponse ».")

    def _ecrire_decoupage(self):
        if self.v_moteur_scenario.get() == "manuel":
            self._copier_prompt()
            FenetreCollage(self, self._reponse_ia)
            return
        if not lire(self.t_pitch):
            messagebox.showinfo("Histoire", "Écrivez d'abord votre idée en quelques phrases.")
            return
        if self.film.plans and not messagebox.askyesno(
                "Remplacer le découpage ?", "Le film a déjà des plans. Les remplacer par un nouveau découpage ?\n"
                "(Les images et vidéos déjà produites restent dans le dossier.)"):
            return
        prompt = self._prompt()
        r = self.r.copie(moteur_scenario=self.v_moteur_scenario.get())

        def travail(prod: Production):
            return ia.generer(prompt, r, prod.etat, prod.arret)
        self._lancer("Écriture du découpage", travail, self._reponse_ia, exige_dossier=False)

    def _reponse_ia(self, texte: str):
        if not texte:
            return
        try:
            film, remarques = ia.film_depuis_ia(texte, self.film, float(self.v_duree_plan.get() or 6),
                                                self.r.duree_max_plan)
        except ia.ReponseIllisible as e:
            messagebox.showerror("Réponse illisible", f"{e}\n\nDébut de la réponse :\n{texte[:600]}")
            return
        self._charger(film)
        if film.dossier:
            film.enregistrer()
        self.nb.select(2)
        msg = f"Découpage prêt : {len(film.personnages)} personnage(s), {len(film.lieux)} lieu(x), {len(film.plans)} plans."
        self._journaliser(msg + ("\n" + "\n".join("• " + r for r in remarques) if remarques else ""))
        self.v_etat.set(msg)
        if remarques and not self.v_production_auto.get():
            messagebox.showinfo("Découpage", msg + "\n\nÀ vérifier :\n" + "\n".join("• " + r for r in remarques))
        if self.v_production_auto.get():
            self.after(400, self._production_auto)

    def _reprendre_production(self):
        if self.travail and self.travail.is_alive():
            return
        self.nb.select(3)
        self._lancer_etape("tout")

    def _production_auto(self):
        """Du texte jusqu'au film : la production complète démarre dès que le découpage est prêt."""
        if self.travail and self.travail.is_alive():
            self.after(300, self._production_auto)
            return
        if not self._assurer_dossier():
            self.v_etat.set("Production automatique annulée : aucun dossier choisi pour le film.")
            return
        self.nb.select(3)
        self._journaliser("Production automatique : le découpage est écrit, le film se fabrique.")
        self._lancer_etape("tout")

    # =========================================================================
    # Personnages et lieux
    # =========================================================================
    def _choisir_element(self):
        self._lire_element()
        sel = self.arbre_el.selection()
        if sel and ":" in sel[0]:
            genre, ident = sel[0].split(":", 1)
            self.sel_element = (genre, ident)
        else:
            self.sel_element = None
        self._charger_element()

    def _ajouter_element(self, genre):
        self._recuperer()
        if genre == "perso":
            ident = identifiant("personnage", {p.id for p in self.film.personnages})
            self.film.personnages.append(Personnage(id=ident, nom="Nouveau personnage"))
        else:
            ident = identifiant("lieu", {l.id for l in self.film.lieux})
            self.film.lieux.append(Lieu(id=ident, nom="Nouveau lieu"))
        self.sel_element = (genre, ident)
        self._rafraichir()

    def _supprimer_element(self):
        el = self._element()
        if el is None or not messagebox.askyesno("Supprimer", f"Supprimer « {el.nom} » ?"):
            return
        if isinstance(el, Personnage):
            self.film.personnages.remove(el)
            for p in self.film.plans:
                if el.id in p.personnages:
                    p.personnages.remove(el.id)
        else:
            self.film.lieux.remove(el)
            for p in self.film.plans:
                if p.lieu == el.id:
                    p.lieu = ""
        self.sel_element = None
        self._recuperer()
        self._rafraichir()

    def _choisir_voix_perso(self):
        chemin = filedialog.askopenfilename(parent=self, filetypes=TYPES_AUDIO, title="Extrait de voix à cloner")
        if chemin:
            self.v_el_voix_ref.set(self.film.relatif(chemin) if self.film.dossier else chemin)

    def _importer_image_element(self):
        el = self._element()
        if el is None or not self._assurer_dossier():
            return
        chemin = filedialog.askopenfilename(parent=self, filetypes=TYPES_IMAGES)
        if not chemin:
            return
        prefixe = "perso" if isinstance(el, Personnage) else "lieu"
        dest = self.film.sous_dossier("references") / f"{prefixe}_{el.id}.png"
        with Image.open(chemin) as img:
            img.convert("RGB").save(dest)
        el.image = self.film.relatif(dest)
        self._recuperer()
        self._rafraichir()

    def _ouvrir_image(self, el):
        if el is not None and el.image and self.film.chemin(el.image).exists():
            ouvrir_fichier(self.film.chemin(el.image))

    def _dessiner_references(self, seul: bool):
        el = self._element() if seul else None
        if seul and el is None:
            messagebox.showinfo("Fiches", "Choisissez un personnage ou un lieu dans la liste.")
            return
        refaire = seul

        def travail(prod: Production):
            if el is None:
                return prod.references()
            if isinstance(el, Personnage):
                return prod.references(forcer=refaire, personnages=[el.id], lieux=[])
            return prod.references(forcer=refaire, personnages=[], lieux=[el.id])
        self._lancer("Fiches de référence", travail)

    # =========================================================================
    # Plans
    # =========================================================================
    def _choisir_plan(self):
        self._lire_plan()
        sel = self._plans_choisis()
        self.sel_plan = sel[0] if sel else None
        self._charger_plan()

    def _ajouter_plan(self):
        self._recuperer()
        position = (self.sel_plan + 1) if self.sel_plan is not None else len(self.film.plans)
        self.film.plans.insert(position, Plan(titre=f"Plan {position + 1}", duree=float(self.v_duree_plan.get() or 6)))
        self.sel_plan = position
        self.arbre_plans.selection_set(())
        self._rafraichir()
        self.arbre_plans.selection_set(str(position))

    def _supprimer_plan(self):
        choix = self._plans_choisis()
        if not choix or not messagebox.askyesno("Supprimer", f"Supprimer {len(choix)} plan(s) ?\n"
                                                             "(Les fichiers restent dans le dossier.)"):
            return
        self._lire_plan()
        for i in reversed(choix):
            del self.film.plans[i]
        if self.film.plans:
            self.film.plans[0].enchainer = False
        self.sel_plan = None
        self.arbre_plans.selection_set(())
        self._recuperer()
        self._rafraichir()

    def _deplacer_plan(self, sens):
        if self.sel_plan is None:
            return
        self._lire_plan()
        i, j = self.sel_plan, self.sel_plan + sens
        if not (0 <= j < len(self.film.plans)):
            return
        plans = self.film.plans
        plans[i], plans[j] = plans[j], plans[i]
        plans[0].enchainer = False
        self.sel_plan = j
        self.arbre_plans.selection_set(())
        self._recuperer()
        self._rafraichir()
        self.arbre_plans.selection_set(str(j))

    def _importer_image_plan(self):
        if self.sel_plan is None or not self._assurer_dossier():
            return
        chemin = filedialog.askopenfilename(parent=self, filetypes=TYPES_IMAGES)
        if not chemin:
            return
        dest = self.film.sous_dossier("images") / f"plan_{self.sel_plan + 1:02d}.png"
        video.ajuster_image(chemin, dest, self.film.largeur, self.film.hauteur)
        self.film.plans[self.sel_plan].image = self.film.relatif(dest)
        self._recuperer()
        self._rafraichir()

    def _ecouter_plan(self):
        if self.sel_plan is None:
            return
        p = self.film.plans[self.sel_plan]
        if p.voix and self.film.chemin(p.voix).exists():
            ouvrir_fichier(self.film.chemin(p.voix))
        else:
            messagebox.showinfo("Voix", "Pas encore de voix pour ce plan : cliquez sur « Générer ».")

    def _lire_prise(self):
        if self.sel_plan is None:
            return
        p = self.film.plans[self.sel_plan]
        if p.prise and self.film.chemin(p.prise.fichier).exists():
            ouvrir_fichier(self.film.chemin(p.prise.fichier))
        else:
            messagebox.showinfo("Vidéo", "Ce plan n'a pas encore de vidéo.")

    def _choisir_prise(self):
        if self.sel_plan is None:
            return
        valeurs = list(self.cb_prise.cget("values"))
        if self.v_prise.get() in valeurs:
            self.film.plans[self.sel_plan].prise_choisie = valeurs.index(self.v_prise.get())
            self._recuperer()
            self._rafraichir()

    def _lancer_plans(self, etape):
        choix = self._plans_choisis()
        if not choix:
            messagebox.showinfo("Plans", "Sélectionnez un ou plusieurs plans dans la liste.")
            return
        if etape == "videos" and not self._workflow_pret():
            return

        def travail(prod: Production):
            if etape == "images":
                return prod.images(choix, forcer=True)
            if etape == "voix":
                return prod.voix(choix, forcer=False)
            return prod.videos(choix, nouvelle_prise=True)
        noms = {"images": "Premières images", "voix": "Voix", "videos": "Rendu vidéo"}
        self._lancer(f"{noms[etape]} (plans {', '.join(str(i + 1) for i in choix)})", travail)

    # =========================================================================
    # Production
    # =========================================================================
    def _choisir_musique(self):
        chemin = filedialog.askopenfilename(parent=self, filetypes=TYPES_AUDIO, title="Musique de fond")
        if chemin:
            self.v_musique.set(chemin)

    def _workflow_pret(self) -> bool:
        """Avant de rendre des vidéos : un workflow vidéo complet doit être choisi."""
        self.r = Reglages.charger()
        try:
            wf = comfy.charger_workflow(self.r.comfy_workflow_video)
            if not wf.manquants():
                return True
            probleme = f"Le workflow vidéo « {wf.nom} » est incomplet."
        except comfy.ErreurComfy:
            probleme = "Aucun workflow vidéo n'est encore installé."
        if messagebox.askyesno("Workflow vidéo", probleme + "\n\nPréparer automatiquement le workflow officiel "
                               "LTX-2.5 maintenant ? (une fenêtre s'ouvre ; ComfyUI démarre une minute, puis "
                               "rouvrez les Réglages pour vérifier)\n\nNon : ouvrir les Réglages pour l'importer "
                               "à la main."):
            from . import comfy_local
            comfy_local.ouvrir_console(["installer-workflow"])
        else:
            self._reglages(0)
        return False

    def _lancer_etape(self, nom):
        refaire = bool(self.v_refaire.get())
        if nom in ("videos", "tout") and not self._workflow_pret():
            return

        def travail(prod: Production):
            if nom == "references":
                return prod.references(forcer=refaire)
            if nom == "images":
                return prod.images(forcer=refaire)
            if nom == "voix":
                return prod.voix(forcer=refaire)
            if nom == "videos":
                return prod.videos(nouvelle_prise=refaire)
            if nom == "export":
                return prod.exporter()
            return prod.tout()
        noms = {"references": "Fiches de référence", "images": "Premières images", "voix": "Voix",
                "videos": "Rendu des vidéos", "export": "Montage", "tout": "Production complète"}
        if nom == "tout":
            self._recuperer(sauver=False)
            if self._assurer_dossier():
                self.film.production_en_cours = True   # si Film Studio est fermé en route, on reprendra
                self.film.enregistrer()
                relance_au_demarrage(True)
        self._lancer(noms[nom], travail)

    def _fiche_publication(self):
        """Titre, description et hashtags (écrits par l'IA) dans une petite fenêtre, avec copie."""
        from .fenetres import zone_texte
        from .production import fiche_publication
        self._recuperer()
        texte = fiche_publication(self.film)
        fen = tk.Toplevel(self)
        fen.title("Fiche de publication")
        fen.transient(self)
        t = zone_texte(fen, 14, 80)
        t.cadre.pack(fill="both", expand=True, padx=10, pady=10)
        ecrire(t, texte)
        b = ttk.Frame(fen, padding=(10, 0, 10, 10))
        b.pack(fill="x")

        def copier():
            self.clipboard_clear()
            self.clipboard_append(lire(t))
            self.v_etat.set("Fiche de publication copiée.")
        ttk.Button(b, text="Copier", command=copier).pack(side="right")
        ttk.Button(b, text="Fermer", command=fen.destroy).pack(side="right", padx=6)

    def _regarder_film(self):
        if not self.film.dossier:
            return
        films = sorted((self.film.dossier / "export").glob("*.mp4"), key=lambda p: p.stat().st_mtime)
        if films:
            ouvrir_fichier(films[-1])
        else:
            messagebox.showinfo("Film", "Le film n'est pas encore monté (étape 5).")

    # =========================================================================
    # Travaux en arrière-plan
    # =========================================================================
    def _libre(self) -> bool:
        if self.travail and self.travail.is_alive():
            messagebox.showinfo("Patience", "Un travail est en cours : attendez-le ou cliquez sur « Arrêter ».")
            return False
        return True

    def _lancer(self, nom: str, travail, au_retour=None, exige_dossier: bool = True):
        if not self._libre():
            return
        self.r = Reglages.charger()
        self._recuperer()
        if exige_dossier and not self._assurer_dossier():
            return
        self.arret = threading.Event()
        for b in self.boutons_action:
            b.configure(state="disabled")
        self.b_arreter.configure(state="normal")
        self.progres.configure(mode="indeterminate")
        self.progres.start(12)
        self._journaliser(f"— {nom} —")
        self.v_etat.set(f"{nom}…")
        film, r = self.film, self.r.copie()
        sauver = (lambda: film.enregistrer()) if film.dossier else (lambda: None)

        def fil():
            try:
                prod = Production(film, r, etat=lambda t: self.messages.put(("etat", t)), arret=self.arret,
                                  sauver=lambda: (sauver(), self.messages.put(("maj", None))))
                resultat = travail(prod)
                self.messages.put(("fin", (nom, resultat, None, au_retour)))
            except Arrete:
                self.messages.put(("fin", (nom, None, "Arrêté.", None)))
            except (ErreurProduction, comfy.ErreurComfy, video.ErreurVideo) as e:
                self.messages.put(("fin", (nom, None, str(e), None)))
            except Exception as e:  # noqa: BLE001
                import traceback
                self.messages.put(("fin", (nom, None, f"{type(e).__name__} : {e}\n{traceback.format_exc(limit=3)}",
                                           None)))
        self.travail = threading.Thread(target=fil, daemon=True)
        self.travail.start()

    def _lire_messages(self):
        try:
            while True:
                genre, contenu = self.messages.get_nowait()
                if genre == "etat":
                    self.v_etat.set(contenu)
                elif genre == "maj":
                    self._lire_element()
                    self._lire_plan()
                    self._rafraichir()
                elif genre == "fin":
                    self._fin(*contenu)
        except queue.Empty:
            pass
        self.after(150, self._lire_messages)

    def _fin(self, nom, resultat, erreur, au_retour):
        self.progres.stop()
        self.progres.configure(mode="determinate", value=0)
        if nom == "Production complète" and self.film.production_en_cours:
            self.film.production_en_cours = False
            if self.film.dossier:
                self.film.enregistrer()
            relance_au_demarrage(False)
        for b in self.boutons_action:
            b.configure(state="normal")
        self.b_arreter.configure(state="disabled")
        if erreur:
            self._journaliser(f"{nom} : {erreur}")
            self.v_etat.set(f"{nom} : interrompu.")
            if erreur != "Arrêté.":
                messagebox.showerror(nom, erreur)
            self._rafraichir()
            return
        if au_retour is not None:
            au_retour(resultat)
            return
        chemin = None
        if isinstance(resultat, tuple):
            chemin, resultat = resultat
        bilan = resultat if isinstance(resultat, Bilan) else Bilan()
        texte = bilan.texte()
        if chemin:
            texte += f"\n→ {chemin}"
        self._journaliser(texte)
        self.v_etat.set(f"{nom} : terminé" + (" (avec des points à revoir)." if bilan.problemes else "."))
        self._rafraichir()
        if bilan.problemes:
            messagebox.showwarning(nom, texte[:1500])
        elif chemin and messagebox.askyesno(nom, f"Le film est prêt :\n{chemin}\n\nLe regarder maintenant ?"):
            ouvrir_fichier(chemin)

    def _arreter(self):
        self.arret.set()
        self.v_etat.set("Arrêt demandé…")

    def _journaliser(self, texte: str):
        self.journal.configure(state="normal")
        self.journal.insert("end", texte.rstrip() + "\n")
        self.journal.see("end")
        self.journal.configure(state="disabled")

    def _quitter(self):
        if self.travail and self.travail.is_alive():
            if not messagebox.askyesno("Quitter", "Un travail est en cours. L'arrêter et quitter ?\n"
                                       "(Une production complète reprendra à la prochaine ouverture.)"):
                return
            self.arret.set()
            self.travail.join(timeout=5)
        self._recuperer()
        from . import comfy_local, tts
        tts.liberer()
        comfy_local.GESTIONNAIRE.arreter()
        self.destroy()


def lancer(dossier: str | None = None) -> None:
    app = Application(dossier)
    app.mainloop()
