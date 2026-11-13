# GTM deal reports

Independent Railway scheduled worker posting as the existing Mercedes Slack bot.
Scope: infrastructure script; no Core, Alpha, WWW, or Temporal runtime imports.

- Project: `67b145f8-d6d9-4402-aa0d-310f005122be`
- Production: `bac057a6-1b9d-4ebe-a91a-1205c0a49ca0`
- Service: `1a94e744-36ce-4e6b-9389-facaac502bf0` (`gtm-deal-reports`)
- Destination: `C08GM9QL7QC` (`gtm-general`), existing Mercedes bot.
- HubSpot portal: `43974586`; required reads: account details, deal properties,
  deals, company associations and companies. HubSpot is never mutated.

The hourly UTC trigger checks `America/Los_Angeles` before doing any API work.
Only Monday–Friday at 12:00, 18:00 and 21:00 local time are eligible.
This handles daylight saving changes without editing cron. Railway may start a
run a few minutes after the hour. At 21:00 the report counts deals created since
Monday 00:00 Pacific and includes counts grouped by deal source (including
missing sources) and lists SDR attribution for sales-sourced outbound deals.
The daily reports list deals created since local midnight and include SDR
attribution on sales-sourced outbound rows. Attribution uses a Nooks call ID
recorded on the deal when available. Otherwise it checks associated contacts
for booking calls within three hours of deal creation. Inconclusive matches
and unavailable call lookups are labeled in Slack. The HubSpot token also
needs access to deal/contact and contact/call associations, calls, and owners.
All active HubSpot deal records, stages and pipelines are included.
Only the 18:00 Pacific daily report with zero deals mentions Dillon, Xavier,
Sarah, Nicole, Liz, and Alex and asks: “what are your priorities tomorrow to
increase our SQL count?” Slack IDs
come from the User ID Lookups Google Sheet (`Sheet1!A2:B7`, verified October 27,
2026). The 21:00 week-to-date summary does not include this prompt.
Distinct deal IDs are counted separately even if company/deal names match. Archived/deleted
records are excluded by HubSpot search. Creation timestamps use `createdate`;
sources use the current display labels of `deal_source`. Each 18:00 daily deal line
includes its current deal owner, resolved from `hubspot_owner_id` through the
HubSpot owners directory (including archived owners). Unassigned deals and
unavailable owner names are explicitly marked. The token must also allow owner reads.
Multiple associated
companies are listed together; missing company/source data is marked explicitly.

`HUBSPOT_PRIVATE_TOKEN` and `SLACK_BOT_TOKEN` reference the existing `leads-update`
service variables. Do not export their values into source or logs. `REPORT_ENABLED`
must equal `true`. `REPORT_STATE_PATH=/data/reports.sqlite` uses the dedicated
Railway volume. No public endpoint or Claude runtime is needed.

Report payloads are durably recorded before posting. Each Slack part has a stable
metadata report ID and a delivery row. Confirmed parts are not resent. If a post
has an ambiguous result, retries reconcile Slack history; if no matching message
can be proven, execution fails for manual investigation rather than risking a
duplicate. A local volume lock serializes runs. HTTP retries are bounded; the
process has a 15-minute deadline and exits after each run. Railway cron runs are
not automatically retried. Manual reruns within the scheduled hour use the same
delivery records. Monitor Railway failed executions; no separate alerting service
is configured. Failed slots are not automatically backfilled in later hours.

Tests:

```sh
python3 -m unittest discover -s . -p 'test_*.py'
```

Read-only preview, with service variables securely injected:

```sh
python reporter.py --preview daily
python reporter.py --preview weekly
python reporter.py --preview daily --at 2026-10-26T23:59:59-07:00
```

Source repository: `mchien-truewind/gtm-deal-reports`.

The service is connected to this repository’s `main` branch and deploys
automatically when pull requests merge into `main`.
The repository root contains the Dockerfile and worker; no local machine is
needed at runtime. Connection status is managed in Railway.
