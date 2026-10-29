-- Provider circuits belonged exclusively to the retired built-in LLM pipeline.
-- Keep historical task events; append one safe audit event per removed circuit.
insert into task_events(event_id, event_type, status, payload, occurred_at)
select 'evt_retire_v0022_' || lower(hex(randomblob(16))),
       'runtime_circuit_retired', 'success',
       json_object('version', 1, 'migration', 22, 'reason_code', 'feature_retired',
                   'scope_kind', scope_kind, 'scope_key', scope_key,
                   'previous_state', state, 'previous_reason_code', reason_code,
                   'consecutive_failures', consecutive_failures),
       strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
from runtime_circuits
where scope_kind = 'provider'
   or (scope_kind = 'platform' and scope_key in ('scys', 'wechat'));

delete from runtime_circuits
where scope_kind = 'provider'
   or (scope_kind = 'platform' and scope_key in ('scys', 'wechat'));
