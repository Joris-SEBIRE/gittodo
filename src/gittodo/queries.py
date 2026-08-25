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

# Sonde légère (1 point de quota) : sert à savoir s'il vaut la peine de payer PR_QUERY,
# qui coûte ~80 points. C'est ce qui rend un rythme de quelques secondes soutenable.
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

PR_QUERY = """
query($mine:String!,$toReview:String!,$commented:String!,$reviewed:String!,$assigned:String!,
      $mentioned:String!,$n:Int!) {
  rateLimit { cost remaining resetAt }
  viewer { login }
  mine: search(query:$mine, type:ISSUE, first:$n) { issueCount nodes { ...PR } }
  toReview: search(query:$toReview, type:ISSUE, first:$n) { issueCount nodes { ...PR } }
  commented: search(query:$commented, type:ISSUE, first:$n) { issueCount nodes { ...PR } }
  reviewed: search(query:$reviewed, type:ISSUE, first:$n) { issueCount nodes { ...PR } }
  assigned: search(query:$assigned, type:ISSUE, first:$n) { issueCount nodes { ...PR } }
  mentioned: search(query:$mentioned, type:ISSUE, first:$n) { issueCount nodes { ...PR } }
}
fragment PR on PullRequest {
  id number title url isDraft createdAt updatedAt
  headRefName baseRefName
  repository { nameWithOwner }
  author { __typename login avatarUrl(size: 64) }
  reviewDecision mergeable
  reviewRequests(first:20){ nodes { asCodeOwner requestedReviewer {
    __typename ... on User{login avatarUrl(size: 64)} ... on Team{slug} } } }
  reviews(last:20){ totalCount nodes { author { login avatarUrl(size: 64) } state submittedAt } }
  commits(last:1){ nodes { commit { committedDate statusCheckRollup { state } } } }
  reviewThreads(first:30){
    totalCount
    nodes { id isResolved isOutdated path line
      opener: comments(first:1){ nodes { author { __typename login } } }
      comments(last:12){ totalCount nodes { author { __typename login avatarUrl(size: 64) } createdAt url body
        reactionGroups { content viewerHasReacted } } } }
  }
  comments(last:15){ totalCount nodes { author { __typename login avatarUrl(size: 64) } createdAt url body
    reactionGroups { content viewerHasReacted } } }
}
"""

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
CLOSED_QUERY = """
query($mine:String!,$involved:String!,$mine_n:Int!,$involved_n:Int!) {
  rateLimit { cost remaining }
  mine: search(query:$mine, type:ISSUE, first:$mine_n) { issueCount nodes { ...Fin } }
  involved: search(query:$involved, type:ISSUE, first:$involved_n) { issueCount nodes { ...Fin } }
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
  comments(last: 6) { totalCount nodes { author { __typename login avatarUrl(size: 64) } createdAt url body
    reactionGroups { content viewerHasReacted } } }
  reviewThreads(first: 8) { nodes { id isResolved
    opener: comments(first:1){ nodes { author { __typename login } } }
    comments(last: 4) { nodes { author { __typename login avatarUrl(size: 64) } createdAt url body
      reactionGroups { content viewerHasReacted } } } } }
}
"""
