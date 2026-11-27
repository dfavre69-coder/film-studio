Tu es scénariste et réalisateur de courts-métrages d'animation. À partir de l'idée ci-dessous, écris le découpage technique d'un court-métrage de {{NB_PLANS}} plans, que des modèles d'IA vont fabriquer :

- un modèle d'images dessine la fiche de chaque personnage et de chaque lieu, puis la première image de chaque plan ;
- un modèle vidéo local (image → vidéo, plans de 4 à {{DUREE_MAX}} secondes) anime chaque plan à partir de sa première image ;
- une synthèse vocale lit les répliques en {{LANGUE}}.

## L'idée

{{PITCH}}

## Contraintes

- Style visuel imposé : {{STYLE}}
- Format de l'image : {{FORMAT}}
- Durée visée par plan : environ {{DUREE_PLAN}} secondes. Durée totale visée : environ {{DUREE_TOTALE}} secondes.
- Peu de personnages (1 à 4) et peu de lieux (1 à 4) : c'est la condition pour qu'ils restent identiques d'un plan à l'autre.
- Chaque plan montre UNE action simple et lisible. Les modèles vidéo ratent les actions complexes, les foules, le texte écrit et les mains qui manipulent de petits objets.
- `apparence`, `image` et `mouvement` sont rédigés en ANGLAIS, avec des mots concrets et visuels (couleurs, matières, tailles, vêtements, lumière). Répète les détails d'apparence importants : le modèle vidéo ne voit pas les autres plans.
- `apparence` d'un personnage : une seule phrase dense, toujours la même, qui permet de le reconnaître (espèce ou âge, silhouette, couleurs, vêtements, signe distinctif).
- `image` : la composition de la PREMIÈRE image du plan (qui est où, pose, expression, cadrage, lumière). Pas de mouvement ici.
- `mouvement` : ce qui bouge pendant le plan, au présent (« The firefly slowly lifts off the leaf and glides toward the camera »), puis le mouvement de caméra.
- `camera` : en français, court (« plan large, travelling avant lent »).
- `enchainer` : true seulement si le plan prolonge directement le précédent sans coupe (même lieu, même action qui continue). Le premier plan vaut toujours false. Un plan enchaîné part de la dernière image du plan précédent : son champ `image` sert seulement de repère.
- `repliques` : courtes (une à deux phrases par plan, 15 mots au plus chacune), en {{LANGUE}}. `personnage` est le nom exact d'un personnage, ou "Narrateur" pour la voix off. Un plan peut n'avoir aucune réplique.
- `voix` d'un personnage : une indication en {{LANGUE}} (âge, timbre, débit).
- Les noms des lieux et des personnages dans les plans reprennent exactement ceux des listes.
- `publication` : de quoi publier la vidéo sur YouTube. `titre` court et accrocheur (60 caractères au plus, sans majuscules partout ni promesse mensongère), `description` de 3 à 5 phrases qui donnent envie de regarder et se terminent par une question au public, `hashtags` : 3 à 5 mots-clés pertinents.
{{SHORT}}
## Réponse

Réponds UNIQUEMENT par un objet JSON valide, sans texte autour, de la forme :

```json
{
  "titre": "Titre du film",
  "personnages": [
    {"nom": "Lumi", "description": "une jeune luciole timide qui a peur du noir", "apparence": "a tiny young firefly with big round amber eyes, soft yellow glowing abdomen, translucent wings, short antennae with a curl at the tip", "voix": "petite voix de fillette, douce et hésitante"}
  ],
  "lieux": [
    {"nom": "La forêt la nuit", "description": "une forêt profonde et inquiétante", "apparence": "a dense old forest at night, giant ferns and mossy roots, deep blue moonlight, light mist between the trunks"}
  ],
  "plans": [
    {
      "titre": "Perdue",
      "action": "Lumi se retrouve seule sous une fougère géante.",
      "lieu": "La forêt la nuit",
      "personnages": ["Lumi"],
      "image": "wide shot, Lumi alone sitting on a mossy root under a giant fern, small and scared, her glow faint, mist around",
      "mouvement": "Lumi looks left and right nervously, her glow flickers, a leaf falls slowly beside her; slow push-in toward her",
      "camera": "plan large, travelling avant lent",
      "duree": 6,
      "enchainer": false,
      "repliques": [{"personnage": "Lumi", "texte": "Il y a quelqu'un ?"}]
    }
  ],
  "publication": {
    "titre": "Elle avait peur du noir… jusqu'à cette nuit",
    "description": "Une toute petite luciole se perd dans la forêt la nuit. … Et vous, de quoi aviez-vous peur enfant ?",
    "hashtags": ["animation", "conte", "luciole"]
  }
}
```
