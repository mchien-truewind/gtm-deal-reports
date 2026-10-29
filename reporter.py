"""Independent, read-only HubSpot deal reports posted by Mercedes's Slack bot."""
import argparse
from collections import Counter
import html
import json
import os
import signal
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PT = ZoneInfo('America/Los_Angeles')
CHANNEL = 'C08GM9QL7QC'
PORTAL = '43974586'
# User ID Lookups, Sheet1!A2:B7; verified October 27, 2026.
ZERO_DEAL_RECIPIENTS = (
    'U0BH19KJY1L',  # Dillon Mullaney
    'U0AKMHVCJMA',  # Xavier Marco
    'U09QC3B292R',  # Sarah Elix
    'U0B172VJEBD',  # Nicole Shen
    'U0C0PEEB88G',  # Elizabeth (Liz) Katzki
    'U04BPMPR29G',  # Alex Lee
)


def api(base, path, token, payload=None, write=False):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
            if base.endswith('slack.com/api/') and not result.get('ok'):
                raise RuntimeError('Slack: ' + result.get('error', 'unknown_error'))
            return result
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < 3:
                time.sleep(min(int(error.headers.get('Retry-After', '5')), 60))
                continue
            if not write and error.code >= 500 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError('Provider HTTP error: ' + str(error.code)) from None
    raise RuntimeError('Provider retry limit reached')


def hub(path, payload=None):
    return api('https://api.hubapi.com', path, os.environ['HUBSPOT_PRIVATE_TOKEN'], payload)


def slack(method, payload=None, write=False):
    return api('https://slack.com/api/', method, os.environ['SLACK_BOT_TOKEN'], payload, write)


def window(now, weekly=False):
    local = now.astimezone(PT)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if weekly:
        start -= timedelta(days=start.weekday())
    return start, local


def scheduled_slot(now):
    local = now.astimezone(PT)
    if local.weekday() >= 5 or local.hour not in (12, 18, 21):
        return None
    return local.replace(minute=0, second=0, microsecond=0)


def deals_between(start, end):
    filters = [
        {'propertyName': 'createdate', 'operator': 'GTE', 'value': str(int(start.timestamp() * 1000))},
        {'propertyName': 'createdate', 'operator': 'LT', 'value': str(int(end.timestamp() * 1000))},
    ]
    rows = {}
    after = None
    for _ in range(50):
        body = {'filterGroups': [{'filters': filters}], 'properties': ['dealname', 'createdate', 'deal_source', 'hubspot_owner_id'],
                'sorts': ['createdate'], 'limit': 200}
        if after is not None:
            body['after'] = after
        result = hub('/crm/v3/objects/deals/search', body)
        if result.get('total', 0) > 10000:
            raise RuntimeError('Report exceeds HubSpot search limit; refusing a partial count')
        for row in result['results']:
            rows[row['id']] = row
        after = result.get('paging', {}).get('next', {}).get('after')
        if after is None:
            return sorted(rows.values(), key=lambda row: (row['properties']['createdate'], row['id']))
        time.sleep(.25)
    raise RuntimeError('Pagination limit exceeded')


def batches(items, size=100):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def company_names(deals):
    associations = {}
    for batch in batches([row['id'] for row in deals]):
        result = hub('/crm/v4/associations/deals/companies/batch/read', {'inputs': [{'id': value} for value in batch]})
        if result.get('errors') or result.get('status') != 'COMPLETE':
            raise RuntimeError('Incomplete company association lookup')
        for row in result['results']:
            if row.get('paging', {}).get('next'):
                raise RuntimeError('Company association pagination requires investigation')
            associations[str(row['from']['id'])] = sorted({str(item['toObjectId']) for item in row['to']})
    names = {}
    ids = sorted({value for values in associations.values() for value in values})
    for batch in batches(ids):
        result = hub('/crm/v3/objects/companies/batch/read', {'properties': ['name'], 'inputs': [{'id': value} for value in batch]})
        if result.get('errors') or result.get('status') != 'COMPLETE':
            raise RuntimeError('Incomplete company name lookup')
        for row in result['results']:
            names[row['id']] = row['properties'].get('name') or 'Unnamed company'
    return {deal_id: ' / '.join(names[value] for value in values) for deal_id, values in associations.items()}


def escape(value):
    return html.escape(str(value), quote=False).replace('\n', ' ')


def owner_names(deals):
    needed = {str(row['properties']['hubspot_owner_id']) for row in deals if row['properties'].get('hubspot_owner_id')}
    names = {}
    for archived in (False, True):
        after = None
        for _ in range(20):
            if needed.issubset(names):
                return names
            query = {'limit': 100, 'archived': str(archived).lower()}
            if after is not None:
                query['after'] = after
            result = hub('/crm/v3/owners?' + urllib.parse.urlencode(query))
            for owner in result['results']:
                owner_id = str(owner['id'])
                if owner_id in needed:
                    full_name = ' '.join(value.strip() for value in (owner.get('firstName') or '', owner.get('lastName') or '') if value.strip())
                    names[owner_id] = full_name or owner.get('email') or f'Owner name unavailable (ID {owner_id})'
            after = result.get('paging', {}).get('next', {}).get('after')
            if after is None:
                break
        else:
            raise RuntimeError('Owner pagination limit exceeded')
    return names


def render(now, weekly=False):
    start, end = window(now, weekly)
    deals = deals_between(start, end)
    as_of = end.strftime('%b %d, %Y at %I:%M %p %Z')
    labels = {option['value']: option['label'] for option in hub('/crm/v3/properties/deals/deal_source')['options']} if deals else {}
    if weekly:
        header = f'*Week To Date*\n*Deals created this week so far: {len(deals)}*\nMonday {start:%b %d} through {as_of}.\nAll stages and pipelines; HubSpot deal creation date.\n*Deal sources this week:*'
        sources = Counter()
        for row in deals:
            raw = row['properties'].get('deal_source')
            source = labels.get(raw, raw) if raw else 'Source not set'
            sources[source] += 1
        lines = [f'• {escape(source)}: {count}' for source, count in sorted(sources.items(), key=lambda item: (-item[1], item[0]))]
        if not lines:
            lines = ['No deals created this week so far.']
        return split_messages(header, lines, '*Week To Date*\n*Deal sources this week — continued*')
    companies = company_names(deals)
    owners = owner_names(deals)
    period = 'am' if end.hour < 12 else 'pm'
    title = f'Daily {end.hour % 12 or 12}{period} Report:'
    header = f'*{title}*\n*Deals created today: {len(deals)}*\nSince midnight Pacific • As of {as_of}'
    lines = []
    for row in deals:
        props = row['properties']
        created = datetime.fromisoformat(props['createdate'].replace('Z', '+00:00')).astimezone(PT)
        name = companies.get(row['id']) or ('Company not set — ' + (props.get('dealname') or 'Unnamed deal'))
        raw = props.get('deal_source')
        source = labels.get(raw, raw) if raw else 'Source not set'
        owner_id = props.get('hubspot_owner_id')
        owner = owners.get(str(owner_id), f'Owner unavailable (ID {owner_id})') if owner_id else 'Unassigned'
        lines.append(f'• <https://app.hubspot.com/contacts/{PORTAL}/record/0-3/{row["id"]}/|{escape(name)}> | {created:%m/%d/%Y %I:%M %p %Z} | {escape(source)} | Owner: {escape(owner)}')
    if not lines:
        lines = ['No deals created today so far.']
        if end.hour == 18:
            mentions = ' '.join(f'<@{user_id}>' for user_id in ZERO_DEAL_RECIPIENTS)
            lines.append(f'{mentions} — what are your priorities tomorrow to increase our SQL count?')
    return split_messages(header, lines, f'*{title}*\n*Deals created today — continued*')


def split_messages(header, lines, continued_header):
    messages = []
    current = header
    for line in lines:
        if len(current) + len(line) > 3000:
            messages.append(current)
            current = continued_header
        current += '\n' + line
    messages.append(current)
    return messages


def verify_identity():
    auth = slack('auth.test', {})
    if auth.get('team_id') != 'T04B95QKL4X' or auth.get('bot_id') != 'B0AMUHSD2BX':
        raise RuntimeError('Slack identity does not match Truewind Mercedes')
    channel = slack('conversations.info?channel=' + CHANNEL)['channel']
    if channel.get('name') != 'gtm-general' or not channel.get('is_member'):
        raise RuntimeError('Mercedes is not a member of the expected channel')
    account = hub('/account-info/v3/details')
    if str(account.get('portalId')) != PORTAL:
        raise RuntimeError('HubSpot portal does not match Truewind')


def deliver(db, key, messages, slot):
    # Persist the exact payload before any remote write. Never blindly retry an ambiguous post.
    for index, text in enumerate(messages):
        part = f'{key}:{index}'
        row = db.execute('SELECT state FROM deliveries WHERE id=?', (part,)).fetchone()
        if row and row[0] == 'sent':
            continue
        if row:
            cursor = ''
            found = False
            for _ in range(20):
                query = urllib.parse.urlencode({'channel': CHANNEL, 'oldest': str(slot.timestamp()), 'limit': 200, 'cursor': cursor, 'include_all_metadata': 'true'})
                result = slack('conversations.history?' + query)
                for message in result['messages']:
                    metadata = message.get('metadata', {})
                    if message.get('bot_id') == 'B0AMUHSD2BX' and metadata.get('event_type') == 'gtm_deal_report' and metadata.get('event_payload', {}).get('report_id') == part:
                        found = True
                cursor = result.get('response_metadata', {}).get('next_cursor', '')
                if not cursor:
                    break
            if not found:
                raise RuntimeError('Ambiguous Slack delivery; manual investigation required for ' + part)
        else:
            with db:
                db.execute('INSERT INTO deliveries VALUES (?, ?)', (part, 'sending'))
            slack('chat.postMessage', {'channel': CHANNEL, 'text': text, 'unfurl_links': False, 'unfurl_media': False,
                  'metadata': {'event_type': 'gtm_deal_report', 'event_payload': {'report_id': part}}}, write=True)
        with db:
            db.execute('UPDATE deliveries SET state=? WHERE id=?', ('sent', part))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview', choices=['daily', 'weekly'])
    parser.add_argument('--at', help='ISO timestamp for read-only preview')
    args = parser.parse_args()
    if args.at and not args.preview:
        raise RuntimeError('--at requires --preview')
    now = datetime.fromisoformat(args.at) if args.at else datetime.now(PT)
    if now.tzinfo is None:
        raise RuntimeError('Timestamp requires an explicit timezone')
    slot = scheduled_slot(now)
    if not args.preview and (not slot or os.environ.get('REPORT_ENABLED') != 'true'):
        print(json.dumps({'status': 'outside_schedule_or_disabled'}))
        return
    verify_identity()
    if args.preview:
        for message in render(now, args.preview == 'weekly'):
            print(message)
        return
    path = os.environ.get('REPORT_STATE_PATH', '/data/reports.sqlite')
    with sqlite3.connect(path, timeout=30) as db:
        db.execute('CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY, state TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        key = slot.isoformat()
        # A process-level exclusive lock on the persistent volume prevents overlapping workers.
        import fcntl
        with open(path + '.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            row = db.execute('SELECT payload FROM reports WHERE id=?', (key,)).fetchone()
            if row:
                messages = json.loads(row[0])
            else:
                messages = render(now, slot.hour == 21)
                with db:
                    db.execute('INSERT INTO reports VALUES (?, ?)', (key, json.dumps(messages)))
            deliver(db, key, messages, slot)
    print(json.dumps({'status': 'delivered', 'slot': key, 'parts': len(messages)}))


if __name__ == '__main__':
    try:
        def deadline(_signal, _frame):
            raise TimeoutError('Report exceeded its 15-minute runtime limit')

        signal.signal(signal.SIGALRM, deadline)
        signal.alarm(900)
        main()
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__, 'error': str(error)[:300]}), file=sys.stderr)
        sys.exit(1)
