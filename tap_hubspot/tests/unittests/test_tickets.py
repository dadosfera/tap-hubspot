import unittest
from unittest.mock import patch
from singer import metadata

from tap_hubspot import (Stream,
                         STREAMS,
                         get_metadata,
                         get_ticket_search_pages,
                         enrich_ticket_associations,
                         normalize_empty_numeric_properties,
                         read_crm_object_batch,
                         read_ticket_batch,
                         ticket_search_body)

mock_response_data = {
    "results": [{
        "updatedAt": "2022-08-18T12:57:17.587Z",
        "createdAt": "2019-08-06T02:43:01.930Z",
        "name": "hs_file_upload",
        "label": "File upload",
        "type": "string",
        "fieldType": "file",
        "description": "Files attached to a support form by a contact.",
        "groupName": "ticketinformation",
        "options": [],
        "displayOrder": -1,
        "calculated": False,
        "externalOptions": False,
        "hasUniqueValue": False,
        "hidden": False,
        "hubspotDefined": True,
        "modificationMetadata": {
            "archivable": True,
            "readOnlyDefinition": True,
            "readOnlyValue": False
        },
        "formField": True
    }]
}


class MockResponse:

    def __init__(self, json_data):
        self.json_data = json_data

    def json(self):
        return self.json_data


class MockContext:
    def get_catalog_from_id(self, stream_name):
        return {
            "stream": "tickets",
            "tap_stream_id": "tickets",
            "schema": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string"
                    },
                    "updatedAt": {
                        "type": [
                            "null",
                            "string"
                        ],
                        "format": "date-time"
                    },
                    "properties": {
                        "type": ["null", "object"],
                        "properties": {
                            "hs_all_team_ids": {
                                "type": [
                                    "null",
                                    "string"
                                ]
                            }
                        }
                    },
                    "property_hs_all_team_ids": {
                        "type": [
                            "null",
                            "string"
                        ]
                    }
                }
            },
            "metadata": [{
                "breadcrumb": [],
                "metadata": {
                    "table-key-properties": ["id"],
                    "forced-replication-method": "INCREMENTAL",
                    "valid-replication-keys": [
                        "updatedAt"
                    ],
                    "selected": True
                }
            },
                {
                    "breadcrumb": ["properties", "id"],
                    "metadata": {
                        "inclusion": "automatic"
                    }
                },

                {
                    "breadcrumb": ["properties", "updatedAt"],
                    "metadata": {
                        "inclusion": "automatic"
                    }
                },
                {
                    "breadcrumb": ["properties", "properties"],
                    "metadata": {
                        "inclusion": "available"
                    }
                },

                {
                    "breadcrumb": ["properties", "property_hs_all_team_ids"],
                    "metadata": {
                        "inclusion": "available",
                        "selected": True
                    }
                }
            ]
        }


class TestTickets(unittest.TestCase):

    def test_ticket_metadata_allows_full_or_incremental(self):
        stream = Stream('tickets', None, ['id'], 'updatedAt', None)
        schema = {'properties': {'id': {}, 'updatedAt': {}}}

        discovered_metadata = metadata.to_map(get_metadata(stream, schema))

        self.assertIsNone(metadata.get(discovered_metadata, (), 'forced-replication-method'))
        self.assertEqual(metadata.get(discovered_metadata, (), 'valid-replication-keys'), ['updatedAt'])

    def test_discovery_never_forces_replication_method(self):
        for stream in STREAMS:
            discovered_metadata = metadata.to_map(get_metadata(stream, {'properties': {}}))
            self.assertIsNone(
                metadata.get(discovered_metadata, (), 'forced-replication-method'),
                stream.tap_stream_id)

    def test_ticket_search_body_uses_last_modified_window(self):
        body = ticket_search_body(1000, 2000, 'subject,hs_pipeline')

        self.assertNotIn('properties', body)
        self.assertEqual(body['sorts'], ['hs_lastmodifieddate'])
        self.assertEqual(body['filterGroups'][0]['filters'], [
            {'propertyName': 'hs_lastmodifieddate', 'operator': 'GTE', 'value': '1000'},
            {'propertyName': 'hs_lastmodifieddate', 'operator': 'LT', 'value': '2000'},
        ])

    @patch('tap_hubspot.post_search_endpoint')
    def test_ticket_search_paginates_with_cursor(self, mocked_post):
        mocked_post.side_effect = [
            MockResponse({'total': 2, 'results': [{'id': '1'}], 'paging': {'next': {'after': 'cursor'}}}),
            MockResponse({'total': 2, 'results': [{'id': '2'}]}),
        ]

        pages = list(get_ticket_search_pages(1000, 2000, 'subject'))

        self.assertEqual(pages, [[{'id': '1'}], [{'id': '2'}]])
        self.assertEqual(mocked_post.call_count, 2)
        self.assertEqual(mocked_post.call_args_list[1].args[1]['after'], 'cursor')

    @patch('tap_hubspot.post_search_endpoint')
    def test_ticket_search_splits_a_window_at_the_api_limit(self, mocked_post):
        mocked_post.side_effect = [
            MockResponse({'total': 10000, 'results': []}),
            MockResponse({'total': 0, 'results': []}),
            MockResponse({'total': 0, 'results': []}),
        ]

        pages = list(get_ticket_search_pages(0, 10, 'subject'))

        self.assertEqual(pages, [[], []])
        self.assertEqual(mocked_post.call_count, 3)
        self.assertEqual(mocked_post.call_args_list[1].args[1]['filterGroups'][0]['filters'][1]['value'], '5')

    @patch('tap_hubspot.post_search_endpoint')
    def test_ticket_search_restores_associations_in_batches(self, mocked_post):
        mocked_post.side_effect = [
            MockResponse({'results': [{'from': {'id': '1'}, 'to': [{'id': '10', 'type': 'ticket_to_contact'}]}]}),
            MockResponse({'results': []}),
            MockResponse({'results': []}),
        ]
        records = [{'id': '1'}, {'id': '2'}]

        enriched = enrich_ticket_associations(records)

        self.assertEqual(enriched[0]['associations']['contacts']['results'], [
            {'id': '10', 'type': 'ticket_to_contact'}
        ])
        self.assertNotIn('associations', enriched[1])
        self.assertEqual(mocked_post.call_count, 3)

    @patch('tap_hubspot.post_search_endpoint')
    def test_ticket_batch_read_sends_properties_in_post_body(self, mocked_post):
        mocked_post.return_value = MockResponse({'results': [{'id': '1'}]})

        records = read_ticket_batch([{'id': '1'}], 'subject,hs_pipeline')

        self.assertEqual(records, [{'id': '1'}])
        self.assertEqual(mocked_post.call_args.args[1], {
            'inputs': [{'id': '1'}],
            'properties': ['subject', 'hs_pipeline'],
        })

    @patch('tap_hubspot.post_search_endpoint')
    def test_batch_reads_bound_records_and_properties(self, mocked_post):
        def response(_, body):
            return MockResponse({'results': [
                {'id': item['id'], 'properties': {field: item['id'] for field in body['properties']}}
                for item in body['inputs']
            ]})

        mocked_post.side_effect = response
        records = [{'id': str(index)} for index in range(26)]
        properties = ','.join('field_{}'.format(index) for index in range(26))

        result = read_crm_object_batch('tickets', records, properties)

        self.assertEqual(mocked_post.call_count, 4)
        self.assertEqual(len(result), 26)
        self.assertEqual(len(result[0]['properties']), 26)
        for call in mocked_post.call_args_list:
            self.assertLessEqual(len(call.args[1]['inputs']), 25)
            self.assertLessEqual(len(call.args[1]['properties']), 25)

    def test_empty_numeric_property_becomes_null(self):
        record = {'properties': {'engagement_score_threshold': '', 'description': ''}}
        schema = {'properties': {'properties': {'properties': {
            'engagement_score_threshold': {'type': ['null', 'number', 'string']},
            'description': {'type': ['null', 'string']},
        }}}}

        normalized = normalize_empty_numeric_properties(record, schema)

        self.assertIsNone(normalized['properties']['engagement_score_threshold'])
        self.assertEqual(normalized['properties']['description'], '')
