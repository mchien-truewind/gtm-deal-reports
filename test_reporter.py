import sqlite3
import unittest
from datetime import datetime
from unittest.mock import patch

import reporter as r


class ReporterTests(unittest.TestCase):
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
        for timestamp in ('2026-10-27T15:00:00+00:00', '2026-11-02T16:00:00+00:00',
                          '2026-10-27T19:00:00+00:00', '2026-10-28T01:00:00+00:00',
                          '2026-10-28T04:00:00+00:00'):
            self.assertIsNotNone(r.scheduled_slot(datetime.fromisoformat(timestamp)))
        for timestamp in ('2026-10-31T15:00:00+00:00', '2026-10-27T20:00:00+00:00'):
            self.assertIsNone(r.scheduled_slot(datetime.fromisoformat(timestamp)))

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


if __name__ == '__main__':
    unittest.main()
