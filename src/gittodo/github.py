"""Accès GitHub en lecture seule : GraphQL pour les PR, REST pour les notifications."""

from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import codeowners
from .config import CONFIG_PATH, Config
from .models import Branch, Closure, Comment, Person, PullRequest, Review, Thread, parse_ts
from .queries import (
    CLOSED_QUERY,
    CLOSED_SEARCHES,
    DIRECT_REVIEW,
    PEOPLE_QUERY,
    PR_QUERY,
    RECENT_ACTIVITY,
    SEARCHES,
    SENTINEL_QUERY,
)

API = "https://api.github.com"
EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)
# Boîte des non-lues : l'API plafonne à 50 par page quoi qu'on demande, et dix pages
# couvrent les comptes très bruyants (mesuré : 424 non lues, la seule mention en page 6).
NOTIFICATION_PAGE_SIZE = 50
NOTIFICATION_PAGES = 10
# Chemins modifiés lus par PR pour rejouer CODEOWNERS : au-delà, une PR est de toute façon
# trop large pour qu'un propriétaire ne soit pas déjà concerné.
OWNER_FILES = 100
# Lots d'alias : le test « a-t-elle eu une PR ? » est léger, la résolution du dernier commit
# l'est beaucoup moins et dépasse les limites de ressources si le lot est gros.
BRANCH_PR_BATCH = 120
BRANCH_REF_BATCH = 40
MAX_BRANCH_PAGES = 10
GH_CANDIDATES = ["/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/usr/bin/gh"]
KEYCHAIN_SERVICE = "gittodo"
TOKEN_FILE = CONFIG_PATH.parent / "token"


class GitHubError(Exception):
    """Échec d'un appel GitHub, avec de quoi le qualifier : 5xx, refus, réseau, quota.

    Le code et l'endpoint servent à dire *quoi* est dégradé, pas seulement que ça a échoué.
    """

    def __init__(self, message: str, status: int | None = None, path: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.path = path

    @property
    def kind(self) -> str:
        """Nature de l'échec, parce que le remède n'est pas le même.

        Le quota se règle en attendant le réarmement horaire, un refus en changeant les droits
        du token, une panne en attendant GitHub. Les confondre enverrait chercher au mauvais
        endroit, et le quota n'est pas une panne de GitHub : c'est notre propre consommation.
        """
        if "rate limit" in str(self).lower():
            return "quota"
        if self.status is not None and self.status >= 500:
            return "panne"
        if self.status in (401, 403, 404):
            return "refus"
        if self.status is None:
            return "réseau"
        return "erreur"


class MissingToken(GitHubError):
    """Aucun token : ce n'est pas une panne de GitHub, c'est un réglage qui manque.

    Sans cette distinction, l'absence de token sort en « réseau injoignable » et le menu
    titre « GITHUB EN PANNE » — il envoie chercher dehors ce qui se règle dedans.
    """

    @property
    def kind(self) -> str:
        return "réglage"


def _run(cmd: list[str], feed: str | None = None) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20, input=feed)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def store_token(value: str) -> str:
    """Range un token dans le trousseau, et dit ce qui a cloché le cas échéant.

    Le token passe par l'entrée standard de `security`, pas par ses arguments : dans le second
    cas il serait lisible dans la liste des processus le temps de l'appel. `security` le
    demande deux fois, d'où les deux lignes.
    """
    value = (value or "").strip()
    if not value:
        return "token vide"
    done = _run(
        ["/usr/bin/security", "add-generic-password", "-U", "-a", getpass.getuser(), "-s", KEYCHAIN_SERVICE, "-w"],
        feed=f"{value}\n{value}\n",
    )
    if done is None:
        return "le trousseau a refusé l'écriture"
    return ""


def find_gh(cfg: Config) -> str | None:
    for path in [cfg.gh_path, *GH_CANDIDATES]:
        if path and Path(path).is_file():
            return path
    if found := shutil.which("gh"):
        return found
    # Une app .app n'hérite pas du PATH du shell : on le demande au shell de login.
    return _run(["/bin/zsh", "-lc", "command -v gh"])


class GitHub:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._token: str | None = None
        self.token_origin = ""
        # Photo du compte du token, lue avec les PR : la barre en a besoin, et elle ne doit
        # dépendre ni d'un annuaire d'organisation ni d'une PR déjà croisée.
        self.viewer_face = ""
        # Membres annoncés par l'organisation contre membres réellement lus : GitHub n'en sert
        # pas plus de cent par page, et une liste tronquée en silence ferait croire que le
        # collègue qu'on cherche n'existe pas.
        self.people_total = 0
        self.defaults: dict[str, str] = {}

    def token(self, refresh: bool = False) -> str:
        if self._token and not refresh:
            return self._token
        self._token, self.token_origin = None, ""
        if self.cfg.token_command:
            self._token = _run(list(self.cfg.token_command))
            self.token_origin = "token_command (réglage)"
        if not self._token:
            # Même rangement que LinearTodo : un secret va dans le trousseau, pas dans un
            # fichier en clair. Le fichier reste lu ensuite, pour les installations d'avant.
            self._token = _run(
                ["/usr/bin/security", "find-generic-password", "-a", getpass.getuser(), "-s", KEYCHAIN_SERVICE, "-w"]
            )
            self.token_origin = f"trousseau macOS (service « {KEYCHAIN_SERVICE} »)"
        if not self._token and TOKEN_FILE.exists():
            self._token = TOKEN_FILE.read_text().strip() or None
            self.token_origin = str(TOKEN_FILE)
        if not self._token:
            self._token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
            self.token_origin = "variable d'environnement GITHUB_TOKEN / GH_TOKEN"
        if not self._token and (gh := find_gh(self.cfg)):
            self._token = _run([gh, "auth", "token"])
            self.token_origin = f"`{gh} auth token`"
        if not self._token:
            self.token_origin = ""
        if not self._token:
            raise MissingToken(
                "Colle un token dans les réglages, ou lance `gh auth login`"
            )
        return self._token

    def _request(self, path: str, payload: dict | None = None, retry: bool = True):
        url = path if path.startswith("http") else API + path
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=body, method="POST" if body else "GET")
        req.add_header("Authorization", f"Bearer {self.token()}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("User-Agent", "GitTodo")
        if body:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and retry:
                self.token(refresh=True)
                return self._request(path, payload, retry=False)
            detail = (exc.read() or b"").decode()[:200]
            raise GitHubError(
                f"GitHub {exc.code} sur {url.removeprefix(API)} : {detail}",
                status=exc.code,
                path=url.removeprefix(API),
            ) from exc
        except urllib.error.URLError as exc:
            raise GitHubError(f"Réseau indisponible ({exc.reason})") from exc
        except TimeoutError as exc:
            # urllib lève TimeoutError sans l'emballer dans URLError : sans cette branche,
            # une lenteur réseau tuait le cycle au lieu d'être une panne passagère.
            raise GitHubError("Délai dépassé côté réseau") from exc

    def graphql(self, query: str, variables: dict, partial: bool = False) -> dict:
        """Requête GraphQL, erreurs fatales par défaut.

        `partial` garde ce qui a été résolu quand une partie du lot échoue : GraphQL renvoie
        une erreur dure dès qu'un seul alias ne résout pas, mais sert quand même les autres.
        Sans cela, un sujet supprimé ou hors de portée du token ferait perdre tout le lot.
        """
        data = self._request("/graphql", {"query": query, "variables": variables})
        if errors := (data or {}).get("errors"):
            if not (partial and (data or {}).get("data")):
                raise GitHubError("GraphQL : " + "; ".join(e.get("message", "?") for e in errors)[:300])
        return data["data"]

    def _search_variables(self) -> dict:
        variables = {key: self.cfg.scoped(q) for key, q in SEARCHES.items()}
        if self.cfg.direct_review_requests_only:
            variables["toReview"] = self.cfg.scoped(DIRECT_REVIEW)
        variables["n"] = max(1, min(self.cfg.max_per_search, 100))
        return variables

    def fetch_signature(self) -> tuple[str, int | None]:
        """Empreinte de l'état côté GitHub, pour 1 point de quota au lieu de ~80."""
        data = self.graphql(SENTINEL_QUERY, self._search_variables())
        # Même normalisation que côté requête complète, sinon les empreintes ne
        # coïncideraient jamais (« …Z » contre « +00:00 »).
        stamps = {
            node["id"]: parse_ts(node["updatedAt"])
            for source in SEARCHES
            for node in ((data.get(source) or {}).get("nodes") or [])
            if node
        }
        return signature_of(stamps), (data.get("rateLimit") or {}).get("remaining")

    def fetch_pull_requests(self) -> tuple[list[PullRequest], str, int | None, list[str]]:
        variables = self._search_variables()
        data = self.graphql(PR_QUERY, variables)
        prs: dict[str, PullRequest] = {}
        truncated: list[str] = []
        for source in SEARCHES:
            block = data.get(source) or {}
            nodes = [n for n in (block.get("nodes") or []) if n]
            if (block.get("issueCount") or 0) > len(nodes):
                truncated.append(f"{source} ({block['issueCount']} résultats, {len(nodes)} lus)")
            for node in nodes:
                existing = prs.get(node["id"])
                pr = existing or _parse_pr(node)
                pr.sources.add(source)
                prs[pr.id] = pr
        rate = (data.get("rateLimit") or {}).get("remaining")
        self.viewer_face = (data.get("viewer") or {}).get("avatarUrl") or ""
        return list(prs.values()), data["viewer"]["login"], rate, truncated

    def fetch_people(self) -> list[Person]:
        """Membres des organisations de `scope`, les plus récemment actifs d'abord."""
        people: dict[str, Person] = {}
        activity: dict[str, datetime] = {}
        self.people_total = 0
        for org in self.cfg.orgs():
            try:
                data = self.graphql(PEOPLE_QUERY, {"org": org, "recent": f"{RECENT_ACTIVITY} org:{org}"})
            except GitHubError:
                continue
            listing = ((data.get("organization") or {}).get("membersWithRole") or {})
            self.people_total += listing.get("totalCount") or 0
            members = listing.get("nodes") or []
            for node in members:
                people[node["login"]] = Person(
                    login=node["login"], name=node.get("name") or "", avatar=node.get("avatarUrl") or ""
                )
            for node in ((data.get("recent") or {}).get("nodes") or []):
                if not node:
                    continue
                _note(activity, (node.get("author") or {}).get("login"), parse_ts(node.get("updatedAt")))
                for comment in ((node.get("comments") or {}).get("nodes") or []):
                    _note(activity, (comment.get("author") or {}).get("login"), parse_ts(comment.get("createdAt")))
                for review in ((node.get("reviews") or {}).get("nodes") or []):
                    _note(activity, (review.get("author") or {}).get("login"), parse_ts(review.get("submittedAt")))
        ranked = [replace(person, last_seen=activity.get(person.login)) for person in people.values()]
        # Actifs récemment d'abord, puis les autres par ordre alphabétique.
        ranked.sort(key=lambda person: (person.name or person.login).lower())
        ranked.sort(key=lambda person: person.last_seen or EPOCH, reverse=True)
        return ranked

    def activity(self, repo: str, login: str, per_page: int) -> list[dict]:
        """Activité d'un dépôt filtrée sur une personne : c'est là qu'est l'acteur du push.

        Une seule page : cette API pagine par curseur, le paramètre `page` est ignoré.
        """
        data = self._request(f"/repos/{repo}/activity?actor={login}&per_page={per_page}")
        return data if isinstance(data, list) else []

    def all_branch_names(self, repos: list[str]) -> list[tuple[str, str]]:
        """Toutes les branches de ces dépôts, noms seuls : 1 point par centaine.

        Ne pas résoudre le dernier commit ici est ce qui rend le balayage exhaustif
        possible : le faire sur 650 branches déclenche `RESOURCE_LIMITS_EXCEEDED`.
        """
        found: list[tuple[str, str]] = []
        for repo in repos:
            owner, _, name = repo.partition("/")
            cursor = None
            for _ in range(MAX_BRANCH_PAGES):
                after = f", after: {json.dumps(cursor)}" if cursor else ""
                query = (
                    "query { repository(owner: %s, name: %s) { defaultBranchRef { name } "
                    "refs(refPrefix: \"refs/heads/\", first: 100%s) "
                    "{ pageInfo { hasNextPage endCursor } nodes { name } } } }"
                    % (json.dumps(owner), json.dumps(name), after)
                )
                block = self.graphql(query, {}).get("repository") or {}
                self.defaults[repo] = (block.get("defaultBranchRef") or {}).get("name") or "main"
                refs = block.get("refs") or {}
                found += [(repo, node["name"]) for node in (refs.get("nodes") or []) if node]
                page = refs.get("pageInfo") or {}
                if not page.get("hasNextPage"):
                    break
                cursor = page.get("endCursor")
        return found

    def contributed_repos(self, login: str, viewer: str) -> list[str]:
        """Dépôts où la personne a committé, restreints au périmètre configuré."""
        owner = "viewer" if login == viewer else f'user(login: {json.dumps(login)})'
        query = (
            "query { " + owner + " { repositoriesContributedTo(contributionTypes: [COMMIT], first: 25, "
            "includeUserRepositories: true, orderBy: {field: PUSHED_AT, direction: DESC}) "
            "{ nodes { nameWithOwner } } } }"
        )
        data = self.graphql(query, {})
        block = (data.get("viewer") or data.get("user") or {}).get("repositoriesContributedTo") or {}
        orgs = self.cfg.orgs()
        return [
            node["nameWithOwner"]
            for node in (block.get("nodes") or [])
            if node and (not orgs or node["nameWithOwner"].split("/")[0] in orgs)
        ]

    def _batched(self, entries: list, size: int, block: callable) -> dict:
        """Interroge par lots d'alias : une requête pour beaucoup de branches."""
        answers: dict = {}
        for start in range(0, len(entries), size):
            chunk = entries[start : start + size]
            blocks = [f"  b{index}: {block(entry)}" for index, entry in enumerate(chunk)]
            data = self.graphql("query {\n" + "\n".join(blocks) + "\n}", {})
            for index, entry in enumerate(chunk):
                answers[entry] = data.get(f"b{index}")
        return answers

    def branches_without_open_pr(self, names: list[tuple[str, str]]) -> list[Branch]:
        """Branches existantes sans PR ouverte, avec l'état de leur PR la plus récente.

        En deux temps, parce que résoudre le dernier commit coûte cher : d'abord l'état des
        PR sur tout le lot, ensuite le commit sur le petit reste. Une PR ouverte est écartée
        ici : elle est déjà couverte par les sections de PR.
        """
        if not names:
            return []

        def pr_block(entry: tuple[str, str]) -> str:
            owner, _, repo = entry[0].partition("/")
            return (
                f"repository(owner: {json.dumps(owner)}, name: {json.dumps(repo)}) {{ "
                f"open: pullRequests(headRefName: {json.dumps(entry[1])}, states: [OPEN], first: 1) "
                "{ totalCount } "
                f"last: pullRequests(headRefName: {json.dumps(entry[1])}, first: 1, "
                "states: [MERGED, CLOSED], orderBy: {field: UPDATED_AT, direction: DESC}) "
                "{ nodes { state } } }"
            )

        answers = self._batched(list(names), BRANCH_PR_BATCH, pr_block)
        states: dict[tuple[str, str], str] = {}
        for entry, block in answers.items():
            block = block or {}
            if ((block.get("open") or {}).get("totalCount") or 0) > 0:
                continue  # une PR ouverte : ce n'est pas une branche orpheline
            nodes = (block.get("last") or {}).get("nodes") or []
            states[entry] = (nodes[0].get("state") if nodes else "") or ""
        orphan_names = list(states)

        def ref_block(entry: tuple[str, str]) -> str:
            owner, _, repo = entry[0].partition("/")
            return (
                f"repository(owner: {json.dumps(owner)}, name: {json.dumps(repo)}) "
                f'{{ ref(qualifiedName: {json.dumps("refs/heads/" + entry[1])}) '
                "{ target { ... on Commit { committedDate author { user { login } } } } } }"
            )

        details = self._batched(orphan_names, BRANCH_REF_BATCH, ref_block)
        branches = []
        for entry, block in details.items():
            ref = (block or {}).get("ref")
            if not ref:
                continue  # branche supprimée côté distant
            target = ref.get("target") or {}
            branches.append(
                Branch(
                    repo=entry[0],
                    name=entry[1],
                    committed_at=parse_ts(target.get("committedDate")),
                    author=((target.get("author") or {}).get("user") or {}).get("login") or "",
                    pr_state=states.get(entry, ""),
                )
            )
        return branches

    def still_valid(self, branches: list[Branch]) -> list[Branch]:
        """Ces branches existent-elles encore, et toujours sans PR ouverte ?

        Une requête, un point de quota : c'est ce qui permet de revalider l'affichage au
        même rythme que les PR, alors que la découverte complète coûte vingt fois plus.
        """
        if not branches:
            return []

        def block(branch: Branch) -> str:
            owner, _, repo = branch.repo.partition("/")
            return (
                f"repository(owner: {json.dumps(owner)}, name: {json.dumps(repo)}) {{ "
                f'ref(qualifiedName: {json.dumps("refs/heads/" + branch.name)}) {{ name }} '
                f"open: pullRequests(headRefName: {json.dumps(branch.name)}, states: [OPEN], first: 1) "
                "{ totalCount } }"
            )

        answers = self._batched(branches, BRANCH_PR_BATCH, block)
        kept = []
        for branch, answer in answers.items():
            answer = answer or {}
            if not answer.get("ref"):
                continue  # supprimée depuis le dernier balayage
            if ((answer.get("open") or {}).get("totalCount") or 0) > 0:
                continue  # une PR a été ouverte : la PR prend le relais
            kept.append(branch)
        return kept

    def compare_to_default(self, candidates: list[Branch]) -> list[Branch]:
        """Divergence de chaque branche avec la branche par défaut du dépôt.

        Appelé après filtrage : comparer les 83 branches orphelines de tout le monde coûtait
        38 points de quota pour n'en garder que deux.
        """
        if not candidates:
            return []

        def block(branch: Branch) -> str:
            owner, _, repo = branch.repo.partition("/")
            default = self.defaults.get(branch.repo, "main")
            return (
                f"repository(owner: {json.dumps(owner)}, name: {json.dumps(repo)}) "
                f'{{ ref(qualifiedName: {json.dumps("refs/heads/" + default)}) '
                f"{{ compare(headRef: {json.dumps(branch.name)}) "
                "{ status aheadBy behindBy } } }"
            )

        try:
            answers = self._batched(candidates, BRANCH_REF_BATCH, block)
        except GitHubError:
            # Une branche supprimée entre-temps fait échouer tout le lot : on garde les
            # branches sans leur écart plutôt que de perdre la section.
            return list(candidates)
        compared = []
        for branch, answer in answers.items():
            data = ((answer or {}).get("ref") or {}).get("compare") or {}
            compared.append(
                replace(
                    branch,
                    base=self.defaults.get(branch.repo, "main"),
                    status=data.get("status") or "",
                    ahead=data.get("aheadBy") or 0,
                    behind=data.get("behindBy") or 0,
                )
            )
        return compared

    def resolve_mentions(self, notifications: list[dict]) -> str:
        """Complète chaque mention avec l'auteur de son sujet, son numéro et son état.

        `GET /notifications` ne donne rien de tout cela, seulement une URL d'API. Une requête
        d'alias suffit pour tout le lot, et les mentions sont rares. L'état est ce qui permet
        d'écarter les sujets fermés, que les recherches `is:open` excluent déjà par ailleurs.
        """
        wanted = []
        for note in notifications:
            if note.get("reason") not in ("mention", "team_mention"):
                continue
            subject = note.get("subject") or {}
            kind = subject.get("type")
            repo = (note.get("repository") or {}).get("full_name") or ""
            tail = (subject.get("url") or "").rsplit("/", 2)[-2:]
            if kind not in ("PullRequest", "Issue") or "/" not in repo or len(tail) != 2:
                continue
            if not tail[1].isdigit():
                continue
            field = "pullRequest" if kind == "PullRequest" else "issue"
            wanted.append((note, repo, field, int(tail[1])))
        if not wanted:
            return ""
        blocks = []
        for index, (_, repo, field, number) in enumerate(wanted):
            owner, _, name = repo.partition("/")
            fields = "state isDraft" if field == "pullRequest" else "state"
            blocks.append(
                f"  m{index}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) "
                f"{{ {field}(number: {number}) {{ {fields} author {{ login avatarUrl(size: 64) }} }} }}"
            )
        try:
            data = self.graphql("query {\n" + "\n".join(blocks) + "\n}", {}, partial=True)
        except GitHubError as exc:
            return str(exc)
        for index, (note, _, field, number) in enumerate(wanted):
            subject = (data.get(f"m{index}") or {}).get(field) or {}
            author = subject.get("author") or {}
            note["gittodo"] = {
                # Le lot a répondu : un sujet vide ici est introuvable ou hors de portée du
                # token, pas victime d'une panne passagère. La distinction sert à l'affichage.
                "resolved": bool(subject),
                "number": number,
                "login": author.get("login") or "",
                "avatar": author.get("avatarUrl") or "",
                "state": subject.get("state") or "",
                "draft": bool(subject.get("isDraft")),
            }
        return ""

    def fetch_closed(self, days: int, mine: int = 20, involved: int = 10) -> tuple[list[Closure], int | None]:
        """PR sorties du périmètre ouvert, avec l'acteur de leur clôture et ce qui s'est dit depuis.

        Deux recherches suffisent et portent déjà tout : pas d'étape d'hydratation. Mesuré à 3
        points pour trente jours, ce qui tient sur une cadence lente.
        """
        since = (datetime.now(timezone.utc).date() - timedelta(days=max(1, days))).isoformat()
        variables = {
            key: self.cfg.scoped(query.replace("{since}", since)) for key, query in CLOSED_SEARCHES.items()
        }
        variables["mine_n"] = max(1, min(mine, 50))
        variables["involved_n"] = max(1, min(involved, 50))
        data = self.graphql(CLOSED_QUERY, variables)
        vus: dict[str, Closure] = {}
        for source in CLOSED_SEARCHES:
            for node in (data.get(source) or {}).get("nodes") or []:
                if not node or node.get("state") not in ("MERGED", "CLOSED"):
                    continue
                closure = _parse_closure(node, source)
                if closure is not None:
                    vus.setdefault(closure.pr.id, closure)
        return list(vus.values()), (data.get("rateLimit") or {}).get("remaining")

    def fetch_code_owners(self, prs: list[PullRequest]) -> dict[str, tuple[str, ...]]:
        """Propriétaires dont la review sera obligatoire, pour les PR où GitHub ne l'a pas dit.

        GitHub ne pose ses demandes `asCodeOwner` qu'à l'ouverture d'une PR : sur un draft, la
        liste est vide alors que la review sera bel et bien exigée. On rejoue donc la règle du
        dépôt sur les fichiers modifiés. Là où GitHub s'est prononcé, on ne le refait pas : sa
        liste est la vérité, puisqu'elle sait aussi qui a déjà rendu son avis.

        Mesuré à 1 point pour une douzaine de PR et leurs dépôts : le fichier de règles et les
        chemins modifiés tiennent dans la même requête d'alias.
        """
        if not prs:
            return {}
        repos = sorted({pr.repo for pr in prs})
        blocks = []
        for index, repo in enumerate(repos):
            owner, _, name = repo.partition("/")
            candidates = " ".join(
                f'c{rank}: object(expression:{json.dumps("HEAD:" + path)}) {{ ... on Blob {{ text }} }}'
                for rank, path in enumerate(codeowners.PATHS)
            )
            blocks.append(
                f"  r{index}: repository(owner:{json.dumps(owner)}, name:{json.dumps(name)}) {{ {candidates} }}"
            )
        for index, pr in enumerate(prs):
            owner, _, name = pr.repo.partition("/")
            blocks.append(
                f"  p{index}: repository(owner:{json.dumps(owner)}, name:{json.dumps(name)}) {{ "
                f"pullRequest(number:{pr.number}) {{ files(first:{OWNER_FILES}) {{ nodes {{ path }} }} }} }}"
            )
        data = self.graphql("query {\n" + "\n".join(blocks) + "\n}", {})
        rules = {}
        for index, repo in enumerate(repos):
            block = data.get(f"r{index}") or {}
            text = next(
                ((block.get(f"c{rank}") or {}).get("text") for rank in range(len(codeowners.PATHS))
                 if (block.get(f"c{rank}") or {}).get("text")),
                "",
            )
            rules[repo] = codeowners.parse(text)
        found: dict[str, tuple[str, ...]] = {}
        for index, pr in enumerate(prs):
            node = ((data.get(f"p{index}") or {}).get("pullRequest") or {})
            paths = [f["path"] for f in ((node.get("files") or {}).get("nodes") or [])]
            owners = set(codeowners.owners(paths, rules.get(pr.repo, ())))
            # Les mêmes exclusions que GitHub applique à ses propres demandes, sans quoi les
            # deux listes ne voudraient pas dire la même chose : jamais l'auteur, et plus
            # personne qui a déjà rendu son avis.
            owners -= {pr.author} | {review.author for review in pr.reviews}
            if owners:
                found[pr.id] = tuple(sorted(owners))
        return found

    def with_code_owners(self, prs: list[PullRequest]) -> list[PullRequest]:
        """Les mêmes PR, complétées des propriétaires que GitHub n'a pas encore sollicités."""
        found = self.fetch_code_owners([pr for pr in prs if not pr.code_owners])
        return [replace(pr, code_owners=found[pr.id]) if pr.id in found else pr for pr in prs]

    def fetch_notifications(self) -> tuple[list[dict], str]:
        """Boîte des non-lues, paginée : une mention peut être très loin dans la pile.

        Sur un compte actif, les demandes de review et l'activité de CI enterrent les
        mentions bien au-delà de la première page. La borne existe pour ne pas enchaîner les
        appels REST à l'infini ; quand elle est atteinte, l'app le dit au lieu de se taire.
        """
        notifications: list[dict] = []
        for page in range(1, NOTIFICATION_PAGES + 1):
            batch = self._request(f"/notifications?all=false&per_page={NOTIFICATION_PAGE_SIZE}&page={page}")
            # Arrêt sur page vide, et non sur page incomplète : l'API sert moins que demandé.
            if not isinstance(batch, list) or not batch:
                return notifications, ""
            notifications += batch
        return notifications, f"{len(notifications)} notifications non lues lues, les plus anciennes ignorées"


# Page d'état officielle de GitHub, hors API : elle dit si la panne est chez eux, et à quel
# point. Les valeurs de `indicator` sont « none », « minor », « major » et « critical ».
STATUS_URL = "https://www.githubstatus.com/api/v2/status.json"


def service_status() -> tuple[str, str]:
    """Gravité annoncée par GitHub, et sa description. Vide si la page ne répond pas.

    La requête part sans en-tête d'autorisation : c'est un autre hôte que l'API, et le token
    n'a rien à y faire. Elle ne consomme aucun quota GitHub.
    """
    try:
        request = urllib.request.Request(STATUS_URL, headers={"User-Agent": "GitTodo"})
        with urllib.request.urlopen(request, timeout=6) as response:
            payload = json.loads(response.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return "", ""
    status = payload.get("status") or {}
    return str(status.get("indicator") or ""), str(status.get("description") or "")


def signature_of(stamps: dict[str, datetime]) -> str:
    """Empreinte comparable d'un ensemble de PR et de leurs dates de mise à jour."""
    return "|".join(f"{pr}:{stamp.isoformat()}" for pr, stamp in sorted(stamps.items()))


def _note(store: dict[str, datetime], login: str | None, when: datetime) -> None:
    """Retient la date d'action la plus récente par personne."""
    if login and when > store.get(login, EPOCH):
        store[login] = when


def _actor(node: dict | None) -> tuple[str, bool, str]:
    node = node or {}
    return node.get("login") or "ghost", node.get("__typename") == "Bot", node.get("avatarUrl") or ""


def _comment(node: dict) -> Comment:
    login, is_bot, avatar = _actor(node.get("author"))
    return Comment(
        author=login,
        is_bot=is_bot,
        created_at=parse_ts(node.get("createdAt")),
        url=node.get("url") or "",
        body=(node.get("body") or "").strip(),
        avatar=avatar,
        my_reactions=tuple(
            group["content"] for group in (node.get("reactionGroups") or []) if group.get("viewerHasReacted")
        ),
    )


def _parse_closure(node: dict, source: str) -> Closure | None:
    """Construit la PR fermée et l'événement de clôture depuis un nœud de recherche.

    L'acteur d'un merge est `mergedBy` ; celui d'une fermeture sans merge n'existe que dans la
    timeline, `PullRequest` n'ayant pas de `closedBy`.
    """
    fin = (node.get("timelineItems") or {}).get("nodes") or []
    ferme = fin[0] if fin else {}
    merged = bool(node.get("merged"))
    acteur = (node.get("mergedBy") or {}) if merged else (ferme.get("actor") or {})
    quand = parse_ts(node.get("mergedAt") if merged else node.get("closedAt"))
    if quand is None:
        return None
    author, _, author_avatar = _actor(node.get("author"))
    threads = tuple(
        Thread(
            id=t["id"],
            resolved=bool(t.get("isResolved")),
            outdated=False,
            path="",
            line=None,
            comments=tuple(_comment(c) for c in ((t.get("comments") or {}).get("nodes") or [])),
            opener=_actor((((t.get("opener") or {}).get("nodes") or [{}])[0]).get("author"))[0],
        )
        for t in ((node.get("reviewThreads") or {}).get("nodes") or [])
    )
    comments = tuple(_comment(c) for c in ((node.get("comments") or {}).get("nodes") or []))
    pr = PullRequest(
        id=node["id"],
        repo=node["repository"]["nameWithOwner"],
        number=node["number"],
        title=(node.get("title") or "").strip(),
        url=node["url"],
        author=author,
        avatar=author_avatar,
        is_draft=False,
        created_at=quand,
        updated_at=parse_ts(node.get("updatedAt")) or quand,
        review_decision=None,
        mergeable=None,
        ci_state=None,
        reviewers=(),
        reviews_count=0,
        threads=threads,
        comments=comments,
        head=node.get("headRefName") or "",
        base=node.get("baseRefName") or "",
        sources={source},
    )
    return Closure(
        pr=pr,
        merged=merged,
        actor=acteur.get("login") or "",
        actor_avatar=acteur.get("avatarUrl") or "",
        at=quand,
    )


def _parse_pr(node: dict) -> PullRequest:
    author, _, author_avatar = _actor(node.get("author"))
    commits = (node.get("commits") or {}).get("nodes") or []
    rollup = ((commits[0]["commit"] if commits else {}) or {}).get("statusCheckRollup") or {}
    reviewers = []
    owners = []
    # Photo par login : l'auteur, les reviewers sollicités, ceux qui ont posé un avis, et tous
    # ceux qui ont écrit. C'est ce qui permet de montrer les visages des gens qu'une ligne cite.
    portraits: dict[str, str] = {author: author_avatar} if author_avatar else {}
    for request in ((node.get("reviewRequests") or {}).get("nodes") or []):
        reviewer = request.get("requestedReviewer") or {}
        if name := reviewer.get("login") or reviewer.get("slug"):
            asked = name if reviewer.get("__typename") == "User" else f"@{name}"
            reviewers.append(asked)
            # Une demande posée par CODEOWNERS ne se retire pas : la review est obligatoire.
            if request.get("asCodeOwner"):
                owners.append(asked)
            if face := reviewer.get("avatarUrl"):
                portraits[name] = face
    threads = tuple(
        Thread(
            id=t["id"],
            resolved=bool(t.get("isResolved")),
            outdated=bool(t.get("isOutdated")),
            path=t.get("path") or "",
            line=t.get("line"),
            comments=tuple(_comment(c) for c in ((t.get("comments") or {}).get("nodes") or [])),
            opener=_actor((((t.get("opener") or {}).get("nodes") or [{}])[0]).get("author"))[0],
        )
        for t in ((node.get("reviewThreads") or {}).get("nodes") or [])
    )
    reviews = []
    for r in ((node.get("reviews") or {}).get("nodes") or []):
        who = (r.get("author") or {}).get("login") or "ghost"
        reviews.append(Review(author=who, state=r.get("state") or "", submitted_at=parse_ts(r.get("submittedAt"))))
        if face := (r.get("author") or {}).get("avatarUrl"):
            portraits[who] = face
    comments = tuple(_comment(c) for c in ((node.get("comments") or {}).get("nodes") or []))
    for comment in tuple(c for t in threads for c in t.comments) + comments:
        if comment.avatar:
            portraits.setdefault(comment.author, comment.avatar)
    return PullRequest(
        id=node["id"],
        repo=node["repository"]["nameWithOwner"],
        number=node["number"],
        title=(node.get("title") or "").strip(),
        url=node["url"],
        author=author,
        avatar=author_avatar,
        is_draft=bool(node.get("isDraft")),
        created_at=parse_ts(node.get("createdAt")),
        updated_at=parse_ts(node.get("updatedAt")),
        review_decision=node.get("reviewDecision"),
        mergeable=node.get("mergeable"),
        ci_state=rollup.get("state"),
        reviewers=tuple(reviewers),
        code_owners=tuple(owners),
        reviews_count=((node.get("reviews") or {}).get("totalCount") or 0),
        threads=threads,
        comments=comments,
        head=node.get("headRefName") or "",
        base=node.get("baseRefName") or "",
        reviews=tuple(reviews),
        portraits=portraits,
        last_commit_at=parse_ts(((commits[0]["commit"] if commits else {}) or {}).get("committedDate")),
    )
