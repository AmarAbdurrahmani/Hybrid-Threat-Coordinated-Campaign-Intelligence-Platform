# GitHub issue and pull request comments, 2018–2020

75,000 distinct comment IDs and normalized texts from 1,552 historical GitHub accounts. The account labels come from Golzadeh et al., [A ground-truth dataset to identify bots in GitHub](https://doi.org/10.5281/zenodo.4000388). There are 123 bot-labelled and 1,429 human-labelled authors. Comment counts are 6,572 and 68,428 respectively.

Comments originate from [GH Archive](https://www.gharchive.org/), queried through the public ClickHouse github_events table. The join uses both the account and project in the annotation release. Only created IssueCommentEvent records dated January 2018 through July 2020 with nonempty bodies of at most 12,000 bytes were eligible. Sampling used shuffled account/project pairs (seed 497), up to 140 comments per author, and global ID and normalized-text deduplication. Account and thread identifiers are hashed; comment IDs retain their source namespace. Common credential-like strings and email addresses were masked. Original comment authors retain their respective rights.

Load the two compressed tables with `detector.datasets.load("github_bodegha_2018_2020")`. Labels describe historical accounts. For evaluation, split by account. The corpus concerns software-development conversations; Kosovo/YouTube accuracy requires separate validation.
