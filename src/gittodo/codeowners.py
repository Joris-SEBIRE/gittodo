"""CODEOWNERS : qui doit reviewer quels fichiers, d'après le fichier du dépôt.

GitHub ne crée les demandes de review de CODEOWNERS qu'à l'ouverture d'une PR : sur un draft,
`reviewRequests` est vide alors que la review sera bel et bien obligatoire. Rejouer la règle
nous-mêmes est le seul moyen de le dire avant, et c'est utile là : on choisit de sortir un
draft du brouillon en sachant qui devra passer derrière.

La syntaxe est celle de `.gitignore`, sans négation ni classes de caractères, avec une règle
propre : c'est le *dernier* motif qui correspond qui décide, et lui seul.
"""

from __future__ import annotations

import re

# Emplacements reconnus par GitHub, dans son ordre de priorité.
PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")


def _owner(token: str) -> str:
    """Un propriétaire, écrit comme le reste de l'app écrit les reviewers sollicités.

    Une personne est un login nu, une équipe garde son `@` : c'est la convention de
    `PullRequest.reviewers`, et les deux listes se comparent donc directement.
    """
    if not token.startswith("@"):
        return token  # une adresse e-mail, que GitHub accepte aussi
    return f"@{token.split('/', 1)[1]}" if "/" in token else token[1:]


def parse(text: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Les règles du fichier, dans l'ordre : un motif, puis ses propriétaires.

    Une règle sans propriétaire est légale et retire l'appartenance : elle est conservée, sans
    quoi le motif précédent continuerait de s'appliquer.
    """
    rules = []
    for line in (text or "").splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        pattern, *owners = stripped.split()
        rules.append((pattern, tuple(_owner(token) for token in owners)))
    return tuple(rules)


def _regex(pattern: str) -> re.Pattern:
    """Traduit un motif CODEOWNERS en expression régulière.

    Trois différences avec un glob naïf, et ce sont elles qui font la justesse : `*` ne
    traverse pas les barres obliques mais `**` oui ; un motif sans barre oblique s'applique à
    toute profondeur, alors qu'un motif qui en contient est ancré à la racine ; et un motif de
    répertoire couvre tout ce qu'il contient, avec ou sans barre finale.
    """
    directory = pattern.endswith("/")
    core = pattern.strip("/")
    body, index = [], 0
    while index < len(core):
        if core.startswith("**", index):
            body.append(".*")
            index += 2
        elif core[index] == "*":
            body.append("[^/]*")
            index += 1
        elif core[index] == "?":
            body.append("[^/]")
            index += 1
        else:
            body.append(re.escape(core[index]))
            index += 1
    # Ancré si le motif porte une barre oblique ailleurs qu'à la fin, flottant sinon.
    lead = "" if pattern.startswith("/") or "/" in core else "(?:.*/)?"
    tail = "/.*" if directory else "(?:/.*)?"
    return re.compile(f"^{lead}{''.join(body)}{tail}$")


def owners(paths, rules) -> tuple[str, ...]:
    """Propriétaires des fichiers donnés : le dernier motif qui correspond décide, par fichier."""
    compiled = [(_regex(pattern), who) for pattern, who in rules]
    found: set[str] = set()
    for path in paths:
        matched: tuple[str, ...] = ()
        for regex, who in compiled:
            if regex.match(path):
                matched = who
        found.update(matched)
    return tuple(sorted(found))
