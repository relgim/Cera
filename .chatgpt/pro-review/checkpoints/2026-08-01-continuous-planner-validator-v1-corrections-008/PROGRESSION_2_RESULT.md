# Progression 2 Result

task_id: `continuous-record-write-schema-and-relationship-authority-v8`
status: `completed`
provider_calls: `0`

`cera.continuous_persistence_policy.v2` defines exact writable semantic roots
for Character and Relationship records. Every other top-level field is
immutable, including identity, schema, revision, visibility, knowledge owner,
participants, source/Genesis/provenance, authority, and index metadata.
Malformed and nested-escape JSON pointers fail before candidate application.

Relationship subjects must equal the target record's exact participant pair
and be justified by the cited final field's role ledger and private-owner
scope. Python validates the complete post-edit record, required identity,
participant pair, immutable metadata, next revision, and semantic field types
before directory promotion. Adversarial tests cover every requested protected
field, a real but unrelated relationship, and JSON-valid record invalidity;
approved add and replace operations remain atomic.
