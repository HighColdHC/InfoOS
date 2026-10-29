-- Point-join active cards from the existing raw-item entry-time index.
create index idx_cards_active_raw on cards(raw_connector_id, raw_item_id, card_id) where status = 'active';
