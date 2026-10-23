"""Requêtes GitHub : l'état complet des PR, et le catalogue des personnes de l'org.

`{who}` vaut `@me`, ou le login de la personne quand on regarde l'app à sa place.
"""

# `review-requested:` inclut les demandes adressées à une équipe dont on est membre ;
# `user-review-requested:` ne garde que celles où la personne est nommément désignée.
DIRECT_REVIEW = "is:open is:pr user-review-requested:{who} archived:false sort:updated-desc"

SEARCHES = {
    "mine": "is:open is:pr author:{who} archived:false sort:updated-desc",
    "toReview": "is:open is:pr review-requested:{who} archived:false sort:updated-desc",
    "commented": "is:open is:pr commenter:{who} -author:{who} archived:false sort:updated-desc",
    "reviewed": "is:open is:pr reviewed-by:{who} -author:{who} archived:false sort:updated-desc",
    "assigned": "is:open is:pr assignee:{who} -author:{who} archived:false sort:updated-desc",
    # Être nommé dans une PR ouverte est une sollicitation qui doit survivre à la lecture de
    # la notification. `mentions:` ne couvre pas les mentions d'équipe, que seule la boîte
    # des non-lues voit passer.
    "mentioned": "is:open is:pr mentions:{who} -author:{who} archived:false sort:updated-desc",
}

# Sonde légère (1 point de quota) : sert à savoir s'il vaut la peine de payer les recherches
# complètes, qui coûtent ~50 points. C'est ce qui rend un rythme de quelques secondes soutenable.
SENTINEL_QUERY = """
query($mine:String!,$toReview:String!,$commented:String!,$reviewed:String!,$assigned:String!,
      $mentioned:String!,$n:Int!) {
  rateLimit { remaining }
  mine: search(query:$mine, type:ISSUE, first:$n) { nodes { ...Stamp } }
  toReview: search(query:$toReview, type:ISSUE, first:$n) { nodes { ...Stamp } }
  commented: search(query:$commented, type:ISSUE, first:$n) { nodes { ...Stamp } }
  reviewed: search(query:$reviewed, type:ISSUE, first:$n) { nodes { ...Stamp } }
  assigned: search(query:$assigned, type:ISSUE, first:$n) { nodes { ...Stamp } }
  mentioned: search(query:$mentioned, type:ISSUE, first:$n) { nodes { ...Stamp } }
}
fragment Stamp on PullRequest { id updatedAt }
"""

# Une requête par recherche, lancées en parallèle. Groupées, les six se suivaient côté GitHub :
# un jour où sa recherche répond en deux secondes, le total passait les dix secondes au-delà
# desquelles GitHub coupe une requête GraphQL (502, 504), alors que la page d'état restait verte.
SEARCH_QUERY = """
query($q:String!,$n:Int!) {
  rateLimit { cost remaining resetAt }
  viewer { login avatarUrl(size: 64) }
  search(query:$q, type:ISSUE, first:$n) { issueCount pageInfo { endCursor } nodes { ...PR } }
}
"""

PR_FRAGMENT = """
fragment PR on PullRequest {
  id number title url isDraft createdAt updatedAt
  headRefName baseRefName
  repository { nameWithOwner }
  author { __typename login avatarUrl(size: 64) }
  reviewDecision mergeable
  reviewRequests(first:20){ nodes { asCodeOwner requestedReviewer {
    __typename ... on User{login avatarUrl(size: 64)} ... on Team{slug avatarUrl(size: 64)} } } }
  reviews(last:50){ totalCount nodes { author { login avatarUrl(size: 64) } state submittedAt } }
  commits(last:1){ nodes { commit { committedDate statusCheckRollup { state } } } }
  reviewThreads(first:30){ totalCount pageInfo { endCursor } nodes { ...Thread } }
  comments(last:15){ totalCount nodes { author { __typename login avatarUrl(size: 64) } createdAt url body
    reactionGroups { content viewerHasReacted } } }
}
"""
THREAD_FRAGMENT = """
fragment Thread on PullRequestReviewThread {
  id isResolved isOutdated path line
  opener: comments(first:1){ nodes { author { __typename login } } }
  comments(last:12){ totalCount nodes { state author { __typename login avatarUrl(size: 64) } createdAt url body
    reactionGroups { content viewerHasReacted } } }
}
"""
PR_FRAGMENT += THREAD_FRAGMENT
SEARCH_QUERY += PR_FRAGMENT

# Une PR relue sans passer par la recherche, quand celle-ci l'a perdue : son état dit si elle est
# vraiment sortie (fermée, mergée) ou seulement absente d'une liste partielle.
NODES_QUERY = """
query($ids:[ID!]!) {
  rateLimit { remaining }
  nodes(ids:$ids) { ... on PullRequest { state ...PR } }
}
""" + PR_FRAGMENT

# PR lues au plus par la requête d'appoint, toutes recherches confondues, dans l'ordre de
# SEARCHES : mes PR d'abord. Une PR coûte ~0,65 point, mesuré : la suite ne coûte que les PR
# manquantes, rien quand aucune recherche ne déborde, et vingt points au pire.
OVERFLOW_BUDGET = 30


def threads_query(count: int) -> str:
    """Suite des fils de revue de `count` PR, une page chacune, à partir de leur curseur.

    Les fils viennent dans l'ordre de leur ouverture : sur une PR très commentée, ceux qui
    dépassent la première page sont les plus récents, ceux qu'on attend. Seuls les fils
    manquants sont demandés, et seulement pour ces PR.
    """
    params = ",".join(f"$id{i}:ID!,$after{i}:String!,$n{i}:Int!" for i in range(count))
    blocks = "\n".join(
        f"  p{i}: node(id:$id{i}) {{ ... on PullRequest {{ reviewThreads(first:$n{i}, after:$after{i}) "
        f"{{ pageInfo {{ endCursor hasNextPage }} nodes {{ ...Thread }} }} }} }}"
        for i in range(count)
    )
    return f"query({params}) {{\n  rateLimit {{ remaining }}\n{blocks}\n}}\n{THREAD_FRAGMENT}"


def more_query(sources: list[str]) -> str:
    """Suite des recherches qui ont débordé, une page chacune, à partir de leur curseur."""
    params = ",".join(f"${s}:String!,${s}_after:String!,${s}_n:Int!" for s in sources)
    blocks = "\n".join(
        f"  {s}: search(query:${s}, type:ISSUE, first:${s}_n, after:${s}_after) {{ nodes {{ ...PR }} }}"
        for s in sources
    )
    return f"query({params}) {{\n  rateLimit {{ remaining }}\n{blocks}\n}}\n{PR_FRAGMENT}"


PEOPLE_QUERY = """
query($org:String!, $recent:String!) {
  organization(login:$org) {
    membersWithRole(first:100) { totalCount nodes { login name avatarUrl(size: 64) } }
  }
  recent: search(query:$recent, type:ISSUE, first:50) {
    nodes { ... on PullRequest {
      updatedAt
      author { login }
      comments(last:3){ nodes { author { login } createdAt } }
      reviews(last:3){ nodes { author { login } submittedAt } }
    } }
  }
}
"""

# Activité récente de l'org, pour classer le menu « voir en tant que » (1 point de quota).
RECENT_ACTIVITY = "is:pr archived:false sort:updated-desc"


# Suivi parallèle des PR sorties du périmètre ouvert. Deux recherches seulement : `author:` pour
# l'histoire de mes PR et l'acteur de leur clôture, `involves:` pour ce qui se dit après. La
# troisième voie évidente, `review-requested:`, est écartée à la mesure : 357 PR fermées y
# remontent sur 30 jours, presque toutes par du bruit administratif, et un message qui ne me
# nomme pas sur une PR que je n'ai jamais touchée n'attend rien de moi.
CLOSED_SEARCHES = {
    "mine": "is:pr author:{who} is:closed archived:false sort:updated-desc closed:>={since}",
    "involved": "is:pr involves:{who} is:closed archived:false sort:updated-desc updated:>={since}",
}

# `mergedBy` donne l'auteur d'un merge ; une fermeture sans merge n'a pas d'équivalent, son
# acteur ne s'obtient que par la timeline.
#
# La profondeur des messages est celle de `SEARCH_QUERY` parce qu'elle est gratuite, et c'est elle
# qui compte : la discussion générale s'acquitte par citation, et une citation qu'on n'a pas lue
# transforme un point réglé en demande éternelle. `reviewThreads`, lui, coûte deux points par fil
# et par recherche, et le mesurer a montré qu'aller au-delà de quinze ne trouvait rien de plus :
# le quota vaut mieux ailleurs. Ce sont les fils les plus récents qui sont lus, parce qu'une
# demande en attente se trouve là et non dans les premiers échanges, et ce qui reste non lu est
# annoncé en bas du menu.
#
# Une requête par recherche, comme pour les PR ouvertes : groupées, les deux passaient 8,5 s un
# jour de lenteur, à un souffle des dix secondes au-delà desquelles GitHub coupe.
CLOSED_QUERY = """
query($q:String!,$n:Int!) {
  rateLimit { cost remaining }
  search(query:$q, type:ISSUE, first:$n) { issueCount nodes { ...Fin } }
}
fragment Fin on PullRequest {
  id number title url state merged mergedAt closedAt updatedAt
  headRefName baseRefName
  repository { nameWithOwner }
  author { __typename login avatarUrl(size: 64) }
  mergedBy { login avatarUrl(size: 64) }
  timelineItems(last: 1, itemTypes: [CLOSED_EVENT]) {
    nodes { ... on ClosedEvent { createdAt actor { login avatarUrl(size: 64) } } }
  }
  comments(last: 15) { totalCount nodes { author { __typename login avatarUrl(size: 64) } createdAt url body
    reactionGroups { content viewerHasReacted } } }
  reviewThreads(last: 15) { totalCount nodes { id isResolved
    opener: comments(first:1){ nodes { author { __typename login } } }
    comments(last: 12) { nodes { state author { __typename login avatarUrl(size: 64) } createdAt url body
      reactionGroups { content viewerHasReacted } } } } }
}
"""
