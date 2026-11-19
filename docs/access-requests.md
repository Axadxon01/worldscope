# Access requests to send

Two data providers grant access by request, with a wait. Send both now so access is
ready when ingestion (Milestone 3) and conflict history (Milestone 9) need it. Replace
the parts in angle brackets.

## ReliefWeb API appname

ReliefWeb requires a pre-approved `appname` on every API call; names that were not
requested have been disabled since 1 November 2025. The approved name must combine
your organisation or name, your purpose and random characters.

Request form: https://apidoc.reliefweb.int/parameters#appname

Suggested request:

> Name / organisation: <your name>, independent developer (Uzbekistan)
> Application: WorldScope Intelligence, a research platform that helps users follow
> humanitarian, political and security developments by country and region.
> Use of the API: read-only search of reports and disasters (about 200 to 500 calls
> per day), storing titles, sources, countries, dates and links, always linking back
> to ReliefWeb and the original publisher.
> Requested appname: worldscope-research-<6 random letters>

When approved, add it to `.env` as `RELIEFWEB_APPNAME=...` (wired up in Milestone 3).

## UCDP API token

Every UCDP API call needs a free token. Tokens are issued by email after the
maintainers review the intended use, usually within 3 to 5 working days. The quota is
5,000 requests per day per token.

Instructions and contact address: https://ucdp.uu.se/apidocs/

Suggested email:

> Subject: API token request: WorldScope Intelligence
>
> Hello,
>
> I am building WorldScope Intelligence, a research platform that gives users
> historical context for current conflicts. I would like a UCDP API token to read
> georeferenced events (GED) and the conflict datasets, at well under 1,000 requests
> per day.
>
> UCDP data would be shown with full attribution to the Uppsala Conflict Data Program
> and links to the dataset, and would not be redistributed in bulk.
>
> Name: <your name>
> Country: Uzbekistan
> Project repository: available on request
>
> Thank you,
> <your name>

When the token arrives, add it to `.env` as `UCDP_ACCESS_TOKEN=...` (wired up in
Milestone 9).
