alter table opportunity_breakdowns add column profile_version integer check (profile_version > 0);
alter table opportunity_breakdowns add column raw_item_revision integer check (raw_item_revision >= 0);
alter table opportunity_verifications add column client_verification_id text;
alter table opportunity_verifications add column request_content_hash text;
alter table opportunity_verifications add column breakdown_id text;
alter table opportunity_verifications add column expected_breakdown_hash text;
alter table opportunity_verifications add column breakdown_snapshot text;
create unique index idx_opportunity_verifications_client
  on opportunity_verifications(token_id, client_verification_id);
