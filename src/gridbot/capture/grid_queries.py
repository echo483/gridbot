"""GRID GraphQL query strings.

The CS2 Series State query is the canonical shape LIVE-VERIFIED 2026-06-22
(cs2agent grid-api skill) — use verbatim, keep in sync with that record.
Non-CS2 titles get the interface-minimal GENERIC query until fields are
confirmed by observation/introspection — never guess title-specific fields.
"""

SERIES_STATE_CS2 = """
query SeriesState($id: ID!) {
  seriesState(id: $id) {
    id
    format
    started
    finished
    valid
    teams { name ... on SeriesTeamStateCs2 { score won } ... on SeriesTeamStateCsgo { score won } }
    games {
      sequenceNumber
      started
      finished
      map { name }
      clock { currentSeconds }
      teams {
        __typename
        name
        ... on GameTeamStateCs2 {
          side won score money loadoutValue netWorth
          objectives { id type completionCount completedFirst }
          players { ... on GamePlayerStateCs2 { alive currentHealth inventory { items { name } } } }
        }
        ... on GameTeamStateCsgo {
          side won score money loadoutValue netWorth
          objectives { id type completionCount completedFirst }
          players { ... on GamePlayerStateCsgo { alive currentHealth inventory { items { name } } } }
        }
      }
      segments {
        sequenceNumber type started finished duration
        teams {
          __typename
          name
          ... on SegmentTeamStateCs2 { side won winType objectives { id type completionCount completedFirst } }
          ... on SegmentTeamStateCsgo { side won winType objectives { id type completionCount completedFirst } }
        }
      }
    }
  }
}
"""

# TODO(introspect): extend with per-title fragments once real __typenames are
# observed in captures (grid-api skill rule: no invented fields).
SERIES_STATE_GENERIC = """
query SeriesState($id: ID!) {
  seriesState(id: $id) {
    id
    format
    started
    finished
    valid
    teams { __typename name }
    games {
      __typename
      sequenceNumber
      started
      finished
      teams { __typename name }
      segments { __typename sequenceNumber type started finished teams { __typename name } }
    }
  }
}
"""

# Discovery ladder: try RICH first; on FieldUndefined fall back. Confirmed core:
# id/startTimeScheduled/pagination (cookbook §7). teams.baseInfo.name and
# tournament.venueType are cookbook-referenced; productServiceLevels appears in
# 03's filter usage — each unconfirmed field is isolated to a richer rung.
DISCOVER_RICH = """
query DiscoverSeries($titleId: ID!, $gte: String!, $lte: String!, $after: Cursor) {
  allSeries(
    filter: { titleId: $titleId, startTimeScheduled: { gte: $gte, lte: $lte } }
    orderBy: StartTimeScheduled
    first: 50
    after: $after
  ) {
    totalCount
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id
        startTimeScheduled
        title { nameShortened }
        tournament { name nameShortened }
        teams { baseInfo { name } }
        productServiceLevels { productName serviceLevel }
      }
    }
  }
}
"""

DISCOVER_MINIMAL = """
query DiscoverSeries($titleId: ID!, $gte: String!, $lte: String!, $after: Cursor) {
  allSeries(
    filter: { titleId: $titleId, startTimeScheduled: { gte: $gte, lte: $lte } }
    orderBy: StartTimeScheduled
    first: 50
    after: $after
  ) {
    totalCount
    pageInfo { hasNextPage endCursor }
    edges { node { id startTimeScheduled teams { baseInfo { name } } } }
  }
}
"""

TITLES_PLAIN = "query Titles { titles { id name nameShortened } }"
TITLES_CONNECTION = (
    "query Titles { titles { edges { node { id name nameShortened } } } }"
)
