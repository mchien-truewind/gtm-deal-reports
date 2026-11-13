import sqlite3
import unittest
from datetime import datetime
from unittest.mock import patch

import reporter as r


class ReporterTests(unittest.TestCase):
    def setUp(self):
        passthrough = patch.object(r, "qualifying_deals", side_effect=lambda rows: rows)
        passthrough.start()
        self.addCleanup(passthrough.stop)

    def test_only_zero_deal_6pm_pacific_reports_mention_team(self):
        for timestamp, mentions_expected in (
            ('2026-10-27T08:00:00-07:00', False),
            ('2026-10-27T12:00:00-07:00', False),
            ('2026-10-28T01:00:00+00:00', True),
            ('2026-11-03T02:00:00+00:00', True),
        ):
            with self.subTest(timestamp=timestamp), patch.object(r, 'deals_between', return_value=[]), patch.object(r, 'hub', return_value={'options': []}):
                text = r.render(datetime.fromisoformat(timestamp))[0]
                self.assertIn('Deals created today: 0', text)
                for user_id in ('U0BH19KJY1L', 'U0AKMHVCJMA', 'U09QC3B292R', 'U0B172VJEBD', 'U0C0PEEB88G', 'U04BPMPR29G'):
                    self.assertEqual(f'<@{user_id}>' in text, mentions_expected)
                self.assertEqual('what are your priorities tomorrow to increase our SQL count?' in text, mentions_expected)

    def test_weekly_zero_deals_does_not_mention_team(self):
        with patch.object(r, 'deals_between', return_value=[]):
            text = r.render(datetime.fromisoformat('2026-10-27T21:00:00-07:00'), weekly=True)[0]
        self.assertIn('Deals created this week so far: 0', text)
        self.assertNotIn('<@', text)
        self.assertNotIn('priorities tomorrow', text)

    def test_pacific_schedule_across_dst_and_weekends(self):
        for timestamp in ('2026-10-27T19:00:00+00:00', '2026-11-02T20:00:00+00:00', '2026-10-28T01:00:00+00:00',
                          '2026-10-28T04:00:00+00:00'):
            self.assertIsNotNone(r.scheduled_slot(datetime.fromisoformat(timestamp)))
        for timestamp in ('2026-10-27T15:00:00+00:00', '2026-11-02T16:00:00+00:00',
                          '2026-10-31T19:00:00+00:00', '2026-10-27T20:00:00+00:00'):
            self.assertIsNone(r.scheduled_slot(datetime.fromisoformat(timestamp)))

    def test_weekly_sources_count_every_deal_and_use_display_labels(self):
        rows = [{'id': str(i), 'properties': {'deal_source': source}}
                for i, source in enumerate(('referral', 'referral', 'outbound', None, 'legacy'))]
        now = datetime.fromisoformat('2026-10-29T21:00:00-07:00')
        with patch.object(r, 'deals_between', return_value=rows) as query, patch.object(r, 'hub', return_value={
            'options': [{'value': 'referral', 'label': 'Partner & Referral'},
                        {'value': 'outbound', 'label': 'Outbound'}]
        }):
            text = '\n'.join(r.render(now, weekly=True))
        self.assertEqual(query.call_args.args[0].isoformat(), '2026-10-26T00:00:00-07:00')
        self.assertEqual(query.call_args.args[1], now)
        self.assertIn('Deals created this week so far: 5', text)
        self.assertIn('• Partner &amp; Referral: 2', text)
        self.assertIn('• Outbound: 1', text)
        self.assertIn('• Source missing — attribution needs review: 1', text)
        self.assertIn('• legacy: 1', text)
        self.assertNotIn('<@', text)

    def test_weekly_sources_split_without_losing_entries(self):
        rows = [{'id': str(i), 'properties': {'deal_source': f'Source {i:03} ' + 'x' * 80}}
                for i in range(100)]
        with patch.object(r, 'deals_between', return_value=rows), patch.object(r, 'hub', return_value={'options': []}):
            messages = r.render(datetime.fromisoformat('2026-10-29T21:00:00-07:00'), weekly=True)
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(message) <= 3000 for message in messages))
        text = '\n'.join(messages)
        for row in rows:
            self.assertEqual(text.count('• ' + row['properties']['deal_source'] + ': 1'), 1)
        self.assertIn('Deal sources this week — continued', messages[1])

    def test_monday_and_midnight_boundaries(self):
        now = datetime.fromisoformat('2026-10-27T04:03:38+00:00')
        self.assertEqual(r.window(now)[0].isoformat(), '2026-10-26T00:00:00-07:00')
        sunday = datetime.fromisoformat('2026-11-02T04:00:00+00:00')
        self.assertEqual(r.window(sunday, True)[0].isoformat(), '2026-10-26T00:00:00-07:00')
        monday = datetime.fromisoformat('2026-11-02T17:00:00+00:00')
        self.assertEqual(r.window(monday, True)[0].isoformat(), '2026-11-02T00:00:00-08:00')

    def test_all_deals_paginated_without_name_deduplication(self):
        first = {'id': '1', 'properties': {'createdate': '2026-10-27T08:00:00Z', 'dealname': 'Same'}}
        second = {'id': '2', 'properties': {'createdate': '2026-10-27T08:00:00Z', 'dealname': 'Same'}}
        with patch.object(r, 'hub', side_effect=[{'total': 2, 'results': [first], 'paging': {'next': {'after': '1'}}},
                                               {'total': 2, 'results': [second]}]) as call:
            start, end = r.window(datetime.fromisoformat('2026-10-27T18:00:00-07:00'))
            self.assertEqual(len(r.deals_between(start, end)), 2)
            filters = call.call_args_list[0].args[1]['filterGroups'][0]['filters']
            self.assertEqual([item['propertyName'] for item in filters], ['createdate', 'createdate'])

    def test_source_label_and_pacific_creation_date(self):
        row = {'id': '66903546706', 'properties': {'createdate': '2026-10-27T04:03:38.097Z', 'deal_source': 'Referral'}}
        with patch.object(r, 'deals_between', return_value=[row]), patch.object(r, 'company_names', return_value={row['id']: 'GRF CPAs & Advisors'}), patch.object(r, 'hub', return_value={'options': [{'value': 'Referral', 'label': 'Inbound - Referral'}]}):
            text = r.render(datetime.fromisoformat('2026-10-27T18:00:00-07:00'))[0]
            self.assertIn('10/26/2026 09:03 PM PDT', text)
            self.assertIn('Inbound - Referral', text)
            self.assertIn('GRF CPAs &amp; Advisors', text)
            self.assertIn('Owner: Unassigned', text)
            self.assertNotIn('<@', text)
            self.assertNotIn('priorities tomorrow', text)

    def test_active_and_archived_owner_names_use_owner_ids(self):
        deals = [{'properties': {'hubspot_owner_id': value}} for value in ('123', '456', '999')]
        responses = [
            {'results': [{'id': '123', 'userId': '456', 'firstName': 'Alex', 'lastName': 'Smith'}], 'paging': {'next': {'after': 'next'}}},
            {'results': []},
            {'results': [{'id': '456', 'firstName': 'Taylor', 'lastName': 'Jones'}]},
        ]
        with patch.object(r, 'hub', side_effect=responses) as call:
            names = r.owner_names(deals)
            self.assertEqual(names, {'123': 'Alex Smith', '456': 'Taylor Jones'})
            self.assertIn('after=next', call.call_args_list[1].args[0])
            self.assertIn('archived=true', call.call_args_list[2].args[0])

    def test_owner_display_in_each_deal_line(self):
        row = {'id': '1', 'properties': {'createdate': '2026-10-27T04:03:38Z', 'hubspot_owner_id': '123'}}
        with patch.object(r, 'deals_between', return_value=[row]), patch.object(r, 'company_names', return_value={'1': 'Company'}), patch.object(r, 'owner_names', return_value={'123': 'Alex Smith'}), patch.object(r, 'hub', return_value={'options': []}):
            text = r.render(datetime.fromisoformat('2026-10-26T22:00:00-07:00'))[0]
            self.assertIn('Owner: Alex Smith', text)

    def test_sdr_attribution_appears_in_daily_and_weekly_reports(self):
        now = datetime.fromisoformat('2026-11-13T12:01:00-08:00')
        deal = {'id': '1', 'properties': {
            'createdate': '2026-11-13T17:20:00Z', 'deal_source': 'sales',
            'hubspot_owner_id': 'ae', 'dealname': 'Example',
        }}
        call = {'id': '3', 'properties': {
            'hs_timestamp': '2026-11-13T17:25:00Z',
            'hs_call_disposition': r.BOOKING_DISPOSITION,
            'hubspot_owner_id': 'sdr',
        }}

        def hub(path, payload=None):
            if path.endswith('deal_source'):
                return {'options': [{'value': 'sales', 'label': 'Outbound - SDR'}]}
            if '/associations/contacts?' in path:
                return {'results': [{'toObjectId': 2}]}
            if '/associations/calls?' in path:
                return {'results': [{'toObjectId': 3}]}
            if path.endswith('/calls/batch/read'):
                return {'status': 'COMPLETE', 'results': [call]}
            self.fail(f'Unexpected HubSpot endpoint: {path}')

        with patch.object(r, 'deals_between', return_value=[deal]), patch.object(
            r, 'company_names', return_value={'1': 'Example'}
        ), patch.object(r, 'hub', side_effect=hub), patch.object(
            r, 'owner_names', return_value={'sdr': 'Ocean Marquez', 'ae': 'Xavier Marco'}
        ):
            noon = r.render(now)[0]
            evening = r.render(now.replace(hour=18))[0]
            weekly = r.render(now.replace(hour=21), weekly=True)[0]

        for message in (noon, evening, weekly):
            self.assertIn('SDR: Ocean Marquez', message)
        self.assertNotIn('Owner:', noon)
        self.assertIn('Owner: Xavier Marco | SDR: Ocean Marquez', evening)
        self.assertIn('Outbound - SDR: 1', weekly)

    def test_direct_booking_call_can_resolve_without_contact_call_activity(self):
        deal = {'properties': {
            'createdate': '2026-11-13T17:20:00Z',
            'description': 'Created from a Nooks booked meeting. Nooks workspace/call-id',
        }}
        response = {'results': [{'properties': {'hubspot_owner_id': 'sdr'}}]}
        with patch.object(r, 'hub', return_value=response) as lookup:
            owners, has_activity = r.booking_call_owners(
                deal, set(), {}, {}, datetime.fromisoformat('2026-11-13T20:01:00+00:00')
            )
        self.assertEqual(owners, {'sdr'})
        self.assertTrue(has_activity)
        filters = lookup.call_args.args[1]['filterGroups'][0]['filters']
        self.assertEqual({item['value'] for item in filters}, {'workspace', 'call-id'})

    def test_failed_sdr_lookup_is_not_reported_as_no_call_activity(self):
        deal = {'id': '1', 'properties': {'deal_source': 'Outbound - SDR'}}
        with patch.object(r, 'hub', side_effect=RuntimeError('permission denied')):
            result = r.sdr_attribution([deal], {}, datetime.now(r.PT))
        self.assertEqual(result, {'1': 'Unverified—lookup unavailable'})

    def test_partial_slack_failure_does_not_repeat_successful_parts(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE deliveries (id TEXT PRIMARY KEY, state TEXT NOT NULL)')
        slot = datetime.fromisoformat('2026-10-27T08:00:00-07:00')
        with patch.object(r, 'slack', side_effect=[{'ok': True}, TimeoutError('timeout')]):
            with self.assertRaises(TimeoutError):
                r.deliver(db, 'slot', ['one', 'two'], slot)
        with patch.object(r, 'slack', return_value={'messages': [{'bot_id': 'B0AMUHSD2BX', 'metadata': {'event_type': 'gtm_deal_report', 'event_payload': {'report_id': 'slot:1'}}}]}) as call:
            r.deliver(db, 'slot', ['one', 'two'], slot)
            self.assertEqual(call.call_count, 1)
            self.assertTrue(call.call_args.args[0].startswith('conversations.history'))
        self.assertEqual(db.execute("SELECT count(*) FROM deliveries WHERE state='sent'").fetchone()[0], 2)

    def test_ambiguous_delivery_does_not_blindly_repost(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE deliveries (id TEXT PRIMARY KEY, state TEXT NOT NULL)')
        db.execute("INSERT INTO deliveries VALUES ('slot:0','sending')")
        with patch.object(r, 'slack', return_value={'messages': []}) as call:
            with self.assertRaisesRegex(RuntimeError, 'Ambiguous Slack'):
                r.deliver(db, 'slot', ['one'], datetime.now(r.PT))
            self.assertEqual(call.call_count, 1)


class QualificationTests(unittest.TestCase):
    def qualify(self, emails, properties=None):
        rows = [{'id': 'deal', 'properties': properties or {}}]
        responses = [
            {'status': 'COMPLETE', 'results': [{'from': {'id': 'deal'}, 'to': [{'toObjectId': str(i)} for i in range(len(emails))]}]},
            {'status': 'COMPLETE', 'results': [{'id': str(i), 'properties': email} for i, email in enumerate(emails)]},
        ]
        with patch.object(r, 'hub', side_effect=responses):
            return r.qualifying_deals(rows)

    def test_internal_primary_or_secondary_excludes_entire_deal(self):
        for email in [{'email': 'TEST@TRYTRUEWIND.COM '},
                      {'email': 'prospect@example.com', 'hs_additional_emails': 'other@example.com;test@trytruewind.com'}]:
            self.assertEqual(self.qualify([{'email': 'real@example.com'}, email]), [])

    def test_internal_meeting_is_excluded(self):
        self.assertEqual(self.qualify([{'email': 'test@trytruewind.com'}],
                         {'calendly_invitee_uri': 'booking', 'truewind_demo_request_contact_id': '1'}), [])

    def test_external_confirmed_booking_and_missing_source_are_preserved(self):
        rows = self.qualify([{'email': 'prospect@example.com'}],
                            {'calendly_invitee_uri': 'booking', 'truewind_demo_request_contact_id': '1'})
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['properties'].get('deal_source'))

    def test_historical_form_only_deals_are_excluded_without_deleting_them(self):
        self.assertEqual(self.qualify([{'email': 'prospect@example.com'}],
                         {'truewind_demo_request_contact_id': '1'}), [])

    def test_similar_domains_do_not_exclude_real_prospects(self):
        self.assertEqual(len(self.qualify([{'email': 'test@trytruewind.com.example.org'}])), 1)

    def test_missing_or_partial_evidence_stops_report(self):
        for response in [{'status': 'COMPLETE', 'results': []},
                         {'status': 'PENDING', 'results': []},
                         {'status': 'COMPLETE', 'results': [], 'errors': ['permission denied']},
                         {'status': 'COMPLETE', 'results': [{'from': {'id': 'deal'}, 'to': [], 'paging': {'next': {'after': '1'}}}]}]:
            with self.subTest(response=response), patch.object(r, 'hub', return_value=response):
                with self.assertRaises(RuntimeError):
                    r.qualifying_deals([{'id': 'deal', 'properties': {}}])


if __name__ == '__main__':
    unittest.main()
