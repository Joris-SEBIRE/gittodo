# GitTodo

Ce qu'il te reste à faire sur GitHub, dans la barre des menus macOS.

Dans la barre : la photo de ton compte, surmontée d'une pastille rouge qui compte ce qu'il y a à
faire. Un clic ouvre le détail, regroupé par nature d'action. Un clic sur une ligne ouvre la PR
au bon endroit.

L'app ne fait que lire. Elle n'écrit jamais rien sur GitHub.

```
  (photo) 9
  ┌──────────────────────────────────────────────────────────────┐
  │ ✉  ON ATTEND MA RÉPONSE (8)                                  │
  │      Ajoute la validation des paniers                        │
  │      backend#412 · @alice · depuis 3 h         ✉ 7   ✉ 1     │
  │ ◉  À REVIEWER (1)                                            │
  │      Expose les tarifs par transporteur                      │
  │      api#88 · @bob · depuis 1 h                ◉ 1           │
  │ ⑄  MES PR EN CONFLIT (1)                                     │
  │      Retire le code de compatibilité                         │
  │      front#210 · @moi · depuis 8 j             conflit       │
  │ ──────────────────────────────────────────────────────────── │
  │ ◷  MES PR EN ATTENTE DE REVIEW (1)                           │
  │ ✎  MES DRAFTS (3)                                            │
  │ ⑂  MES BRANCHES SANS PR (5)                                  │
  │ ──────────────────────────────────────────────────────────── │
  │ ↻  Actualiser   prochaine dans 14 s · 9 notification(s)      │
  │ ◉  Voir en tant que ▸                                        │
  │ ⚙  Réglages et mode d'emploi                                 │
  └──────────────────────────────────────────────────────────────┘
```

## Prérequis

- macOS 13 ou plus récent.
- Les outils de développement en ligne de commande : `xcode-select --install`. Sans eux, `make`
  n'est qu'un stub qui affiche une invite d'installation.
- Python 3.12 ou 3.13 installé par Homebrew (`brew install python@3.13`). PyObjC a besoin d'une
  installation *framework*, ce que fournit Homebrew.
- Un token GitHub avec les droits `repo` et `read:org` en lecture. Le plus simple est
  `brew install gh && gh auth login`, GitTodo réutilise alors le token de la CLI.
- Un compte administrateur, parce que l'installation écrit dans `/Applications`.
- Un dépôt sur `github.com`. GitHub Enterprise n'est pas géré, l'hôte de l'API est en dur dans
  `src/gittodo/github.py`.

Le `Makefile` cible `/opt/homebrew/bin/python3.13` par défaut. Sur une machine Intel, ou avec un
autre interpréteur, passe le chemin : `make install PYTHON=/usr/local/bin/python3.13`.

## Installation

```bash
make install
```

La cible construit `build/GitTodo.app`, le recopie dans `/Applications` et le lance. Le bundle
embarque ses dépendances Python et ne dépend plus du dépôt une fois installé, mais son
interpréteur reste lié au framework Homebrew qui l'a construit, par un chemin qui contient le
numéro de version exact. Un `brew upgrade python@3.13` suivi d'un `brew cleanup` casse donc
l'app installée : refais `make install` après une montée de version de Python.

Pour que l'app démarre avec la session : menu **Lancer au démarrage**, qui écrit
`~/Library/LaunchAgents/fr.jsebire.gittodo.plist`.

Autres cibles du `Makefile` :

- `make print` : affiche en texte ce que le menu contiendrait, sans lancer d'interface.
- `make run` : lance depuis les sources, sans construire de bundle.
- `make restart`, `make stop` : relance ou arrête toute instance.
- `make uninstall` : retire l'app, le LaunchAgent, l'état local et le cache des photos.
- `make clean` : supprime `build/` et `.venv/`.

## Premier lancement

Le périmètre des recherches est vide par défaut, donc GitTodo interroge **tous** les dépôts que
ton token voit. Sur un compte qui en voit beaucoup, restreins-le : menu **Réglages et mode
d'emploi**, champ *Qualificateurs de recherche*, par exemple `org:mon-organisation`.

Le token est cherché dans cet ordre, au premier trouvé :

- le réglage `token_command`, une commande qui imprime un token sur sa sortie standard ;
- le fichier `~/.config/gittodo/token` ;
- la variable d'environnement `GITHUB_TOKEN`, sinon `GH_TOKEN` ;
- `gh auth token`, donc le compte de ton `gh auth login`.

Aucun token n'est stocké par l'app. Une PR dans un dépôt hors de portée du token n'existe pas
pour elle.

## Utilisation

- Clic sur une ligne : ouvre la PR ou le commentaire dans le navigateur.
- **⌥** maintenu : la ligne devient « Masquer », et l'élément disparaît jusqu'à sa prochaine
  activité sur GitHub. Le masquage est local, rien n'est écrit sur GitHub.
- **⌘** maintenu : la ligne devient « Copier le lien ».
- **⌘R** : actualise tout de suite.
- Le point ● marque ce qui est arrivé depuis la dernière ouverture du menu.
- Le visage d'une ligne n'est pas toujours l'auteur de la PR : les métadonnées disent qui c'est et
  ce qu'il a fait, toujours sous la même forme — « créée par », « écrit par », « répondu par »,
  « refusée par », « approuvée par », « confiée à », « réponse attendue de », « mergée par »,
  « poussée par ». Tes propres PR disent « créée par @toi », comme les autres. Aucune ligne ne
  reste sans visage.
- Trois sortes de pastilles, et seulement trois. Chiffrée : elle prend la couleur de la ligne,
  rouge pour ce qu'il reste à faire, violet pour le suivi des PR clôturées, grise sur une ligne
  informative — le nombre dit alors combien, pas quoi faire. État de fin de PR : la couleur de
  GitHub, violet `mergée` ou rouge `fermée`, sans nombre. Drapeau d'état : gris, sans nombre.
- Le délai « depuis X » prend la couleur de la pastille de la ligne, et reste gris quand la ligne
  ne compte rien.
- Le bouclier, devant le nom de la branche, marque une review obligatoire encore en attente :
  CODEOWNERS l'a demandée, elle ne peut pas être contournée. Il tient cette ligne parce que la
  protection porte sur la branche visée. L'infobulle dit de qui la review est attendue.
- Dans un titre de section, un compte suivi d'un `+` est un plancher : la liste est écrêtée, ou
  une recherche a buté sur sa limite. Sans `+`, le compte est exact, même s'il tombe pile sur le
  plafond. La pastille de la barre, elle, ne porte jamais de `+` : elle donne le nombre, et c'est
  le menu qui nomme ce qui a été écrêté.
- Un anneau se remplit autour de la photo, dans le sens horaire, jusqu'au prochain cycle : le
  délai avant la prochaine lecture se voit sans ouvrir le menu.
- Une pastille violette en bas à gauche de la photo suit les PR sorties du périmètre ouvert :
  messages restés sans réponse dessus, et clôtures faites par quelqu'un d'autre. Elle décompte
  quand tu ouvres la ligne. La pastille rouge, en haut à droite, ne concerne que les PR ouvertes.

Le menu se met à jour pendant qu'il est ouvert, sans qu'il faille le refermer.

Une seule instance tourne à la fois, garantie par un verrou sur
`~/Library/Application Support/GitTodo/gittodo.lock`.

## La couleur de l'app

GitTodo est violet, [LinearTodo](https://github.com/Joris-SEBIRE/lineartodo) est bleu. La couleur porte l'anneau du prochain
cycle, le compteur animé qui le remplace pendant une lecture, les titres de section — icône et
texte — et les titres de la fenêtre de réglages. Le suivi des PR clôturées, lui, prend le violet
dont GitHub colore un merge : compte secondaire, nombres de la ligne et état de la PR dans la
même teinte. Le rouge dit l'urgence, sauf sur une PR fermée sans merge, où il dit la fin de la PR
comme GitHub le fait. La section des pannes garde la couleur de son niveau : là, l'alerte passe
devant l'identité.

## Réglages

Menu **Réglages et mode d'emploi**. La fenêtre contient les deux : à gauche un formulaire pour
tous les réglages, à droite le mode d'emploi complet, alimenté par les valeurs de ton
installation. Le bouton d'enregistrement n'apparaît que si tu as modifié quelque chose.

Les réglages sont écrits dans `~/.config/gittodo/config.json`, relu à chaud. Le fichier se
complète seul quand une option apparaît.

Le mode d'emploi de la fenêtre va plus loin que ce README : les vingt sections du menu une
par une, les règles de comptage et d'anti-doublon, le coût en quota de chaque requête, et les
valeurs réelles de ton installation.

## Ce que l'app écrit sur ta machine

- `~/.config/gittodo/config.json` : tes réglages.
- `~/Library/Application Support/GitTodo/state.json` : éléments masqués et éléments déjà vus.
- `~/Library/Application Support/GitTodo/status.json` : ce que la barre affiche, utile au
  diagnostic.
- `~/Library/Application Support/GitTodo/errors.log` : les pannes et leur pile.
- `~/Library/Caches/GitTodo/avatars/` : les photos de profil, gardées quatorze jours.
- `~/Library/LaunchAgents/fr.jsebire.gittodo.plist`, seulement si tu actives le lancement au
  démarrage.

`make uninstall` décharge le LaunchAgent puis retire l'app, le LaunchAgent, l'état local et le
cache des photos. Seul `~/.config/gittodo/` reste, pour ne pas perdre tes réglages.

## À qui l'app parle

- `api.github.com`, avec ton token : c'est la seule destination qui le reçoit.
- `avatars.githubusercontent.com`, sans token, pour les photos de profil.
- `www.githubstatus.com`, sans token, uniquement quand une requête a échoué, pour savoir si la
  panne vient de GitHub.

GitTodo n'émet aucune écriture sur GitHub : pas de mutation GraphQL, aucun verbe HTTP d'écriture.
Les seules requêtes sont des recherches de PR, la boîte des notifications, l'activité des dépôts
et les branches. Ouvrir une PR depuis le menu la marque lue côté GitHub, parce que c'est ton
navigateur qui la charge.

## Prudence avec une configuration reçue de quelqu'un d'autre

Deux réglages font exécuter un programme : `token_command`, la commande qui imprime un token, et
`gh_path`, le chemin de la CLI GitHub. N'importe quoi placé là sera exécuté avec tes droits. Ne
recopie pas un `config.json` venu d'ailleurs sans avoir lu ces deux champs.

## Quand ça va mal

- Un triangle d'avertissement apparaît en haut à gauche de la photo dès qu'une source ne répond
  plus, dans le coin que les deux comptes n'occupent pas : il ne masque donc jamais un nombre, il
  dit que ces nombres datent. Le menu
  ouvre alors une section qui nomme la source, l'erreur, depuis quand, et ce que cela fausse.
  Trois niveaux : jaune quand rien n'est cassé chez GitHub mais que l'app ne peut plus tout lire,
  orange quand une source auxiliaire échoue, rouge quand la requête principale échoue ou que
  GitHub annonce un incident majeur.
- `make print` reproduit le contenu du menu en texte, sans interface.
- `PYTHONPATH=src .venv/bin/python -m gittodo --print --as <login>` montre ce que verrait un
  collègue.
- `errors.log` et `status.json` disent le reste. Ils contiennent les noms de tes dépôts et ton
  login GitHub : relis-les avant de les joindre à une issue publique.

Si l'icône disparaît de la barre alors que l'app tourne, c'est que macOS l'a reléguée hors écran
faute de place. Réduis sa largeur avec le réglage *Format de l'élément*, ou ⌘-glisse l'icône vers
la droite. `status.json` donne la géométrie réelle de l'élément, ce qui permet de trancher entre
une icône absente et une icône hors écran.

## Architecture

- `src/gittodo/app.py` : élément de barre, menu, minuteries, rendu.
- `src/gittodo/engine.py` : règles de déduction, à partir de l'état des PR.
- `src/gittodo/github.py` et `queries.py` : appels GitHub et requêtes GraphQL.
- `src/gittodo/models.py` : types, sections, ordre d'affichage.
- `src/gittodo/settings.py` et `help.py` : formulaire des réglages et mode d'emploi.
- `src/gittodo/branches.py` : branches distantes sans PR.
- `src/gittodo/state.py`, `config.py`, `avatars.py`, `formatting.py`, `launchagent.py` : état
  local, réglages, photos, mise en forme des durées, lancement au démarrage.

`engine.py` ne connaît ni AppKit ni le réseau : il transforme des PR en lignes à afficher, ce qui
permet de le vérifier avec `make print`.

## Support

Version 1.0.0. Interface en français uniquement. Le bundle n'est ni signé ni notarisé : il est
construit sur ta machine, donc macOS ne le met pas en quarantaine, mais il ne se distribue pas
tel quel.

## Marques

Projet personnel, sans aucun lien avec GitHub ni son approbation. « GitHub » et son logo
appartiennent à leur propriétaire ; ce dépôt n'utilise que son API publique, en lecture.

## Licence

MIT, voir `LICENSE`.
